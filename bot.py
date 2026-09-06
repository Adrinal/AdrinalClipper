"""
bot.py — main entry point for the OpenShorts Telegram pipeline.

Flow:
  1. You send a YouTube link.
  2. Bot downloads it, fetches transcript + best-effort heatmap.
  3. Gemini finds candidate moments; ffmpeg cuts + reframes each to 9:16.
  4. Bot sends each clip as a video with Approve/Reject/Edit buttons.
  5. On Approve: syncs to Google Drive (respecting SYNC_MODE).
     (Auto-posting to YT/FB/IG is a later stage — not built yet, see NOTE below.)

NOTE ON SCOPE: this version stops at "approved clip synced to Drive." Auto-
publishing to YouTube/Facebook/Instagram needs separate API app registrations
for each platform (each has its own approval process and quirks) and is
deliberately left for the next stage, per the plan to prove the core loop
first before adding more moving parts.
"""

import os
import logging
import asyncio
from pathlib import Path
from dataclasses import dataclass

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters,
)

from downloader import download_video, DownloadError
from heatmap import get_heatmap
from clipper import find_clip_moments, cut_and_reframe, ClippingError
from drive_sync import sync_file, SyncSkipped

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("openshorts.bot")

# --- Config ---
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ALLOWED_USER_ID = int(os.environ["TELEGRAM_ALLOWED_USER_ID"])
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
SYNC_MODE = os.environ.get("SYNC_MODE", "wifi_only")
GDRIVE_REMOTE = os.environ.get("GDRIVE_REMOTE_NAME", "gdrive")
GDRIVE_FOLDER = os.environ.get("GDRIVE_FOLDER", "OpenShortsClips")
MAX_CLIPS = int(os.environ.get("MAX_CLIPS_PER_VIDEO", "5"))
OUTPUT_WIDTH = int(os.environ.get("OUTPUT_WIDTH", "1080"))
OUTPUT_HEIGHT = int(os.environ.get("OUTPUT_HEIGHT", "1920"))

BASE_DIR = Path.home() / "openshorts"
DOWNLOADS_DIR = BASE_DIR / "downloads"
CLIPS_DIR = BASE_DIR / "clips"

# In-memory map of pending clips awaiting approval: {clip_id: Path}
# Lost on restart by design — this is a queue for the current session, not a
# database. If you need clips to survive a bot restart, that's a future
# upgrade (sqlite), not silently assumed here.
_pending_clips: dict[str, Path] = {}


def _authorized(update: Update) -> bool:
    user_id = update.effective_user.id if update.effective_user else None
    if user_id != ALLOWED_USER_ID:
        logger.warning(f"Rejected message from unauthorized user_id={user_id}")
        return False
    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    await update.message.reply_text(
        "OpenShorts pipeline ready.\nSend a YouTube link to generate clips."
    )


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return

    url = update.message.text.strip()
    if "youtube.com" not in url and "youtu.be" not in url:
        await update.message.reply_text("That doesn't look like a YouTube link.")
        return

    status_msg = await update.message.reply_text("Downloading video...")

    try:
        video_info = await asyncio.to_thread(download_video, url, DOWNLOADS_DIR)
    except DownloadError as e:
        await status_msg.edit_text(f"Download failed:\n{e}")
        return

    await status_msg.edit_text(
        f"Downloaded: {video_info['title']}\nChecking for viewer replay data..."
    )

    heatmap = await asyncio.to_thread(get_heatmap, video_info["video_id"])
    if heatmap:
        await status_msg.edit_text(f"Found replay data ({len(heatmap)} markers). Finding clip moments...")
    else:
        await status_msg.edit_text("No replay data available — using transcript analysis only.")

    from clipper import _load_transcript
    transcript_text = _load_transcript(video_info["subtitle_path"])

    try:
        candidates = await asyncio.to_thread(
            find_clip_moments,
            transcript_text,
            video_info["duration"] or 0,
            MAX_CLIPS,
            heatmap,
            GEMINI_API_KEY,
        )
    except ClippingError as e:
        await status_msg.edit_text(f"Couldn't find clip moments:\n{e}")
        return

    await status_msg.edit_text(f"Found {len(candidates)} candidates. Cutting clips...")

    video_stem = video_info["video_id"]
    output_dir = CLIPS_DIR / video_stem

    for i, candidate in enumerate(candidates, start=1):
        try:
            clip_path = await asyncio.to_thread(
                cut_and_reframe,
                video_info["video_path"], candidate, output_dir,
                OUTPUT_WIDTH, OUTPUT_HEIGHT, i,
            )
        except ClippingError as e:
            await update.message.reply_text(f"Clip {i} failed:\n{e}")
            continue

        clip_id = f"{video_stem}_{i}"
        _pending_clips[clip_id] = clip_path

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Approve", callback_data=f"approve:{clip_id}"),
            InlineKeyboardButton("Reject", callback_data=f"reject:{clip_id}"),
        ]])

        caption = f"Clip {i}: {candidate.title}\n\n{candidate.reason}\n({candidate.start_seconds:.0f}s-{candidate.end_seconds:.0f}s)"
        with open(clip_path, "rb") as f:
            await update.message.reply_video(video=f, caption=caption, reply_markup=keyboard)

    await status_msg.edit_text(f"Done — sent {len(candidates)} clips for your review.")


async def handle_approval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ALLOWED_USER_ID:
        await query.answer("Not authorized.")
        return

    await query.answer()
    action, clip_id = query.data.split(":", 1)
    clip_path = _pending_clips.get(clip_id)

    if clip_path is None or not clip_path.exists():
        await query.edit_message_caption(caption=f"{query.message.caption}\n\n[Expired — bot may have restarted]")
        return

    if action == "reject":
        clip_path.unlink(missing_ok=True)
        del _pending_clips[clip_id]
        await query.edit_message_caption(caption=f"{query.message.caption}\n\nREJECTED (deleted)")
        return

    # approve
    await query.edit_message_caption(caption=f"{query.message.caption}\n\nApproved — syncing to Drive...")
    try:
        remote_path = await asyncio.to_thread(
            sync_file, clip_path, GDRIVE_REMOTE, GDRIVE_FOLDER, SYNC_MODE
        )
        await query.edit_message_caption(caption=f"{query.message.caption}\n\nSynced to Drive: {remote_path}")
    except SyncSkipped as e:
        await query.edit_message_caption(caption=f"{query.message.caption}\n\nApproved (saved locally — {e})")
    except RuntimeError as e:
        await query.edit_message_caption(caption=f"{query.message.caption}\n\nApproved, but Drive sync failed:\n{e}")

    del _pending_clips[clip_id]


def main():
    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(CallbackQueryHandler(handle_approval))

    logger.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
