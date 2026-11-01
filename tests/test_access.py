"""Login walls, the opt-in browser login, DRM and paywalls. Most tests use a fake yt-dlp.
The one that runs the real yt-dlp talks to a local server and reads a throwaway Firefox
profile holding a made-up cookie. No real site, browser or cookie is involved."""
import http.server
import json
import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts import access, download, resolve
from scripts.access import LoginRequired, is_login_wall, login_help, site_name

ROOT = Path(__file__).parent.parent
IG_URL = "https://www.instagram.com/reel/C0abcdefghi/"

# Captured from a real anonymous yt-dlp 2026.10.26 run on 2026-11-01.
IG_EMPTY_MEDIA = (
    "WARNING: [Instagram] C0abcdefghi: Instagram API is not granting access\n"
    "ERROR: [Instagram] C0abcdefghi: Instagram sent an empty media response. Check if this "
    "post is accessible in your browser without being logged-in. If it is not, then use "
    "--cookies-from-browser or --cookies for the authentication. See  "
    "https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp  for how to "
    "manually pass cookies."
)
IG_RATE_LIMIT = (
    "ERROR: [Instagram] abc: Requested content is not available, rate-limit reached or "
    "login required. Use --cookies, --cookies-from-browser, --username and --password"
)
YT_BOT_WALL = (
    "ERROR: [youtube] abc: Sign in to confirm you're not a bot. Use --cookies-from-browser "
    "or --cookies for the authentication."
)
YT_PRIVATE = "ERROR: [youtube] abc: Private video. Sign in if you've been granted access"


@pytest.mark.parametrize("stderr", [IG_EMPTY_MEDIA, IG_RATE_LIMIT, YT_BOT_WALL, YT_PRIVATE])
def test_login_walls_are_recognised(stderr):
    assert is_login_wall(stderr)


@pytest.mark.parametrize("stderr", [
    "ERROR: Unable to download webpage: <urlopen error [Errno 8] nodename nor servname provided>",
    "ERROR: Unsupported URL: https://example.com/x",
    "ERROR: [youtube] abc: Video unavailable",
    "ERROR: [generic] abc: HTTP Error 404: Not Found",
    "",
])
def test_other_failures_are_not_blamed_on_login(stderr):
    assert not is_login_wall(stderr)


@pytest.mark.parametrize("url,name", [
    (IG_URL, "Instagram"),
    ("https://instagram.com/p/x", "Instagram"),
    ("https://vm.tiktok.com/x", "TikTok"),
    ("https://youtu.be/x", "YouTube"),
    ("https://notinstagram.com/p/x", "This site"),
    ("not a url", "This site"),
])
def test_site_name(url, name):
    assert site_name(url) == name


