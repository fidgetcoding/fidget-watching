"""Turn yt-dlp's login-wall failures into setup instructions a person can follow."""
from __future__ import annotations

import os
import platform
import re
from pathlib import Path
from urllib.parse import urlparse

# The exit code watch.py returns when a video needs a login. SKILL.md reads it.
EXIT_LOGIN_REQUIRED = 5

_LOGIN_WALL_RX = re.compile(
    r"cookies-from-browser"
    r"|--cookies\b"
    r"|login[ _]required"
    r"|rate-limit reached"
    r"|empty media response"
    r"|sign in"
    r"|log in\b"
    r"|logged[- ]in",
    re.IGNORECASE,
)

_SITE_NAMES = {
    "instagram.com": "Instagram",
    "tiktok.com": "TikTok",
    "facebook.com": "Facebook",
    "fb.watch": "Facebook",
    "x.com": "X",
    "twitter.com": "X",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
}


class LoginRequired(RuntimeError):
    """The site wants a logged-in session. str(e) is the full set of instructions."""


def is_login_wall(stderr: str) -> bool:
    return bool(_LOGIN_WALL_RX.search(stderr or ""))


def site_name(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    for domain, name in _SITE_NAMES.items():
        if host == domain or host.endswith("." + domain):
            return name
    return "This site"


def ytdlp_config_path() -> Path:
    """Where yt-dlp reads its per-user config. Mirrors yt-dlp's own lookup order."""
    if platform.system() == "Windows":
        return Path(os.environ.get("APPDATA", "~")).expanduser() / "yt-dlp" / "config"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "yt-dlp" / "config"


def _cookies_already_configured(config: Path) -> bool:
    try:
        text = config.read_text()
    except OSError:
        return False
    return any(
        line.strip().startswith(("--cookies-from-browser", "--cookies "))
        for line in text.splitlines()
    )


def _last_error(stderr: str) -> str:
    errors = [ln.strip() for ln in stderr.splitlines() if ln.strip().startswith("ERROR:")]
    last = errors[-1] if errors else stderr.strip()
    return last[:300]


def login_help(url: str, stderr: str) -> str:
    """The message shown when a video sits behind a login."""
    site = site_name(url)
    config = ytdlp_config_path()
    said = _last_error(stderr)
    if _cookies_already_configured(config):
        return (
            f"{site} refused this video even with your cookie config at {config}.\n"
            f"\n"
            f"yt-dlp said: {said}\n"
            f"\n"
            f"Try these in order:\n"
            f"  1. Open the video in the browser named in that config. Check you are still\n"
            f"     logged in there and can play it. Cookies expire, and private posts need\n"
            f"     an account that can see them.\n"
            f"  2. Update yt-dlp. Sites change often and old versions stop working.\n"
            f"  3. Run the same command again.\n"
        )
    return (
        f"{site} wants a login for this video, and yt-dlp has no cookies to send.\n"
        f"\n"
        f"yt-dlp said: {said}\n"
        f"\n"
        f"To fix it, let yt-dlp read cookies from a browser you are logged in on:\n"
        f"  1. Log in to {site if site != 'This site' else 'the site'} in that browser and\n"
        f"     check the video plays there.\n"
        f"  2. Add this one line to {config} (create the file if it isn't there):\n"
        f"\n"
        f"       --cookies-from-browser firefox\n"
        f"\n"
        f"     Swap firefox for chrome, safari, edge, brave, chromium, opera or vivaldi.\n"
        f"  3. Run the same command again.\n"
        f"\n"
        f"Good to know: the cookies stay on your machine and only go to the site they\n"
        f"belong to. On macOS, Chrome asks for Keychain access the first time, and Safari\n"
        f"needs Full Disk Access for your terminal. Sites can rate-limit or flag accounts\n"
        f"that download a lot, so go easy on an account you care about.\n"
    )
