"""Harness tool server: MCP over streamable HTTP, one instance per run.

SPIKE STATE: `load_extension` only registers a placeholder tool. It exists to prove
that a tool added mid-session reaches the agent as a direct, named tool. Real
loading (build, deploy into the host, reload) replaces it once that is confirmed.
"""

from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path

from mcp import types
from mcp.server.fastmcp import Context, FastMCP, Image
from mcp.server.lowlevel.server import NotificationOptions

from . import capture
from .events import EventLog

READ_ONLY = types.ToolAnnotations(readOnlyHint=True, openWorldHint=False)
HOOK_NAME = re.compile(r"[a-z][a-z0-9_]{2,47}")


def build_server(log: EventLog, host: str, port: int, window_title: str = "Factorio",
                 max_screenshot_width: int = 1280) -> FastMCP:
    mcp = FastMCP("harness", host=host, port=port, log_level="WARNING")
    _advertise_tool_list_changed(mcp)
    hooks: dict[str, dict] = {}
    builtin = {"screenshot", "load_extension", "list_hooks", "call_hook"}

    @mcp.tool(annotations=READ_ONLY)
    def screenshot() -> Image:
        """Capture the game window as an image."""
        with log.call("screenshot", "screenshot", {}) as res:
            png, meta = capture.screenshot_png(window_title, max_screenshot_width)
            res.set("image", len(png), **meta)
            return Image(data=png, format="png")

    @mcp.tool(annotations=types.ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                                idempotentHint=False, openWorldHint=False))
    async def load_extension(name: str, description: str, ctx: Context) -> str:
        """Load an extension and register it as a new tool named `name`.

        SPIKE: registers a placeholder tool that echoes its `query` argument.
        """
        with log.call("load_extension", "extension", {"name": name, "description": description}) as res:
            if not HOOK_NAME.fullmatch(name) or name in builtin:
                raise ValueError(f"invalid or reserved tool name: {name!r}")
            version = hooks.get(name, {}).get("version", 0) + 1
            hooks[name] = {"description": description, "version": version,
                           "loaded_at": datetime.now(timezone.utc).isoformat()}
            if version == 1:
                mcp.add_tool(_hook_tool(name), name=name, description=description, annotations=READ_ONLY)
            log.write("hook_loaded", hook=name, version=version)
            await _notify_tool_list_changed(ctx)
            msg = f"Loaded {name} (version {version}). It is now available as a tool named {name}."
            res.set("text", len(msg))
            return msg

    @mcp.tool(annotations=READ_ONLY)
    def list_hooks() -> list[dict]:
        """List loaded hooks with their descriptions and versions."""
        with log.call("list_hooks", "meta", {}) as res:
            out = [{"name": n, **h} for n, h in sorted(hooks.items())]
            res.set("json", len(str(out)))
            return out

    @mcp.tool(annotations=READ_ONLY)
    def call_hook(name: str, query: str = "") -> str:
        """Fallback: call a loaded hook by name, for clients that don't show it as its own tool."""
        if name not in hooks:
            raise ValueError(f"no hook named {name!r}; see list_hooks")
        return _run_hook(name, query, via="call_hook")

    def _hook_tool(name: str):
        def hook(query: str = "") -> str:
            return _run_hook(name, query, via="direct")
        return hook

    def _run_hook(name: str, query: str, via: str) -> str:
        with log.call(name, "hook", {"query": query}, via=via,
                      hook={"name": name, "version": hooks[name]["version"]}) as res:
            out = f"{name} ok: received query={query!r}"
            res.set("text", len(out))
            return out

    return mcp


def _advertise_tool_list_changed(mcp: FastMCP) -> None:
    """FastMCP advertises `tools.listChanged: false` by default; clients may then ignore our notifications."""
    low = mcp._mcp_server
    original = low.create_initialization_options

    def with_list_changed(notification_options=None, experimental_capabilities=None):
        return original(notification_options or NotificationOptions(tools_changed=True),
                        experimental_capabilities)

    low.create_initialization_options = with_list_changed


async def _notify_tool_list_changed(ctx: Context) -> None:
    note = types.ServerNotification(types.ToolListChangedNotification(method="notifications/tools/list_changed"))
    # On the current request's stream (always delivered), then on the session's
    # standalone stream for clients that listen there instead.
    await ctx.session.send_notification(note, related_request_id=ctx.request_id)
    await ctx.session.send_notification(note)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--run-dir", type=Path, required=True, help="events.jsonl is written here")
    ap.add_argument("--run-id", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    ap.add_argument("--window-title", default="Factorio")
    args = ap.parse_args()

    log = EventLog(args.run_dir / "events.jsonl", args.run_id)
    log.write("server_started", host=args.host, port=args.port)
    build_server(log, args.host, args.port, args.window_title).run(transport="streamable-http")


if __name__ == "__main__":
    main()
