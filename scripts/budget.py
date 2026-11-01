"""Frame budget that scales with the video, and the token cost that goes with it.

Frames are what a /watch run costs, so the budget is a curve and not one number:
dense early on, because the first minutes of a short video can move fast, and
thinner later, but always above the coverage floor, so a long lecture never loses
its one-frame-every-45-seconds guarantee to the budget.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

# Frames per minute allowed in each stretch of the video: (minutes up to, rate).
_CURVE = ((15.0, 6.0), (60.0, 2.5), (math.inf, 1.5))
_MIN_BUDGET = 20

# Anthropic's published image cost: one token per 28x28 patch, rounding each side up.
_PATCH_PX = 28
_DEFAULT_ASPECT = 9 / 16

# Close enough for an estimate of transcript JSON.
_CHARS_PER_TOKEN = 4


def auto_max_frames(duration_s: float) -> int:
    """How many frames a video of this length is allowed by default."""
    minutes = max(duration_s, 0.0) / 60.0
    total = 0.0
    start = 0.0
    for end, rate in _CURVE:
        if minutes <= start:
            break
        total += (min(minutes, end) - start) * rate
        start = end
    return max(_MIN_BUDGET, math.ceil(total))


def tokens_per_frame(width_px: int, aspect: float = _DEFAULT_ASPECT) -> int:
    """Estimated input tokens for one frame at this width. aspect = height / width."""
    height_px = width_px * aspect
    return math.ceil(width_px / _PATCH_PX) * math.ceil(height_px / _PATCH_PX)


def text_tokens(text: str) -> int:
    return math.ceil(len(text) / _CHARS_PER_TOKEN)


def transcript_tokens(path: Path) -> int:
    """Estimated input tokens for reading a transcript file. 0 if it isn't there."""
    try:
        return math.ceil(path.stat().st_size / _CHARS_PER_TOKEN)
    except OSError:
        return 0


def summarize_kinds(scenes: list) -> tuple[int, int]:
    """(scene changes, coverage frames) in a scene list."""
    detected = sum(1 for s in scenes if s.kind == "detected")
    return detected, len(scenes) - detected


def format_tokens(n: int) -> str:
    return f"{n / 1000:.0f}k" if n >= 1000 else str(n)


def early_estimate(duration_s: float, *, width_px: int, override: int | None) -> str:
    """The cost line printed before anything is downloaded. It's an upper bound,
    because the real frame count depends on the scene changes found later."""
    limit = override if override is not None else auto_max_frames(duration_s)
    source = "--max-frames" if override is not None else "scaled to length"
    return (
        "=== cost estimate ===\n"
        f"frame budget: up to {limit} frames ({source})\n"
        f"frame tokens: up to ~{format_tokens(limit * tokens_per_frame(width_px))} "
        f"at {width_px} px"
    )


def final_cost(
    *,
    planned: list,
    kept: list,
    limit: int,
    override: bool,
    width_px: int,
    aspect: float,
    transcript_path: Path,
) -> dict:
    """The cost of this run once the scenes are known, before any frame is extracted."""
    detected, coverage = summarize_kinds(kept)
    per_frame = tokens_per_frame(width_px, aspect)
    frame_tokens = len(kept) * per_frame
    text_tokens = transcript_tokens(transcript_path)
    return {
        "frames": len(kept),
        "scene_change_frames": detected,
        "coverage_frames": coverage,
        "dropped_scene_changes": len(planned) - len(kept),
        "max_frames": limit,
        "max_frames_source": "override" if override else "auto",
        "tokens_per_frame": per_frame,
        "frame_tokens": frame_tokens,
        "transcript_tokens": text_tokens,
        "total_tokens": frame_tokens + text_tokens,
    }


def format_cost(cost: dict) -> str:
    source = "--max-frames" if cost["max_frames_source"] == "override" else "scaled to length and scene changes"
    lines = [
        "=== cost ===",
        f"frames: {cost['frames']} ({cost['scene_change_frames']} scene changes "
        f"+ {cost['coverage_frames']} coverage), budget {cost['max_frames']} ({source})",
    ]
    if cost["dropped_scene_changes"]:
        lines.append(f"dropped: {cost['dropped_scene_changes']} weakest scene changes, over budget")
    lines.append(
        f"input tokens to read it all: ~{format_tokens(cost['total_tokens'])} "
        f"(~{format_tokens(cost['frame_tokens'])} frames at ~{cost['tokens_per_frame']} each "
        f"+ ~{format_tokens(cost['transcript_tokens'])} transcript)"
    )
    lines.append("each extra frame costs the same again; --max-frames N sets a different budget")
    return "\n".join(lines)


def write_cost(path: Path, cost: dict) -> None:
    path.write_text(json.dumps(cost, indent=2))
