"""Built-in tools for seeing and acting on the host: screenshot, mouse and keyboard
input, and set_goal. Every call is logged (events.jsonl) with a plain-language
`summary`; screenshots also save a thumbnail under shots/ for replay.

Nothing here knows what the host is. Which program to capture and send input to comes
from the host config (`capture.Target`), and the descriptions the agent reads say only
"the program you're working in".
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import anyio
from mcp import types
from mcp.server.fastmcp import FastMCP, Image

from . import capture
from .capture import Target
from .events import EventLog
from .inputs import Controller, InputError, Win32Backend

NAMES = {"screenshot", "click", "drag", "move", "scroll", "key", "type", "set_goal"}
READ_ONLY = types.ToolAnnotations(readOnlyHint=True, openWorldHint=False)
ACTS = types.ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False,
                             openWorldHint=False)
SHOWN = {"window": "the program's window", "process": "the program's windows", "monitor": "the screen"}


def register(mcp: FastMCP, log: EventLog, target: Target, max_screenshot_width: int,
             backend=None, shoot: Callable[[], capture.Shot] | None = None) -> Controller:
    """`backend` and `shoot` replace Win32 input and screen capture (tests)."""
    run_dir = Path(log.path).parent
    ctl = Controller(backend or _LazyBackend(), target)
    shoot = shoot or (lambda: capture.screenshot(target, max_screenshot_width))

    @mcp.tool(annotations=READ_ONLY)
    def screenshot() -> Image:
        """Take a screenshot of the program you're working in. Mouse coordinates for the other
        tools are pixels in the most recent screenshot."""
        with log.call("screenshot", "screenshot", {}) as res:
            shot = shoot()
            if shot.capture is not None:
                ctl.capture = shot.capture
            what = SHOWN.get(shot.meta.get("source", ""), "the screen") if shot.capture else \
                "the whole monitor (the program was not found)"
            res.set("image", len(shot.png), **shot.meta, summary=f"Screenshot of {what}")

            def save(seq: int) -> str:
                rel = f"shots/{seq:06d}.jpg"
                (run_dir / "shots").mkdir(exist_ok=True)
                (run_dir / rel).write_bytes(shot.thumb_jpeg)
                return rel
            res.save_artifact(save)
            return Image(data=shot.png, format="png")

    async def act(tool: str, args: dict, fn: Callable[[], str]) -> str:
        def run() -> str:
            with log.call(tool, "input", args) as res:
                try:
                    summary = fn()
                except InputError as e:
                    res.extra = {"summary": f"{tool} failed: {e}"}
                    raise
                res.set("text", len(summary), summary=summary)
                return summary
        return await anyio.to_thread.run_sync(run)

    @mcp.tool(annotations=ACTS)
    async def click(x: float, y: float, button: str = "left", double: bool = False,
                    modifiers: str = "") -> str:
        """Click in the program you're working in. button: left, right or middle. double:
        double-click. modifiers: keys held during the click, e.g. "shift" or "ctrl+shift".
        x and y are pixels in the last screenshot you received (0,0 is its top-left), so take
        a screenshot first, and a new one whenever what you see changes size."""
        return await act("click", {"x": x, "y": y, "button": button, "double": double, "modifiers": modifiers},
                         lambda: ctl.click(x, y, button, double, modifiers))

    @mcp.tool(annotations=ACTS)
    async def drag(x1: float, y1: float, x2: float, y2: float, button: str = "left",
                   modifiers: str = "") -> str:
        """Press a mouse button at (x1, y1), move to (x2, y2) and release.
        x and y are pixels in the last screenshot you received."""
        return await act("drag", {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "button": button,
                                  "modifiers": modifiers},
                         lambda: ctl.drag(x1, y1, x2, y2, button, modifiers))

    @mcp.tool(annotations=ACTS)
    async def move(x: float, y: float) -> str:
        """Move the mouse without clicking (to hover). x and y are pixels in the last screenshot."""
        return await act("move", {"x": x, "y": y}, lambda: ctl.move(x, y))

    @mcp.tool(annotations=ACTS)
    async def scroll(x: float, y: float, amount: int, modifiers: str = "") -> str:
        """Scroll the mouse wheel at (x, y), in notches: positive scrolls up (away from you),
        negative down. x and y are pixels in the last screenshot."""
        return await act("scroll", {"x": x, "y": y, "amount": amount, "modifiers": modifiers},
                         lambda: ctl.scroll(x, y, amount, modifiers))

    @mcp.tool(annotations=ACTS)
    async def key(keys: str, hold_ms: int = 0) -> str:
        """Press a key or key combination in the program you're working in, e.g. "a", "enter",
        "escape", "ctrl+s", "shift+tab". Key names: letters, digits, punctuation, f1-f12, enter,
        escape, tab, space, backspace, delete, insert, home, end, pageup, pagedown,
        up/down/left/right, ctrl, shift, alt, joined with +. hold_ms keeps the keys held down
        that long (up to 10000)."""
        return await act("key", {"keys": keys, "hold_ms": hold_ms}, lambda: ctl.key(keys, hold_ms))

    @mcp.tool(name="type", annotations=ACTS)
    async def type_(text: str) -> str:
        """Type text into the program you're working in, as key presses, wherever its keyboard
        focus is. A newline presses enter."""
        return await act("type", {"text": text}, lambda: ctl.type_text(text))

    @mcp.tool(annotations=ACTS)
    def set_goal(text: str, serves: str) -> str:
        """Say what you are working on now and what it is for. Call it whenever your current
        subgoal changes. text: the subgoal, in a few words. serves: the larger goal it is a step
        toward."""
        with log.call("set_goal", "meta", {"text": text, "serves": serves},
                      summary=f"Goal: {text}") as res:
            if not text.strip():
                raise ValueError("text is empty")
            log.write("goal", text=text, serves=serves)
            res.set("text", 2)
            return "ok"

    return ctl


class _LazyBackend:
    """Creates the Win32 backend on first use, so the server starts on any OS (tests,
    the Mac) and the input tools fail with a clear message there instead."""

    def __init__(self) -> None:
        self._b: Win32Backend | None = None

    def __getattr__(self, name: str):
        if self._b is None:
            self._b = Win32Backend()
        return getattr(self._b, name)
