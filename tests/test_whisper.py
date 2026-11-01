import io
import json
import os
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from scripts.whisper import (
    pick_backend,
    transcribe_groq,
    transcribe_local,
    transcribe_openai,
    WhisperError,
)


def test_pick_backend_prefers_groq_when_both_keys_set():
    assert pick_backend(groq_key="g", openai_key="o", forced=None) == "groq"


def test_pick_backend_falls_back_to_openai():
    assert pick_backend(groq_key=None, openai_key="o", forced=None) == "openai"


def test_pick_backend_returns_none_when_no_keys():
    assert pick_backend(groq_key=None, openai_key=None, forced=None) is None


def test_pick_backend_honors_forced_backend():
    assert pick_backend(groq_key="g", openai_key="o", forced="openai") == "openai"


def test_pick_backend_forced_without_key_returns_none():
    assert pick_backend(groq_key=None, openai_key="o", forced="groq") is None


@patch("scripts.whisper.urlopen")
def test_transcribe_groq_posts_to_correct_endpoint_with_api_key(mock_urlopen, tmp_path):
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"\x00\x00\x00\x00")
    resp = MagicMock()
    resp.read.return_value = json.dumps({
        "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}]
    }).encode()
    resp.__enter__.return_value = resp
    mock_urlopen.return_value = resp
    out = transcribe_groq(audio, api_key="testkey")
    assert out == [{"t_start": 0.0, "t_end": 1.0, "text": "hello"}]
    req = mock_urlopen.call_args[0][0]
    assert "api.groq.com" in req.full_url
    # Use the documented Request API; urllib stores headers with title-case keys.
    assert req.get_header("Authorization") == "Bearer testkey"


@patch("scripts.whisper.urlopen")
def test_transcribe_openai_posts_to_correct_endpoint(mock_urlopen, tmp_path):
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"\x00")
    resp = MagicMock()
    resp.read.return_value = json.dumps({
        "segments": [{"start": 1.0, "end": 2.0, "text": "world"}]
    }).encode()
    resp.__enter__.return_value = resp
    mock_urlopen.return_value = resp
    out = transcribe_openai(audio, api_key="k")
    assert out == [{"t_start": 1.0, "t_end": 2.0, "text": "world"}]
    req = mock_urlopen.call_args[0][0]
    assert "api.openai.com" in req.full_url


@patch("scripts.whisper.urlopen", side_effect=Exception("boom"))
def test_transcribe_groq_wraps_errors_in_whisper_error(mock_urlopen, tmp_path):
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"\x00")
    with pytest.raises(WhisperError):
        transcribe_groq(audio, api_key="k")


FAKE_WHISPER_CLI = """#!/usr/bin/env bash
# Behaves like Homebrew's whisper-cli: anything but WAV prints an error and still
# exits 0 with no JSON. Records its arguments for the test to read.
echo "$@" > "$FAKE_WHISPER_LOG"
out="" ; prev="" ; last=""
for a in "$@"; do
  [ "$prev" = "-of" ] && out="$a"
  prev="$a"; last="$a"
done
case "$last" in
  *.wav)
    printf '%s' '{"transcription":[{"offsets":{"from":1500,"to":4000},"text":" hello there "},{"offsets":{"from":4000,"to":4000},"text":"  "}]}' > "$out.json"
    ;;
  *)
    echo "error: failed to read audio file '$last'" >&2
    ;;
esac
exit 0
"""


def _install_fake_whisper_cli(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "whisper-cli"
    fake.write_text(FAKE_WHISPER_CLI)
    fake.chmod(0o755)
    log = tmp_path / "whisper.log"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_WHISPER_LOG", str(log))
    return log


def _make_m4a(path):
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-nostdin", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=1", "-ac", "1", "-b:a", "32k", str(path)],
        check=True,
    )


@pytest.mark.integration
def test_transcribe_local_converts_m4a_to_wav_and_keeps_timestamps(tmp_path, monkeypatch):
    log = _install_fake_whisper_cli(tmp_path, monkeypatch)
    model = tmp_path / "model.bin"
    model.write_bytes(b"\x00")
    audio = tmp_path / "audio.m4a"
    _make_m4a(audio)

    out = transcribe_local(audio, model_path=model)

    assert out == [{"t_start": 1.5, "t_end": 4.0, "text": "hello there"}]
    args = log.read_text().split()
    assert args[-1].endswith(".wav")
    # -nt runs whisper with no timestamp tokens, which flattens the segments to
    # whole 30-second windows, so it must stay off.
    assert "-nt" not in args


@pytest.mark.integration
def test_transcribe_local_surfaces_whisper_cli_error_when_no_json(tmp_path, monkeypatch):
    _install_fake_whisper_cli(tmp_path, monkeypatch)
    model = tmp_path / "model.bin"
    model.write_bytes(b"\x00")
    audio = tmp_path / "audio.m4a"
    _make_m4a(audio)
    with patch("scripts.whisper.subprocess.run") as run:
        run.side_effect = [
            MagicMock(returncode=0, stderr=""),
            MagicMock(returncode=0, stderr="error: failed to read audio file"),
        ]
        with pytest.raises(WhisperError, match="failed to read audio"):
            transcribe_local(audio, model_path=model)


def test_transcribe_local_reports_ffmpeg_conversion_failure(tmp_path):
    model = tmp_path / "model.bin"
    model.write_bytes(b"\x00")
    with patch("scripts.whisper.subprocess.run") as run:
        run.return_value = MagicMock(returncode=1, stderr="Invalid data found")
        with pytest.raises(WhisperError, match="Invalid data found"):
            transcribe_local(tmp_path / "broken.m4a", model_path=model)
    assert run.call_count == 1
