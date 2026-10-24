"""Live check of screenshot and input against a real program on this PC (Windows).

Goes through the real tool server code (MCP tools in process, Win32 SendInput), so it
moves the real cursor, takes focus and sends real keys. Start the program first.

Two checks; each compares screenshots against the program's own idle flicker:
  --key K     press K: the screen must change; press K again: it must change back
              (a key that toggles a panel)
  --type T    type T into the focused text field: the screen must change; backspace once
              per character: it must change back. A dialog or anything else that changes
              the screen on its own fails the check rather than passing it.

  uv run --project toolserver python toolserver/scripts/live_input_check.py \
      --host-config hosts/notepad.toml --before ctrl+n --type "hello from the harness" \
      --after ctrl+w

The program's own instructions (which key toggles what, what must be open first) belong
with its host config, in hosts/. Exit 0 on pass. Screenshots, thumbnails and events.jsonl
are left in runs/live-input-check-<host>/.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
from pathlib import Path

import anyio
from PIL import Image, ImageChops, ImageStat

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "toolserver" / "src"))

from toolserver import capture  # noqa: E402
from toolserver.events import EventLog  # noqa: E402
from toolserver.extensions import HostConfig  # noqa: E402
from toolserver.server import build_server  # noqa: E402


TILES = (16, 8)


def diff(a: Image.Image, b: Image.Image) -> float:
    """How much the most-changed part of the image changed: the largest mean absolute
    difference (0-255, grayscale) over a 16x8 grid of tiles. A line of text or a small
    panel shows up here even when it is a tiny share of a wide screen."""
    if a.size != b.size:
        return 255.0
    d = ImageChops.difference(a.convert("L"), b.convert("L"))
    tw, th = d.width // TILES[0], d.height // TILES[1]
    return max(ImageStat.Stat(d.crop((x * tw, y * th, (x + 1) * tw, (y + 1) * th))).mean[0]
               for x in range(TILES[0]) for y in range(TILES[1]))


async def check(args, hc: HostConfig) -> dict:
    out: Path = args.out
    log = EventLog(out / "events.jsonl", f"live-input-check-{hc.name}")
    mcp = build_server(log, "127.0.0.1", 0, host_config=hc, workspace=out / "workspace",
                       archive_dir=out / "extensions")

    async def shot(name: str) -> Image.Image:
        res = await mcp.call_tool("screenshot", {})
        content = res[0] if isinstance(res, tuple) else res
        img = Image.open(io.BytesIO(base64.b64decode(content[0].data))).convert("RGB")
        img.save(out / f"{name}.png")
        return img

    async def call(tool: str, **kw) -> str:
        res = await mcp.call_tool(tool, kw)
        content = res[0] if isinstance(res, tuple) else res
        return content[0].text

    d = diff
    start = await shot("0_start")  # input maps through the last screenshot
    # Any input brings the program to the front; do it before the idle shots, so they
    # aren't of a window that happened to cover it.
    print(await call("move", x=start.width // 2, y=start.height // 2))
    await anyio.sleep(args.wait)
    await shot("0_focused")
    for keys in args.before:
        print(await call("key", keys=keys))
        await anyio.sleep(args.wait)
    idle_a = await shot("1_idle_a")
    await anyio.sleep(1)
    idle_b = await shot("2_idle_b")
    noise = d(idle_a, idle_b)
    threshold = max(4 * noise, args.min_change)
    result: dict = {"host": hc.name, "capture": hc.capture, "image_size": list(idle_b.size),
                    "idle_noise": round(noise, 2), "threshold": round(threshold, 2)}

    if args.key:
        print(await call("key", keys=args.key))
        await anyio.sleep(args.wait)
        after = await shot("3_after_key")
        print(await call("key", keys=args.key))
        await anyio.sleep(args.wait)
        back = await shot("4_after_key_again")
        result.update(after_key=round(d(idle_b, after), 2), after_key_again=round(d(idle_b, back), 2))
        result["pass"] = result["after_key"] > threshold and result["after_key_again"] < threshold
    else:
        print(await call("type", text=args.type))
        await anyio.sleep(args.wait)
        after = await shot("3_after_type")
        for _ in args.type:  # erase exactly what was typed, so only our text is touched
            await call("key", keys="backspace")
        await anyio.sleep(args.wait)
        back = await shot("4_after_erase")
        result.update(after_type=round(d(idle_b, after), 2), after_erase=round(d(idle_b, back), 2))
        result["pass"] = result["after_type"] > threshold and result["after_erase"] < threshold
    for keys in args.after:
        await shot("5_cleanup")
        print(await call("key", keys=keys))
        await anyio.sleep(args.wait)
    result["out"] = str(out)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host-config", type=Path, required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--key", help="a key that toggles something visible")
    g.add_argument("--type", help="text to type into the focused field")
    ap.add_argument("--wait", type=float, default=1.0, help="seconds between input and screenshot")
    ap.add_argument("--min-change", type=float, default=3.0, help="smallest mean difference that counts")
    ap.add_argument("--before", action="append", default=[], metavar="KEYS",
                    help="keys to press first, e.g. ctrl+n for a new document (repeatable)")
    ap.add_argument("--after", action="append", default=[], metavar="KEYS",
                    help="keys to press at the end to clean up (repeatable)")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    hc = HostConfig.load(args.host_config, Path("runs") / "live-input-check")
    args.out = args.out or REPO / "runs" / f"live-input-check-{hc.name}"
    args.out.mkdir(parents=True, exist_ok=True)
    target = capture.Target(hc.window_title, hc.process, hc.capture)
    try:
        capture.locate(target)
    except capture.NotFound as e:
        print(f"{e}: start it first", file=sys.stderr)
        return 2
    result = anyio.run(check, args, hc)
    print(json.dumps(result, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
