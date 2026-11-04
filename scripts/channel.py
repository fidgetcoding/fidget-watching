"""Channel / playlist enumeration + index emission.

`yt-dlp --flat-playlist --dump-single-json <url>` is the probe. Any URL whose
top-level `_type` is `playlist`, or whose `entries` array has length > 1, is
treated as a multi-video source.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Optional

from scripts import access
from scripts.storage import write_text


_SLUG_BAD = re.compile(r"[^a-z0-9]+")


def _slugify(text: str, *, max_len: int = 40) -> str:
    s = _SLUG_BAD.sub("-", text.lower()).strip("-")
    return s[:max_len] or "untitled"


def _short_hash(text: str, *, n: int = 4) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:n]


def probe(url: str, *, limit: int = 10) -> dict:
    """Run yt-dlp's flat-playlist probe. Returns the parsed JSON or raises."""
    proc = subprocess.run(
        ["yt-dlp", *access.ytdlp_auth_args(), "--flat-playlist", "--playlist-end", str(limit),
         "--dump-single-json", url],
        capture_output=True, text=True, check=False, timeout=120,
    )
    if proc.returncode != 0:
        access.raise_for_failure(url, proc.stderr)
        raise RuntimeError(f"yt-dlp channel probe failed: {access.scrub(proc.stderr).strip()[:300]}")
    return json.loads(proc.stdout)


def is_channel_or_playlist(probe_info: dict) -> bool:
    """Even an empty or single-item playlist belongs on the batch path."""
    return probe_info.get("_type") in ("playlist", "multi_video")


def enumerate_videos(probe_info: dict, *, limit: Optional[int] = None) -> list[dict]:
    """Flatten yt-dlp's entries into [{id, title, url, duration}, ...].

    Handles two shapes:
    - Plain playlists: entries = [{id, title, url, duration, ...}, ...]
    - Channel tabs: entries = [{_type: "playlist", entries: [...]}, ...] — flatten one level.
    """
    if limit is not None and limit <= 0:
        return []

    def entries(items):
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("_type") in ("playlist", "multi_video"):
                yield from entries(item.get("entries") or [])
            else:
                yield item

    out: list[dict] = []
    seen = set()
    for e in entries(probe_info.get("entries") or []):
        vid = e.get("id")
        if not vid:
            continue
        url = e.get("webpage_url") or e.get("url") or f"https://youtu.be/{vid}"
        if not url.startswith(("http://", "https://")):
            url = f"https://youtu.be/{vid}"
        if url in seen:
            continue
        seen.add(url)
        out.append({
            "id": vid,
            "title": e.get("title") or vid,
            "url": url,
            "duration": float(e.get("duration") or 0.0),
        })
        if limit and len(out) >= limit:
            break
    return out


def channel_slug(probe_info: dict, url: str) -> str:
    """Stable per-channel directory name: YYYY... is provided by caller; here just title-hash."""
    title = probe_info.get("title") or probe_info.get("uploader") or "channel"
    return f"channel-{_slugify(title)}-{_short_hash(url)}"


def write_index(
    channel_dir: Path,
    *,
    channel_url: str,
    channel_title: str,
    results: list[dict],
    watched_at: str,
) -> Path:
    """Write a top-level index.md that links to every per-video notes.md.

    `results` items: {title, duration_s, library_dir, transcript_kind, frames, status, error?}
    """
    channel_dir.mkdir(parents=True, exist_ok=True)
    index_path = channel_dir / "index.md"

    ok = [r for r in results if r.get("status") == "ok"]
    failed = [r for r in results if r.get("status") != "ok"]

    lines: list[str] = []
    lines.append(f"# {channel_title}")
    lines.append("")
    lines.append(f"**Source:** {channel_url}  ·  **Watched:** {watched_at}  ·  **Videos:** {len(ok)}/{len(results)}")
    lines.append("")
    lines.append("## Videos")
    lines.append("")
    lines.append("| # | Title | Duration | Transcript | Frames | Notes |")
    lines.append("|---|---|---|---|---|---|")
    for i, r in enumerate(ok, 1):
        d = int(r.get("duration_s") or 0)
        dur = f"{d // 60}:{d % 60:02d}"
        title = r.get("title", "").replace("|", "\\|")[:60]
        notes_rel = Path(r["library_dir"]) / "notes.md"
        frames = r.get("frames")
        frame_count = len(frames) if isinstance(frames, list) else int(frames or 0)
        lines.append(
            f"| {i} | {title} | {dur} | {r.get('transcript_kind', '?')} | "
            f"{frame_count} | [{notes_rel.name}]({notes_rel}) |"
        )
    lines.append("")

    if failed:
        lines.append("## Failed")
        lines.append("")
        for r in failed:
            lines.append(f"- **{r.get('title', '?')}** — {r.get('error', 'unknown error')}")
        lines.append("")

    lines.append("## Cross-channel synthesis")
    lines.append("")
    lines.append("_Claude: fill in patterns across the videos above — recurring hooks, "
                 "thumbnail formulas, script structures, what's trending in this niche, "
                 "and the 3 most cloneable moves._")
    lines.append("")

    write_text(index_path, "\n".join(lines))
    return index_path
