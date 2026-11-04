"""yt-dlp download wrapper + local file linker."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from scripts import access


def media_files(out_dir: Path, basename: str = "video") -> list[Path]:
    """Completed media only: never mistake a partial download or sidecar for video."""
    extensions = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".flv", ".ts", ".mpeg", ".mpg", ".ogv"}
    return sorted(p for p in out_dir.glob(f"{basename}.*")
                  if p.suffix.lower() in extensions and p.is_file() and p.stat().st_size > 0)


def download_video(url: str, out_dir: Path, *, basename: str = "video") -> Path:
    """Download to `out_dir/<basename>.<ext>` via yt-dlp. Returns the downloaded file."""
    out_dir.mkdir(parents=True, exist_ok=True)
    template = str(out_dir / f"{basename}.%(ext)s")
    cmd = [
        "yt-dlp",
        *access.ytdlp_auth_args(),
        "--no-playlist",
        "-f", "best[ext=mp4]/best",
        "-o", template,
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        access.raise_for_failure(url, proc.stderr)
        raise RuntimeError(f"yt-dlp failed: {access.scrub(proc.stderr).strip()}")
    matches = media_files(out_dir, basename)
    if not matches:
        raise RuntimeError(f"yt-dlp returned 0 but no {basename}.* file in {out_dir}")
    return matches[0]


def copy_local(src: Path, out_dir: Path, *, basename: str = "video") -> Path:
    """For local sources, symlink (cheap, no copy) into out_dir/<basename>.<ext>.
    Falls back to a regular file copy if symlink fails."""
    out_dir.mkdir(parents=True, exist_ok=True)
    src = src.expanduser().resolve()
    dst = out_dir / f"{basename}{src.suffix}"
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    try:
        os.symlink(src, dst)
    except OSError:
        shutil.copyfile(src, dst)
    return dst
