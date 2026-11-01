import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "sample_10s.mp4"


@pytest.mark.integration
def test_watch_end_to_end_on_local_fixture(tmp_path):
    """`scripts/watch.py <fixture> --no-whisper --out-dir <tmp>` runs the full pipeline."""
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "watch.py"),
            str(FIXTURE),
            "--no-whisper",
            "--out-dir", str(tmp_path),
        ],
        capture_output=True, text=True, check=False, cwd=str(ROOT),
    )
    assert proc.returncode == 0, f"watch.py failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"

    # Library dir created
    library_dirs = list(tmp_path.glob("*"))
    assert len(library_dirs) == 1, f"expected one library dir, got {library_dirs}"
    lib = library_dirs[0]

    # Manifest exists and has the right shape
    manifest = json.loads((lib / "manifest.json").read_text())
    assert manifest["meta"]["title"]
    assert manifest["transcript_path"]
    # Should have detected at least t=0 anchor + the two cuts at t≈3 and t≈6
    assert len(manifest["frames"]) >= 3

    # All frame files exist
    for frame in manifest["frames"]:
        assert (lib / frame["path"]).exists()

    # Stdout should contain the structured manifest block
    assert "=== watch manifest ===" in proc.stdout
    assert "library_dir:" in proc.stdout


FAKE_WHISPER_CLI = """#!/usr/bin/env bash
out="" ; prev=""
for a in "$@"; do
  [ "$prev" = "-of" ] && out="$a"
  prev="$a"
done
if [ -f "$FAKE_WHISPER_FAIL" ]; then
  echo "error: simulated failure" >&2
  exit 1
fi
printf '%s' '{"transcription":[{"offsets":{"from":0,"to":2000},"text":"spoken words"}]}' > "$out.json"
"""


def _local_whisper_env(tmp_path):
    """Env for a run on the fake local backend. API keys are removed on purpose:
    a test must never be able to fall through to a paid route."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "whisper-cli"
    fake.write_text(FAKE_WHISPER_CLI)
    fake.chmod(0o755)
    model = tmp_path / "model.bin"
    model.write_bytes(b"\x00")
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in ("GROQ_API_KEY", "OPENAI_API_KEY")}
    env.update(
        HOME=str(home),
        PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        WHISPER_CPP_MODEL=str(model),
        FAKE_WHISPER_FAIL=str(tmp_path / "fail-flag"),
    )
    return env


def _run_watch(video, out_dir, env):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "watch.py"), str(video),
         "--whisper", "local", "--out-dir", str(out_dir)],
        capture_output=True, text=True, check=False, cwd=str(ROOT), env=env,
    )


@pytest.mark.integration
def test_watch_continues_frames_only_when_the_video_has_no_audio(tmp_path):
    proc = _run_watch(FIXTURE, tmp_path / "lib", _local_whisper_env(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert "transcript_source: none" in proc.stdout
    assert "No audio to transcribe" in proc.stderr


@pytest.mark.integration
def test_watch_retries_transcription_after_an_empty_cached_transcript(tmp_path):
    video = tmp_path / "talk.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-nostdin",
         "-f", "lavfi", "-i", "color=white:size=160x120:duration=2:rate=10",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)],
        check=True,
    )
    env = _local_whisper_env(tmp_path)
    out_dir = tmp_path / "lib"
    flag = tmp_path / "fail-flag"

    flag.write_text("fail")
    first = _run_watch(video, out_dir, env)
    assert first.returncode == 0, first.stderr
    assert "transcript_source: none" in first.stdout
    assert "simulated failure" in first.stderr

    flag.unlink()
    second = _run_watch(video, out_dir, env)
    assert second.returncode == 0, second.stderr
    assert "transcript_source: whisper" in second.stdout

    transcript = json.loads(next(out_dir.glob("*/transcript.json")).read_text())
    assert transcript[0]["text"] == "spoken words"
