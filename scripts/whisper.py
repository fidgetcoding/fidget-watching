"""Stdlib HTTP clients for Groq and OpenAI Whisper APIs + local whisper.cpp shell-out.

The API routes reject uploads over 25 MB, so longer audio is split into overlapping
chunks with ffmpeg and the results are merged back onto one timeline.
"""
from __future__ import annotations

import json
import math
import mimetypes
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen


GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3"
OPENAI_URL = "https://api.openai.com/v1/audio/transcriptions"
OPENAI_MODEL = "whisper-1"

LOCAL_BIN = "whisper-cli"

# Groq and OpenAI both cap uploads at 25 MB. Stay under it with room for the
# multipart wrapper.
MAX_UPLOAD_BYTES = 24 * 1024 * 1024
# Neighbouring chunks share this much audio, so a word cut at one chunk's edge is
# whole in the other.
CHUNK_OVERLAP_S = 5.0
MAX_SPLIT_DEPTH = 8
# Long audio means many requests, so one rate limit shouldn't lose the whole run.
RETRY_STATUSES = (429, 500, 502, 503, 504)
MAX_ATTEMPTS = 4
MAX_RETRY_WAIT_S = 120.0


class WhisperError(Exception):
    pass


def pick_backend(
    *,
    groq_key: Optional[str],
    openai_key: Optional[str],
    forced: Optional[str],
    local_available: bool = False,
) -> Optional[str]:
    """Return 'local', 'groq', 'openai', or None.

    Forced backend wins iff its prerequisite is present. Otherwise prefer local
    (free + offline), then Groq, then OpenAI.
    """
    if forced == "local":
        return "local" if local_available else None
    if forced == "groq":
        return "groq" if groq_key else None
    if forced == "openai":
        return "openai" if openai_key else None
    if local_available:
        return "local"
    if groq_key:
        return "groq"
    if openai_key:
        return "openai"
    return None


def _build_multipart(audio: Path, model: str) -> tuple[bytes, str]:
    """Encode a multipart/form-data body for the audio + model fields."""
    boundary = f"----whisper-{uuid.uuid4().hex}"
    crlf = b"\r\n"
    parts: list[bytes] = []
    # Field: model
    parts.append(f"--{boundary}".encode())
    parts.append(b'Content-Disposition: form-data; name="model"')
    parts.append(b"")
    parts.append(model.encode())
    # Field: response_format = verbose_json (gives us segments with timestamps)
    parts.append(f"--{boundary}".encode())
    parts.append(b'Content-Disposition: form-data; name="response_format"')
    parts.append(b"")
    parts.append(b"verbose_json")
    # Field: file
    mime = mimetypes.guess_type(audio.name)[0] or "application/octet-stream"
    parts.append(f"--{boundary}".encode())
    parts.append(
        f'Content-Disposition: form-data; name="file"; filename="{audio.name}"'.encode()
    )
    parts.append(f"Content-Type: {mime}".encode())
    parts.append(b"")
    parts.append(audio.read_bytes())
    parts.append(f"--{boundary}--".encode())
    parts.append(b"")
    body = crlf.join(parts)
    return body, boundary


def _post(url: str, audio: Path, *, model: str, api_key: str) -> list[dict]:
    """POST audio + model + response_format=verbose_json to a Whisper endpoint.

    Returns: [{"t_start": float, "t_end": float, "text": str}, ...]
    Raises: WhisperError on any network, HTTP, JSON, or response-shape failure.
    """
    body, boundary = _build_multipart(audio, model)
    req = Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
    )
    attempt = 0
    while True:
        attempt += 1
        try:
            with urlopen(req, timeout=300) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            segs = payload.get("segments") or []
            return [
                {
                    "t_start": float(s["start"]),
                    "t_end": float(s["end"]),
                    "text": s["text"].strip(),
                }
                for s in segs
            ]
        except HTTPError as e:
            if e.code in RETRY_STATUSES and attempt < MAX_ATTEMPTS:
                time.sleep(_retry_wait(e, attempt))
                continue
            # Surface the API's JSON error body so users see "Invalid API Key" etc.
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            raise WhisperError(f"HTTP {e.code} {e.reason}: {detail}") from e
        except Exception as e:
            # Network, JSON parse, missing keys on segments, all surface as WhisperError.
            raise WhisperError(str(e)) from e


