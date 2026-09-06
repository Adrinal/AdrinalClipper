#!/data/data/com.termux/files/usr/bin/bash
# OpenShorts pipeline — Termux one-shot setup for Xperia Z5
# Run with: bash setup_termux.sh

set -e  # stop on first error — don't silently continue on a broken step

echo "=== 1/7: Requesting storage permission ==="
termux-setup-storage
echo "-> If a permission popup appeared, tap Allow, then press Enter here."
read -p "Press Enter to continue..."

echo "=== 2/7: Updating package lists ==="
pkg update -y

echo "=== 3/7: Installing core packages ==="
# python: bot + clipping logic
# ffmpeg: video cutting/reframing
# git: pulling this project / OpenShorts if needed later
# aria2: fast downloads (per your existing preference)
# rclone: Google Drive sync
# termux-api: needed for termux-wake-lock
pkg install -y python ffmpeg git aria2 rclone termux-api

echo "=== 4/7: Acquiring wake-lock (stops Android from sleeping the CPU) ==="
termux-wake-lock
echo "-> Wake-lock acquired. This persists until you run 'termux-wake-unlock' or close Termux."
echo "-> IMPORTANT: also go to Android Settings > Apps > Termux > Battery"
echo "   and set battery optimization to 'Unrestricted', or Android will still kill it."

echo "=== 5/7: Installing Python dependencies ==="
pip install --upgrade pip
pip install python-telegram-bot yt-dlp google-genai python-dotenv requests

echo "=== 6/7: Setting up rclone (Google Drive) ==="
if [ ! -f "$HOME/.config/rclone/rclone.conf" ]; then
    echo "-> No rclone config found. Starting interactive setup."
    echo "-> When prompted: choose 'n' for new remote, name it 'gdrive', choose 'drive' (Google Drive) as the storage type."
    echo "-> Accept defaults for client_id/client_secret (leave blank), scope 'drive' (full access), and follow the browser login link."
    rclone config
else
    echo "-> rclone already configured, skipping."
fi

echo "=== 7/7: Creating working directories ==="
mkdir -p "$HOME/openshorts/downloads"
mkdir -p "$HOME/openshorts/clips"
mkdir -p "$HOME/openshorts/logs"

echo ""
echo "=== Setup complete ==="
echo "Next steps:"
echo "1. Copy .env.example to .env and fill in your keys"
echo "2. Run: python bot.py"
echo ""
echo "To keep this running when you close Termux, use:"
echo "  termux-wake-lock && nohup python bot.py > openshorts/logs/bot.log 2>&1 &"
