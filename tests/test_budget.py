import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import budget, scenes as scenes_mod
from scripts.scenes import Scene

ROOT = Path(__file__).parent.parent


def test_short_videos_keep_the_minimum_budget():
    assert budget.auto_max_frames(0) == 20
    assert budget.auto_max_frames(60) == 20


def test_budget_grows_with_length_but_slows_down():
    ten = budget.auto_max_frames(10 * 60)
    hour = budget.auto_max_frames(60 * 60)
    three_hours = budget.auto_max_frames(3 * 3600)
    assert ten == 60
    assert hour == 90 + 113  # 15 min at 6/min, 45 min at 2.5/min, rounded up
    assert three_hours == hour + 180
    assert ten / 10 > hour / 60 > three_hours / 180


@pytest.mark.parametrize("hours", [0.5, 1, 2, 3, 6, 12])
def test_budget_always_leaves_room_for_the_coverage_floor(hours):
    duration = hours * 3600
    floor_frames = int(duration / 45) + 1
    assert budget.auto_max_frames(duration) >= floor_frames


def test_a_static_lecture_uses_only_what_it_needs():
    """Budget is a ceiling. A slow video shouldn't pay for frames it doesn't need."""
    duration = 60 * 60.0
    raw = [Scene(t=0.0, score=1.0, kind="detected")]
    floored = scenes_mod.apply_coverage_floor(raw, duration_s=duration, max_gap_s=45.0)
    kept = scenes_mod.apply_budget_cap(floored, max_frames=budget.auto_max_frames(duration))
    assert len(kept) == len(floored) == 80


def test_fast_cuts_hit_the_budget_and_drop_the_weakest():
    duration = 10 * 60.0
    raw = [Scene(t=float(i), score=(i % 100) / 100, kind="detected") for i in range(600)]
    floored = scenes_mod.apply_coverage_floor(raw, duration_s=duration, max_gap_s=45.0)
    limit = budget.auto_max_frames(duration)
    kept = scenes_mod.apply_budget_cap(floored, max_frames=limit)
    assert len(kept) == limit == 60
    assert min(s.score for s in kept) >= 0.8


def test_tokens_follow_the_published_image_formula():
    # 512 x 288: 19 patches across, 11 down.
    assert budget.tokens_per_frame(512) == 209
    # The docs' own worked example: 1000 x 1000 costs 1,296.
    assert budget.tokens_per_frame(1000, aspect=1.0) == 1296
    # Doubling the width roughly quadruples it.
    assert budget.tokens_per_frame(1024) == 777


def test_cost_counts_frames_and_transcript(tmp_path):
    transcript = tmp_path / "t.json"
    transcript.write_text("x" * 4000)
    planned = [Scene(t=float(i), score=0.5, kind="detected") for i in range(12)]
    kept = planned[:10]
    cost = budget.final_cost(
        planned=planned, kept=kept, limit=10, override=False,
        width_px=512, aspect=9 / 16, transcript_path=transcript,
    )
    assert cost["frames"] == 10
    assert cost["dropped_scene_changes"] == 2
    assert cost["frame_tokens"] == 2090
    assert cost["transcript_tokens"] == 1000
    assert cost["total_tokens"] == 3090
    text = budget.format_cost(cost)
    assert "=== cost ===" in text
    assert "2 weakest scene changes" in text
    assert "~3k" in text
    assert "--max-frames" in text


def test_missing_transcript_costs_nothing(tmp_path):
    assert budget.transcript_tokens(tmp_path / "nope.json") == 0


@pytest.fixture
def cutty_video(tmp_path):
    """90 seconds that cut every 3 seconds, so scene changes outnumber a low budget."""
    colors = ["red", "blue", "green", "yellow", "white", "black"]
    inputs = []
    filters = ""
    for i in range(30):
        inputs += ["-f", "lavfi", "-i", f"color={colors[i % len(colors)]}:size=160x120:duration=3:rate=10"]
        filters += f"[{i}:v]"
    out = tmp_path / "cuts.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-nostdin", *inputs,
         "-filter_complex", f"{filters}concat=n=30:v=1:a=0[v]", "-map", "[v]",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
        check=True,
    )
    return out


def _watch(video, out_dir, *extra):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "watch.py"), str(video),
         "--no-whisper", "--out-dir", str(out_dir), *extra],
        capture_output=True, text=True, check=False, cwd=str(ROOT),
    )


@pytest.mark.integration
def test_watch_prints_the_cost_before_the_frames(cutty_video, tmp_path):
    proc = _watch(cutty_video, tmp_path / "lib")
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert out.index("=== cost estimate ===") < out.index("=== cost ===") < out.index("=== frames ===")
    assert "scaled to length" in out

    cost = json.loads(next((tmp_path / "lib").glob("*/cost.json")).read_text())
    assert cost["max_frames_source"] == "auto"
    assert cost["max_frames"] == 20
    assert cost["frames"] == 20
    # 160x120 is 4:3, and the estimate should use the video's real shape.
    assert cost["tokens_per_frame"] == budget.tokens_per_frame(512, aspect=120 / 160)


@pytest.mark.integration
def test_max_frames_still_overrides_the_budget(cutty_video, tmp_path):
    proc = _watch(cutty_video, tmp_path / "lib", "--max-frames", "25")
    assert proc.returncode == 0, proc.stderr
    assert "--max-frames" in proc.stdout
    manifest = json.loads(next((tmp_path / "lib").glob("*/manifest.json")).read_text())
    assert len(manifest["frames"]) == 25
    cost = json.loads(next((tmp_path / "lib").glob("*/cost.json")).read_text())
    assert cost["max_frames_source"] == "override"
