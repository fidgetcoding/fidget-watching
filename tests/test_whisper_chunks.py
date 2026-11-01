"""Long-audio splitting for the Whisper API routes.

No test here reaches a real API. Every HTTP call is mocked, and the audio is generated
to files with ffmpeg's lavfi sources, so no audio device is involved.
"""
import json
import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest

from scripts import library as lib
from scripts import setup as setup_mod
from scripts import watch as watch_mod
from scripts import whisper
from scripts.whisper import (
    WhisperError,
    merge_chunk_segments,
    plan_chunks,
    split_audio,
    transcribe_groq,
)

MB = 1024 * 1024
API_LIMIT_BYTES = 25 * 1000 * 1000  # The vendors' documented limit, in its strictest reading.


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-nostdin", *args], check=True)


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout)


def _uploaded_audio(req, out_dir: Path) -> Path:
    """Pull the file part back out of a mocked request's multipart body."""
    body = req.data
    head_end = body.index(b"\r\n\r\n", body.index(b'name="file"')) + 4
    boundary = req.get_header("Content-type").split("boundary=")[1].encode()
    tail = body.rindex(b"\r\n--" + boundary + b"--")
    name = re.search(rb'filename="([^"]+)"', body).group(1).decode()
    path = out_dir / f"upload-{len(list(out_dir.iterdir()))}-{name}"
    path.write_bytes(body[head_end:tail])
    return path


def _json_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode()
    resp.__enter__.return_value = resp
    return resp


def test_plan_chunks_is_one_pass_under_the_limit():
    assert plan_chunks(600.0, 10 * MB, max_bytes=24 * MB) == [(0.0, 600.0)]


def test_plan_chunks_covers_the_whole_duration_with_overlap():
    duration = 7200.0
    plan = plan_chunks(duration, 58 * MB, max_bytes=24 * MB, overlap_s=5.0)
    assert len(plan) > 1
    assert plan[0][0] == 0.0
    assert plan[-1][0] + plan[-1][1] == pytest.approx(duration)
    for (start, length), (next_start, _) in zip(plan, plan[1:]):
        assert next_start + 5.0 == pytest.approx(start + length)
    # Steady bitrate estimate stays under the limit for every chunk.
    bytes_per_s = 58 * MB / duration
    assert all(length * bytes_per_s < 24 * MB for _, length in plan)


def test_plan_chunks_refuses_a_bitrate_too_high_to_split():
    with pytest.raises(WhisperError, match="too high"):
        plan_chunks(10.0, 100 * MB, max_bytes=1 * MB, overlap_s=5.0)


def test_merge_shifts_timestamps_and_cuts_overlaps_at_the_middle():
    chunks = [
        (0.0, [
            {"t_start": 0.0, "t_end": 50.0, "text": "first"},
            {"t_start": 50.0, "t_end": 54.0, "text": "in overlap, early half"},
            {"t_start": 58.0, "t_end": 60.0, "text": "in overlap, late half, chunk one"},
        ]),
        (55.0, [
            {"t_start": 0.0, "t_end": 2.0, "text": "in overlap, early half, chunk two"},
            {"t_start": 3.0, "t_end": 5.0, "text": "in overlap, late half"},
            {"t_start": 10.0, "t_end": 20.0, "text": "last"},
        ]),
    ]
    out = merge_chunk_segments(chunks, overlap_s=5.0)
    assert [s["text"] for s in out] == [
        "first", "in overlap, early half", "in overlap, late half", "last",
    ]
    assert out[-1]["t_start"] == 65.0
    assert out[-1]["t_end"] == 75.0


def test_merge_keeps_one_copy_of_a_segment_repeated_across_the_cut():
    chunks = [
        (0.0, [{"t_start": 50.0, "t_end": 57.0, "text": "Same words."}]),
        (55.0, [
            {"t_start": 2.0, "t_end": 6.0, "text": "same words"},
            {"t_start": 6.0, "t_end": 8.0, "text": "next"},
        ]),
    ]
    out = merge_chunk_segments(chunks, overlap_s=5.0)
    assert [s["text"] for s in out] == ["Same words.", "next"]


@patch("scripts.whisper.urlopen")
def test_small_audio_goes_up_in_one_piece_without_ffmpeg(mock_urlopen, tmp_path):
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"\x00" * 1024)
    mock_urlopen.return_value = _json_response({"segments": []})
    with patch("scripts.whisper.subprocess.run") as run:
        assert transcribe_groq(audio, api_key="k") == []
    run.assert_not_called()
    assert mock_urlopen.call_count == 1


