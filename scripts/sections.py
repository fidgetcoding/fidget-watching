"""Split a long video into sections, so notes get written one manageable piece at a time.

One notes pass over a whole long video is where accuracy falls off: hundreds of frames
and a huge transcript all sit in context, and the later parts get thinner. Each section
gets its own frames and its own slice of the transcript. Cuts land on a natural break,
a pause in the speech or a strong scene change, and never in the middle of a sentence.
merge_notes.py puts the section notes back together.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from scripts import budget as budget_mod

# Below this length a single notes pass holds up fine.
TRIGGER_S = 30 * 60
TARGET_S = 20 * 60
MAX_FRAMES_PER_SECTION = 60
# How far from the even split a cut may move to find a natural break.
SNAP_WINDOW_S = 180.0
MIN_PAUSE_S = 1.5

PLAN_FILE = "sections.json"


def is_long(window_s: float, *, section_minutes: float | None = None) -> bool:
    if section_minutes is not None:
        return window_s > section_minutes * 60
    return window_s > TRIGGER_S


def section_count(window_s: float, n_frames: int, *, target_s: float = TARGET_S) -> int:
    return max(1, math.ceil(window_s / target_s), math.ceil(n_frames / MAX_FRAMES_PER_SECTION))


def _best_cut(ideal: float, lo: float, hi: float, cues: list[dict], scenes: list[dict]) -> float:
    """The most natural place to cut between lo and hi, nearest to the ideal point."""
    best: tuple[float, float] | None = None
    for prev, cue in zip(cues, cues[1:]):
        t = cue["t_start"]
        if not lo <= t <= hi:
            continue
        pause = t - prev["t_end"]
        if pause < MIN_PAUSE_S:
            continue
        # A longer pause wins. Distance from the even split only breaks near-ties.
        score = pause - abs(t - ideal) / SNAP_WINDOW_S
        if best is None or score > best[0]:
            best = (score, t)
    if best is not None:
        return best[1]

    strong = [s for s in scenes if s["kind"] == "detected" and lo <= s["t"] <= hi]
    if strong:
        top = max(strong, key=lambda s: (s["score"], -abs(s["t"] - ideal)))
        return _snap_to_cue_start(top["t"], cues)
    return _snap_to_cue_start(ideal, cues)


def _snap_to_cue_start(t: float, cues: list[dict]) -> float:
    """Move a cut out of the middle of a sentence, to the start of the cue it falls in."""
    for cue in cues:
        if cue["t_start"] <= t < cue["t_end"]:
            return cue["t_start"]
    return t


def plan_cuts(
    start_s: float,
    end_s: float,
    *,
    n_frames: int,
    transcript: list[dict],
    scenes: list[dict],
    target_s: float = TARGET_S,
) -> list[float]:
    """Section edges from start to end, inclusive of both."""
    window = end_s - start_s
    n = section_count(window, n_frames, target_s=target_s)
    step = window / n
    reach = min(SNAP_WINDOW_S, step / 4)
    cuts = [start_s]
    for i in range(1, n):
        ideal = start_s + i * step
        cut = _best_cut(ideal, ideal - reach, ideal + reach, transcript, scenes)
        cuts.append(cut)
    cuts.append(end_s)
    return cuts


def build_plan(
    cuts: list[float],
    *,
    frames: list[dict],
    transcript: list[dict],
    tokens_per_frame: int,
) -> list[dict]:
    """One entry per section. A cue belongs to the section it starts in."""
    plan = []
    last = len(cuts) - 2
    for i, (a, b) in enumerate(zip(cuts, cuts[1:])):
        in_section = (lambda t, a=a, b=b, final=(i == last): a <= t < b or (final and t == b))
        section_frames = [f for f in frames if in_section(f["t"])]
        cues = [c for c in transcript if in_section(c["t_start"])]
        name = f"{i + 1:02d}"
        plan.append({
            "index": i + 1,
            "start_s": a,
            "end_s": b,
            "frames": section_frames,
            "cues": cues,
            "transcript_path": f"sections/{name}.transcript.json",
            "notes_path": f"sections/{name}.notes.md",
            "tokens": (
                len(section_frames) * tokens_per_frame
                + budget_mod.text_tokens(json.dumps(cues, indent=2, ensure_ascii=False))
            ),
        })
    return plan


def write_plan(work: Path, plan: list[dict]) -> None:
    """Write the section files. Notes already written survive a re-run of the same plan,
    so an interrupted long run picks up where it stopped. A changed plan clears them,
    because they'd describe frames that no longer exist."""
    sections_dir = work / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)
    public = [{k: v for k, v in s.items() if k != "cues"} for s in plan]
    new_text = json.dumps({"version": 1, "sections": public}, indent=2, ensure_ascii=False)

    plan_path = work / PLAN_FILE
    old_text = plan_path.read_text() if plan_path.exists() else None
    if old_text != new_text:
        for stale in sections_dir.glob("*.notes.md"):
            stale.unlink()
        (work / "tldr.md").unlink(missing_ok=True)

    plan_path.write_text(new_text)
    for s in plan:
        (work / s["transcript_path"]).write_text(
            json.dumps(s["cues"], indent=2, ensure_ascii=False)
        )


def clear_plan(work: Path) -> None:
    """A short video, or --no-sections, shouldn't inherit an older plan."""
    (work / PLAN_FILE).unlink(missing_ok=True)


def fmt_t(t: float) -> str:
    total = int(t)
    return f"{total // 60:02d}:{total % 60:02d}"


def format_plan(plan: list[dict], library_dir: Path) -> str:
    lines = [
        "=== sections ===",
        f"long video: write the notes one section at a time ({len(plan)} sections)",
        f"plan: {library_dir / PLAN_FILE}",
    ]
    for s in plan:
        lines.append(
            f"{s['index']:02d}  {fmt_t(s['start_s'])}-{fmt_t(s['end_s'])}  "
            f"frames={len(s['frames'])}  ~{budget_mod.format_tokens(s['tokens'])} tokens  "
            f"transcript={s['transcript_path']}  notes={s['notes_path']}"
        )
    merge_script = Path(__file__).parent / "merge_notes.py"
    lines.append(f"merge when done: python3 \"{merge_script}\" \"{library_dir}\"")
    return "\n".join(lines)
