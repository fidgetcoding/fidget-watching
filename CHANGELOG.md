# Changelog

All notable changes to this skill (originally `claude-watch`, renamed `watch` in this fork) are documented here.

## [Unreleased]: fidgetcoding fork

### Fixed
- **The local whisper.cpp route works now.** `whisper-cli` can't read the m4a `watch.py` extracts, and it exits 0 anyway, so every local run ended in `transcript_source: none`. The audio now goes through WAV first. Dropping `-nt` also brings back real segment timestamps instead of 30-second windows.
- **Scene frames land after the transition.** They were grabbed on the cut, so soft transitions came out as blurs.
- **A video with no audio stream** continues frames-only instead of crashing.
- **An empty cached transcript** gets retried instead of sticking.

### Added
- **Long videos get written in sections.** Over 30 minutes, `watch.py` cuts at pauses and scene changes, each part is written from only its own frames and transcript, and `merge_notes.py` checks them against the source and merges them. `--start`/`--end` are no longer needed for good long notes.
- **A frame budget that scales.** The flat 80-frame default is gone. The budget follows length and scene changes, the token cost prints before anything is read, and `--max-frames` still overrides it.
- **`--cookies-from-browser`.** Opt-in, one run at a time, for videos your own account can watch. Cookies are never stored, printed or uploaded. DRM and paywalls your account doesn't cover stop with a plain message and exit code 6.
- **A CI test that the plugin really loads.** It installs the plugin into Claude Code, starts a session against a fake API, checks the skill registered and the startup hook ran, then puts each past defect back to prove they'd get caught.
- **No Whisper API upload limit.** Audio over 24 MB goes up as overlapping chunks and merges back onto one timeline. Rate limits and server errors get retried.
- **Login-wall help.** Instagram and other logged-in sites now get clear setup steps for yt-dlp's browser-cookie config and exit code 5, not a traceback. See the README.
- **CI.** `pytest` on Linux and macOS, plus a fresh end-to-end run on a real public-domain video through captions and whisper.cpp (`tests/e2e/fresh_watch.sh`).

### Changed
- **Every link points at `fidgetcoding/fidget-watching`,** and `marketplace.json` names this fork as owner while crediting `devinilabs/claude-watch`.
- **Renamed `claude-watch` → `watch`.** Slash command is now `/watch`. Skill installs at `~/.claude/skills/watch/`. Library lives at `~/watch/library/<slug>/`. Config at `~/.config/watch/.env`. The repo itself stays `claude-watch` on GitHub to preserve the fork lineage with `devinilabs/claude-watch`.
- **Hardened natural-language activation.** SKILL.md `description:` now ends with an explicit `Triggers on:` enumeration of phrases (mirrors the Higgsfield-skill pattern) so Claude Code's auto-router picks the skill on plain-English requests — `watch this video`, `study this lecture`, `transcribe this reel`, `summarize this with frames`, `break down this video`, `analyze this channel`, `frame by frame`, `scrape this creator`, `sweep this channel`, `hook analysis`, `script structure`, `cloneable moves`, etc. — not just the slash command. README ships a Natural-language activation section listing the trigger families.

### Added
- **Local whisper.cpp backend.** `scripts/whisper.py::transcribe_local()` shells out to `whisper-cli -oj` and parses the JSON `transcription[].offsets` into the existing segment shape — same contract as the HTTP backends. `pick_backend()` prefers local when present (`whisper-cli` on PATH AND a ggml model file resolvable). Net effect: the skill runs key-free + offline on machines with `brew install whisper-cpp`.
- **Channel / playlist mode.** `scripts/channel.py` + a refactor in `scripts/watch.py` so playlist URLs (including channel `@handle/videos` URLs) are detected via `yt-dlp --flat-playlist --dump-single-json` and processed as a batch (default `--limit 10`, `--single` to force per-video). Writes a `~/watch/library/channel-<slug>-<hash>/index.md` rolling up the batch with a Cross-channel synthesis stub.
- **Shared whisper-model fallback.** `scripts/setup.py::_resolve_local_model()` walks a search list: `$WHISPER_CPP_MODEL` env var, then `~/.config/watch/models/ggml-base.en.bin` (the default), then `EXTRA_LOCAL_MODEL_PATHS` — currently `~/.whisper/ggml-base.en.bin` (creativity-maxxing media-module path) and `~/.config/claude-watch/models/ggml-base.en.bin` (legacy, pre-rename installs).
- **`--whisper local` flag** alongside the existing `groq` / `openai` options.
- **Test coverage:** 9 channel-mode unit tests (`tests/test_channel.py`) + 5 model-resolution tests + 1 local-whisper-ready-path test in `tests/test_setup.py`. Suite at 73+ assertions, all green.

## [0.1.0] — 2026-05-03

### Added
- `/claude-watch <url-or-path> [topic]` slash command that produces structured study notes.
- Scene-aware frame extraction: ffmpeg scene detection (default threshold 0.30) with a coverage floor (synthetic boundaries every 45s across long static gaps) and a budget cap (default 80 frames, drops lowest-scoring detected scenes first; floor boundaries are always preserved).
- Persistent library at `~/claude-watch/library/<slug>/` with cached download, transcript, and scenes — re-runs only regenerate frames + notes.
- Slug rule `YYYY-MM-DD-<title>-<sha1(source+focus)[:4]>` so chronological + collision-safe across focus-range re-watches.
- Native caption pull via yt-dlp (manual + auto-subs) with VTT dedupe.
- Whisper fallback: Groq `whisper-large-v3` (preferred), OpenAI `whisper-1` (alt). Stdlib HTTP clients — no SDKs.
- `--start`/`--end` focused mode with denser coverage floor (15s vs 45s default).
- `setup.py` preflight (`--check` / `--json`) with cross-platform installer (`brew` on macOS auto-runs; `apt`/`dnf`/`winget`/`pip` commands printed elsewhere).
- Three-surface distribution: Claude Code plugin, claude.ai `.skill` bundle (built by `scripts/build-skill.sh`), Codex skill.
- SessionStart hook prints a one-liner only when remediation is needed.
- Strict notes template baked into SKILL.md: TLDR, Key Concepts, per-scene Notes (On screen + Said + Synthesis), Code & Commands, Diagrams Referenced, Open Questions.
