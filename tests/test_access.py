"""Login-wall errors. A fake yt-dlp stands in for the real one, so no site gets contacted
and no browser cookies get read."""
import json
import os
import subprocess
import sys
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
    assert "Add this one line" in login_help(IG_URL, IG_EMPTY_MEDIA)


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
