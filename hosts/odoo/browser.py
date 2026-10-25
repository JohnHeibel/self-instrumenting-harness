"""The agent's browser for raw (input-only) Odoo runs. Host side only; stdlib only.

    python hosts/odoo/browser.py open  --run-dir RUN [--url http://127.0.0.1:8069/odoo]
    python hosts/odoo/browser.py close --run-dir RUN
    python hosts/odoo/browser.py check

One dedicated Chrome for Testing (the Chromium that Playwright installs, `Google Chrome for Testing.app`) in app
mode: a single 1600x960 window at a fixed position, a fresh profile in <run>/browser-profile, no address bar and
no tabs, so Odoo's web UI is all the agent can reach. The window's owner process is named "Google Chrome for
Testing", which no other app on the machine has (your own Chrome is "Google Chrome"), so hosts/odoo-raw.toml's
`process` matches this window and nothing else. The window title follows the Odoo page, so the host config does not
use it. Opening it takes the keyboard focus for a moment.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PROCESS = "Google Chrome for Testing"
BUNDLE_ID = "com.google.chrome.for.testing"
SIZE = (1600, 960)         # points; fits a 1728x1117-point display below the menu bar and above the Dock
POSITION = (64, 50)
DEFAULT_URL = "http://127.0.0.1:8069/odoo"
CANDIDATES = [
    "~/Library/Caches/ms-playwright/chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    "~/Library/Caches/ms-playwright/chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
]


def binary() -> str:
    for pat in CANDIDATES:
        found = sorted(glob.glob(os.path.expanduser(pat)))
        if found:
            return found[-1]
    sys.exit("Chrome for Testing not found: run `uv run --with playwright python -m playwright install chromium`")


def window() -> dict | None:
    """The agent browser's window (largest on-screen layer-0 window of that process), or None."""
    import Quartz
    best = None
    for w in Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID) or []:
        if w.get("kCGWindowOwnerName") != PROCESS or w.get("kCGWindowLayer", 0) != 0:
            continue
        b = w["kCGWindowBounds"]
        if b["Width"] < 200 or b["Height"] < 200:
            continue
        if best is None or b["Width"] * b["Height"] > best["w"] * best["h"]:
            best = {"id": int(w["kCGWindowNumber"]), "pid": int(w["kCGWindowOwnerPID"]), "x": b["X"], "y": b["Y"],
                    "w": b["Width"], "h": b["Height"]}
    return best


def open_browser(run_dir: Path, url: str) -> int:
    profile = run_dir / "browser-profile"
    (profile / "Default").mkdir(parents=True, exist_ok=True)
    # No password manager, translate or restore bubbles over Odoo's page.
    (profile / "Default" / "Preferences").write_text(json.dumps({
        "credentials_enable_service": False, "profile": {"password_manager_enabled": False},
        "translate": {"enabled": False}, "browser": {"has_seen_welcome_page": True},
        "intl": {"accept_languages": "en-US,en"}}))
    cmd = [binary(), f"--user-data-dir={profile}", f"--app={url}", f"--window-size={SIZE[0]},{SIZE[1]}",
           f"--window-position={POSITION[0]},{POSITION[1]}", "--no-first-run", "--no-default-browser-check",
           "--use-mock-keychain", "--password-store=basic", "--lang=en-US", "--disable-session-crashed-bubble",
           "--disable-features=Translate,PasswordLeakDetection,AutofillServerCommunication",
           "--disable-sync", "--disable-background-networking"]
    log = (run_dir / "browser.log").open("ab")
    p = subprocess.Popen(cmd, stdout=log, stderr=log, start_new_session=True)
    (run_dir / "browser.pid").write_text(str(p.pid))
    for _ in range(60):
        w = window()
        if w:
            print(json.dumps(w))
            return 0
        time.sleep(0.5)
    print("browser window did not appear in 30 s (see browser.log)", file=sys.stderr)
    return 1


def close_browser(run_dir: Path) -> int:
    pidf = run_dir / "browser.pid"
    if pidf.exists():
        try:
            os.killpg(int(pidf.read_text()), signal.SIGTERM)
        except (OSError, ValueError):
            pass
        pidf.unlink(missing_ok=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["open", "close", "check"])
    ap.add_argument("--run-dir", type=Path)
    ap.add_argument("--url", default=DEFAULT_URL)
    a = ap.parse_args()
    if a.action == "check":
        w = window()
        print(json.dumps(w) if w else "no agent browser window")
        return 0 if w else 1
    if not a.run_dir:
        ap.error("--run-dir is required")
    return open_browser(a.run_dir, a.url) if a.action == "open" else close_browser(a.run_dir)


if __name__ == "__main__":
    sys.exit(main())