def test_help_gives_the_exact_config_line_and_path(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    text = login_help(IG_URL, IG_EMPTY_MEDIA)
    assert "Instagram wants a login" in text
    assert "--cookies-from-browser firefox" in text
    assert str(tmp_path / "yt-dlp" / "config") in text
    assert "Instagram sent an empty media response" in text
    assert "Instagram API is not granting access" not in text


def test_config_path_defaults_under_home(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert access.ytdlp_config_path() == tmp_path / ".config" / "yt-dlp" / "config"


def test_help_changes_when_cookies_are_already_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config = tmp_path / "yt-dlp" / "config"
    config.parent.mkdir()
    config.write_text("# mine\n--cookies-from-browser chrome\n")
    text = login_help(IG_URL, IG_EMPTY_MEDIA)
    assert "even with your cookie config" in text
    assert "Add this one line" not in text
    # A commented-out line doesn't count as configured.
    config.write_text("# --cookies-from-browser chrome\n")
    assert "Run the same command again with this added" in login_help(IG_URL, IG_EMPTY_MEDIA)


@patch("scripts.resolve.subprocess.run")
def test_resolve_raises_login_required(run):
    run.return_value = MagicMock(returncode=1, stderr=IG_EMPTY_MEDIA)
    with pytest.raises(LoginRequired, match="Instagram wants a login"):
        resolve.resolve_source(IG_URL, focus_range=None)


@patch("scripts.resolve.subprocess.run")
def test_resolve_keeps_plain_error_for_other_failures(run):
    run.return_value = MagicMock(returncode=1, stderr="ERROR: Unsupported URL: x")
    with pytest.raises(RuntimeError, match="yt-dlp probe failed: ERROR: Unsupported URL") as e:
        resolve.resolve_source("https://example.com/x", focus_range=None)
    assert not isinstance(e.value, LoginRequired)


@patch("scripts.download.subprocess.run")
def test_download_raises_login_required(run, tmp_path):
    run.return_value = MagicMock(returncode=1, stderr=IG_RATE_LIMIT)
    with pytest.raises(LoginRequired):
        download.download_video(IG_URL, tmp_path)


def _fake_ytdlp_env(tmp_path, script):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "yt-dlp"
    fake.write_text(script)
    fake.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items()
           if k not in ("GROQ_API_KEY", "OPENAI_API_KEY", "XDG_CONFIG_HOME")}
    env.update(HOME=str(home), PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return env, home


def _watch(url, tmp_path, env):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "watch.py"), url,
         "--no-whisper", "--out-dir", str(tmp_path / "lib")],
        capture_output=True, text=True, check=False, cwd=str(ROOT), env=env,
    )


def test_watch_exits_cleanly_with_setup_steps_on_an_instagram_wall(tmp_path):
    env, home = _fake_ytdlp_env(
        tmp_path, f"#!/usr/bin/env bash\ncat >&2 <<'MSG'\n{IG_EMPTY_MEDIA}\nMSG\nexit 1\n"
    )
    proc = _watch(IG_URL, tmp_path, env)
    assert proc.returncode == access.EXIT_LOGIN_REQUIRED
    assert "Traceback" not in proc.stderr
    assert "channel probe failed" not in proc.stderr
    assert proc.stderr.count("wants a login") == 1
    assert "--cookies-from-browser firefox" in proc.stderr
    assert str(home / ".config" / "yt-dlp" / "config") in proc.stderr


def test_watch_still_reports_a_plain_failure_for_other_errors(tmp_path):
    env, _ = _fake_ytdlp_env(
        tmp_path, "#!/usr/bin/env bash\necho 'ERROR: Unsupported URL: x' >&2\nexit 1\n"
    )
    proc = _watch("https://example.com/x", tmp_path, env)
    assert proc.returncode not in (0, access.EXIT_LOGIN_REQUIRED)
    assert "yt-dlp probe failed" in proc.stderr
    assert "wants a login" not in proc.stderr


def test_channel_run_prints_the_steps_once_and_still_writes_the_index(tmp_path):
    playlist = json.dumps({
        "_type": "playlist", "id": "x", "title": "Creator",
        "entries": [
            {"id": "a", "title": "A", "url": "https://www.instagram.com/reel/aaa/"},
            {"id": "b", "title": "B", "url": "https://www.instagram.com/reel/bbb/"},
        ],
    })
    script = (
        "#!/usr/bin/env bash\n"
        f"if [[ \"$*\" == *--flat-playlist* ]]; then cat <<'JSON'\n{playlist}\nJSON\nexit 0; fi\n"
        f"cat >&2 <<'MSG'\n{IG_EMPTY_MEDIA}\nMSG\nexit 1\n"
    )
    env, _ = _fake_ytdlp_env(tmp_path, script)
    proc = _watch("https://www.instagram.com/creator/", tmp_path, env)
    assert proc.returncode == 0, proc.stderr
    assert proc.stderr.count("FAILED: login required") == 2
    assert proc.stderr.count("wants a login") == 1
    assert "videos_failed: 2" in proc.stdout
    assert list((tmp_path / "lib").glob("*/index.md"))


# ---- Opt-in browser login, DRM and paywalls ----

SECRET = "s3cr3t-cookie-value-do-not-print"

