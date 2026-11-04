# fidget-watching: how it got here

## Short version

devinilabs built claude-watch in two days in May 2026. Eight days later I installed it, got told I needed an API key, and had Claude Code patch it to use the Whisper already on my Mac. Same night it learned to do whole channels. I forked it, renamed the skill, and folded it into creativity-maxxing. In August I found out the plugin hadn't been loading properly, and fixed that. In November the first real end-to-end test showed my key-free route had never worked with what the tool feeds it, and fixed that too.

## Credit first

**Upstream:** [devinilabs/claude-watch](https://github.com/devinilabs/claude-watch), MIT. 19 commits, 2026-05-03 to 2026-05-04, ending at `ade7d61`. Scene detection, frame budget, library, Whisper clients, the notes template, and the Claude Code, claude.ai and Codex packaging are all theirs.

**This fork:** my commits on top of `ade7d61`, directing Claude Code, which did much of the writing. They're listed below. Run `git diff ade7d61 HEAD` to see exactly what's mine.

## Origin, in my words

"i thought i have audio downloaders setup so i dont need these stupid keys. dont i already have ffmpeg and whisper? we can create a new flow out of this where the same concept still works?"

Then: "can i do it to a whole youtube cahnnel?" and "lets fix the script so it can handle channels or whatever".

## Timeline

| Date | Commit | What happened |
|---|---|---|
| 2026-05-03 | `d9e5174` | devinilabs starts the repo, MIT from the first commit |
| 2026-05-04 | `ade7d61` | Upstream's last commit: the skill is named claude-watch |
| 2026-05-11 | `8f63941` | Mine: local whisper.cpp backend and channel/playlist mode |
| 2026-05-11 | `845ca40` | Mine: finds a Whisper model you already have |
| 2026-05-11 | `7aa6290` | Mine: skill renamed `watch` |
| 2026-05-11 | `da0dbeb` | Mine: plain-English triggers |
| 2026-05-11 | `6ad87f3` | README title becomes fidget-watching (after a brief `fidgetwatch`) |
| 2026-08-07 | `545c4e1` | Mine: repairs the plugin manifest so it loads |
| 2026-08-07 | `e52a1e1` | Mine: repairs `hooks.json`, so the startup hook runs |
| 2026-11-01 | `0b9b283` | Mine: local route works, first fresh end-to-end in CI |
| 2026-11-01 | `4bcf8be` | Mine: long audio splits, so the API routes have no upload limit |
| 2026-11-01 | `f1c91dd` | Mine: every link points here, and the README credits upstream at the top |
| 2026-11-01 | `d4c5345` | Mine: login walls explain themselves |
| 2026-11-01 | `102c14a`, `317a919` | Mine: the frame budget scales, long videos get sections |
| 2026-11-01 | `571aa27` | Mine: browser login for videos your account can watch |
| 2026-11-01 | `d04d059` | Mine: CI proves the plugin loads and its hook fires |

## Round by round

**Round 1: lose the keys (05-11).**
Built: `transcribe_local()`, which shells out to `whisper-cli` and returns the same shape as the API backends.
Tested: smoke test on whisper.cpp's sample clip, 226 ms.
What it made me want next: "can i do it to a whole youtube cahnnel?"

**Round 2: whole channels (05-11).**
Built: `scripts/channel.py` and a split of `watch.py` into per-video and per-channel paths, with `--limit` and `--single`.
Tested: 9 new unit tests in `tests/test_channel.py`.
What it made me want next: Instagram.

**Round 3: make it mine (05-11).**
Built: the rename to `/watch`, new paths, about 50 trigger phrases, and a shared model lookup so creativity-maxxing and this skill don't each download Whisper.
Tested: a real `/watch` on YouTube after the rename, 14.7 s, 6 frames.
Broke: the shellcheck workflow in creativity-maxxing went red on 4 warnings, 2 of them mine. A Discord notification told me.
Fixed: same night.

**Round 4: it wasn't loading (08-07).**
Tested: `claude plugin details watch`.
Broke: "Failed to load skill folder as plugin". Declaring `commands` in the manifest zeroed out skill registration in 7 of 7 trials. `hooks.json` was missing its `hooks` wrapper and pointed at `CLAUDE_PLUGIN_DIR`, which doesn't exist, so the startup hook never ran.
Fixed: `545c4e1`, `e52a1e1`. `claude plugin validate` passes and the plugin list shows it loaded.

**Round 5: run it for real, from nothing (11-01).**
Built: `tests/e2e/fresh_watch.sh`, which runs a public-domain NASA clip from a throwaway home folder with API keys unset, and the repo's first test workflow.
Broke: the first real run on a local path showed the key-free route had never worked with what `watch.py` feeds it. `whisper-cli` can't read the m4a it extracts, prints "failed to read audio" and still exits 0, so every local run ended with no transcript. `-nt` flattened segments to 30-second windows. A video with no audio stream crashed. An empty transcript got cached, so a failed run stuck forever.
Fixed: `0b9b283`. Convert to 16 kHz WAV first, drop `-nt`, carry on frames-only with no audio, and retry after an empty transcript.

**Round 6: the other walls (11-01).**
Built: long audio goes up in overlapping chunks cut with ffmpeg stream copy and merges back onto one timeline, with retries on 429s and 5xxs (`4bcf8be`). A login wall now prints one clear set of steps and exits 5, where it used to end in a Python traceback (`d4c5345`).
Tested: generated audio and mocked HTTP, so no paid API ran. One test pushes a real file over 25 MB and 30 minutes through the real limit, and another checks that a beep at a known time comes back at that time. A fake yt-dlp stands in for login walls, using error text captured from real anonymous runs.
Also: `f1c91dd` points every link here, credits upstream at the top of the README, and names fidgetcoding as the marketplace owner, with `LICENSE` untouched.

**Round 7: long videos and logins (11-01).**
Built: `6b6c441` and `102c14a` replace the flat 80 frames with a budget that scales with length and prints its cost first. `317a919` writes videos over 30 minutes in parts of about 20 minutes, then checks and merges them. `571aa27` adds `--cookies-from-browser`. `d04d059` adds plugin load checks to CI.
Tested: CI on Linux and macOS, fresh end-to-end included. The login path hasn't run on a real logged-in site, and the paid Whisper APIs still haven't been called.

## Test log

| Date | Check | Result |
|---|---|---|
| 05-11 | `CHANGELOG.md` | "73+" assertions |
| 05-11 | Real `/watch` on YouTube | 14.7 s, 6 frames, captions transcript |
| 11-01 | pytest in CI at `0654d8d` | 121 passed, Linux and macOS |
| 11-01 | CI end-to-end, by URL through captions | 8 frames, 24 segments, longest 6.0 s |
| 11-01 | CI end-to-end, by path through whisper.cpp | 8 frames, 32 segments, longest 3.6 s |
| 11-01 | pytest in CI at `4ac9b28` | 184 collected, green on Linux and macOS, with fresh end-to-end and plugin load |

The API chunking has only been tested locally, against mocked APIs.

## Roads not taken

- **Renaming the GitHub repo at first.** It stayed `claude-watch` to keep the fork lineage visible, then became `fidget-watching` when I renamed my repos. The fork link to devinilabs stayed.
- **Adding my name to the license.** The `LICENSE` file is byte-identical to upstream's.
- **Reading browser cookies myself.** `/watch` never touches them. yt-dlp does, and only if you turn that on.

## Sources

- `git log` and `git diff ade7d61 HEAD` on `fidgetcoding/fidget-watching` and `devinilabs/claude-watch`.
- `CHANGELOG.md`, `LICENSE`, `tests/e2e/fresh_watch.sh`, and the bodies of the commits above.
- CI runs on the repo's Actions tab.

## Maintenance pass (2026-11-04, local branch)

An execution-path audit found missing-media cache crashes, scene caches that reused capped results across settings, nonexistent frames accepted after ffmpeg exit 0, unbounded channel probes, and setup attempting to install a separate ffprobe package. The baseline suite on this Mac was 183 passed and one failure caused by a test reading the developer's yt-dlp cookie configuration.

The maintenance changes add cache validation and source invalidation, reuse matching frames, stage replacements, serialize library writers, validate options, limit playlist probes, fix caption and section edge cases, and isolate test settings. The generated ten-second fixture previously launched three ffmpeg frame extractions on each warm run. Verification uses local generated media and mocked backends; no live audio devices or paid API calls.
