---
name: watch
description: Watch a tutorial / lecture / talk video, OR sweep a whole YouTube channel or playlist, and produce structured study notes. yt-dlp + ffmpeg scene detection + transcript (captions, local whisper.cpp, or cloud whisper fallback) — Claude reads every frame as an image and writes section-per-scene `notes.md` to `~/watch/library/<slug>/`. Channel mode rolls up an `index.md` with cross-channel synthesis. Activates on the slash command `/watch <url-or-path>` AND on natural language. Use whenever the user wants to study, take notes on, transcribe, summarize, analyze, scrape, sweep, ingest, or break down a video / YouTube channel / playlist / Instagram Reel / TikTok / podcast video / lecture / tutorial / talk. Triggers on, but not limited to, any of these phrases (and obvious variants) when a video URL or local video path is in the message: watch this video, watch this lecture, watch this tutorial, watch this talk, study this video, study this lecture, take notes on this video, take notes on this lecture, give me notes on this video, transcribe this video, transcribe this reel, transcribe this tiktok, summarize this video, summarize this with frames, break down this video, break down this reel, break down this lecture, analyze this video, analyze this youtube video, analyze this channel, frame by frame, frame-by-frame analysis, give me frames of this video, scrape this channel, scrape this creator, scrape this youtube, sweep this channel, sweep this playlist, ingest this channel, ingest this playlist, what hooks does this creator use, hook analysis, what's the script structure, give me the cloneable moves, study this creator, research this creator, channel research, competitor research on this channel, content research from this video, key concepts from this video, screenshot the important parts, with screenshots, with frames, frame-aware notes, make me notes from this, what can I learn from this video, what's working on this channel, scrape what this creator is doing, give me a breakdown of this. Use even when the user just pastes a URL and says "watch this", "study this", "scrape this", or "take notes."
argument-hint: "<video-or-channel-url-or-path> [topic-or-question]"
allowed-tools: Bash, Read, Write, AskUserQuestion
homepage: https://github.com/fidgetcoding/fidget-watching
repository: https://github.com/fidgetcoding/fidget-watching
license: MIT
user-invocable: true
---

# /watch — Claude turns a video into study notes

You don't have a video input. This skill gives you one *and* turns each viewing into a saved notes artifact.

## Step 0 — Setup preflight (silent on success)

Run on every `/watch` invocation:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/setup.py" --check
```

Exit codes: `0` ready (silent — proceed), `2` missing binaries, `3` missing API key, `4` both. On non-zero, run the installer:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/setup.py"
```

On macOS this auto-`brew install`s ffmpeg + yt-dlp. On Linux/Windows it prints the right commands. It scaffolds `~/.config/watch/.env` (mode 0600) with commented placeholders.

**Backends, in priority order:**
1. **Local whisper.cpp** — preferred (free, offline, Metal-accelerated on Apple Silicon). Requires `whisper-cli` on PATH (`brew install whisper-cpp`) AND a ggml model file at `~/.config/watch/models/ggml-base.en.bin` (or wherever `WHISPER_CPP_MODEL` env var points).
2. **Groq** — needs `GROQ_API_KEY` in `~/.config/watch/.env`.
3. **OpenAI** — needs `OPENAI_API_KEY` in `~/.config/watch/.env`.

If no backend is configured, use `AskUserQuestion` to ask whether the user wants to install whisper-cpp locally (`brew install whisper-cpp` + download `ggml-base.en.bin`), set a Groq key, set an OpenAI key, or run with `--no-whisper` (frames-only when no native captions).

## When to use

- User pastes a tutorial / lecture / talk URL and asks to study it
- User points at a local screen recording or video and wants notes
- User pastes a **YouTube channel URL** (`youtube.com/@handle`, `youtube.com/c/...`, `youtube.com/channel/...`) or **playlist URL** and asks to study the channel / find recurring patterns / scrape what a creator is doing
- User types `/watch <url-or-path> [topic]`

## Channel / playlist mode

If `source` is a multi-video URL (yt-dlp probe returns `_type: playlist` with >1 entries), `watch.py` runs the per-video pipeline on each and writes a top-level `index.md` to `~/watch/library/channel-<slug>-<hash>/`. Defaults to the first 10 videos; override with `--limit N`. Force single-video mode (skip channel detection) with `--single`.

