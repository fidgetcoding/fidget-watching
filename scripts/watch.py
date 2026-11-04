"""watch orchestrator — runs the full pipeline and prints a manifest block.

Single-video URLs and local files run the per-video pipeline. Channel and
playlist URLs are detected via yt-dlp's flat-playlist probe and processed as
a batch — each video runs the per-video pipeline, then a top-level index.md
rolls up the channel.
"""
from __future__ import annotations

import argparse
import math
import tempfile
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
from scripts import budget as budget_mod
from scripts import library as lib
from scripts import resolve as resolve_mod
from scripts import download as download_mod
from scripts import transcribe as transcribe_mod
from scripts import scenes as scenes_mod
from scripts import sections as sections_mod
from scripts import frames as frames_mod
from scripts import setup as setup_mod
from scripts import channel as channel_mod
from scripts import whisper
from scripts.storage import entry_lock, read_json, write_json


def _parse_ts(s: str) -> float:
    """Accept SS, MM:SS, or HH:MM:SS."""
    parts = s.split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"bad timestamp: {s}")
    values = [float(part) for part in parts]
    if any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError(f"timestamp must be finite and nonnegative: {s}")
    if any(v >= 60 for v in values[1:]) or any(not v.is_integer() for v in values[:-1]):
        raise ValueError(f"bad timestamp: {s}")
    result = 0.0
    for value in values:
        result = result * 60 + value
    if not math.isfinite(result):
        raise ValueError(f"timestamp is too large: {s}")
    return result


def _focus_range(args) -> tuple[float, float] | None:
    if args.start is None and args.end is None:
        return None
    s = _parse_ts(args.start) if args.start else 0.0
    e = _parse_ts(args.end) if args.end else 1e12  # clamped later by duration
    if e <= s:
        raise ValueError("focus end must be after start")
    return (s, e)


