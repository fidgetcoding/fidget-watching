# fidget-watching: how it got here

My words are quoted as I wrote them, typos fixed, nothing else. Commit hashes are from this repo. "Private note" means a dated record I keep offline.

## 1. The short version

devinilabs built claude-watch in two days in May 2026. Eight days later I installed it, got told I needed an API key, and patched it to use the Whisper already on my Mac. Same night it learned to do whole channels. I forked it, renamed the skill, and folded it into creativity-maxxing. In August I found out the plugin hadn't been loading properly, and fixed that. On November 1 the first real end-to-end test showed my key-free route had never worked with what the tool feeds it, so I fixed that too, then added long-video sections, a frame budget that scales and opt-in browser login. On November 4 I went through every way a re-run could reuse something broken, fixed those, and shipped it once CI passed on Linux and macOS.

**Credit first.** Upstream is [devinilabs/claude-watch](https://github.com/devinilabs/claude-watch), MIT: 19 commits on May 3 and 4, 2026, ending at `ade7d61`. Scene detection, the library, the notes template, the Whisper API clients, and the Claude Code, claude.ai and Codex packaging are all theirs. Everything after `ade7d61` is mine, and `git diff ade7d61 HEAD` shows exactly what that is.

## 2. Origin, in my words

"I thought I have audio downloaders set up so I don't need these stupid keys. Don't I already have ffmpeg and Whisper? We can create a new flow out of this where the same concept still works?"

Then: "Can I do it to a whole YouTube channel?" and "Let's fix the script so it can handle channels or whatever".

## 3. Timeline

| Date (2026) | What happened | Evidence |
|---|---|---|
| May 3 | devinilabs starts the repo, MIT from the first commit | `d9e5174` |
| May 4 | Upstream's last commit: the skill is named claude-watch | `ade7d61` |
| May 11 | Local whisper.cpp backend and channel/playlist mode | `8f63941` |
| May 11 | Finds a Whisper model you already have | `845ca40` |
| May 11 | Skill renamed `watch` | `7aa6290` |
| May 11 | Plain-English triggers | `da0dbeb` |
| May 11 | README title becomes fidget-watching (after a brief `fidgetwatch`) | `6ad87f3` |
| Aug 7 | Plugin manifest repaired so it loads | `545c4e1` |
| Aug 7 | `hooks.json` repaired, so the startup hook runs | `e52a1e1` |
| Nov 1 | Local route works, first fresh end-to-end in CI | `0b9b283` |
| Nov 1 | Long audio splits, so the API routes have no upload limit | `4bcf8be` |
| Nov 1 | Every link points here, and the README credits upstream at the top | `f1c91dd` |
| Nov 1 | Login walls explain themselves | `d4a9c23` |
| Nov 1 | Scene frames land after the transition, not on the cut | `6b6c441` |
| Nov 1 | The frame budget scales, long videos get sections | `102c14a`, `317a919` |
| Nov 1 | Browser login for videos your account can watch | `571aa27` |
| Nov 1 | CI proves the plugin loads and its hook fires | `d04d059` |
| Nov 1 and 2 | Essence and build-history docs, README says why I forked it | `4c892e5`, `e723770`, `b345a88` |
| Nov 4 | Cache recovery, one writer per library entry, input checks, isolated tests | `a525969` |

## 4. Round by round

### Round 1: lose the keys (May 11)
- **Built:** `transcribe_local()`, which shells out to `whisper-cli` and returns the same shape as the API backends.
- **Tested:** a smoke test on whisper.cpp's sample clip, 226 ms.
- **Wanted next:** "Can I do it to a whole YouTube channel?"

### Round 2: whole channels (May 11)
- **Built:** `scripts/channel.py` and a split of `watch.py` into per-video and per-channel paths, with `--limit` and `--single`.
- **Tested:** 9 new unit tests in `tests/test_channel.py`.
- **Wanted next:** Instagram.

### Round 3: make it mine (May 11)
- **Built:** the rename to `/watch`, new paths, about 50 trigger phrases, and a shared model lookup so creativity-maxxing and this skill don't each download Whisper.
- **Tested:** a real `/watch` on YouTube after the rename, 14.7 s, 6 frames.
- **Broke:** the ShellCheck workflow in creativity-maxxing went red on 4 warnings, 2 of them mine. A Discord notification told me.
- **Fixed:** same night.
- **Wanted next:** nothing until August.

### Round 4: it wasn't loading (August 7)
- **Tested:** `claude plugin details watch`.
- **Broke:** "Failed to load skill folder as plugin". Declaring `commands` in the manifest zeroed out skill registration in 7 of 7 trials. `hooks.json` was missing its `hooks` wrapper and pointed at `CLAUDE_PLUGIN_DIR`, which doesn't exist, so the startup hook never ran.
- **Fixed:** `545c4e1`, `e52a1e1`. `claude plugin validate` passes and the plugin list shows it loaded.
- **Wanted next:** a test that would have caught it.

### Round 5: run it for real, from nothing (November 1)
- **Built:** `tests/e2e/fresh_watch.sh`, which runs a public-domain NASA clip from a throwaway home folder with API keys unset, and the repo's first test workflow.
- **Broke:** the first real run on a local path showed the key-free route had never worked with what `watch.py` feeds it. `whisper-cli` can't read the m4a it extracts, prints "failed to read audio" and still exits 0, so every local run ended with no transcript. `-nt` flattened segments to 30-second windows. A video with no audio stream crashed. An empty transcript got cached, so a failed run stuck forever.
- **Fixed:** `0b9b283`. Convert to 16 kHz WAV first, drop `-nt`, carry on frames-only with no audio, and retry after an empty transcript.
- **Wanted next:** the other walls: upload limits and logins.

### Round 6: the other walls (November 1)
- **Built:** long audio goes up in overlapping chunks cut with ffmpeg stream copy and merges back onto one timeline, with retries on 429s and 5xxs (`4bcf8be`). A login wall now prints one clear set of steps and exits 5, where it used to end in a Python traceback (`d4a9c23`). `f1c91dd` points every link here, credits upstream at the top of the README, and names fidgetcoding as the marketplace owner, with `LICENSE` untouched.
- **Tested:** generated audio and mocked HTTP, so no paid API ran. One test pushes a real file over 25 MB and 30 minutes through the real limit, and another checks that a beep at a known time comes back at that time. A fake yt-dlp stands in for login walls, using error text captured from real anonymous runs.
- **Wanted next:** long videos that come out as good as short ones.

### Round 7: long videos and logins (November 1)
- **Built:** `6b6c441` grabs scene frames after the transition, because frames taken on the cut came out as blurs. `102c14a` replaces the flat 80 frames with a budget that scales with length and prints its cost first. `317a919` writes videos over 30 minutes in parts of about 20 minutes, then checks and merges them. `571aa27` adds `--cookies-from-browser`. `d04d059` adds plugin load checks to CI, including putting each past defect back to prove the check catches it.
- **Tested:** CI on Linux and macOS, fresh end-to-end included, 184 tests at `4ac9b28`.
- **Not run:** the login path on a real logged-in site, and the paid Whisper APIs.
- **Wanted next:** to trust re-runs as much as first runs.

### Round 8: re-runs you can trust (November 4)
- **Tested:** an audit of every execution path. The baseline on my Mac was 183 passed and 1 failed, and the failure was a test reading my own yt-dlp cookie config.
- **Broke:** a cached run crashed when its media file had gone missing. Scene caches reused capped results across different settings. A frame that ffmpeg never wrote got accepted because ffmpeg exited 0. Channel probes had no limit. A channel error fell through to a single-video retry. Fractional caption timestamps and HTML entities came out wrong. Setup tried to install a separate ffprobe package, when ffprobe ships with ffmpeg.
- **Fixed:** all in `a525969`. Caches get validated, edited local files invalidate by size and modification time, matching frames get reused, new frames are staged before they replace old ones, each library entry takes one writer at a time, options get checked, playlist probes respect `--limit`, and tests no longer read your real settings. That's 65 new regression cases in `tests/test_maintenance.py`.
- **Tested after:** 248 passed locally, then 249 in CI on Linux and macOS, plus the fresh end-to-end and the plugin load checks. The macOS jobs first got cancelled for runner capacity with zero steps run, and passed on the rerun.
- **Measured:** on a 10-second test clip, a warm re-run went from 0.37 and 0.54 seconds to 0.15 and 0.16, and from three ffmpeg frame extractions to zero, with all three frames kept.
- **Wanted next:** a real logged-in site and a real paid Whisper call, which still haven't run.

## 5. Feature family tree

```
claude-watch (devinilabs): video → frames + transcript → notes.md
├─ "these stupid keys" → local whisper.cpp route (May 11)
│  ├─ shared model lookup → reuse with creativity-maxxing
│  └─ first real end-to-end (Nov 1) → route actually fixed → fresh end-to-end in CI
├─ "a whole YouTube channel" → channel/playlist mode → index.md
│  └─ Instagram → login-wall steps → --cookies-from-browser
├─ rename to /watch → plain-English triggers
├─ plugin not loading (Aug 7) → manifest + hooks fix → plugin-load CI with defect put-backs
├─ 25 MB upload cap → chunked long audio
│  └─ long videos → scaling frame budget → sections + check + merge
└─ re-runs → cache validation, staged frames, one writer per entry (Nov 4)
```

## 6. Test log

| When | What | Result | Where |
|---|---|---|---|
| May 11 | Test suite | "73+" assertions | `CHANGELOG.md` |
| May 11 | Real `/watch` on YouTube | 14.7 s, 6 frames, captions transcript | Private note |
| Nov 1, `0654d8d` | pytest in CI | 121 passed, Linux and macOS | GitHub Actions |
| Nov 1 | CI end-to-end, by URL through captions | 8 frames, 24 segments, longest 6.0 s | GitHub Actions |
| Nov 1 | CI end-to-end, by path through whisper.cpp | 8 frames, 32 segments, longest 3.6 s | GitHub Actions |
| Nov 1, `4ac9b28` | pytest in CI | 184, green on Linux and macOS, with fresh end-to-end and plugin load | GitHub Actions |
| Nov 4, before | pytest on my Mac | 183 passed, 1 failed (read my real cookie config) | Private note |
| Nov 4, after | pytest on my Mac | 248 passed | Private note |
| Nov 4 | Warm re-run, 10-second clip | 0.37 / 0.54 s before, 0.15 / 0.16 s after, 3 ffmpeg extractions to 0 | Private note |
| Nov 4, `a525969` | pytest in CI | 249 passed on Linux and on macOS | GitHub Actions |
| Nov 4, `a525969` | CI end-to-end | URL: 8 frames, 24 segments. Path through whisper.cpp: 8 frames, 32 segments | GitHub Actions |
| Nov 4, `a525969` | CI plugin load | 5 of 5 checks, and all 3 past defects caught when put back | GitHub Actions |

The API chunking and the browser login have only been tested locally, against mocked APIs and a fake site.

## 7. Roads not taken

- **Renaming the GitHub repo at first.** It stayed `claude-watch` to keep the fork lineage visible, then became `fidget-watching` when I renamed my repos. The fork link to devinilabs stayed.
- **Adding my name to the license.** The `LICENSE` file is byte-identical to upstream's.
- **Reading browser cookies myself.** `/watch` never touches them. yt-dlp does, and only if you turn that on.
- **Getting around paywalls or DRM.** A login gets you what your account already has. Anything past that stops with a plain message.

## 8. Sources

- `git log` and `git diff ade7d61 HEAD` on `fidgetcoding/fidget-watching` and `devinilabs/claude-watch`.
- `CHANGELOG.md`, `LICENSE`, `tests/e2e/fresh_watch.sh`, `tests/test_maintenance.py`, and the bodies of the commits above.
- CI runs on the repo's Actions tab.
- Private notes from May 11 and the November 4 maintenance and release records.