LOGGING_YTDLP = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$YTDLP_LOG"
{body}
"""


def _logging_env(tmp_path, body):
    log = tmp_path / "calls.log"
    env, home = _fake_ytdlp_env(tmp_path, LOGGING_YTDLP.format(body=body))
    env["YTDLP_LOG"] = str(log)
    return env, home, log


def _watch_url(env, tmp_path, *extra):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "watch.py"), "https://example.com/v",
         "--single", "--no-whisper", "--out-dir", str(tmp_path / "lib"), *extra],
        capture_output=True, text=True, check=False, cwd=str(ROOT), env=env,
    )


def test_browser_cookies_are_off_unless_asked_for(tmp_path):
    env, _, log = _logging_env(tmp_path, 'echo "ERROR: nope" >&2; exit 1')
    _watch_url(env, tmp_path)
    assert "cookies" not in log.read_text()


def test_the_flag_reaches_every_ytdlp_call(tmp_path):
    video = tmp_path / "v.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-nostdin", "-f", "lavfi",
         "-i", "color=white:size=64x36:duration=2:rate=5", "-pix_fmt", "yuv420p", str(video)],
        check=True,
    )
    body = f"""
case "$*" in
  *--dump-single-json*|*" -j "*) echo '{{"title":"t","duration":2,"id":"x"}}' ;;
  *--skip-download*) : ;;
  *) out="$(printf '%s' "$*" | sed -E 's/.* -o ([^ ]+) .*/\\1/')"; cp "{video}" "${{out%.%(ext)s}}.mp4" ;;
esac
"""
    env, _, log = _logging_env(tmp_path, body)
    proc = _watch_url(env, tmp_path, "--cookies-from-browser", "firefox")
    assert proc.returncode == 0, proc.stderr
    calls = log.read_text().splitlines()
    assert len(calls) >= 3  # metadata, captions, download
    assert all("--cookies-from-browser firefox" in c for c in calls)


@pytest.mark.parametrize("bad", ["netscape", "firefox;rm -rf /", "--cookies=/tmp/x", ""])
def test_an_unknown_browser_is_refused_before_anything_runs(tmp_path, bad):
    env, _, log = _logging_env(tmp_path, "exit 0")
    proc = _watch_url(env, tmp_path, f"--cookies-from-browser={bad}")
    assert proc.returncode == 2
    assert not log.exists()


def test_verbose_output_with_a_cookie_never_gets_repeated(tmp_path):
    """Someone's yt-dlp config might have --verbose, and then headers land on stderr."""
    env, _, _ = _logging_env(
        tmp_path,
        f'echo "[debug] Request Headers: Cookie: session={SECRET}" >&2\n'
        f'echo "ERROR: [generic] Unable to download: HTTP Error 500" >&2\nexit 1',
    )
    proc = _watch_url(env, tmp_path, "--cookies-from-browser", "firefox")
    assert proc.returncode != 0
    assert SECRET not in proc.stdout + proc.stderr


def test_drm_stops_with_a_plain_answer_even_with_a_login(tmp_path):
    env, _, log = _logging_env(tmp_path, 'echo "ERROR: [x] abc: This video is DRM protected" >&2; exit 1')
    proc = _watch_url(env, tmp_path, "--cookies-from-browser", "firefox")
    assert proc.returncode == access.EXIT_BLOCKED
    assert "I don't try to get around DRM" in proc.stderr
    assert "Traceback" not in proc.stderr
    assert len(log.read_text().splitlines()) == 1  # no second try


def test_a_paywall_without_a_login_suggests_the_users_own_login(tmp_path):
    env, _, _ = _logging_env(tmp_path, 'echo "ERROR: [x] abc: Join this channel to get access to members-only content" >&2; exit 1')
    proc = _watch_url(env, tmp_path)
    assert proc.returncode == access.EXIT_LOGIN_REQUIRED
    assert "--cookies-from-browser" in proc.stderr
    assert "can't get past a paywall" in proc.stderr


