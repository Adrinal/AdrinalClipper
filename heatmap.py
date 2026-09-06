"""
heatmap.py — best-effort extraction of YouTube's "most replayed" data.

IMPORTANT HONESTY NOTE: This is UNDOCUMENTED and UNOFFICIAL. YouTube does not
publish an API for this. We're parsing internal fields from the same JSON
YouTube's own player page loads, which:
  - can change format or disappear at any time without notice
  - is not guaranteed to exist for every video (needs enough view data)
  - should NOT be hammered aggressively (one request per video, no retries-loop)

This function MUST fail gracefully — returning None is the correct, expected
outcome for many videos, not a bug. Every caller must treat None as "fall
back to transcript-only" rather than as an error to surface to the user.
"""

import logging
import re
import json
import requests

logger = logging.getLogger("openshorts.heatmap")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


def get_heatmap(video_id: str) -> list[dict] | None:
    """
    Attempts to fetch replay heatmap markers for a video.
    Returns a list of {start_seconds, end_seconds, intensity} dicts sorted by
    intensity descending, or None if unavailable for any reason.
    """
    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()

        # YouTube embeds a large JSON blob assigned to ytInitialData in the page.
        match = re.search(r"var ytInitialData = ({.*?});</script>", resp.text)
        if not match:
            logger.info(f"[{video_id}] ytInitialData not found in page — heatmap unavailable.")
            return None

        data = json.loads(match.group(1))

        # The heatmap lives under a deeply nested, undocumented path that YouTube
        # has moved before and will move again. We search for it defensively
        # rather than hardcoding one exact path.
        markers = _find_heatmap_markers(data)
        if not markers:
            logger.info(f"[{video_id}] No heatmap markers found (video may lack enough views/data).")
            return None

        logger.info(f"[{video_id}] Heatmap found: {len(markers)} markers.")
        return sorted(markers, key=lambda m: -m["intensity"])

    except Exception as e:
        # Broad catch is deliberate here: any failure in this best-effort path
        # must degrade to None, never crash the pipeline or block clipping.
        logger.warning(f"[{video_id}] Heatmap scrape failed ({type(e).__name__}: {e}). Falling back to transcript-only.")
        return None


def _find_heatmap_markers(data: dict) -> list[dict]:
    """Walk the ytInitialData tree looking for heatmap marker structures."""
    markers = []

    def walk(node):
        if isinstance(node, dict):
            if "heatMarkerRenderer" in node:
                hm = node["heatMarkerRenderer"]
                try:
                    start_ms = hm["timeRangeStartMillis"]
                    duration_ms = hm["markerDurationMillis"]
                    intensity = hm.get("heatMarkerIntensityScoreNormalized", 0)
                    markers.append({
                        "start_seconds": start_ms / 1000,
                        "end_seconds": (start_ms + duration_ms) / 1000,
                        "intensity": intensity,
                    })
                except KeyError:
                    pass
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return markers
