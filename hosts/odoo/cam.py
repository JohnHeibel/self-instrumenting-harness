"""Follow-the-agent camera for the Odoo hero run. Host side only.

    uv run --with playwright python hosts/odoo/cam.py --run-dir RUN [--base-url http://127.0.0.1:8069]

Headless Chromium logged in as the read-only observer (sisyphus_observer; web password only, in the keys file).
Tails <run>/injector/odoo_calls.jsonl (written by apiproxy.py). On a new call that touched records it opens that
record's form view and holds it HOLD_S seconds; when calls arrive faster it shows the latest one (preferring a
write) with a "+N more" badge. Between actions it rests on an overview page, rotated every OVERVIEW_S seconds.
Records its own video (1920x1080) to <run>/cam/ and writes <run>/video.json
  {"file": "cam/<name>.webm", "start_utc": ISO UTC of the first frame, "width": 1920, "height": 1080}
Stops on <run>/STOP, SIGTERM or SIGINT, closing the context so the video file is finalised.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

KEYS = Path.home() / ".sisyphus" / "odoo" / "odoo-keys.json"
HOLD_S = 2.8
OVERVIEW_S = 15
OVERVIEWS = ["/odoo/action-sale.action_orders", "/odoo/action-purchase.purchase_rfq",
             "/odoo/action-stock.action_picking_tree_all", "/odoo/action-account.action_move_in_invoice_type"]
BADGE_JS = """(t) => { let b = document.getElementById('cam-badge');
  if (!t) { if (b) b.remove(); return; }
  if (!b) { b = document.createElement('div'); b.id = 'cam-badge';
    b.style.cssText = 'position:fixed;right:18px;bottom:18px;z-index:99999;background:rgba(20,20,20,.88);color:#fff;'
      + 'font:600 20px sans-serif;padding:10px 18px;border-radius:22px;pointer-events:none';
    document.body.appendChild(b); }
  b.textContent = t; }"""

stop = False


def on_signal(*_):
    global stop
    stop = True


def utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def read_new(path: Path, pos: int) -> tuple[list[dict], int]:
    if not path.exists():
        return [], pos
    with path.open("rb") as f:
        f.seek(pos)
        data = f.read()
    end = data.rfind(b"\n")
    if end < 0:
        return [], pos
    out = []
    for ln in data[:end].splitlines():
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out, pos + end + 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--base-url", default="http://127.0.0.1:8069")
    ap.add_argument("--keys", type=Path, default=KEYS)
    a = ap.parse_args()
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    keys = json.loads(a.keys.read_text())
    obs = keys["observer"]
    calls = a.run_dir / "injector" / "odoo_calls.jsonl"
    cam_dir = a.run_dir / "cam"
    cam_dir.mkdir(parents=True, exist_ok=True)
    base = a.base_url.rstrip("/")

    with sync_playwright() as p:
        br = p.chromium.launch(headless=True)
        ctx = br.new_context(viewport={"width": 1920, "height": 1080}, record_video_dir=str(cam_dir),
                             record_video_size={"width": 1920, "height": 1080})
        t_before = time.time()
        page = ctx.new_page()
        start = datetime.fromtimestamp((t_before + time.time()) / 2, timezone.utc)   # the video starts with the page
        page.set_default_timeout(20000)

        def goto(url: str) -> bool:
            try:
                page.goto(base + url, wait_until="domcontentloaded")
                page.wait_for_selector(".o_action_manager, .o_web_client", timeout=15000)
                page.wait_for_timeout(600)
                return True
            except Exception as e:  # noqa: BLE001
                print(f"{utc()} goto {url} failed: {str(e)[:120]}", flush=True)
                return False

        page.goto(f"{base}/web/login", wait_until="domcontentloaded")
        page.fill("input[name=login]", obs["login"])
        page.fill("input[name=password]", obs["password"])
        page.click("button[type=submit]")
        page.wait_for_selector(".o_web_client", timeout=60000)
        (a.run_dir / "video.json").write_text(json.dumps({
            "file": str(Path(page.video.path()).relative_to(a.run_dir)),
            "start_utc": start.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z", "width": 1920, "height": 1080}, indent=2))
        print(f"{utc()} cam up, video {page.video.path()}", flush=True)

        pos = calls.stat().st_size if calls.exists() else 0
        ov_i, last_ov, rest = 0, 0.0, True
        while not stop and not (a.run_dir / "STOP").exists():
            new, pos = read_new(calls, pos)
            shown = [c for c in new if c.get("ids") and c.get("ok") and not c["model"].startswith(("ir.", "res.users"))]
            if shown:
                pick = next((c for c in reversed(shown) if c.get("mutating")), shown[-1])
                rid = pick["ids"][-1]
                url = f"/odoo/{pick['model']}/{rid}"
                if goto(url):
                    badge = f"{pick['method']} {pick['model']} #{rid}" + (f"   +{len(shown) - 1} more" if len(shown) > 1 else "")
                    page.evaluate(BADGE_JS, badge)
                    print(f"{utc()} show {url} ({len(shown)} calls)", flush=True)
                    page.wait_for_timeout(int(HOLD_S * 1000))
                    page.evaluate(BADGE_JS, "")
                last_ov = time.time()
                rest = False
                continue
            if time.time() - last_ov > OVERVIEW_S or not rest and time.time() - last_ov > HOLD_S:
                if goto(OVERVIEWS[ov_i % len(OVERVIEWS)]):
                    ov_i += 1
                last_ov, rest = time.time(), True
            time.sleep(0.4)
        vp = page.video.path()
        ctx.close()
        br.close()
        print(f"{utc()} cam stopped, video {vp}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
