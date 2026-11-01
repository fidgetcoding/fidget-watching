"""watch orchestrator — runs the full pipeline and prints a manifest block.

Single-video URLs and local files run the per-video pipeline. Channel and
playlist URLs are detected via yt-dlp's flat-playlist probe and processed as
a batch — each video runs the per-video pipeline, then a top-level index.md
rolls up the channel.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# When invoked as `python scripts/watch.py` the repo root is not automatically
# on sys.path.  Insert it so that `from scripts import …` works correctly
# regardless of how the script is launched.
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts import access
from scripts import library as lib
from scripts import resolve as resolve_mod
from scripts import download as download_mod
from scripts import transcribe as transcribe_mod
from scripts import scenes as scenes_mod
from scripts import frames as frames_mod
from scripts import setup as setup_mod
from scripts import channel as channel_mod
from scripts import whisper


def _parse_ts(s: str) -> float:
    """Accept SS, MM:SS, or HH:MM:SS."""
    parts = s.split(":")
    if len(parts) == 1:
        return float(parts[0])
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    raise ValueError(f"bad timestamp: {s}")


def _focus_range(args) -> tuple[float, float] | None:
    if args.start is None and args.end is None:
        return None
    s = _parse_ts(args.start) if args.start else 0.0
    e = _parse_ts(args.end) if args.end else 1e12  # clamped later by duration
    return (s, e)


def _run_one_video(source: str, args) -> dict:
    """Run the full per-video pipeline. Returns a summary dict for the channel index."""
    focus = _focus_range(args)
    meta = resolve_mod.resolve_source(source, focus_range=focus)
    meta["watched_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = lib.slug_for(meta)
    work = lib.LIBRARY_ROOT / slug
    work.mkdir(parents=True, exist_ok=True)

    # Cache check — skip download/transcribe/scenes if same source_hash
    cached = lib.cache_lookup(slug, meta["source_hash"])

    # ---- Stage 2: download ----
    src_dir = work / "source"
    if cached and (src_dir.glob("video.*")):
        video = next(iter(src_dir.glob("video.*")))
    else:
        if meta["is_url"]:
            video = download_mod.download_video(meta["source"], src_dir, basename="video")
        else:
            video = download_mod.copy_local(Path(meta["source"]), src_dir, basename="video")

    # ---- Stage 3: transcribe ----
    transcript_path = work / "transcript.json"
    # An empty cached transcript means the last run got nothing (no backend, or
    # the backend failed), so it earns another try instead of sticking forever.
    transcript: list[dict] = []
    if cached and transcript_path.exists():
        transcript = json.loads(transcript_path.read_text())
    if not transcript:
        if meta["is_url"]:
            vtt = transcribe_mod.fetch_native_captions(meta["source"], work / "subs")
            if vtt:
                transcript = transcribe_mod.dedupe_cues(
                    transcribe_mod.parse_vtt(vtt.read_text())
                )
                # Keep the raw VTT alongside transcript.json for grepability
                (work / "transcript.vtt").write_bytes(vtt.read_bytes())
        if not transcript and not args.no_whisper:
            env = setup_mod._read_env()
            local_model = setup_mod._resolve_local_model(env)
            backend = whisper.pick_backend(
                groq_key=env.get("GROQ_API_KEY"),
                openai_key=env.get("OPENAI_API_KEY"),
                forced=args.whisper,
                local_available=whisper.local_available(local_model),
            )
            if backend:
                audio = work / "audio.m4a"
                try:
                    transcribe_mod.extract_audio_for_whisper(video, audio)
                    transcript = transcribe_mod.transcribe_via_whisper(
                        audio,
                        backend=backend,
                        groq_key=env.get("GROQ_API_KEY"),
                        openai_key=env.get("OPENAI_API_KEY"),
                        local_model_path=local_model,
                    )
                except subprocess.CalledProcessError:
                    # A video with no audio stream has nothing to transcribe.
                    print("No audio to transcribe, continuing frames-only.", file=sys.stderr)
                except whisper.WhisperError as e:
                    print(f"Whisper failed ({backend}): {e}", file=sys.stderr)
        transcript = transcribe_mod.insert_speaker_breaks(transcript)
        transcript_path.write_text(json.dumps(transcript, indent=2, ensure_ascii=False))

    if focus:
        transcript_for_window = transcribe_mod.slice_to_window(
            transcript, start_s=focus[0], end_s=focus[1]
        )
    else:
        transcript_for_window = transcript

    # ---- Stage 4: detect_scenes (within window if focused) ----
    scenes_path = work / "scenes.json"
    if cached and scenes_path.exists() and not focus:
        raw_scenes = [
            scenes_mod.Scene(t=s["t"], score=s["score"], kind=s["kind"])
            for s in json.loads(scenes_path.read_text())
        ]
    else:
        raw_scenes = scenes_mod.detect_scenes(video, threshold=args.scene_threshold)

    if focus:
        s0, s1 = focus
        s1 = min(s1, meta["duration_s"])
        raw_scenes = [s for s in raw_scenes if s0 <= s.t <= s1]
        if not raw_scenes or raw_scenes[0].t > s0:
            raw_scenes.insert(0, scenes_mod.Scene(t=s0, score=1.0, kind="detected"))
        max_gap = min(args.max_gap, 15.0)  # focus mode: denser coverage
        duration_for_floor = s1
    else:
        max_gap = args.max_gap
        duration_for_floor = meta["duration_s"]

    floored = scenes_mod.apply_coverage_floor(
        raw_scenes, duration_s=duration_for_floor, max_gap_s=max_gap
    )
    capped = scenes_mod.apply_budget_cap(floored, max_frames=args.max_frames)

    if not focus:
        scenes_path.write_text(json.dumps(
            [{"t": s.t, "score": s.score, "kind": s.kind} for s in capped],
            indent=2,
        ))

    # ---- Stage 5+6: extract frames ----
    frames_dir = work / "frames"
    # Wipe any prior frames so re-runs don't accumulate orphans.
    if frames_dir.exists():
        for f in frames_dir.iterdir():
            f.unlink()
    raw_frame_records = frames_mod.extract_frames(
        video, capped, out_dir=frames_dir, width_px=args.resolution
    )
    frame_records = [
        {**fr, "path": f"frames/{fr['path']}"} for fr in raw_frame_records
    ]

    # ---- Stage 7: emit manifest + meta ----
    transcript_window_path = work / "transcript.window.json"
    if focus:
        transcript_window_path.write_text(
            json.dumps(transcript_for_window, indent=2, ensure_ascii=False)
        )
        transcript_consumer_path = "transcript.window.json"
    else:
        transcript_consumer_path = "transcript.json"

    (work / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    lib.write_manifest(
        path=work / "manifest.json",
        meta=meta,
        scenes=[{"t": s.t, "score": s.score, "kind": s.kind} for s in capped],
        frames=frame_records,
        transcript_path=transcript_consumer_path,
        focus_range=focus,
    )

    transcript_kind = (
        "captions" if (work / "transcript.vtt").exists()
        else "whisper" if transcript
        else "none"
    )

    return {
        "title": meta["title"],
        "duration_s": meta["duration_s"],
        "library_dir": str(work),
        "slug": slug,
        "transcript_kind": transcript_kind,
        "transcript_path": str(work / transcript_consumer_path),
        "frames": frame_records,
        "scenes_detected": sum(1 for s in capped if s.kind == "detected"),
        "focus": focus,
        "source": source,
    }


def _print_video_manifest(result: dict, args) -> None:
    """Print the per-video stdout block that Claude consumes."""
    duration_str = (
        f"{int(result['duration_s']) // 60:02d}:{int(result['duration_s']) % 60:02d}"
    )
    focus_str = "full" if result.get("focus") is None else (
        f"{args.start or '0:00'}–{args.end or 'end'}"
    )
    print("=== watch manifest ===")
    print(f"title: {result['title']!r}")
    print(f"source: {result['source']}")
    print(f"duration: {duration_str}")
    print(f"focus: {focus_str}")
    print(f"transcript_source: {result['transcript_kind']}")
    print(f"scenes_detected: {result['scenes_detected']}")
    print(f"frames_extracted: {len(result['frames'])}")
    print(f"library_dir: {result['library_dir']}")
    print()
    print("=== frames ===")
    for fr in result["frames"]:
        mm = int(fr["t"]) // 60
        ss = int(fr["t"]) % 60
        print(f"{fr['index']:04d}  t={mm:02d}:{ss:02d}  {fr['path']}  ({fr['kind']})")
    print()
    print("=== transcript ===")
    print(f"{result['transcript_path']}  (load this — too long to inline)")


def _run_channel(source: str, args, probe: dict) -> int:
    """Enumerate a channel/playlist and run the per-video pipeline on each."""
    videos = channel_mod.enumerate_videos(probe, limit=args.limit)
    if not videos:
        print(f"channel probe returned no videos: {source}", file=sys.stderr)
        return 1

    watched_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    channel_title = probe.get("title") or probe.get("uploader") or "channel"
    channel_dir = lib.LIBRARY_ROOT / channel_mod.channel_slug(probe, source)
    channel_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== watch channel ===", flush=True)
    print(f"channel: {channel_title!r}", flush=True)
    print(f"source: {source}", flush=True)
    print(f"videos_found: {len(probe.get('entries') or [])}", flush=True)
    print(f"videos_processing: {len(videos)}", flush=True)
    print(f"channel_dir: {channel_dir}", flush=True)
    print()

    results: list[dict] = []
    login_help = ""
    for i, v in enumerate(videos, 1):
        print(f"--- [{i}/{len(videos)}] {v['title']} ({v['url']}) ---", flush=True)
        try:
            r = _run_one_video(v["url"], args)
            r["status"] = "ok"
            results.append(r)
            _print_video_manifest(r, args)
            print(flush=True)
        except access.LoginRequired as e:
            # The same fix applies to every video here, so print it once at the end.
            login_help = str(e)
            results.append({
                "status": "error",
                "title": v["title"],
                "url": v["url"],
                "error": "login required",
            })
            print("FAILED: login required", file=sys.stderr, flush=True)
        except Exception as e:
            results.append({
                "status": "error",
                "title": v["title"],
                "url": v["url"],
                "error": f"{type(e).__name__}: {e}",
            })
            print(f"FAILED: {type(e).__name__}: {e}", file=sys.stderr, flush=True)

    index_path = channel_mod.write_index(
        channel_dir,
        channel_url=source,
        channel_title=channel_title,
        results=results,
        watched_at=watched_at,
    )

    print("=== watch channel summary ===")
    print(f"channel_index: {index_path}")
    print(f"videos_ok: {sum(1 for r in results if r.get('status') == 'ok')}")
    print(f"videos_failed: {sum(1 for r in results if r.get('status') != 'ok')}")
    for r in results:
        if r.get("status") == "ok":
            print(f"  - {r['title']} -> {r['library_dir']}")
    if login_help:
        print(f"\n{login_help}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="watch")
    p.add_argument("source", help="URL (single video, channel, or playlist) or local path")
    p.add_argument("--start", help="focus start (SS, MM:SS, or HH:MM:SS)")
    p.add_argument("--end", help="focus end (SS, MM:SS, or HH:MM:SS)")
    p.add_argument("--max-frames", type=int, default=80)
    p.add_argument("--resolution", type=int, default=512, help="frame width in px")
    p.add_argument("--scene-threshold", type=float, default=0.30)
    p.add_argument("--max-gap", type=float, default=45.0, help="coverage floor seconds")
    p.add_argument("--whisper", choices=["local", "groq", "openai"], help="force Whisper backend")
    p.add_argument("--no-whisper", action="store_true", help="disable Whisper fallback")
    p.add_argument("--out-dir", help="library root (default: ~/watch/library)")
    p.add_argument("--limit", type=int, default=10,
                   help="max videos to process when source is a channel/playlist (default 10)")
    p.add_argument("--single", action="store_true",
                   help="force single-video mode even if URL looks like a channel/playlist")
    args = p.parse_args(argv)

    if args.out_dir:
        lib.LIBRARY_ROOT = Path(args.out_dir).expanduser().resolve()
    lib.LIBRARY_ROOT.mkdir(parents=True, exist_ok=True)

    try:
        # Channel/playlist dispatch: only for URLs, only when not forced single.
        if not args.single and resolve_mod.is_url(args.source):
            try:
                probe = channel_mod.probe(args.source)
                if channel_mod.is_channel_or_playlist(probe):
                    return _run_channel(args.source, args, probe)
            except access.LoginRequired:
                raise
            except Exception as e:
                # Probe failed, so fall through to the single-video path.
                print(f"channel probe failed, treating as single video: {e}",
                      file=sys.stderr)

        # Single-video path.
        result = _run_one_video(args.source, args)
    except access.LoginRequired as e:
        print(e, file=sys.stderr)
        return access.EXIT_LOGIN_REQUIRED
    _print_video_manifest(result, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