After channel runs, write a `synthesis.md` (in the channel dir, alongside `index.md`) that fills the `## Cross-channel synthesis` section in `index.md`: recurring hooks, thumbnail formulas, script structures, what's trending, and the 3 most cloneable moves. You don't need to write a notes.md per video synthesis (each video already got its own).

## How to invoke

**Step 1 — parse input.** Separate the source (URL or path) from any topic the user mentioned. The topic shapes which sections you emphasize in the notes — pass it through to your synthesis, not to the script.

**Step 2 — run the watch script.**

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/watch.py" "<source>"
```

Optional flags:
- `--start T` / `--end T` — focus on a section (`SS`, `MM:SS`, or `HH:MM:SS`)
- `--max-frames N` — set the frame budget yourself. By default it scales with length and scene changes: up to 6 frames a minute for the first 15 minutes, 2.5 a minute to the hour, 1.5 a minute after that, and never below one frame every 45 seconds
- `--resolution W` — bump frame width to 1024 px when on-screen text is tiny
- `--scene-threshold X` — sensitivity (default 0.30; raise for fewer cuts, lower for more)
- `--max-gap S` — coverage floor in seconds (default 45)
- `--no-sections` — write a long video in one pass (not recommended, accuracy falls off)
- `--section-minutes N` — change the section length (default: about 20 minutes, for videos over 30)
- `--whisper local|groq|openai` — force backend
- `--no-whisper` — disable Whisper entirely
- `--out-dir DIR` — override library root
- `--cookies-from-browser BROWSER` — use the user's own browser login for this run. Only with their say-so, see Failure modes

**Tell the user the cost before you read anything.** The script prints `=== cost estimate ===` before it downloads and `=== cost ===` before it extracts frames. Pass on the frame count and token estimate in one line. If it's over 150k tokens, ask with `AskUserQuestion` whether to go ahead, focus with `--start`/`--end`, or set a lower `--max-frames`.

**Long video? If the output has a `=== sections ===` block, follow "Long videos" below instead of Steps 3 to 5.**

**Step 3 — read every frame.** The script ends with a structured `=== frames ===` block listing each frame's path and timestamp. `Read` them all in parallel — they render as images in your context.

**Step 4 — load the transcript.** The `=== transcript ===` block points to `transcript.json` (or `transcript.window.json` for focused mode). `Read` it — it's a list of `{t_start, t_end, text, speaker_break}`.

**Step 5 — write `notes.md` to the library directory.** Use the **strict template** below. Save to `<library_dir>/notes.md`. Then print a 3-line summary to chat:
1. Title and slug
2. Number of sections + key concepts
3. Path to the notes file

Do **not** delete the library dir. It is the artifact.

## Long videos

Over 30 minutes, the script splits the video into sections of about 20 minutes, cutting at pauses and scene changes, and writes `sections.json` in the library dir. Don't read the whole thing in one go. That's where notes get thin and timestamps drift.

1. **One section at a time, in order.** For each section in `sections.json`: `Read` only that section's `frames` and its `transcript_path`, then write `notes_path` using the section template below. If you can spawn subagents, give each section its own, so earlier frames don't crowd out later ones. Hand each one the previous section's `## Section Summary` so the notes connect. An interrupted run keeps the section notes already written, so skip any that exist.
2. **Check.** Run `python3 "${CLAUDE_SKILL_DIR}/scripts/merge_notes.py" "<library_dir>" --check`. Fix every `ERROR`. Each `WARN` means a long stretch with no notes, or a "Said" line that doesn't match the transcript. Go back to that part of the video and fix what's wrong.
3. **TLDR.** Read every `## Section Summary` and write `<library_dir>/tldr.md`: 3-4 sentences on the whole video and its single most important takeaway. Write it last, so the end of the video counts as much as the start.
4. **Merge.** Run `merge_notes.py` again without `--check`. It writes `notes.md`, with the concepts and open questions deduplicated.

Section template. Frame links stay `frames/...`, relative to the library dir, even though the file sits in `sections/`:

````markdown
## Section Summary
<3-5 sentences on this section only.>

## Key Concepts
- **<concept>** — <one-line definition> · `[t=MM:SS]`

