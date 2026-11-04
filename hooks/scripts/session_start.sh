#!/usr/bin/env bash
# Print a one-line watch status only when remediation is needed.
# Silent on `ready`.
set -e
RAW=$(python3 "${CLAUDE_PLUGIN_ROOT:-$(dirname "$0")/../..}/scripts/setup.py" --check --json 2>/dev/null || true)
[ -z "$RAW" ] && exit 0
STATUS=$(printf '%s' "$RAW" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' 2>/dev/null || echo "")
case "$STATUS" in
  ready|"") exit 0 ;;
  needs_install) echo "[watch] Run /watch — setup will install ffmpeg/yt-dlp on first run." ;;
  needs_key) echo "[watch] Run /watch to set up free local Whisper, add an API key, or use captions/--no-whisper." ;;
  needs_install_and_key) echo "[watch] Setup incomplete: run /watch to install tools and choose local Whisper, an API key, or captions/--no-whisper." ;;
esac
