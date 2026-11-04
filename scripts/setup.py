"""Cross-platform preflight + installer for watch.

Exit codes (read by SKILL.md):
    0  ready
    2  needs_install
    3  needs_key
    4  needs_install_and_key
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

CONFIG_DIR = Path.home() / ".config" / "watch"
ENV_PATH = CONFIG_DIR / ".env"
LIBRARY_ROOT = Path.home() / "watch" / "library"
DEFAULT_LOCAL_MODEL = CONFIG_DIR / "models" / "ggml-base.en.bin"
# Additional known-good model locations.
# - creativity-maxxing's media module installs the model at ~/.whisper/ggml-base.en.bin
# - pre-rename installs (when this skill was named "claude-watch") used
#   ~/.config/claude-watch/models/ggml-base.en.bin — honor that for backwards
#   compatibility so users upgrading from the old name don't lose their ~141MB model.
EXTRA_LOCAL_MODEL_PATHS: tuple[Path, ...] = (
    Path.home() / ".whisper" / "ggml-base.en.bin",
    Path.home() / ".config" / "claude-watch" / "models" / "ggml-base.en.bin",
)
REQUIRED_BINS = ("ffmpeg", "ffprobe", "yt-dlp")


def _which(name: str) -> Optional[str]:
    return shutil.which(name)


def _read_env() -> dict[str, str]:
    """Read CONFIG_DIR/.env if present. Falls back to process env if file absent."""
    env: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("GROQ_API_KEY", "OPENAI_API_KEY", "WHISPER_CPP_MODEL", "SETUP_COMPLETE"):
        if k in os.environ and not env.get(k):
            env[k] = os.environ[k]
    return env


def _resolve_local_model(env: dict[str, str]) -> Optional[Path]:
    """Resolve the whisper.cpp model path.

    Search order:
        1. $WHISPER_CPP_MODEL (env or .env file)
        2. ~/.config/watch/models/ggml-base.en.bin  (watch default)
        3. EXTRA_LOCAL_MODEL_PATHS  (shared paths used by sibling installers,
           e.g. ~/.whisper/ggml-base.en.bin from creativity-maxxing's media
           module)

    Returns the first path that exists on disk, or None.
    """
    raw = env.get("WHISPER_CPP_MODEL")
    if raw:
        candidate = Path(raw).expanduser()
        return candidate if candidate.is_file() else None
    if DEFAULT_LOCAL_MODEL.is_file():
        return DEFAULT_LOCAL_MODEL
    for p in EXTRA_LOCAL_MODEL_PATHS:
        if p.is_file():
            return p
    return None


def status_for() -> dict:
    missing = [b for b in REQUIRED_BINS if not _which(b)]
    env = _read_env()
    has_key = bool(env.get("GROQ_API_KEY") or env.get("OPENAI_API_KEY"))
    local_model = _resolve_local_model(env)
    has_local = bool(_which("whisper-cli") and local_model)
    has_backend = has_key or has_local
    if missing and not has_backend:
        status = "needs_install_and_key"
    elif missing:
        status = "needs_install"
    elif not has_backend:
        status = "needs_key"
    else:
        status = "ready"
    return {
        "status": status,
        "missing_binaries": missing,
        "has_api_key": has_key,
        "has_local_whisper": has_local,
        "local_model_path": str(local_model) if local_model else None,
        "whisper_backend": (
            "local" if has_local
            else "groq" if env.get("GROQ_API_KEY")
            else "openai" if env.get("OPENAI_API_KEY")
            else None
        ),
        "config_file": str(ENV_PATH),
        "platform": platform.system().lower(),
        "first_run": env.get("SETUP_COMPLETE", "").lower() not in ("true", "1", "yes"),
    }


def exit_code_for(status: str) -> int:
    return {"ready": 0, "needs_install": 2, "needs_key": 3, "needs_install_and_key": 4}[status]


def _scaffold_env() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    LIBRARY_ROOT.mkdir(parents=True, exist_ok=True)
    if ENV_PATH.exists():
        return
    ENV_PATH.write_text(
        "# watch credentials\n"
        "# Preferred: local whisper.cpp (no key, no network). Install: brew install whisper-cpp\n"
        "# Then set WHISPER_CPP_MODEL to a ggml model path (default: ~/.config/watch/models/ggml-base.en.bin)\n"
        "# WHISPER_CPP_MODEL=\n"
        "# Cloud fallbacks (only if you don't want local):\n"
        "# GROQ_API_KEY=\n"
        "# OPENAI_API_KEY=\n"
        "SETUP_COMPLETE=false\n"
    )
    os.chmod(ENV_PATH, 0o600)


def _print_install_instructions() -> None:
    sysname = platform.system().lower()
    missing = [b for b in REQUIRED_BINS if not _which(b)]
    if not missing:
        return
    packages = list(dict.fromkeys("ffmpeg" if b == "ffprobe" else b for b in missing))
    if sysname == "darwin":
        # macOS — try brew automatically
        if _which("brew"):
            print(f"Installing missing tools via brew: {', '.join(missing)}", file=sys.stderr)
            subprocess.run(["brew", "install", *packages], check=False)
            return
        print("Homebrew not found. Install brew, then re-run setup.py.", file=sys.stderr)
    elif sysname == "linux":
        print("Run one of:", file=sys.stderr)
        print(f"  sudo apt-get install -y {' '.join(packages)}", file=sys.stderr)
        print(f"  sudo dnf install -y {' '.join(packages)}", file=sys.stderr)
        print("  pipx install yt-dlp  # (if 'yt-dlp' isn't in apt)", file=sys.stderr)
    elif sysname == "windows":
        print("Run:", file=sys.stderr)
        for b in packages:
            if b == "yt-dlp":
                print("  pip install --user yt-dlp", file=sys.stderr)
            else:
                print("  winget install --id Gyan.FFmpeg", file=sys.stderr)
    else:
        print(f"Unsupported platform '{sysname}'. Install manually: {', '.join(missing)}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true", help="Silent on success; exit code reflects state")
    p.add_argument("--json", action="store_true", help="Emit structured JSON status")
    args = p.parse_args(argv)
    s = status_for()
    if args.json:
        print(json.dumps(s))
        return exit_code_for(s["status"])
    if args.check:
        return exit_code_for(s["status"])
    # Full install / scaffold flow
    _scaffold_env()
    _print_install_instructions()
    s2 = status_for()
    return exit_code_for(s2["status"])


if __name__ == "__main__":
    raise SystemExit(main())
