"""Stdlib HTTP clients for Groq and OpenAI Whisper APIs + local whisper.cpp shell-out."""
from __future__ import annotations

import json
import mimetypes
import shutil
import subprocess
import tempfile
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
        # Surface the API's JSON error body so users see "Invalid API Key" etc.
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        raise WhisperError(f"HTTP {e.code} {e.reason}: {detail}") from e
    except Exception as e:
        # Network, JSON parse, missing keys on segments — all surface as WhisperError.
        raise WhisperError(str(e)) from e


def transcribe_groq(audio: Path, *, api_key: str) -> list[dict]:
    return _post(GROQ_URL, audio, model=GROQ_MODEL, api_key=api_key)


def transcribe_openai(audio: Path, *, api_key: str) -> list[dict]:
    return _post(OPENAI_URL, audio, model=OPENAI_MODEL, api_key=api_key)


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