@pytest.mark.integration
def test_timestamps_land_on_the_real_position_in_the_audio(tmp_path, monkeypatch):
    """A beep at a known time must come back at that time, from whichever chunk holds it."""
    beep_at = 100.0
    audio = tmp_path / "long.m4a"
    _ffmpeg(
        "-f", "lavfi", "-i", "anoisesrc=color=white:amplitude=0.0001:duration=180:seed=7",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=1",
        "-filter_complex",
        f"[1:a]adelay={int(beep_at * 1000)},volume=8[beep];[0:a][beep]amix=inputs=2:duration=first:normalize=0",
        "-ac", "1", "-ar", "16000", "-c:a", "aac", "-b:a", "64k", str(audio),
    )
    limit = audio.stat().st_size // 4
    monkeypatch.setattr(whisper, "MAX_UPLOAD_BYTES", limit)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    sizes = []

    def fake_api(req, timeout=None):
        sizes.append(len(req.data))
        path = _uploaded_audio(req, uploads)
        # Find the beep in this chunk with ffmpeg, the way a transcriber would hear it.
        found = subprocess.run(
            ["ffmpeg", "-nostdin", "-i", str(path), "-af",
             "silencedetect=noise=-40dB:d=0.5", "-f", "null", "-"],
            capture_output=True, text=True, check=True,
        ).stderr
        ends = re.findall(r"silence_end: ([\d.]+)", found)
        segments = [
            {"start": float(t), "end": float(t) + 1.0, "text": "beep"} for t in ends
            if float(t) > 0.5
        ]
        return _json_response({"segments": segments})

    with patch("scripts.whisper.urlopen", side_effect=fake_api):
        out = transcribe_groq(audio, api_key="k")

    assert len(sizes) >= 4
    assert max(sizes) < limit + 4096
    beeps = [s for s in out if s["text"] == "beep"]
    assert len(beeps) == 1, out
    assert beeps[0]["t_start"] == pytest.approx(beep_at, abs=0.5)


@pytest.mark.integration
def test_real_file_over_25_mb_and_30_minutes_is_fully_covered(tmp_path):
    """The real limit on a real file: an hour of audio, every upload under 25 MB."""
    audio = tmp_path / "hour.m4a"
    _ffmpeg(
        "-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.5:duration=3660:seed=11",
        "-ac", "1", "-ar", "16000", "-c:a", "aac", "-b:a", "64k", str(audio),
    )
    assert audio.stat().st_size > API_LIMIT_BYTES
    total = _duration(audio)
    assert total > 30 * 60

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    sizes = []

    def fake_api(req, timeout=None):
        sizes.append(len(req.data))
        length = _duration(_uploaded_audio(req, uploads))
        # One segment per 20 seconds of whatever this chunk really holds.
        segments = []
        t = 0.0
        while t < length:
            segments.append({"start": t, "end": min(t + 20.0, length), "text": f"at {t}"})
            t += 20.0
        return _json_response({"segments": segments})

    with patch("scripts.whisper.urlopen", side_effect=fake_api):
        out = transcribe_groq(audio, api_key="k")

    assert len(sizes) >= 2
    assert max(sizes) < API_LIMIT_BYTES
    assert out[0]["t_start"] == 0.0
    assert out[-1]["t_end"] == pytest.approx(total, abs=2.0)
    for prev, cur in zip(out, out[1:]):
        assert cur["t_start"] >= prev["t_start"]
        # A real gap would mean lost audio. 20-second segments plus a little seam slack.
        assert cur["t_start"] - prev["t_end"] < 6.0


@pytest.mark.integration
def test_a_bitrate_spike_gets_split_again(tmp_path):
    """Silence then loud noise: the even plan leaves one chunk over, which is re-split."""
    audio = tmp_path / "spiky.m4a"
    _ffmpeg(
        "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono:d=120",
        "-f", "lavfi", "-i", "anoisesrc=r=16000:color=white:amplitude=0.9:duration=60:seed=3",
        "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1",
        "-ac", "1", "-c:a", "aac", "-b:a", "64k", str(audio),
    )
    limit = int(audio.stat().st_size * 0.4)
    out_dir = tmp_path / "chunks"
    out_dir.mkdir()
    chunks = split_audio(audio, out_dir, max_bytes=limit, overlap_s=1.0)
    assert len(chunks) > 3
    assert all(path.stat().st_size <= limit for path, _ in chunks)
    starts = [start for _, start in chunks]
    assert starts == sorted(starts)
    assert chunks[-1][1] + _duration(chunks[-1][0]) == pytest.approx(_duration(audio), abs=1.0)