def _retry_wait(err: HTTPError, attempt: int) -> float:
    """Honor Retry-After when the API sends one, else back off 2s, 4s, 8s."""
    try:
        wait = float(err.headers.get("Retry-After"))
    except (AttributeError, TypeError, ValueError):
        wait = 2.0 ** attempt
    return min(wait, MAX_RETRY_WAIT_S)


def _audio_duration(audio: Path) -> float:
    proc = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=nw=1:nk=1",
            str(audio),
        ],
        capture_output=True, text=True, check=False,
    )
    try:
        return float(proc.stdout.strip())
    except ValueError as e:
        raise WhisperError(f"ffprobe could not read {audio}: {proc.stderr[-500:]}") from e


def plan_chunks(
    duration_s: float,
    size_bytes: int,
    *,
    max_bytes: int,
    overlap_s: float = CHUNK_OVERLAP_S,
) -> list[tuple[float, float]]:
    """Return (start, length) pairs that cover the audio with chunks under max_bytes.

    Assumes a steady bitrate, with 10% headroom because AAC isn't perfectly steady.
    split_audio re-checks real sizes afterwards.
    """
    if duration_s <= 0 or size_bytes <= max_bytes:
        return [(0.0, duration_s)]
    max_chunk_s = duration_s * (max_bytes * 0.9) / size_bytes
    step = max_chunk_s - overlap_s
    if step <= overlap_s:
        raise WhisperError(
            f"audio bitrate too high to split: {max_chunk_s:.1f}s per chunk "
            f"with {overlap_s:.0f}s of overlap"
        )
    count = max(1, math.ceil((duration_s - overlap_s) / step))
    return [
        (i * step, min(max_chunk_s, duration_s - i * step))
        for i in range(count)
    ]


def _cut_chunk(audio: Path, out: Path, start: float, length: float) -> None:
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error", "-nostdin",
            "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
            "-i", str(audio),
            "-vn", "-c", "copy",
            str(out),
        ],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise WhisperError(f"ffmpeg could not split {audio}: {proc.stderr[-500:]}")


def split_audio(
    audio: Path,
    out_dir: Path,
    *,
    max_bytes: int,
    overlap_s: float = CHUNK_OVERLAP_S,
    _depth: int = 0,
) -> list[tuple[Path, float]]:
    """Cut audio into overlapping chunks under max_bytes. Returns (path, start_s) pairs.

    Stream copy keeps this fast and lossless. A chunk that still comes out over the
    limit gets split again, so a bitrate spike can't produce a rejected upload.
    """
    if _depth > MAX_SPLIT_DEPTH:
        raise WhisperError(f"could not split {audio} under {max_bytes} bytes")
    chunks: list[tuple[Path, float]] = []
    plan = plan_chunks(
        _audio_duration(audio), audio.stat().st_size, max_bytes=max_bytes, overlap_s=overlap_s
    )
    for start, length in plan:
        out = out_dir / f"chunk-{uuid.uuid4().hex[:8]}{audio.suffix}"
        _cut_chunk(audio, out, start, length)
        if out.stat().st_size > max_bytes:
            chunks.extend(
                (sub, start + sub_start)
                for sub, sub_start in split_audio(
                    out, out_dir, max_bytes=max_bytes, overlap_s=overlap_s, _depth=_depth + 1
                )
            )
            out.unlink()
        else:
            chunks.append((out, start))
    return chunks


def _normalize_text(text: str) -> str:
    return re.sub(r"\W+", " ", text).strip().lower()


def merge_chunk_segments(
    chunks: list[tuple[float, list[dict]]],
    *,
    overlap_s: float = CHUNK_OVERLAP_S,
) -> list[dict]:
    """Merge per-chunk segments, given as (start_s, segments), onto one timeline.

    Each overlap is cut at its middle: a segment belongs to whichever chunk holds its
    midpoint. A segment repeated word for word across that cut is kept once.
    """
    out: list[dict] = []
    for i, (start, segs) in enumerate(chunks):
        lower = start + overlap_s / 2 if i > 0 else float("-inf")
        upper = (
            chunks[i + 1][0] + overlap_s / 2 if i + 1 < len(chunks) else float("inf")
        )
        seam = True
        for seg in segs:
            t_start = seg["t_start"] + start
            t_end = seg["t_end"] + start
            if not lower <= (t_start + t_end) / 2 < upper:
                continue
            if (
                seam and out
                and _normalize_text(out[-1]["text"]) == _normalize_text(seg["text"])
            ):
                seam = False
                continue
            seam = False
            out.append({"t_start": t_start, "t_end": t_end, "text": seg["text"]})
    return out


