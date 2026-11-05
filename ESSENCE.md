# fidget-watching: the essence

**What it is.** A fork of [devinilabs/claude-watch](https://github.com/devinilabs/claude-watch), which turns a video into study notes for Claude: scene-aware screenshots, a timestamped transcript, and a markdown file saved to a library. devinilabs built that, and it's a great idea. This fork adds the things I needed. The skill is called `watch` here.

**Who it's for.** Anyone using Claude Code who learns from videos and wants notes they can come back to, especially if you don't want to pay for a transcription API.

**Why it exists.** I installed claude-watch and it asked for an API key. My exact reaction: "I thought I have audio downloaders set up so I don't need these stupid keys. Don't I already have ffmpeg and Whisper?" I did. Then: "Can I do it to a whole YouTube channel?" Now it can.

**What's different from upstream.** Only what I changed:

- **No keys needed.** A local whisper.cpp backend, used first when it's installed. Upstream's Groq and OpenAI options are still there.
- **Whole channels and playlists.** Paste a channel or playlist URL and it does a batch (10 by default) and rolls them up in an `index.md`. A failed video still gets a row in the index, and the run exits nonzero so you notice.
- **Long audio goes up in pieces.** The Whisper APIs cap uploads at 25 MB, so longer audio goes up in overlapping chunks and comes back as one timeline. Tested locally with generated audio and mocked APIs, so no paid call ran.
- **Login walls explain themselves.** When Instagram or another site blocks yt-dlp, `/watch` stops and prints the exact setup steps.
- **Long videos get sections.** Over 30 minutes, the video splits into parts of about 20 minutes, Claude writes each from its own frames and transcript, and a check and a merge build one `notes.md`.
- **A frame budget that scales.** Upstream's flat 80 became up to 6 frames a minute for the first 15 minutes, 2.5 to the hour and 1.5 after that, with the count and estimated tokens printed before it reads anything.
- **Private videos with your own login.** `--cookies-from-browser` gets you what your account can already watch. I haven't tested it on a real logged-in site.
- **Re-runs reuse what's still good.** Downloads, transcripts, scene detections and matching frames are kept. Edit a local file or change a setting and only what that affects gets rebuilt. One run writes to a library entry at a time, and a failed frame grab keeps the old frames.
- **Shared Whisper model.** It finds a model you already downloaded for other tools.
- **Plain-English triggers.** "Watch this lecture" or "transcribe this reel" activates it without the slash command.
- **Renamed.** `/claude-watch` became `/watch`.
- **Plugin repair.** The manifest and hook file had defects that stopped the plugin from loading and the startup hook from running.

Everything else, including scene detection, the library, the notes template, the Whisper API clients and the Claude Code, claude.ai and Codex packaging, is upstream's work.

**How to try it.** In Claude Code:

```
/plugin marketplace add fidgetcoding/fidget-watching
/plugin install watch@watch
/watch https://youtu.be/<lecture> backprop intuition
```

On claude.ai, download [`watch.skill`](https://github.com/fidgetcoding/fidget-watching/releases/latest/download/watch.skill) from the latest release and add it under Settings → Capabilities → Skills. To build it yourself instead, clone the repo and run `bash scripts/build-skill.sh`.

For key-free transcripts: `brew install whisper-cpp`, plus a model at `~/.config/watch/models/ggml-base.en.bin`. The [README](README.md) has the full steps.

**What works today.** You can read all of this on the repo's Actions tab, run at `a525969`:

- 249 tests pass in CI on Linux and macOS.
- CI installs the plugin from the marketplace, starts a real Claude Code session against a fake API, and checks that the skill registers and the startup hook runs. Then it puts back three past defects one at a time and confirms the check catches each.
- CI runs a fresh end-to-end on a public-domain NASA clip, 76 seconds, from a throwaway home folder with no API keys. By URL through captions: 8 frames, 24 transcript segments. By local path through whisper.cpp: 8 frames, 32 segments.
- All three CI jobs also run every Monday, so a yt-dlp or vendor change shows up without a push.
- On a 10-second test clip, a warm re-run went from three ffmpeg frame extractions to zero, with all three frames kept. That one I measured on my Mac.

**License.** MIT, inherited from upstream. The `LICENSE` file is unchanged from devinilabs' and reads "claude-watch contributors".
