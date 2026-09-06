# Setting Up and Running the AdrinalClipper

## Why `.env` shouldn't go through GitHub

`.env` holds your secrets (bot token, API key). Most repo setups deliberately
block it from being pushed/uploaded — GitHub's push protection and standard
`.gitignore` templates both do this on purpose, because leaked keys in a
public repo get scraped and abused within minutes. That's almost certainly
why your upload was refused — it's not corruption, it's the platform
protecting you.

**The fix: never upload `.env`. Create it directly on the phone, after you
clone the repo.** The `.env.example` file (safe, no real secrets) is the only
one that should live in the repo.

---

## Step 1 — Clone the repo on your phone

In Termux:

```bash
cd ~
git clone https://github.com/Adrinal/AdrinalClipper.git AdrinalClipper
cd AdrinalClipper
```

## Step 2 — Run the setup script

```bash
bash setup_termux.sh
```

This installs Python, ffmpeg, aria2, rclone, sets up the wake-lock, and walks
you through connecting Google Drive. Follow its on-screen prompts.

## Step 3 — Create `.env` directly on the phone

Do **not** try to download or upload this file. Create it in place with:

```bash
cat > .env << 'EOF'
TELEGRAM_BOT_TOKEN=paste_your_botfather_token_here
TELEGRAM_ALLOWED_USER_ID=paste_your_numeric_telegram_id_here
GEMINI_API_KEY=paste_your_gemini_api_key_here
SYNC_MODE=wifi_only
GDRIVE_REMOTE_NAME=gdrive
GDRIVE_FOLDER=OpenShortsClips
MAX_CLIPS_PER_VIDEO=5
OUTPUT_WIDTH=1080
OUTPUT_HEIGHT=1920
EOF
```

Then open it to fill in the three real values (token, user ID, Gemini key):

```bash
nano .env
```

- Replace each `paste_your_..._here` with your real value.
- No quotes needed around the values.
- Save and exit nano: `Ctrl+O`, `Enter`, then `Ctrl+X`.

### Where to get each value

| Variable | Where to get it |
|---|---|
| `TELEGRAM_BOT_TOKEN` | From @BotFather on Telegram (you said you already have this) |
| `TELEGRAM_ALLOWED_USER_ID` | Your numeric Telegram ID (you said you already know this) |
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey (free) |

## Step 4 — Verify `.env` is being ignored by git (safety check)

Run this once to confirm git won't try to track it:

```bash
git check-ignore -v .env
```

If it prints a line pointing at `.gitignore`, you're safe — future commits
won't touch `.env`. If it prints nothing, add this line to `.gitignore`
before doing anything else:

```bash
echo ".env" >> .gitignore
```

## Step 5 — Run the bot

```bash
python bot.py
```

You should see log lines ending in `Bot starting...`. Open Telegram, message
your bot `/start`, then send it a YouTube link to test the full pipeline.

### Keeping it running after you close Termux

```bash
termux-wake-lock
nohup python bot.py > ~/openshorts/logs/bot.log 2>&1 &
```

To check on it later:

```bash
tail -f ~/openshorts/logs/bot.log
```

To stop it:

```bash
pkill -f "python bot.py"
```

---

## If something goes wrong

Send me the actual error text from the terminal or from `bot.log` — not just
"it doesn't work." The bot is written to fail loudly with real error
messages (download failures, ffmpeg errors, Gemini errors all print their
real cause) specifically so problems are debuggable instead of silent.
