"""In-app update checker for LegalMind Pro.

Compares the running VERSION against the latest GitHub Release and, if a newer
one exists, points the user at the installable download.

Design note: this is deliberately NOT a silent self-replacer. For a tool that
handles legal evidence, code is never swapped underneath the user without their
say-so. The updater checks, notifies, and — on explicit confirmation — downloads
the release's installer and launches it. Nothing is overwritten automatically.

The check is best-effort: any network error, or a private repo the caller
cannot see, resolves to "no update" rather than an error.
"""
from __future__ import annotations

import json
import logging
import os
import re
import ssl
import urllib.error
import urllib.request

from core import VERSION, GITHUB_REPO

logger = logging.getLogger("legalmind.updater")

RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases/latest"

_SEMVER = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def _parse(version: str) -> tuple[int, int, int] | None:
    """'v11.0.0' / '11.0.0' / 'LegalMind 11.0.0' -> (11, 0, 0)."""
    m = _SEMVER.search(version or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def is_newer(latest: str, current: str = VERSION) -> bool:
    """True if `latest` is a strictly higher semantic version than `current`."""
    lv, cv = _parse(latest), _parse(current)
    if lv is None or cv is None:
        return False
    return lv > cv


def check_for_update(timeout: float = 6.0) -> dict | None:
    """Return details of a newer release, or None.

    On success with an update available:
        {"version": "11.1.0", "tag": "v11.1.0", "notes": "...",
         "page": "<html url>", "asset_url": "<installer download or None>",
         "asset_name": "<filename or None>"}
    Returns None when up to date, unreachable, or the repo is not visible.
    """
    req = urllib.request.Request(
        RELEASES_API,
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": f"LegalMind-Pro/{VERSION}"})
    try:
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # 404 on a private/absent repo, 403 rate-limit, etc. Not an error here.
        logger.info("update check unavailable (HTTP %s).", e.code)
        return None
    except Exception as e:
        logger.info("update check skipped: %s", e)
        return None

    tag = data.get("tag_name", "")
    if not is_newer(tag):
        logger.info("up to date (running %s, latest %s).", VERSION, tag or "?")
        return None

    # Prefer a Windows installer/zip asset if the release carries one.
    asset_url = asset_name = None
    for a in data.get("assets", []):
        name = (a.get("name") or "").lower()
        if name.endswith((".exe", ".zip", ".msi")):
            asset_url = a.get("browser_download_url")
            asset_name = a.get("name")
            break

    return {
        "version": _fmt(_parse(tag)),
        "tag": tag,
        "notes": data.get("body", "") or "",
        "page": data.get("html_url") or RELEASES_PAGE,
        "asset_url": asset_url,
        "asset_name": asset_name,
    }


def _fmt(parts) -> str:
    return ".".join(str(p) for p in parts) if parts else "?"


def download_asset(url: str, dest_dir: str, timeout: float = 300.0) -> str:
    """Download a release asset to dest_dir. Returns the local path."""
    os.makedirs(dest_dir, exist_ok=True)
    name = os.path.basename(url.split("?", 1)[0]) or "LegalMind-update"
    dest = os.path.join(dest_dir, name)
    req = urllib.request.Request(
        url, headers={"User-Agent": f"LegalMind-Pro/{VERSION}"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp, \
            open(dest, "wb") as f:
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            f.write(chunk)
    logger.info("downloaded update asset to %s", dest)
    return dest


if __name__ == "__main__":
    upd = check_for_update()
    if upd:
        print(f"Update available: {VERSION} -> {upd['version']}")
        print(f"  {upd['page']}")
    else:
        print(f"LegalMind Pro {VERSION} is up to date.")
