"""Long videos: splitting into sections, checking section notes, merging them back."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import merge_notes, sections

ROOT = Path(__file__).parent.parent


def cues_every(step, until, pause_at=()):
    """Evenly spaced cues, with an extra pause before the listed times."""
    out = []
    t = 0.0
    while t < until:
        end = t + step - 0.2
        if any(t < p <= end + 5 for p in pause_at):
            end = t + 1.0
        out.append({"t_start": t, "t_end": end, "text": f"cue at {int(t)} seconds"})
        t += step
    return out


def scene(t, score=0.5, kind="detected"):
    return {"t": t, "score": score, "kind": kind}


def test_only_long_videos_use_sections():
    assert not sections.is_long(29 * 60)
    assert sections.is_long(31 * 60)
    assert sections.is_long(11 * 60, section_minutes=10)


def test_section_count_follows_length_and_frames():
    assert sections.section_count(60 * 60, 100) == 3
    assert sections.section_count(3 * 3600, 300) == 9
    # A fast-cutting video splits sooner than its length alone would ask for.
    assert sections.section_count(40 * 60, 200) == 4


def test_cuts_cover_the_whole_video_in_order():
    cuts = sections.plan_cuts(0, 3600, n_frames=120, transcript=cues_every(10, 3600), scenes=[])
    assert cuts[0] == 0 and cuts[-1] == 3600
    assert cuts == sorted(cuts)
    assert len(cuts) == 4


def test_a_cut_moves_to_a_pause_in_the_speech():
    # Even split is at 20:00. There's a real pause in the speech at 21:00.
    transcript = cues_every(10, 3600, pause_at=[1260])
    cuts = sections.plan_cuts(0, 3600, n_frames=120, transcript=transcript, scenes=[])
    assert cuts[1] == 1260


def test_a_cut_prefers_a_strong_scene_change_when_nobody_pauses():
    transcript = [{"t_start": t, "t_end": t + 10, "text": "x"} for t in range(0, 3600, 10)]
    scenes = [scene(1100.0, score=0.4), scene(1250.0, score=0.9), scene(1500.0, score=1.0)]
    cuts = sections.plan_cuts(0, 3600, n_frames=120, transcript=transcript, scenes=scenes)
    assert cuts[1] == 1250.0


def test_a_cut_never_lands_inside_a_sentence():
    transcript = [{"t_start": t, "t_end": t + 10, "text": "x"} for t in range(0, 3600, 10)]
    # No pauses and no scenes, so the cut falls back on the even split.
    cuts = sections.plan_cuts(0, 1205 * 3, n_frames=10, transcript=transcript, scenes=[])
    starts = {c["t_start"] for c in transcript}
    assert all(c in starts for c in cuts[1:-1])


def test_no_transcript_still_cuts_evenly():
    cuts = sections.plan_cuts(0, 3600, n_frames=10, transcript=[], scenes=[])
    assert cuts == [0, 1200, 2400, 3600]


def make_plan(cuts, frames, transcript):
    return sections.build_plan(cuts, frames=frames, transcript=transcript, tokens_per_frame=209)


def test_every_frame_and_cue_lands_in_exactly_one_section():
    frames = [{"index": i, "t": float(i * 30), "path": f"frames/{i}.jpg", "kind": "floor"} for i in range(121)]
    transcript = cues_every(10, 3601)
    plan = make_plan([0, 1200, 2400, 3600], frames, transcript)
    assert sum(len(s["frames"]) for s in plan) == len(frames)
    assert sum(len(s["cues"]) for s in plan) == len(transcript)
    # Including a frame exactly on the last edge.
    assert plan[-1]["frames"][-1]["t"] == 3600.0


def test_tokens_are_counted_per_section():
    frames = [{"index": 1, "t": 5.0, "path": "frames/1.jpg", "kind": "floor"}]
    plan = make_plan([0, 10], frames, [])
    assert plan[0]["tokens"] >= 209


def test_rerunning_the_same_plan_keeps_written_notes(tmp_path):
    plan = make_plan([0, 1200], [], [])
    sections.write_plan(tmp_path, plan)
    (tmp_path / "sections/01.notes.md").write_text("mine")
    sections.write_plan(tmp_path, plan)
    assert (tmp_path / "sections/01.notes.md").read_text() == "mine"


def test_a_changed_plan_clears_notes_about_old_frames(tmp_path):
    sections.write_plan(tmp_path, make_plan([0, 1200], [], []))
    (tmp_path / "sections/01.notes.md").write_text("stale")
    (tmp_path / "tldr.md").write_text("stale")
    sections.write_plan(tmp_path, make_plan([0, 1000], [], []))
    assert not (tmp_path / "sections/01.notes.md").exists()
    assert not (tmp_path / "tldr.md").exists()


NOTES = """## Section Summary
{summary}

