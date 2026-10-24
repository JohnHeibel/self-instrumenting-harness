"""Built-in tools for seeing and acting on the host: screenshot, mouse and keyboard
input, and set_goal. Every call is logged (events.jsonl) with a plain-language
`summary`; screenshots also save a thumbnail under shots/ for replay.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import anyio
from mcp import types
from mcp.server.fastmcp import FastMCP, Image

from . import capture
from .events import EventLog
from .inputs import Controller, InputError, Win32Backend

NAMES = {"screenshot", "click", "drag", "move", "scroll", "key", "type", "set_goal"}
READ_ONLY = types.ToolAnnotations(readOnlyHint=True, openWorldHint=False)
ACTS = types.ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False,
                             openWorldHint=False)


def register(mcp: FastMCP, log: EventLog, window_title: str, max_screenshot_width: int,
             controller: Controller | None = None,
             shoot: Callable[[], capture.Shot] | None = None) -> Controller:
    run_dir = Path(log.path).parent
    if controller is None:
        controller = Controller(_LazyBackend(), window_title)
    ctl = controller
    shoot = shoot or (lambda: capture.screenshot(window_title, max_screenshot_width))

    @mcp.tool(annotations=READ_ONLY)
    def screenshot() -> Image:
        """Capture the game window as an image."""
        with log.call("screenshot", "screenshot", {}) as res:
            shot = shoot()
            if shot.capture is not None:
                ctl.capture = shot.capture
            res.set("image", len(shot.png), **shot.meta,
                    summary=f"Screenshot of the {'game window' if shot.capture else 'whole monitor'}")

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
        """Click in the game window. button: left, right or middle. double: double-click.
        modifiers: keys held during the click, e.g. "shift" or "ctrl+shift".
        x and y are pixels in the last screenshot you received (0,0 is its top-left), so take
        a screenshot first, and a new one after the window changes size."""
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
        """Press a key or combination in the game window, e.g. "e", "escape", "ctrl+z",
        "shift+f5", "space". Names: letters, digits, f1-f12, enter, escape, tab, space,
        backspace, delete, up/down/left/right, home, end, pageup, pagedown, ctrl, shift, alt.
        hold_ms keeps it held that long (up to 10000), e.g. "w" with 2000 to walk."""
        return await act("key", {"keys": keys, "hold_ms": hold_ms}, lambda: ctl.key(keys, hold_ms))

    @mcp.tool(name="type", annotations=ACTS)
    async def type_(text: str) -> str:
        """Type text into the game window as key presses (for text fields and the console).
        A newline presses enter."""
        return await act("type", {"text": text}, lambda: ctl.type_text(text))

    @mcp.tool(annotations=ACTS)
    def set_goal(text: str, serves: str) -> str:
        """Say what you are working on now and what it is for. Call it whenever your current
        subgoal changes. text: the subgoal, e.g. "Automate iron plates". serves: what it is for,
        e.g. "Red science needs 1 plate/s"."""
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