## Notes

### [t=MM:SS] <Section title>

![](frames/0001_t00-04.jpg)

**On screen:** <...>

**Said:** <...>

**Synthesis:** <...>

## Code & Commands

## Diagrams Referenced

## Open Questions
````

## Notes template (non-negotiable structure)

````markdown
# <Video Title>

**Source:** <URL or path>  ·  **Duration:** MM:SS  ·  **Watched:** YYYY-MM-DD

## TLDR
<3-4 sentences: what the video is about and the single most important takeaway.>

## Key Concepts
- **<concept>** — <one-line definition> · `[t=MM:SS]`
- ...

## Notes

### [t=00:04] <Section title you derive from on-screen + spoken content>

![](frames/0001_t00-04.jpg)

**On screen:** <Transcribe / describe the slide, code, diagram. If code, transcribe verbatim.>

**Said:** <Relevant transcript excerpt for this scene, lightly cleaned.>

**Synthesis:** <Your connection — what this section is teaching, how it links to prior section.>

### [t=00:31] <next section>
...

## Code & Commands
<every code-on-screen frame's content as a runnable fenced block, language-tagged, with [t=MM:SS] back-link>

```python
# [t=03:45]
def forward(x):
    return x @ W + b
```

## Diagrams Referenced
- `[t=02:10]` — <one-line description of the diagram in frame 0008>
- ...

## Open Questions
- <things mentioned but not fully covered, or follow-ups to explore>
````

## Rules baked into the template

- **One scene = one section.** Use the `t=MM:SS` from each frame as the section anchor.
- **Adjacent scenes that are clearly the same topic** can be merged. When you do, mention it parenthetically: *(merged scenes at t=02:10 and t=02:42)*
- **Code blocks must be fenced** with the right language tag, transcribed verbatim from the frame.
- **The "On screen" block is required even for title slides.** Keeps the structure parallel.
- **Timestamps are absolute** (real video timeline) — for YouTube sources, a viewer can paste `<URL>&t=<seconds>` to jump there.

## Re-runs

If the user re-watches the same URL, the script reuses the cached download, transcript, and scenes. Only frames + notes regenerate. To force a full re-run, delete `<library_dir>/meta.json` first.

## Failure modes

- **Setup preflight non-zero** → run `setup.py`, then ask for a key via `AskUserQuestion`.
- **`watch.py` exits 5** → the video sits behind a login. The script's stderr holds the steps. Relay them, and only if the user says their own account can watch it, ask which browser and re-run with `--cookies-from-browser <browser>`. Never add that flag on your own, never read, print or copy cookies yourself, and don't try other flags or other sites to get the same video.
- **`watch.py` exits 6** → DRM, or a paywall the user's login doesn't cover. Tell them plainly and stop. Don't look for a way around it.
- **No transcript** → script emits `transcript_source: none`. Generate notes frames-only and tell the user.
- **Whisper failure** → retry with `--whisper openai` (if Groq failed) or vice versa. Audio over the 25 MB upload limit is split automatically, so size alone isn't a reason to give up on an API route.

## Token budget

Frames dominate cost, and it grows with every frame: about 210 input tokens per frame at 512 px on a 16:9 video, from Anthropic's published cost of one token per 28x28 patch. `=== cost ===` gives the number for this run, and `cost.json` in the library dir keeps it. A long transcript adds real cost too. `--resolution 1024` quadruples per-frame cost — only when the user must read tiny on-screen text.

If the user asks a follow-up about a video you already watched in this session, do NOT re-run the script. The library directory is on disk; re-`Read` only the frames you need.

## Security

- Runs `yt-dlp`, `ffmpeg`, `ffprobe` locally
- Sends extracted mono 16 kHz audio to Groq (preferred) or OpenAI Whisper API only when captions are missing
- Reads/writes `~/.config/watch/.env` (mode 0600) for keys
- Persists artifacts to `~/watch/library/<slug>/` — review the directory after first run if you're cautious
- Does NOT log or transmit API keys, video files, or the original URL outside the audio-to-Whisper call
- `--cookies-from-browser` is opt-in per run. yt-dlp reads the browser's cookies and sends them only to the video's site. This skill never stores, prints or uploads them, and never gets around a paywall or DRM
