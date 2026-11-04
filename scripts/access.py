"""Opt-in browser logins for yt-dlp, and plain answers when a site says no.

--cookies-from-browser hands yt-dlp the user's own session for one run. This module
only ever passes the browser's name along. It never reads, stores, prints or uploads
a cookie. yt-dlp reads them, and sends them only to the site they belong to.
"""
from __future__ import annotations

import os
import platform
import re
from pathlib import Path
from urllib.parse import urlparse

# The exit code watch.py returns when a video needs a login. SKILL.md reads it.
EXIT_LOGIN_REQUIRED = 5
# DRM, or a paywall the user's own account doesn't get through. Nothing to retry.
EXIT_BLOCKED = 6

BROWSERS = ("brave", "chrome", "chromium", "edge", "firefox", "opera", "safari", "vivaldi", "whale")

# firefox, firefox:profile, chrome+gnomekeyring:Default::container
_BROWSER_SPEC_RX = re.compile(r"^([a-z]+)(\+[a-z0-9]+)?(:.*)?$")

_browser_spec: str | None = None

_LOGIN_WALL_RX = re.compile(
    r"cookies-from-browser"
    r"|--cookies\b"
    r"|login[ _]required"
    r"|rate-limit reached"
    r"|empty media response"
    r"|sign in"
    r"|log in\b"
    r"|logged[- ]in"
    r"|http error 401",
    re.IGNORECASE,
)

_DRM_RX = re.compile(r"\bdrm\b|widevine|playready|fairplay", re.IGNORECASE)

_PAYWALL_RX = re.compile(
    r"requires? (a )?(payment|purchase|subscription)"
    r"|purchase (this|the) (video|film|movie)"
    r"|rent(al)? (this|the) (video|film|movie)"
    r"|members?[- ]only"
    r"|join this channel"
    r"|subscribers?[- ]only"
    r"|premium (members?|subscribers?|account|content)"
    r"|paid (content|subscribers?|members?)",
    re.IGNORECASE,
)

# yt-dlp doesn't print cookies unless someone has --verbose in their config, and then
# the request headers carry them. Nothing with a cookie in it reaches our output.
_COOKIE_LINE_RX = re.compile(
    r"""^.*\b(?:(?:set-)?cookies?|authorization)["']?\s*[:=].*$""",
    re.IGNORECASE | re.MULTILINE,
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


class Blocked(RuntimeError):
    """DRM, or a paywall this account doesn't get through. str(e) says so plainly."""


def use_browser_cookies(spec: str | None) -> None:
    """Turn on the user's own browser login for this run. Rejects anything but a known
    browser, because this string goes on a yt-dlp command line."""
    global _browser_spec
    if spec is None:
        _browser_spec = None
        return
    m = _BROWSER_SPEC_RX.match(spec)
    if not m or m.group(1) not in BROWSERS:
        raise ValueError(
            f"--cookies-from-browser needs one of {', '.join(BROWSERS)}, got {spec!r}"
        )
    _browser_spec = spec


def ytdlp_auth_args() -> list[str]:
    """Extra yt-dlp arguments for this run. Empty unless the user opted in."""
    return ["--cookies-from-browser", _browser_spec] if _browser_spec else []


def scrub(text: str) -> str:
    """Remove any line carrying a cookie before yt-dlp's output goes anywhere."""
    return _COOKIE_LINE_RX.sub("[line with cookie data removed]", text or "")


def raise_for_failure(url: str, stderr: str) -> None:
    """Turn a yt-dlp failure we understand into a clear error. Returns if we don't."""
    if _DRM_RX.search(stderr or ""):
        raise Blocked(drm_message(url, stderr))
    if _PAYWALL_RX.search(stderr or ""):
        if _browser_spec:
            raise Blocked(paywall_message(url, stderr))
        raise LoginRequired(login_help(url, stderr))
    if is_login_wall(stderr):
        raise LoginRequired(login_help(url, stderr))


def drm_message(url: str, stderr: str) -> str:
    return (
        f"{site_name(url)} protects this video with DRM, so /watch can't process it.\n"
        f"\n"
        f"yt-dlp said: {_last_error(stderr)}\n"
        f"\n"
        f"I don't try to get around DRM, and a login won't change this.\n"
    )


def paywall_message(url: str, stderr: str) -> str:
    return (
        f"{site_name(url)} says this account can't watch this video.\n"
        f"\n"
        f"yt-dlp said: {_last_error(stderr)}\n"
        f"\n"
        f"The login from your browser went through, and it doesn't include this video.\n"
        f"If you've paid for it, check it plays in that browser. /watch only does what your\n"
        f"own login can do, and I don't try to get around paywalls.\n"
    )


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
    stderr = scrub(stderr)
    errors = [ln.strip() for ln in stderr.splitlines() if ln.strip().startswith("ERROR:")]
    last = errors[-1] if errors else stderr.strip()
    return last[:300]


def login_help(url: str, stderr: str) -> str:
    """The message shown when a video sits behind a login."""
    site = site_name(url)
    config = ytdlp_config_path()
    said = _last_error(stderr)
    if _browser_spec or _cookies_already_configured(config):
        used = f"your {_browser_spec} login" if _browser_spec else f"your cookie config at {config}"
        return (
            f"{site} refused this video even with {used}.\n"
            f"\n"
            f"yt-dlp said: {said}\n"
            f"\n"
            f"Try these in order:\n"
            f"  1. Open the video in that browser. Check you are still\n"
            f"     logged in there and can play it. Cookies expire, and private posts need\n"
            f"     an account that can see them.\n"
            f"  2. Update yt-dlp. Sites change often and old versions stop working.\n"
            f"  3. Run the same command again.\n"
        )
    return (
        f"{site} wants a login for this video, and this run isn't using one.\n"
        f"\n"
        f"yt-dlp said: {said}\n"
        f"\n"
        f"If your own account can watch it, you can use your browser's login for this run:\n"
        f"  1. Log in to {site if site != 'This site' else 'the site'} in a browser and\n"
        f"     check the video plays there.\n"
        f"  2. Run the same command again with this added:\n"
        f"\n"
        f"       --cookies-from-browser firefox\n"
        f"\n"
        f"     Swap firefox for chrome, safari, edge, brave, chromium, opera or vivaldi.\n"
        f"\n"
        f"To use it on every run, put that same line in {config}\n"
        f"(create the file if it isn't there).\n"
        f"\n"
        f"Good to know: the cookies stay on your machine and only go to the site they\n"
        f"belong to. /watch never stores or prints them, and the video it saves stays in\n"
        f"your library. This can't get past a paywall your account hasn't paid for, or DRM.\n"
        f"On macOS, Chrome asks for Keychain access the first time, and Safari\n"
        f"needs Full Disk Access for your terminal. Sites can rate-limit or flag accounts\n"
        f"that download a lot, so go easy on an account you care about.\n"
    )