def test_a_paywall_the_login_does_not_cover_stops(tmp_path):
    env, _, _ = _logging_env(tmp_path, 'echo "ERROR: [x] abc: Join this channel to get access to members-only content" >&2; exit 1')
    proc = _watch_url(env, tmp_path, "--cookies-from-browser", "firefox")
    assert proc.returncode == access.EXIT_BLOCKED
    assert "I don't try to get around paywalls" in proc.stderr


@pytest.fixture
def members_only_site(tmp_path):
    """A local server that only hands over a video to the right session cookie."""
    video = tmp_path / "served.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-nostdin", "-f", "lavfi",
         "-i", "color=white:size=64x36:duration=3:rate=5", "-pix_fmt", "yuv420p", str(video)],
        check=True,
    )
    data = video.read_bytes()

    class Handler(http.server.BaseHTTPRequestHandler):
        def _respond(self, body):
            if f"session={SECRET}" not in self.headers.get("Cookie", ""):
                self.send_error(401)
                return
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if body:
                self.wfile.write(data)

        def do_GET(self):
            self._respond(True)

        def do_HEAD(self):
            self._respond(False)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/private.mp4"
    server.shutdown()


@pytest.fixture
def throwaway_firefox_profile(tmp_path):
    """A made-up Firefox profile holding one made-up cookie. No real browser is touched."""
    profile = tmp_path / "firefox-profile"
    profile.mkdir()
    db = sqlite3.connect(profile / "cookies.sqlite")
    db.execute(
        "CREATE TABLE moz_cookies (id INTEGER PRIMARY KEY, originAttributes TEXT DEFAULT '', "
        "name TEXT, value TEXT, host TEXT, path TEXT, expiry INTEGER, lastAccessed INTEGER, "
        "creationTime INTEGER, isSecure INTEGER, isHttpOnly INTEGER, inBrowserElement INTEGER "
        "DEFAULT 0, sameSite INTEGER DEFAULT 0, rawSameSite INTEGER DEFAULT 0, schemeMap INTEGER DEFAULT 0)"
    )
    db.execute(
        "INSERT INTO moz_cookies (name, value, host, path, expiry, lastAccessed, creationTime, "
        "isSecure, isHttpOnly) VALUES ('session', ?, '127.0.0.1', '/', 4102444800, 0, 0, 0, 0)",
        (SECRET,),
    )
    db.commit()
    db.close()
    return profile


def _real_watch(url, tmp_path, *extra):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in ("GROQ_API_KEY", "OPENAI_API_KEY")}
    env["HOME"] = str(home)
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "watch.py"), url, "--single", "--no-whisper",
         "--out-dir", str(tmp_path / "lib"), *extra],
        capture_output=True, text=True, check=False, cwd=str(ROOT), env=env,
    )


@pytest.mark.integration
def test_real_ytdlp_gets_a_private_video_with_the_users_login_and_only_then(
    members_only_site, throwaway_firefox_profile, tmp_path
):
    refused = _real_watch(members_only_site, tmp_path)
    assert refused.returncode == access.EXIT_LOGIN_REQUIRED, refused.stdout + refused.stderr
    assert "--cookies-from-browser" in refused.stderr

    allowed = _real_watch(
        members_only_site, tmp_path, "--cookies-from-browser", f"firefox:{throwaway_firefox_profile}"
    )
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    lib = next((tmp_path / "lib").glob("*"))
    assert list((lib / "frames").glob("*.jpg"))

    # The cookie went to the site and nowhere else.
    assert SECRET not in refused.stdout + refused.stderr + allowed.stdout + allowed.stderr
    secret = SECRET.encode()
    for root in (tmp_path / "lib", tmp_path / "home"):
        for f in root.rglob("*"):
            if f.is_file():
                assert secret not in f.read_bytes(), f"cookie value ended up in {f}"
    assert not [f for f in (tmp_path / "lib").rglob("*") if "cookie" in f.name.lower()]
