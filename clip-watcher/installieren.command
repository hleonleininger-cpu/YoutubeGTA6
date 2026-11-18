#!/bin/bash
cd "$(dirname "$0")"
command -v brew >/dev/null || { echo "Homebrew fehlt (brew.sh). Erst installieren."; exit 1; }
brew install ffmpeg python
python3 -m pip install --upgrade --break-system-packages streamlink faster-whisper
echo "Fertig. Starte jetzt starten.command"
