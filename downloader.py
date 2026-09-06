"""
downloader.py — fetches a YouTube video + its transcript/captions.

Uses yt-dlp, which handles YouTube's stream extraction (aria2c alone can't —
it has no idea how to resolve a YouTube URL into a real media stream, it just
accelerates whatever URL it's given). We hand yt-dlp's downloaded fragments
to aria2c as the external downloader for speed, matching your existing
aria2c preference.
"""

import os
import subprocess
import json
import logging
from pathlib import Path

logger = logging.getLogger("openshorts.downloader")


class DownloadError(Exception):
    pass


def download_video(url: str, output_dir: Path) -> dict:
    """
    Downloads video + auto captions if available.
    Returns dict with: video_path, subtitle_path (or None), title, duration, video_id.
    Raises DownloadError with a clear reason on failure — never fails silently.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    out_template = str(output_dir / "%(id)s.%(ext)s")

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--format", "bv*[height<=1080]+ba/b[height<=1080]",
        "--merge-output-format", "mp4",
        "--write-auto-sub", "--write-sub",
        "--sub-lang", "en.*",
        "--convert-subs", "srt",
        "--downloader", "aria2c",
        "--downloader-args", "aria2c:-x 8 -s 8 -k 1M",
        "--print", "after_move:filepath",
        "--print", "%(id)s|%(title)s|%(duration)s",
        "-o", out_template,
        url,
    ]

    logger.info(f"Starting download: {url}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)

    if result.returncode != 0:
        raise DownloadError(
            f"yt-dlp failed (exit {result.returncode}). "
            f"stderr tail: {result.stderr[-800:]}"
        )

    lines = [l for l in result.stdout.strip().split("\n") if l]
    if len(lines) < 2:
        raise DownloadError(f"Unexpected yt-dlp output, could not parse: {result.stdout[-500:]}")

    video_path = Path(lines[0])
    video_id, title, duration = lines[1].split("|", 2)

    if not video_path.exists():
        raise DownloadError(f"yt-dlp reported success but file missing: {video_path}")

    # Subtitle file, if one was written, sits next to the video with .srt extension
    subtitle_path = None
    for candidate in output_dir.glob(f"{video_id}*.srt"):
        subtitle_path = candidate
        break

    if subtitle_path is None:
        logger.warning(
            f"No subtitles found for {video_id} — Gemini will need to work from "
            f"audio/video directly, which is slower and costs more. Consider checking "
            f"if the source video has captions enabled."
        )

    return {
        "video_path": video_path,
        "subtitle_path": subtitle_path,
        "title": title,
        "duration": float(duration) if duration != "NA" else None,
        "video_id": video_id,
    }
