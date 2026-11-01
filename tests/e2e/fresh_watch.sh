#!/usr/bin/env bash
# Fresh end-to-end run of the /watch pipeline on a real video, from a throwaway
# HOME with no config and no API keys, so it cannot reach a paid route.
#
# Video: "Is Polar Ice Melting" by NASA, public domain, 76 seconds.
# https://commons.wikimedia.org/wiki/File:Is_Polar_Ice_Melting.webm
#
# Route 1 pulls the video and its caption track by URL, with Whisper off.
# Route 2 feeds the downloaded file back in as a local path through whisper.cpp.
# Route 2 needs whisper-cli on PATH and WHISPER_CPP_MODEL set, and it fails rather
# than skips when REQUIRE_LOCAL_WHISPER=1, which is how CI runs it.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
URL="https://commons.wikimedia.org/wiki/File:Is_Polar_Ice_Melting.webm"
PYTHON="${PYTHON:-python3}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

export HOME="$WORK/home"
mkdir -p "$HOME"
unset GROQ_API_KEY OPENAI_API_KEY

check_manifest() {
  "$PYTHON" - "$1" "$2" "$3" <<'PY'
import json, sys
from pathlib import Path

lib, want_word, min_segments = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
manifest = json.loads((lib / "manifest.json").read_text())
frames = manifest["frames"]
assert len(frames) >= 3, f"too few frames: {len(frames)}"
for frame in frames:
    assert (lib / frame["path"]).stat().st_size > 0, frame["path"]
transcript = json.loads((lib / manifest["transcript_path"]).read_text())
assert len(transcript) >= min_segments, f"too few segments: {len(transcript)}"
text = " ".join(c["text"] for c in transcript).lower()
assert want_word in text, f"{want_word!r} not in transcript: {text[:200]}"
longest = max(c["t_end"] - c["t_start"] for c in transcript)
assert longest < 29, f"segment spans {longest}s, timestamps are too coarse"
print(f"ok: {len(frames)} frames, {len(transcript)} segments, longest {longest:.1f}s")
PY
}

echo "== Route 1: URL with captions, Whisper off"
"$PYTHON" "$ROOT/scripts/watch.py" "$URL" --no-whisper --out-dir "$WORK/lib-url" | tee "$WORK/url.out"
grep -q '^transcript_source: captions$' "$WORK/url.out"
LIB_URL="$(echo "$WORK"/lib-url/*)"
check_manifest "$LIB_URL" "melting" 10

echo "== Route 2: local file through whisper.cpp"
if command -v whisper-cli >/dev/null && [ -f "${WHISPER_CPP_MODEL:-/nonexistent}" ]; then
  "$PYTHON" "$ROOT/scripts/watch.py" "$(echo "$LIB_URL"/source/video.*)" \
    --whisper local --out-dir "$WORK/lib-local" | tee "$WORK/local.out"
  grep -q '^transcript_source: whisper$' "$WORK/local.out"
  check_manifest "$(echo "$WORK"/lib-local/*)" "melting" 5
elif [ "${REQUIRE_LOCAL_WHISPER:-0}" = "1" ]; then
  echo "whisper-cli or WHISPER_CPP_MODEL missing, and REQUIRE_LOCAL_WHISPER=1" >&2
  exit 1
else
  echo "SKIP: whisper-cli or WHISPER_CPP_MODEL missing"
fi

echo "== Fresh end-to-end run passed"