def _transcribe_any_length(audio: Path, transcribe_one) -> list[dict]:
    """Send audio to an API route, splitting it first when it's over the upload limit."""
    if audio.stat().st_size <= MAX_UPLOAD_BYTES:
        return transcribe_one(audio)
    with tempfile.TemporaryDirectory() as td:
        chunks = split_audio(audio, Path(td), max_bytes=MAX_UPLOAD_BYTES)
        print(
            f"Audio is {audio.stat().st_size / 1_000_000:.0f} MB, over the upload limit. "
            f"Sending it as {len(chunks)} chunks.",
            file=sys.stderr,
        )
        results: list[tuple[float, list[dict]]] = []
        for i, (path, start) in enumerate(chunks, 1):
            try:
                results.append((start, transcribe_one(path)))
            except WhisperError as e:
                raise WhisperError(f"chunk {i} of {len(chunks)} (from {start:.0f}s): {e}") from e
    return merge_chunk_segments(results)


def transcribe_groq(audio: Path, *, api_key: str) -> list[dict]:
    return _transcribe_any_length(
        audio, lambda p: _post(GROQ_URL, p, model=GROQ_MODEL, api_key=api_key)
    )


def transcribe_openai(audio: Path, *, api_key: str) -> list[dict]:
    return _transcribe_any_length(
        audio, lambda p: _post(OPENAI_URL, p, model=OPENAI_MODEL, api_key=api_key)
    )


def local_available(model_path: Optional[Path]) -> bool:
    """True if whisper-cli is on PATH and the model file exists."""
    if not shutil.which(LOCAL_BIN):
        return False
    return bool(model_path and Path(model_path).expanduser().exists())


def transcribe_local(audio: Path, *, model_path: Path) -> list[dict]:
    """Shell out to whisper.cpp's whisper-cli. Zero network, zero keys.

    Parses whisper.cpp's JSON output (transcription[].offsets.{from,to} in ms)
    into the same segment shape as the HTTP backends.
    """
    model = Path(model_path).expanduser()
    if not model.exists():
        raise WhisperError(f"whisper.cpp model not found: {model}")
    with tempfile.TemporaryDirectory() as td:
        # whisper-cli reads WAV, not the m4a watch.py extracts: given an m4a it
        # prints "failed to read audio" and still exits 0, leaving no JSON.
        wav = Path(td) / "audio.wav"
        conv = subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error", "-nostdin",
                "-i", str(audio),
                "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                str(wav),
            ],
            capture_output=True, text=True, check=False,
        )
        if conv.returncode != 0:
            raise WhisperError(
                f"ffmpeg could not convert {audio} to WAV: {conv.stderr[-500:]}"
            )
        out_base = Path(td) / "out"
        cmd = [
            LOCAL_BIN,
            "-m", str(model),
            "-oj", "-of", str(out_base),
            "-l", "en",
            str(wav),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise WhisperError(
                f"whisper-cli exit {proc.returncode}: {proc.stderr[-500:]}"
            )
        json_path = out_base.with_suffix(".json")
        if not json_path.exists():
            raise WhisperError(
                f"whisper-cli produced no JSON: {proc.stderr[-500:]}"
            )
        try:
            payload = json.loads(json_path.read_text())
        except json.JSONDecodeError as e:
            raise WhisperError(f"whisper-cli JSON parse failed: {e}") from e
        segs = payload.get("transcription") or []
        return [
            {
                "t_start": float(s["offsets"]["from"]) / 1000.0,
                "t_end": float(s["offsets"]["to"]) / 1000.0,
                "text": s["text"].strip(),
            }
            for s in segs
            if s.get("text", "").strip()
        ]
