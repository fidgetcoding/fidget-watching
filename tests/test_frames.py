import subprocess
from pathlib import Path

import pytest

from scripts.frames import format_filename, extract_frames
from scripts.scenes import Scene

FIXTURE = Path(__file__).parent / "fixtures" / "sample_10s.mp4"


def test_format_filename_zero_pads_index_and_timestamp():
    assert format_filename(1, 0.0) == "0001_t00-00.jpg"
    assert format_filename(42, 65.0) == "0042_t01-05.jpg"
    assert format_filename(100, 3661.0) == "0100_t61-01.jpg"  # past 1h, MM keeps growing


def test_format_filename_handles_subsecond_t():
    # We round to nearest second for the filename label
    assert format_filename(1, 3.4) == "0001_t00-03.jpg"
    assert format_filename(1, 3.6) == "0001_t00-04.jpg"


@pytest.mark.integration
def test_extract_frames_writes_one_jpeg_per_scene(tmp_path):
    scenes = [
        Scene(t=0.5, score=1.0, kind="detected"),
        Scene(t=4.0, score=0.9, kind="detected"),
        Scene(t=8.0, score=0.9, kind="detected"),
    ]
    frames = extract_frames(FIXTURE, scenes, out_dir=tmp_path, width_px=320)
    assert len(frames) == 3
    for f in frames:
        p = tmp_path / f["path"]
        assert p.exists()
        assert p.stat().st_size > 0
        assert p.suffix == ".jpg"
    # Returned objects carry the scene's timestamp + index
    assert [f["index"] for f in frames] == [1, 2, 3]
    assert [round(f["t"], 1) for f in frames] == [0.5, 4.0, 8.0]


@pytest.mark.integration
def test_extract_frames_resolution_controls_width(tmp_path):
    scenes = [Scene(t=1.0, score=1.0, kind="detected")]
    frames_512 = extract_frames(FIXTURE, scenes, out_dir=tmp_path / "a", width_px=512)
    frames_320 = extract_frames(FIXTURE, scenes, out_dir=tmp_path / "b", width_px=320)
    size_512 = (tmp_path / "a" / frames_512[0]["path"]).stat().st_size
    size_320 = (tmp_path / "b" / frames_320[0]["path"]).stat().st_size
    assert size_512 > size_320, "512px JPEG should be larger than 320px JPEG"


def _mean_rgb(jpeg):
    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-nostdin", "-i", str(jpeg),
         "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    ).stdout
    return tuple(raw[:3])


@pytest.fixture
def crossfade_video(tmp_path):
    """Red for 3 s, then a 1 s crossfade starting at t=3, then blue."""
    out = tmp_path / "fade.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-nostdin",
         "-f", "lavfi", "-i", "color=red:size=160x120:duration=4:rate=25",
         "-f", "lavfi", "-i", "color=blue:size=160x120:duration=6:rate=25",
         "-filter_complex", "[0][1]xfade=transition=fade:duration=1:offset=3[v]",
         "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
        check=True,
    )
    return out


@pytest.mark.integration
def test_a_scene_frame_lands_after_the_transition_not_inside_it(crossfade_video, tmp_path):
    # t=3.5 is the middle of the fade, the kind of moment scene detection reports.
    frames = extract_frames(
        crossfade_video, [Scene(t=3.5, score=0.5, kind="detected")],
        out_dir=tmp_path / "f", width_px=160,
    )
    r, _, b = _mean_rgb(tmp_path / "f" / frames[0]["path"])
    assert b > 200 and r < 30, f"expected settled blue, got r={r} b={b}"
    # The notes still point at the moment of the change.
    assert frames[0]["t"] == 3.5


@pytest.mark.integration
def test_coverage_frames_and_the_opening_frame_stay_exactly_where_they_were(crossfade_video, tmp_path):
    scenes = [
        Scene(t=0.0, score=1.0, kind="detected"),
        Scene(t=2.9, score=0.0, kind="floor"),
    ]
    frames = extract_frames(crossfade_video, scenes, out_dir=tmp_path / "f", width_px=160)
    for f in frames:
        r, _, b = _mean_rgb(tmp_path / "f" / f["path"])
        assert r > 200 and b < 30, f"t={f['t']} should still be red, got r={r} b={b}"


@pytest.mark.integration
def test_settling_stays_inside_a_short_scene(crossfade_video, tmp_path):
    # Two cuts 0.4 s apart: settling 1.5 s would skip the first scene entirely.
    frames = extract_frames(
        crossfade_video,
        [Scene(t=1.0, score=0.5, kind="detected"), Scene(t=1.4, score=0.5, kind="detected")],
        out_dir=tmp_path / "f", width_px=160,
    )
    r, _, _ = _mean_rgb(tmp_path / "f" / frames[0]["path"])
    assert r > 200


@pytest.mark.integration
def test_a_cut_just_before_the_end_still_gets_a_frame(crossfade_video, tmp_path):
    frames = extract_frames(
        crossfade_video, [Scene(t=8.9, score=0.5, kind="detected")],
        out_dir=tmp_path / "f", width_px=160,
    )
    assert (tmp_path / "f" / frames[0]["path"]).stat().st_size > 0
