"""
clipper.py — finds candidate clip moments via Gemini, cuts them with ffmpeg,
and reframes to 9:16 vertical.

Design note on 9:16 reframing: true "smart" reframing (tracking a face/speaker
around the frame) needs a face-detection pass per frame, which is expensive
on a phone CPU. We do a center-crop to 9:16 by default — reliable, fast,
correct most of the time for talking-head podcast/interview content. This
is a deliberate speed/quality tradeoff for phone hardware, not an oversight.
"""

import json
import logging
import subprocess
from pathlib import Path
from dataclasses import dataclass

from google import genai

logger = logging.getLogger("openshorts.clipper")


@dataclass
class ClipCandidate:
    start_seconds: float
    end_seconds: float
    reason: str
    title: str


class ClippingError(Exception):
    pass


def _load_transcript(subtitle_path: Path | None) -> str:
    if subtitle_path is None or not subtitle_path.exists():
        return ""
    # Strip SRT numbering/timestamps down to readable text with rough timing,
    # so Gemini gets time-anchored text without megabytes of formatting noise.
    lines = subtitle_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    out = []
    for line in lines:
        line = line.strip()
        if not line or line.isdigit():
            continue
        if "-->" in line:
            # convert "00:01:23,456 --> 00:01:26,000" into a compact marker
            start = line.split("-->")[0].strip().replace(",", ".")
            out.append(f"\n[{start}]")
        else:
            out.append(line)
    return " ".join(out)


def find_clip_moments(
    transcript_text: str,
    duration: float,
    max_clips: int,
    heatmap_markers: list[dict] | None,
    api_key: str,
) -> list[ClipCandidate]:
    """
    Asks Gemini to identify the best short-clip moments.
    Raises ClippingError if Gemini can't be reached or returns unusable output —
    this is a hard requirement (no transcript = no clips), so we do not fall
    back silently here the way we do for the heatmap.
    """
    if not transcript_text:
        raise ClippingError(
            "No transcript available for this video, and Gemini needs either "
            "a transcript or direct video access to find moments. This video "
            "likely has captions disabled."
        )

    client = genai.Client(api_key=api_key)

    heatmap_note = ""
    if heatmap_markers:
        top = heatmap_markers[:10]
        heatmap_note = (
            "\n\nViewer replay data (moments other viewers rewatched most, "
            "highest intensity first — treat as a strong hint, not a rule):\n"
            + "\n".join(
                f"- {m['start_seconds']:.0f}s to {m['end_seconds']:.0f}s "
                f"(intensity {m['intensity']:.2f})"
                for m in top
            )
        )

    prompt = f"""You are selecting the best short-form clips from a {duration:.0f}-second video transcript for a YouTube Shorts/TikTok/Reels channel that clips podcasts and YouTube videos.

Find up to {max_clips} moments that would work as standalone vertical clips: strong opinions, jokes, surprising reveals, emotional peaks, or self-contained stories. Each clip should be 20-90 seconds long and make sense without the rest of the video.

Transcript (timestamps in brackets):
{transcript_text[:50000]}
{heatmap_note}

Respond with ONLY a JSON array, no other text, in this exact format:
[{{"start_seconds": 12.5, "end_seconds": 55.0, "title": "short catchy title", "reason": "why this moment works as a clip"}}]
"""

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        raw = response.text.strip()
        # Gemini sometimes wraps JSON in markdown fences despite instructions
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        parsed = json.loads(raw)
    except Exception as e:
        raise ClippingError(f"Gemini call or JSON parse failed: {type(e).__name__}: {e}")

    candidates = []
    for item in parsed:
        try:
            candidates.append(ClipCandidate(
                start_seconds=float(item["start_seconds"]),
                end_seconds=float(item["end_seconds"]),
                title=item["title"],
                reason=item["reason"],
            ))
        except (KeyError, ValueError, TypeError):
            logger.warning(f"Skipping malformed clip candidate from Gemini: {item}")

    if not candidates:
        raise ClippingError("Gemini returned no usable clip candidates.")

    return candidates


def cut_and_reframe(
    source_video: Path,
    candidate: ClipCandidate,
    output_dir: Path,
    output_width: int,
    output_height: int,
    index: int,
) -> Path:
    """
    Cuts [start, end] from source_video and center-crops to output_width x
    output_height (9:16). Raises ClippingError on ffmpeg failure with the
    real stderr, not a swallowed generic message.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    duration = candidate.end_seconds - candidate.start_seconds
    if duration <= 0:
        raise ClippingError(f"Invalid clip duration for candidate {index}: {duration}s")

    safe_title = "".join(c for c in candidate.title if c.isalnum() or c in " -_")[:40].strip()
    out_path = output_dir / f"clip_{index}_{safe_title or 'untitled'}.mp4"

    # Scale so the SHORTER dimension fills the target, then crop the excess —
    # this is the center-crop approach. -2 keeps dimensions even (required by
    # most encoders).
    vf = (
        f"scale=w={output_width}:h={output_height}:force_original_aspect_ratio=increase,"
        f"crop={output_width}:{output_height}"
    )

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(candidate.start_seconds),
        "-i", str(source_video),
        "-t", str(duration),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        str(out_path),
    ]

    logger.info(f"Cutting clip {index}: {candidate.start_seconds}s-{candidate.end_seconds}s -> {out_path.name}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    if result.returncode != 0:
        raise ClippingError(
            f"ffmpeg failed on clip {index} (exit {result.returncode}). "
            f"stderr tail: {result.stderr[-800:]}"
        )

    if not out_path.exists() or out_path.stat().st_size == 0:
        raise ClippingError(f"ffmpeg reported success but output missing/empty: {out_path}")

    return out_path
