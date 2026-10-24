"""Live check of the input tools against the real Factorio window (Windows, this PC).

Takes a screenshot, presses "e" (opens the character inventory), takes another and checks
the images differ by much more than the game's own animation does between two idle
screenshots. Then presses "e" again and checks the screen went back. It goes through
the real tool server code (MCP tools in process, Win32 SendInput), so it moves the real
cursor and takes focus.

  uv run --project toolserver python toolserver/scripts/live_input_check.py

First start Factorio and get into a game: Single player > New game > Freeplay > Play, then
press Tab to skip the crash-site intro (while it runs, "e" does nothing). Launching it
with arguments (--load-game) from here makes Steam ask the user to confirm them, so the
script doesn't. Use a new game, not one of your saves.
Exit 0 on pass. Images and events.jsonl are left in runs/live-input-check/.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import time
from pathlib import Path

import anyio
from PIL import Image, ImageChops, ImageStat

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "toolserver" / "src"))

from toolserver import capture  # noqa: E402
from toolserver.events import EventLog  # noqa: E402
from toolserver.server import build_server  # noqa: E402

def wait_for_window(title: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if capture._find_window(title):
            return
        time.sleep(1)
    raise SystemExit(f"no '{title}' window after {timeout:.0f} s")


def diff(a: Image.Image, b: Image.Image, center: bool = False) -> float:
    """Mean absolute difference, 0-255, on small grayscale copies; `center`: only the
    middle half of the screen, where the inventory opens (on an ultrawide it covers a
    small share of the whole frame)."""
    if center:
        box = (a.width // 4, a.height // 4, a.width * 3 // 4, a.height * 3 // 4)
        a, b = a.crop(box), b.crop(box)
    small = (320, round(320 * a.height / a.width))
    d = ImageChops.difference(a.convert("L").resize(small), b.convert("L").resize(small))
    return ImageStat.Stat(d).mean[0]


async def main_async(args) -> int:
    out: Path = args.out
    log = EventLog(out / "events.jsonl", "live-input-check")
    mcp = build_server(log, "127.0.0.1", 0, args.window_title)

    async def shot(name: str) -> Image.Image:
        res = await mcp.call_tool("screenshot", {})
        content = res[0] if isinstance(res, tuple) else res
        data = base64.b64decode(content[0].data)
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.save(out / f"{name}.png")
        return img

    async def call(tool: str, **kw) -> str:
        res = await mcp.call_tool(tool, kw)
        content = res[0] if isinstance(res, tuple) else res
        return content[0].text

    # Wait until the game has finished loading: two screenshots in a row look alike.
    deadline = time.monotonic() + args.load_timeout
    prev = await shot("loading")
    while True:
        await anyio.sleep(3)
        cur = await shot("loading")
        if diff(prev, cur) < 2.0 or time.monotonic() > deadline:
            break
        prev = cur
    await anyio.sleep(args.settle)

    idle_a = await shot("1_idle_a")
    await anyio.sleep(1)
    idle_b = await shot("2_idle_b")
    noise = diff(idle_a, idle_b, center=True)

    print(await call("key", keys="e"))
    await anyio.sleep(1)
    opened = await shot("3_after_e")
    changed = diff(idle_b, opened, center=True)

    print(await call("key", keys="e"))
    await anyio.sleep(1)
    closed = await shot("4_after_e_again")
    back = diff(idle_b, closed, center=True)

    threshold = max(4 * noise, 3.0)
    result = {"idle_noise": round(noise, 2), "after_e": round(changed, 2),
              "after_second_e": round(back, 2), "threshold": round(threshold, 2),
              "inventory_opened": changed > threshold, "inventory_closed": back < threshold,
              "image_size": list(opened.size), "out": str(out)}
    print(json.dumps(result, indent=2))
    return 0 if result["inventory_opened"] and result["inventory_closed"] else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--window-title", default="Factorio")
    ap.add_argument("--out", type=Path, default=REPO / "runs" / "live-input-check")
    ap.add_argument("--load-timeout", type=float, default=180)
    ap.add_argument("--settle", type=float, default=5, help="seconds to wait after loading looks done")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    wait_for_window(args.window_title, 5)
    return anyio.run(main_async, args)


if __name__ == "__main__":
    sys.exit(main())