## Key Concepts
- **Gradient** — how much the loss moves · `[t={t1}]`
- **{unique}** — only in this part · `[t={t2}]`

## Notes

### [t={t1}] First point

![](frames/{f1})

**On screen:** A slide.

**Said:** the gradient tells you which direction reduces loss

**Synthesis:** Sets up the rest.

### [t={t2}] Second point

![](frames/{f2})

**On screen:** Another slide.

**Said:** we repeat this until the model converges nicely

**Synthesis:** Pays it off.

## Code & Commands
```python
# [t={t2}]
## not a heading, it's a comment inside a fence
step = step + 1
```

## Diagrams Referenced
- `[t={t1}]` a loss curve

## Open Questions
- What about momentum?
"""


@pytest.fixture
def library(tmp_path):
    """A two-section video, 40 minutes, with its plan, frames and transcript on disk."""
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    frames = []
    for i, t in enumerate([60.0, 600.0, 1300.0, 2000.0], start=1):
        name = f"{i:04d}_t.jpg"
        (frames_dir / name).write_bytes(b"\xff")
        frames.append({"index": i, "t": t, "path": f"frames/{name}", "kind": "detected"})
    transcript = [
        {"t_start": 50.0, "t_end": 70.0, "text": "the gradient tells you which direction reduces loss"},
        {"t_start": 590.0, "t_end": 610.0, "text": "we repeat this until the model converges nicely"},
        {"t_start": 1290.0, "t_end": 1310.0, "text": "the gradient tells you which direction reduces loss"},
        {"t_start": 1990.0, "t_end": 2010.0, "text": "we repeat this until the model converges nicely"},
    ]
    plan = make_plan([0, 1200, 2400], frames, transcript)
    sections.write_plan(tmp_path, plan)
    (tmp_path / "meta.json").write_text(json.dumps({
        "title": "A Long Lecture", "source": "https://example.com/v", "duration_s": 2400.0,
        "watched_at": "2026-11-01",
    }))
    return tmp_path


def write_notes(library, index, **overrides):
    values = {
        1: dict(summary="Part one.", unique="Learning rate", t1="01:00", t2="10:00", f1="0001_t.jpg", f2="0002_t.jpg"),
        2: dict(summary="Part two.", unique="Convergence", t1="21:40", t2="33:20", f1="0003_t.jpg", f2="0004_t.jpg"),
    }[index]
    values.update(overrides)
    (library / f"sections/{index:02d}.notes.md").write_text(NOTES.format(**values))


def run_merge(library, check_only=False):
    lines = []

    class Out:
        def write(self, s):
            lines.append(s)

        def flush(self):
            pass

    rc = merge_notes.run(library, check_only=check_only, out=Out())
    return rc, "".join(lines)


def test_merge_refuses_when_a_section_is_missing(library):
    write_notes(library, 1)
    (library / "tldr.md").write_text("Overall.")
    rc, out = run_merge(library)
    assert rc == 1
    assert "section 02: no notes yet" in out
    assert not (library / "notes.md").exists()


def test_merge_needs_a_tldr_but_check_does_not(library):
    write_notes(library, 1)
    write_notes(library, 2)
    assert run_merge(library, check_only=True)[0] == 0
    rc, out = run_merge(library)
    assert rc == 1
    assert "no tldr.md yet" in out


@pytest.fixture
def merged(library):
    write_notes(library, 1)
    write_notes(library, 2)
    (library / "tldr.md").write_text("The whole lecture in brief.")
    rc, out = run_merge(library)
    assert rc == 0, out
    return (library / "notes.md").read_text()


def test_merged_notes_keep_the_template_shape(merged):
    headings = [ln for ln in merged.splitlines() if ln.startswith("## ") and "not a heading" not in ln]
    assert headings == [
        "## TLDR", "## Parts", "## Key Concepts", "## Notes",
        "## Code & Commands", "## Diagrams Referenced", "## Open Questions",
    ]
    assert merged.startswith("# A Long Lecture\n")
    assert "**Duration:** 40:00" in merged


def test_merged_notes_stay_in_video_order(merged):
    anchors = [ln.split("]")[0] for ln in merged.splitlines() if ln.startswith("### [t=")]
    assert anchors == ["### [t=01:00", "### [t=10:00", "### [t=21:40", "### [t=33:20"]


def test_a_concept_in_two_sections_becomes_one_line_with_both_times(merged):
    lines = [ln for ln in merged.splitlines() if "**Gradient**" in ln]
    assert len(lines) == 1
    assert "`[t=01:00]`" in lines[0] and "`[t=21:40]`" in lines[0]
    assert "**Learning rate**" in merged and "**Convergence**" in merged


def test_repeated_open_questions_collapse_but_code_stays_whole(merged):
    assert merged.count("What about momentum?") == 1
    assert merged.count("step = step + 1") == 2


def test_section_summaries_become_the_parts_list(merged):
    assert "- **00:00 to 20:00.** Part one." in merged
    assert "- **20:00 to 40:00.** Part two." in merged


def test_a_timestamp_outside_its_section_blocks_the_merge(library):
    write_notes(library, 1, t2="35:00")
    write_notes(library, 2)
    rc, out = run_merge(library, check_only=True)
    assert rc == 1
    assert "[t=35:00] is outside this section" in out


def test_a_link_to_a_missing_frame_blocks_the_merge(library):
    write_notes(library, 1, f1="9999_invented.jpg")
    write_notes(library, 2)
    rc, out = run_merge(library, check_only=True)
    assert rc == 1
    assert "links a frame that doesn't exist" in out


def test_notes_with_no_timestamps_block_the_merge(library):
    write_notes(library, 2)
    (library / "sections/01.notes.md").write_text("## Section Summary\nx\n\n## Notes\nNothing structured.\n")
    rc, out = run_merge(library, check_only=True)
    assert rc == 1
    assert "no ### [t=MM:SS] sections" in out


def test_a_heading_inside_a_code_fence_does_not_split_the_notes(library):
    blocks = merge_notes.split_h2("## Code & Commands\n```\n## nope\n```\n## Open Questions\n- q\n")
    assert set(blocks) == {"Code & Commands", "Open Questions"}
    assert "## nope" in blocks["Code & Commands"]


def test_a_long_gap_in_the_notes_gets_flagged(library):
    write_notes(library, 1, t2="02:00")  # nothing from 02:00 to the section's end at 20:00
    write_notes(library, 2)
    rc, out = run_merge(library, check_only=True)
    assert rc == 0
    assert "no notes between 02:00 and 20:00" in out


def test_a_quote_that_is_not_in_the_transcript_gets_flagged(library):
    write_notes(library, 2)
    text = NOTES.format(summary="s", unique="u", t1="01:00", t2="10:00", f1="0001_t.jpg", f2="0002_t.jpg")
    text = text.replace(
        "the gradient tells you which direction reduces loss",
        "quantum entanglement reorganizes medieval spreadsheet governance overnight",
    )
    (library / "sections/01.notes.md").write_text(text)
    rc, out = run_merge(library, check_only=True)
    assert rc == 0
    assert "of the \"Said\" words are in the transcript" in out


def test_a_true_quote_is_not_flagged(library):
    write_notes(library, 1)
    write_notes(library, 2)
    _, out = run_merge(library, check_only=True)
    assert "Said" not in out


def test_merge_on_a_short_video_says_so(tmp_path):
    rc, out = run_merge(tmp_path)
    assert rc == 2
    assert "doesn't use sections" in out


@pytest.fixture
def long_video(tmp_path):
    """31 minutes with a colour change every 5 minutes, generated, no audio. It encodes
    in seconds because almost nothing moves."""
    out = tmp_path / "long.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-nostdin",
         "-f", "lavfi", "-i", "color=white:size=64x36:rate=1:duration=1860",
         "-vf", "hue=H=2*PI*floor(t/300)/7:s=if(lt(t\\,300)\\,0\\,1),format=yuv420p",
         "-c:v", "libx264", "-preset", "ultrafast", str(out)],
        check=True,
    )
    return out


@pytest.mark.integration
def test_watch_splits_a_long_video_without_start_or_end(long_video, tmp_path):
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "watch.py"), str(long_video),
         "--no-whisper", "--out-dir", str(tmp_path / "lib")],
        capture_output=True, text=True, check=False, cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    assert "=== sections ===" in proc.stdout
    lib = next((tmp_path / "lib").glob("*"))
    plan = json.loads((lib / "sections.json").read_text())["sections"]
    assert len(plan) == 2
    assert plan[0]["start_s"] == 0
    assert plan[-1]["end_s"] == pytest.approx(1860, abs=1)
    manifest = json.loads((lib / "manifest.json").read_text())
    assert sum(len(s["frames"]) for s in plan) == len(manifest["frames"])
    for s in plan:
        for fr in s["frames"]:
            assert (lib / fr["path"]).exists()
    assert (lib / plan[0]["transcript_path"]).exists()


@pytest.mark.integration
def test_no_sections_flag_keeps_one_pass(long_video, tmp_path):
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "watch.py"), str(long_video),
         "--no-whisper", "--no-sections", "--out-dir", str(tmp_path / "lib")],
        capture_output=True, text=True, check=False, cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    assert "=== sections ===" not in proc.stdout
    assert not list((tmp_path / "lib").glob("*/sections.json"))


@pytest.mark.integration
def test_a_short_video_has_no_sections(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "watch.py"),
         str(ROOT / "tests" / "fixtures" / "sample_10s.mp4"),
         "--no-whisper", "--out-dir", str(tmp_path / "lib")],
        capture_output=True, text=True, check=False, cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    assert "=== sections ===" not in proc.stdout