def test_split_gives_up_cleanly_when_chunks_never_fit(tmp_path):
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"\x00" * 2048)

    def fake_cut(src, out, start, length):
        out.write_bytes(b"\x00" * 2048)

    with patch("scripts.whisper._audio_duration", return_value=600.0), \
            patch("scripts.whisper._cut_chunk", side_effect=fake_cut), \
            patch("scripts.whisper.plan_chunks", return_value=[(0.0, 600.0)]):
        with pytest.raises(WhisperError, match="could not split"):
            split_audio(audio, tmp_path, max_bytes=1024)


@pytest.mark.integration
def test_failed_chunk_is_named_in_the_error(tmp_path, monkeypatch):
    audio = tmp_path / "long.m4a"
    _ffmpeg("-f", "lavfi", "-i", "anoisesrc=color=white:amplitude=0.5:duration=60:seed=5",
            "-ac", "1", "-ar", "16000", "-c:a", "aac", "-b:a", "64k", str(audio))
    monkeypatch.setattr(whisper, "MAX_UPLOAD_BYTES", audio.stat().st_size // 2)

    responses = iter([_json_response({"segments": []})])

    def fake_api(req, timeout=None):
        try:
            return next(responses)
        except StopIteration:
            raise HTTPError(req.full_url, 401, "Unauthorized", None, None)

    with patch("scripts.whisper.urlopen", side_effect=fake_api):
        with pytest.raises(WhisperError, match=r"chunk 2 of \d+ \(from \d+s\): HTTP 401"):
            transcribe_groq(audio, api_key="k")


def test_rate_limit_is_retried_honoring_retry_after(tmp_path):
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"\x00")
    calls = []

    def fake_api(req, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            raise HTTPError(req.full_url, 429, "Too Many Requests", {"Retry-After": "7"}, None)
        return _json_response({"segments": [{"start": 0, "end": 1, "text": "ok"}]})

    with patch("scripts.whisper.urlopen", side_effect=fake_api), \
            patch("scripts.whisper.time.sleep") as sleep:
        out = transcribe_groq(audio, api_key="k")
    assert out[0]["text"] == "ok"
    sleep.assert_called_once_with(7.0)


def test_retries_stop_after_the_last_attempt(tmp_path):
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"\x00")
    err = HTTPError("https://x", 503, "Unavailable", {}, None)
    with patch("scripts.whisper.urlopen", side_effect=err) as api, \
            patch("scripts.whisper.time.sleep"):
        with pytest.raises(WhisperError, match="HTTP 503"):
            transcribe_groq(audio, api_key="k")
    assert api.call_count == whisper.MAX_ATTEMPTS


def test_auth_errors_are_not_retried(tmp_path):
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"\x00")
    err = HTTPError("https://x", 401, "Unauthorized", {}, None)
    with patch("scripts.whisper.urlopen", side_effect=err) as api, \
            patch("scripts.whisper.time.sleep") as sleep:
        with pytest.raises(WhisperError, match="HTTP 401"):
            transcribe_groq(audio, api_key="k")
    assert api.call_count == 1
    sleep.assert_not_called()


@pytest.mark.integration
def test_full_pipeline_writes_one_transcript_from_many_chunks(tmp_path, monkeypatch, capsys):
    video = tmp_path / "talk.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "color=white:size=160x120:duration=120:rate=5",
        "-f", "lavfi", "-i", "anoisesrc=color=white:amplitude=0.5:duration=120:seed=9",
        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video),
    )
    # Fake key, mocked HTTP, and _read_env replaced so no real .env is opened.
    monkeypatch.setattr(setup_mod, "_read_env", lambda: {"GROQ_API_KEY": "test-key"})
    monkeypatch.setattr(setup_mod, "_resolve_local_model", lambda env: None)
    monkeypatch.setattr(whisper, "MAX_UPLOAD_BYTES", 150 * 1024)
    monkeypatch.setattr(lib, "LIBRARY_ROOT", lib.LIBRARY_ROOT)  # main() reassigns it; restore after.

    calls = []

    def fake_api(req, timeout=None):
        calls.append(1)
        return _json_response(
            {"segments": [{"start": 0.0, "end": 10.0, "text": f"chunk {len(calls)}"}]}
        )

    with patch("scripts.whisper.urlopen", side_effect=fake_api):
        rc = watch_mod.main([str(video), "--whisper", "groq", "--out-dir", str(tmp_path / "lib")])

    assert rc == 0
    assert "transcript_source: whisper" in capsys.readouterr().out
    assert len(calls) > 1
    transcript = json.loads(next((tmp_path / "lib").glob("*/transcript.json")).read_text())
    assert [c["text"] for c in transcript] == [f"chunk {i}" for i in range(1, len(calls) + 1)]
    starts = [c["t_start"] for c in transcript]
    assert starts[0] == 0.0 and starts == sorted(starts) and starts[-1] > 60.0
