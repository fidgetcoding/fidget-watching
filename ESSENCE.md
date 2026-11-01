# fidget-watching: the essence

**What it is.** A fork of [devinilabs/claude-watch](https://github.com/devinilabs/claude-watch), which turns a video into study notes for Claude: scene-aware screenshots, a timestamped transcript, and a markdown file saved to a library. devinilabs built that, and it's a great idea. This fork adds a few things I needed. The skill is called `watch` here.

**Who it's for.** Anyone using Claude Code who learns from videos and wants notes they can come back to, especially if you don't want to pay for a transcription API.

**Why it exists.** I installed claude-watch and it asked for an API key. My exact reaction: "i thought i have audio downloaders setup so i dont need these stupid keys. dont i already have ffmpeg and whisper?" I did. Then: "can i do it to a whole youtube cahnnel?" Now it can.

**What's different from upstream.** Only what I changed:

- **No keys needed.** A local whisper.cpp backend, used first when it's installed. Upstream's Groq and OpenAI options are still there.
- **Whole channels and playlists.** Paste a channel or playlist URL and it does a batch (10 by default) and rolls them up in an `index.md`.
- **Long audio just works.** The Whisper APIs cap uploads at 25 MB, so longer audio goes up in overlapping chunks and comes back as one timeline. Tested locally with generated audio and mocked APIs, so no paid call ran.
- **Login walls explain themselves.** When Instagram or another site blocks yt-dlp, `/watch` stops and prints the exact setup steps.
- **Shared Whisper model.** It finds a model you already downloaded for other tools.
- **Plain-English triggers.** "Watch this lecture" or "transcribe this reel" activates it without the slash command.
- **Renamed.** `/claude-watch` became `/watch`.
- **Plugin repair.** The manifest and hook file had defects that stopped the plugin from loading and the startup hook from running.

Everything else, the scene detection, frame budget, library, notes template and packaging, is upstream's work.

**How to try it.** In Claude Code:

```
/plugin marketplace add fidgetcoding/fidget-watching
/plugin install watch@watch
/watch https://youtu.be/<lecture> backprop intuition
```

For key-free transcripts: `brew install whisper-cpp`, plus a model at `~/.config/watch/models/ggml-base.en.bin`.

**What works today.** You can read all of this on the repo's Actions tab:

- 121 tests pass in CI on Linux and macOS.
- CI runs a fresh end-to-end on a public-domain NASA clip, 76 seconds, from a throwaway home folder with no API keys. By URL through captions: 8 frames, 24 transcript segments. By local path through whisper.cpp: 8 frames, 32 segments.
- Both CI jobs also run every Monday, so a yt-dlp or vendor change shows up without a push.

**License.** MIT, inherited from upstream. The `LICENSE` file is unchanged from devinilabs' and reads "claude-watch contributors".
