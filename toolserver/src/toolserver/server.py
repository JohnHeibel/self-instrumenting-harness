"""Harness tool server: MCP over streamable HTTP, one instance per run.

The agent's only interface to the host: screenshots, input, docs, and its own extensions
(extensions.py), each of whose tools is registered as a named MCP tool when loaded.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from mcp import types
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.lowlevel.server import NotificationOptions
from mcp.server.transport_security import TransportSecuritySettings

from . import io_tools
from .capture import Target
from .events import EventLog
from .extensions import ContainerExecutor, Executor, Extensions, HostConfig, LocalExecutor

READ_ONLY = types.ToolAnnotations(readOnlyHint=True, openWorldHint=False)
# Names agents can't give their own tools, including ones reserved for tools still to come.
BUILTIN = {"screenshot", "load_extension", "list_hooks", "call_hook", "set_goal", "wait",
           "click", "drag", "move", "scroll", "key", "type", "read_docs"}

LOAD_EXTENSION_DOC = """Load or update one of your extensions, and register its tools so you can call them by name.

`manifest` is the path, relative to your workspace (/home/agent/work), of an extension.json:
{
  "name": "factory_probe",
  "reason": "why you wrote or changed it",
  "in_host": {"source": "mod", "install_as": "factory-probe"},
  "reload": "<a reload action of this host, optional>",
  "tools": [{"name": "get_assembler_status", "description": "what it returns",
             "input_schema": {"type": "object", "properties": {"area": {"type": "string"}}},
             "run": ["python3", "tools/assembler_status.py"], "where": "sandbox", "timeout_s": 30, "read_only": true,
             "depends_on": ["other_tool"]}],
  "health_check": {"tool": "get_assembler_status", "args": {}}
}
- in_host (optional): a directory next to the manifest, copied into the host's extension folder under
  install_as (for example a mod). The previous version is restored if the reload fails.
- Each tool's `run` command executes in your workspace sandbox, in the manifest's directory. It gets its
  arguments as JSON on stdin and returns its result on stdout. Exit non-zero to report an error. Print a
  JSON object with a "summary" string to give a one-line summary. How it reaches the host is up to you.
- Loading again with the same name replaces the extension; tools it no longer lists are removed."""


def build_server(log: EventLog, host: str, port: int, window_title: str = "",
                 max_screenshot_width: int = 1280, *, host_config: HostConfig | None = None,
                 workspace: Path | None = None, executor: Executor | None = None,
                 archive_dir: Path | None = None, io: dict | None = None) -> FastMCP:
    """`window_title` overrides the host config's. `io`: overrides for io_tools.register
    (tests pass a fake input controller and capture)."""
    # Bound to localhost, the SDK only accepts Host: localhost. The agent container
    # reaches us as host.docker.internal, so allow that too; rebinding protection stays on.
    local = ["127.0.0.1:*", "localhost:*", "[::1]:*", "host.docker.internal:*"]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=local,
        allowed_origins=[f"http://{h}" for h in local],
    )
    mcp = FastMCP("harness", host=host, port=port, log_level="WARNING",
                  transport_security=security)
    _advertise_tool_list_changed(mcp)
    ext = Extensions(mcp, log, host_config or HostConfig(), workspace or Path("workspace"),
                     executor or LocalExecutor(workspace or Path("workspace")),
                     archive_dir or Path("extensions"), BUILTIN)
    # screenshot, the input tools and set_goal: io_tools.py
    hc = host_config or HostConfig()
    target = Target(title=window_title or hc.window_title, process=hc.process, mode=hc.capture)
    io_tools.register(mcp, log, target, max_screenshot_width, **(io or {}))

    @mcp.tool(annotations=types.ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                                idempotentHint=False, openWorldHint=False),
              description=LOAD_EXTENSION_DOC)
    async def load_extension(manifest: str, ctx: Context) -> str:
        with log.call("load_extension", "extension", {"manifest": manifest}) as res:
            msg = await ext.load(manifest)
            await _notify_tool_list_changed(ctx)
            res.set("text", len(msg), summary=msg.split(". ")[0])
            return msg

    @mcp.tool(annotations=READ_ONLY)
    def list_hooks() -> list[dict]:
        """List the tools your extensions provide, with their extension, version and description."""
        with log.call("list_hooks", "meta", {}) as res:
            out = ext.describe()
            res.set("json", len(str(out)))
            return out

    @mcp.tool(annotations=READ_ONLY)
    async def call_hook(name: str, args: dict | None = None) -> str:
        """Call one of your extension tools by name. Only needed if it doesn't appear as its own tool."""
        return await ext.call(name, args or {}, via="call_hook")

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
    ap.add_argument("--window-title", default=None, help="overrides the host config's window title")
    ap.add_argument("--host-config", type=Path, help="hosts/<host>.toml: install dir and reload actions")
    ap.add_argument("--agent-container", help="run extension tools in this container (default harness-agent-<run-id>); "
                                              "'local' runs them on this machine in the workspace")
    args = ap.parse_args()

    host_config = HostConfig.load(args.host_config)
    workspace = args.run_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    container = args.agent_container or f"harness-agent-{args.run_id}"
    executor = LocalExecutor(workspace) if container == "local" else ContainerExecutor(container)
    log = EventLog(args.run_dir / "events.jsonl", args.run_id)
    log.write("server_started", host=args.host, port=args.port, host_config=host_config.name)
    build_server(log, args.host, args.port, args.window_title or "",
                 host_config=host_config, workspace=workspace, executor=executor,
                 archive_dir=args.run_dir / "extensions").run(transport="streamable-http")


if __name__ == "__main__":
    main()
