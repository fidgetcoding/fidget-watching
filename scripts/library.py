"""Slug, cache, and manifest helpers for watch's persistent library."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.storage import read_json, write_json

LIBRARY_ROOT = Path.home() / "watch" / "library"

_SLUG_BAD = re.compile(r"[^a-z0-9]+")


def sanitize_title(title: str) -> str:
    """Lowercase, replace any non-[a-z0-9] run with a single dash, strip ends."""
    s = _SLUG_BAD.sub("-", title.lower()).strip("-")
    return s[:180].rstrip("-") or "untitled"


def slug_for(meta: dict) -> str:
    """`YYYY-MM-DD-<sanitized-title>-<short-hash>` where hash = sha1(source + focus)[:4]."""
    date = meta.get("watched_at") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    title = sanitize_title(meta.get("title", "untitled"))
    src = meta.get("source", "")
    focus = meta.get("focus_range_str", "")
    h = hashlib.sha1((src + "|" + focus).encode("utf-8")).hexdigest()[:4]
    return f"{date}-{title}-{h}"


def cache_lookup(slug: str, source_hash: str) -> Path | None:
    """Return the library dir if it exists and meta.source_hash matches; else None."""
    d = LIBRARY_ROOT / slug
    meta_path = d / "meta.json"
    if not meta_path.exists():
        return None
    data = read_json(meta_path)
    return d if isinstance(data, dict) and data.get("source_hash") == source_hash else None


def entry_for(meta: dict) -> Path:
    """Reuse the same source/focus entry across dates and title changes.

    The short suffix narrows the search, but full source/focus equality decides
    identity. A short-hash collision must never overwrite another video's notes.
    """
    slug = slug_for(meta)
    desired = LIBRARY_ROOT / slug
    suffix = slug.rsplit("-", 1)[-1]
    candidates = [desired, *sorted(LIBRARY_ROOT.glob(f"*-{suffix}")),
                  *sorted(LIBRARY_ROOT.glob(f"*-{suffix}-*"))]
    for candidate in candidates:
        data = read_json(candidate / "meta.json")
        if (isinstance(data, dict) and data.get("source") == meta["source"]
                and data.get("focus_range_str", "") == meta.get("focus_range_str", "")):
            return candidate
    existing = read_json(desired / "meta.json")
    if isinstance(existing, dict) and existing.get("source") is not None:
        identity = meta["source"] + "|" + meta.get("focus_range_str", "")
        return LIBRARY_ROOT / f"{slug}-{hashlib.sha256(identity.encode()).hexdigest()[:16]}"
    return desired


def write_manifest(
    *,
    path: Path,
    meta: dict[str, Any],
    scenes: list[dict[str, Any]],
    frames: list[dict[str, Any]],
    transcript_path: str,
    focus_range: tuple[float, float] | None,
) -> None:
    """Write manifest.json that Claude consumes."""
    payload = {
        "meta": meta,
        "scenes": scenes,
        "frames": frames,
        "transcript_path": transcript_path,
        "focus_range": (
            None
            if focus_range is None
            else {"start_s": focus_range[0], "end_s": focus_range[1]}
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, payload)