def _run_one_video(source: str, args) -> dict:
    """Run the full per-video pipeline. Returns a summary dict for the channel index."""
    focus = _focus_range(args)
    meta = resolve_mod.resolve_source(source, focus_range=focus)
    meta["watched_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    work = lib.entry_for(meta)
    slug = work.name
    with entry_lock(work):
        return _process_video(source, args, meta, focus, slug, work)


def _process_video(source, args, meta, focus, slug, work) -> dict:
    if focus and meta["duration_s"] > 0 and focus[0] >= meta["duration_s"]:
        raise ValueError("focus start must be before the video duration")
    if meta["duration_s"] > 0:
        window_s = meta["duration_s"] if not focus else min(focus[1], meta["duration_s"]) - focus[0]
        print(budget_mod.early_estimate(window_s, width_px=args.resolution, override=args.max_frames),
              flush=True)
        print(flush=True)

    # Cache check — skip download/transcribe/scenes if same source_hash
    cached = lib.cache_lookup(slug, meta["source_hash"])

    # ---- Stage 2: download ----
    src_dir = work / "source"
    media = download_mod.media_files(src_dir)
    if cached and media:
        video = media[0]
    else:
        cached = None
        if meta["is_url"]:
            video = download_mod.download_video(meta["source"], src_dir, basename="video")
        else:
            video = download_mod.copy_local(Path(meta["source"]), src_dir, basename="video")
    # Direct links often report no duration, and everything below depends on it.
    if meta["duration_s"] <= 0:
        meta["duration_s"] = resolve_mod.probe_duration(video)
    if not math.isfinite(meta["duration_s"]) or meta["duration_s"] <= 0:
        raise ValueError("video duration must be finite and positive")
    if focus:
        focus = (focus[0], min(focus[1], meta["duration_s"]))
        if focus[0] >= focus[1]:
            raise ValueError("focus start must be before the video duration")

    # ---- Stage 3: transcribe ----
    transcript_path = work / "transcript.json"
    # An empty cached transcript means the last run got nothing (no backend, or
    # the backend failed), so it earns another try instead of sticking forever.
    transcript: list[dict] = []
    if cached and transcript_path.exists():
        transcript = read_json(transcript_path, [])
        if not transcribe_mod.valid_cues(transcript):
            transcript = []
    if not transcript:
        (work / "transcript.vtt").unlink(missing_ok=True)
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
        write_json(transcript_path, transcript)

    if focus:
        transcript_for_window = transcribe_mod.slice_to_window(
            transcript, start_s=focus[0], end_s=focus[1]
        )
    else:
        transcript_for_window = transcript

    # ---- Stage 4: detect_scenes (within window if focused) ----
    scenes_path = work / "scenes.json"
    raw_path = work / "scenes.raw.json"
    raw_cache = read_json(raw_path, {}) if cached else {}
    raw_scenes = None
    if isinstance(raw_cache, dict) and raw_cache.get("threshold") == args.scene_threshold:
        try:
            raw_scenes = [scenes_mod.Scene(**item) for item in raw_cache["scenes"]]
            if not raw_scenes or any(not math.isfinite(s.t) or not math.isfinite(s.score)
                                     or s.t < 0 or s.kind != "detected" for s in raw_scenes):
                raw_scenes = None
        except (KeyError, TypeError, ValueError):
            raw_scenes = None
    if raw_scenes is None:
        raw_scenes = scenes_mod.detect_scenes(video, threshold=args.scene_threshold)
        write_json(raw_path, {"threshold": args.scene_threshold,
                             "scenes": [vars(s) for s in raw_scenes]})
    raw_scenes = [s for s in raw_scenes if s.t < meta["duration_s"]]

    if focus:
        s0, s1 = focus
        s1 = min(s1, meta["duration_s"])
        raw_scenes = [s for s in raw_scenes if s0 <= s.t < s1]
        if not raw_scenes or raw_scenes[0].t > s0:
            raw_scenes.insert(0, scenes_mod.Scene(t=s0, score=1.0, kind="detected"))
        max_gap = min(args.max_gap, 15.0)  # focus mode: denser coverage
        duration_for_floor = s1
        window_s = s1 - s0
    else:
        max_gap = args.max_gap
        duration_for_floor = meta["duration_s"]
        window_s = meta["duration_s"]

    floored = scenes_mod.apply_coverage_floor(
        raw_scenes, duration_s=duration_for_floor, max_gap_s=max_gap
    )
    max_frames = args.max_frames if args.max_frames is not None else budget_mod.auto_max_frames(window_s)
    capped = scenes_mod.apply_budget_cap(floored, max_frames=max_frames)

    transcript_window_path = work / "transcript.window.json"
    if focus:
        write_json(transcript_window_path, transcript_for_window)
        transcript_consumer_path = "transcript.window.json"
    else:
        transcript_consumer_path = "transcript.json"

    # Say what this run costs before spending any of it.
    cost = budget_mod.final_cost(
        planned=floored,
        kept=capped,
        limit=max_frames,
        override=args.max_frames is not None,
        width_px=args.resolution,
        aspect=frames_mod.video_aspect(video),
        transcript_path=work / transcript_consumer_path,
    )
    budget_mod.write_cost(work / "cost.json", cost)
    print(budget_mod.format_cost(cost), flush=True)
    print(flush=True)

    # Cache extraction inputs independently of the public manifest. Build a fresh
    # directory first so a failed ffmpeg run leaves the previous images intact.
    frame_key = {"version": 1, "source_hash": meta["source_hash"],
                 "resolution": args.resolution, "scenes": [vars(s) for s in capped]}
    frame_records = [
        {"index": i, "t": scene.t, "kind": scene.kind,
         "path": f"frames/{frames_mod.format_filename(i, scene.t)}"}
        for i, scene in enumerate(capped, 1)
    ]
    frames_dir = work / "frames"
    frames_valid = (cached and read_json(work / "frames.cache.json") == frame_key
                    and all((work / fr["path"]).is_file()
                            and (work / fr["path"]).stat().st_size > 0 for fr in frame_records))
    if not frames_valid:
        with tempfile.TemporaryDirectory(prefix=".frames-", dir=work) as td:
            staging = Path(td) / "new"
            raw_frame_records = frames_mod.extract_frames(
                video, capped, out_dir=staging, width_px=args.resolution
            )
            frame_records = [{**fr, "path": f"frames/{fr['path']}"} for fr in raw_frame_records]
            old = Path(td) / "old"
            try:
                if frames_dir.exists():
                    frames_dir.rename(old)
                staging.rename(frames_dir)
            except BaseException:
                if old.exists() and not frames_dir.exists():
                    old.rename(frames_dir)
                raise
        write_json(work / "frames.cache.json", frame_key)
    write_json(scenes_path, [vars(s) for s in capped])

    # ---- Long videos: one section at a time ----
    section_plan: list[dict] = []
    if not args.no_sections and sections_mod.is_long(window_s, section_minutes=args.section_minutes):
        window_start = focus[0] if focus else 0.0
        target_s = args.section_minutes * 60 if args.section_minutes else sections_mod.TARGET_S
        cuts = sections_mod.plan_cuts(
            window_start,
            window_start + window_s,
            n_frames=len(frame_records),
            transcript=transcript_for_window,
            scenes=[{"t": s.t, "score": s.score, "kind": s.kind} for s in capped],
            target_s=target_s,
        )
        section_plan = sections_mod.build_plan(
            cuts,
            frames=frame_records,
            transcript=transcript_for_window,
            tokens_per_frame=cost["tokens_per_frame"],
        )
        sections_mod.write_plan(work, section_plan, source_hash=meta["source_hash"])
    else:
        sections_mod.clear_plan(work)

    # ---- Stage 7: emit manifest + meta ----
    write_json(work / "meta.json", meta)
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
        "section_plan": section_plan,
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
    if result.get("section_plan"):
        print(sections_mod.format_plan(result["section_plan"], Path(result["library_dir"])))
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

    print("=== watch channel ===", flush=True)
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
        except access.Blocked as e:
            results.append({
                "status": "error",
                "title": v["title"],
                "url": v["url"],
                "error": "blocked: DRM or paywall",
            })
            print(f"FAILED: {e}", file=sys.stderr, flush=True)
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
    if any(r.get("status") != "ok" for r in results):
        return access.EXIT_LOGIN_REQUIRED if login_help else 1
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="watch")
    p.add_argument("source", help="URL (single video, channel, or playlist) or local path")
    p.add_argument("--start", help="focus start (SS, MM:SS, or HH:MM:SS)")
    p.add_argument("--end", help="focus end (SS, MM:SS, or HH:MM:SS)")
    p.add_argument("--max-frames", type=int, default=None,
                   help="override the frame budget (default: scales with length and scene changes)")
    p.add_argument("--resolution", type=int, default=512, help="frame width in px")
    p.add_argument("--scene-threshold", type=float, default=0.30)
    p.add_argument("--max-gap", type=float, default=45.0, help="coverage floor seconds")
    p.add_argument("--no-sections", action="store_true",
                   help="write long videos in one pass instead of section by section")
    p.add_argument("--section-minutes", type=float, default=None,
                   help="section length, and the length above which a video gets split")
    p.add_argument("--whisper", choices=["local", "groq", "openai"], help="force Whisper backend")
    p.add_argument("--no-whisper", action="store_true", help="disable Whisper fallback")
    p.add_argument("--out-dir", help="library root (default: ~/watch/library)")
    p.add_argument("--cookies-from-browser", metavar="BROWSER", default=None,
                   help="opt in to your own browser login for this run, for videos your account "
                        f"can already watch ({', '.join(access.BROWSERS)})")
    p.add_argument("--limit", type=int, default=10,
                   help="max videos to process when source is a channel/playlist (default 10)")
    p.add_argument("--single", action="store_true",
                   help="force single-video mode even if URL looks like a channel/playlist")
    args = p.parse_args(argv)

    try:
        _focus_range(args)
        for name in ("max_frames", "resolution", "max_gap", "section_minutes", "limit"):
            value = getattr(args, name)
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ValueError(f"--{name.replace('_', '-')} must be finite and positive")
        if not math.isfinite(args.scene_threshold) or not 0 <= args.scene_threshold <= 1:
            raise ValueError("--scene-threshold must be between 0 and 1")
        access.use_browser_cookies(args.cookies_from_browser)
    except ValueError as e:
        p.error(str(e))

    if args.out_dir:
        lib.LIBRARY_ROOT = Path(args.out_dir).expanduser().resolve()
    lib.LIBRARY_ROOT.mkdir(parents=True, exist_ok=True)

    try:
        # Channel/playlist dispatch: only for URLs, only when not forced single.
        if not args.single and resolve_mod.is_url(args.source):
            try:
                probe = channel_mod.probe(args.source, limit=args.limit)
            except (access.LoginRequired, access.Blocked):
                raise
            except Exception as e:
                # Probe failed, so fall through to the single-video path.
                print(f"channel probe failed, treating as single video: {e}",
                      file=sys.stderr)
            else:
                if channel_mod.is_channel_or_playlist(probe):
                    return _run_channel(args.source, args, probe)

        # Single-video path.
        result = _run_one_video(args.source, args)
    except access.LoginRequired as e:
        print(e, file=sys.stderr)
        return access.EXIT_LOGIN_REQUIRED
    except access.Blocked as e:
        print(e, file=sys.stderr)
        return access.EXIT_BLOCKED
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as e:
        print(f"watch failed: {access.scrub(str(e))}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("watch cancelled", file=sys.stderr)
        return 130
    _print_video_manifest(result, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
