"""Per-scene frame extraction via ffmpeg."""
from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.scenes import Scene

# A scene change is detected on the cut itself, and on a soft transition that's the
# middle of a blur or a wipe. Grabbing the frame a moment later gets the slide people
# actually saw, and the timestamp in the notes still points at the cut.
SETTLE_S = 1.5


def format_filename(index: int, t: float) -> str:
    """`NNNN_tMM-SS.jpg`. MM may exceed 59 for videos > 1h — that's intentional
    so filenames sort naturally."""
    total = round(t)
    mm, ss = divmod(total, 60)
    return f"{index:04d}_t{mm:02d}-{ss:02d}.jpg"


def _seek_time(scene: Scene, next_t: float | None) -> float:
    """Where to grab this scene's frame: past the transition, inside the scene."""
    if scene.kind != "detected" or scene.t <= 0.0:
        return scene.t
    settle = SETTLE_S if next_t is None else min(SETTLE_S, (next_t - scene.t) / 2)
    return scene.t + settle


def extract_frames(
    video: Path,
    scenes: list[Scene],
    *,
    out_dir: Path,
    width_px: int = 512,
) -> list[dict]:
    """For each scene, run a single-frame ffmpeg seek + extract.

    Returns: [{"index": int, "t": float, "path": str (relative to out_dir), "kind": str}]
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    for i, scene in enumerate(scenes, start=1):
        name = format_filename(i, scene.t)
        out_path = out_dir / name
        next_t = scenes[i].t if i < len(scenes) else None
        seek = _seek_time(scene, next_t)
        # `-ss` before `-i` is fast (key-frame seek) and accurate enough for our purposes.
        # `-frames:v 1` writes exactly one frame; `scale=W:-2` keeps aspect, even-height.
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-nostdin",
            "-y",
            "-ss", f"{seek:.3f}",
            "-i", str(video),
            "-frames:v", "1",
            "-vf", f"scale={width_px}:-2",
            "-q:v", "3",  # JPEG quality (2 best, 31 worst); 3 is a good balance
            str(out_path),
        ]
        try:
            # Seeking past the end of the video writes nothing. Depending on the
            # ffmpeg version that's an error or a silent exit 0, so check for the file.
            first = subprocess.run(cmd, capture_output=True)
            if (first.returncode != 0 or not out_path.exists()) and seek != scene.t:
                cmd[cmd.index("-ss") + 1] = f"{scene.t:.3f}"
                subprocess.run(cmd, check=True, capture_output=True)
            elif first.returncode != 0:
                raise subprocess.CalledProcessError(first.returncode, cmd, stderr=first.stderr)
        except subprocess.CalledProcessError as e:
            stderr_text = (
                e.stderr.decode("utf-8", errors="replace")
                if isinstance(e.stderr, (bytes, bytearray))
                else (e.stderr or "")
            )
            raise RuntimeError(
                f"ffmpeg failed extracting frame {i} at t={scene.t:.3f}s "
                f"(exit {e.returncode}): {stderr_text.strip()}"
            ) from e
        results.append({
            "index": i,
            "t": scene.t,
            "path": name,
            "kind": scene.kind,
        })
    return results
