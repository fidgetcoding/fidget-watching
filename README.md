# fidget-watching

A fork of [devinilabs/claude-watch](https://github.com/devinilabs/claude-watch). devinilabs built the pipeline. I added the key-free whisper.cpp route, channel and playlist mode, and the fixes in the [changelog](CHANGELOG.md).

**Turn any tutorial or lecture video into structured study notes.** Paste a URL, walk away, come back to a markdown file with embedded screenshots, timestamped transcript, and Claude's synthesis — saved to a persistent library.

```
/watch https://youtu.be/<lecture> backprop intuition
```

## Install

| Surface | Command |
|---|---|
| **Claude Code** | `/plugin marketplace add fidgetcoding/fidget-watching` then `/plugin install watch@watch` |
| **claude.ai** (web) | Download `watch.skill` from the latest release → Settings → Capabilities → Skills → `+` |
| **Codex** | `git clone https://github.com/fidgetcoding/fidget-watching ~/.codex/skills/watch` |

## What it does

1. Downloads via `yt-dlp` (or accepts a local file).
2. Detects scene changes with `ffmpeg`. Inserts coverage-floor frames every 45s across long static gaps so a lecture with one slide for 5 minutes still gets ~7 frames, not 1.
3. Pulls a timestamped transcript — captions first (free), Whisper API (Groq preferred, OpenAI alt) only when missing.
4. Hands frames + transcript to Claude. Claude `Read`s every frame as an image and writes `notes.md` to a strict template:
   - `## TLDR` — 3-4 sentence synthesis
   - `## Key Concepts` — bulleted with timestamps
   - `## Notes` — one section per scene with embedded screenshot, on-screen text, what was said, Claude's synthesis
   - `## Code & Commands` — every code-on-screen frame transcribed into a runnable fenced block
   - `## Diagrams Referenced`, `## Open Questions`
5. Saves everything to `~/watch/library/<slug>/` — re-running the same URL is a cache hit.

## Why this exists

`claude-video`'s uniform frame sampling spends the budget poorly on long lectures with slow-changing slides. And answers live in chat, so you can't go back to "the notes from that video." `watch` is opinionated for the tutorial workflow: scene-aware frames, persistent library, structured notes file.

## Usage

```
/watch <url-or-path> [topic]
/watch ~/Lectures/cs231n.mp4 backpropagation derivation
/watch https://youtu.be/<long> --start 5:00 --end 25:00
/watch <url> --resolution 1024            # for slides with tiny code text
```

Flags: `--start/--end`, `--max-frames`, `--resolution`, `--scene-threshold`, `--max-gap`, `--whisper local|groq|openai`, `--no-whisper`, `--out-dir`, `--limit N` (channel mode), `--single` (force per-video).

### Natural-language activation

You don't have to remember the slash command. Drop a video URL into chat with any of these (and obvious variants) and the skill auto-triggers:

- "watch this video / lecture / tutorial / talk" + URL
- "study this video / take notes on this / give me notes on this"
- "transcribe this video / reel / tiktok"
- "summarize this video" / "summarize this with frames"
- "break down this video / reel / lecture"
- "analyze this video / channel / youtube"
- "frame by frame" / "frame-aware notes" / "with screenshots"
- "scrape this channel / creator / youtube"
- "sweep this channel / playlist"
- "what hooks does this creator use" / "hook analysis" / "script structure" / "cloneable moves"
- "study this creator" / "research this creator" / "channel research"
- Or just paste a URL and say "watch this" / "scrape this" / "take notes."

The SKILL.md description ships an explicit trigger list so Claude Code's auto-router picks the right skill instead of guessing.

## Bring your own keys

Captions cover the majority of public videos for free. Whisper only kicks in when a video has no caption track.

| Need | Cost |
|---|---|
| Download + native captions | free (`yt-dlp` + `ffmpeg`) |
| Whisper fallback (preferred) | Groq `whisper-large-v3` — cheap, fast |
| Whisper fallback (alt) | OpenAI `whisper-1` |
| Disable Whisper | `--no-whisper` (frames-only when no captions) |

Keys go in `~/.config/watch/.env` (mode 0600).

## Re-running the same video

The library is keyed on `slug = YYYY-MM-DD-<title>-<short-hash>` where the short hash is `sha1(source + focus_range)[:4]`. Re-running the same URL with the same focus range hits the cache — no re-download, no re-transcribe, only frames + notes regenerate. Different focus range = different slug = a separate notes file.

To force a fresh run, delete the `meta.json` in the library dir.

## Limits

- **Long videos work, and frames are what they cost.** Scene frames plus one every 45 seconds add up, so past about 30 minutes a single notes pass gets expensive. Use `--start`/`--end` to focus, or raise `--max-gap`.
- **Hard frame cap: 80** by default. Bump with `--max-frames` (token cost grows linearly).
- **No transcript length limit.** The Whisper APIs cap uploads at 25 MB (about 50 minutes here), so longer audio goes up in overlapping chunks and comes back as one timeline. Local whisper.cpp has no upload to cap.
- **No private platforms.** Public URLs and local files only.

## Develop

```bash
git clone https://github.com/fidgetcoding/fidget-watching
cd fidget-watching
python3 -m pytest                         # full suite
bash scripts/build-skill.sh               # → dist/watch.skill (claude.ai bundle)
```

Releasing: tag `vX.Y.Z`, push the tag — CI builds and attaches `watch.skill`.

## License

MIT. Built on `yt-dlp`, `ffmpeg`, and Claude's multimodal `Read` tool. Whisper transcription via [Groq](https://groq.com) or [OpenAI](https://openai.com).
