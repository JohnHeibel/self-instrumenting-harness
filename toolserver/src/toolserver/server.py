"""Harness tool server: MCP over streamable HTTP, one instance per run.

The agent's only interface to the host: screenshots, input, docs, and its own extensions
(extensions.py), each of whose tools is registered as a named MCP tool when loaded.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import anyio

from mcp import types
from starlette.requests import Request
from starlette.responses import JSONResponse
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.lowlevel.server import NotificationOptions
from mcp.server.transport_security import TransportSecuritySettings

from mcp.server.fastmcp.exceptions import ToolError
from . import costs as costs_mod, io_tools, scripts, spectator
from .capture import Target
from .docs import Docs, answer as answer_docs
from .events import VIA, EventLog
from .extensions import ContainerExecutor, Executor, Extensions, HostConfig, LocalExecutor

READ_ONLY = types.ToolAnnotations(readOnlyHint=True, openWorldHint=False)
SCRIPT_BLOCKED = {"screenshot", "load_extension", "run_script", "wait", "call_hook"}
# Names agents can't give their own tools, including ones reserved for tools still to come.
BUILTIN = {"screenshot", "load_extension", "list_hooks", "call_hook", "set_goal", "wait",
           "click", "drag", "move", "scroll", "key", "type", "read_docs", "costs", "run_script"}

LOAD_EXTENSION_DOC = """Load or update one of your extensions, and register its tools so you can call them by name.

`manifest` is the path, relative to your workspace (/home/agent/work), of an extension.json:
{
  "name": "status_probe",
  "reason": "why you wrote or changed it",
  "in_host": {"source": "plugin", "install_as": "status-probe"},
  "reload": "<one of this host's reload actions, below>",
  "tools": [{"name": "get_status", "description": "what it returns",
             "input_schema": {"type": "object", "properties": {"area": {"type": "string"}}},
             "run": ["python3", "tools/status.py"], "timeout_s": 30, "read_only": true,
             "depends_on": ["read_log"]}],
  "health_check": {"tool": "get_status", "args": {}}
}
- in_host (optional): code that runs inside the program, such as a plugin or mod. `source` is a directory
  next to the manifest; it is copied into the program's extension folder as a folder named `install_as`,
  which must not clash with another extension's. {install}
  If the reload fails, the previous version is put back.
- reload (optional): what to do after installing, so the program picks up the change. {reload}
- Each tool's `run` command executes in your workspace sandbox, in the manifest's directory. It gets its
  arguments as JSON on stdin and returns its result on stdout. Exit non-zero to report an error. Print a
  JSON object with a "summary" string to give a one-line summary. {reach}
- depends_on (optional): other tools of yours that this one relies on. It is recorded with the tool, so
  when one breaks it is clear what else may be affected; nothing is enforced.
- health_check (optional): a tool call run right after loading; if it fails, the tool is marked broken.
- Loading again with the same name replaces the extension; tools it no longer lists are removed."""


def load_extension_doc(hc: HostConfig) -> str:
    """The description with this host's specifics filled in from its host config."""
    install = ("You can't write to that folder directly; this is the way in." if hc.install_dir else
               "This host has no extension folder, so in_host can't be used here.")
    if hc.reload_actions:
        reload = "This host's reload actions: " + ", ".join(f'"{n}"' for n in sorted(hc.reload_actions)) + "."
    else:
        reload = "This host has no reload actions, so leave it out."
    if hc.agent_ports:
        reach = ("Your sandbox reaches the program on the host at host.docker.internal, on port(s) "
                 + ", ".join(str(p) for p in hc.agent_ports) + "; other host ports are closed.")
    else:
        reach = "How it gets information out of the program is up to you."
    doc = LOAD_EXTENSION_DOC  # holds JSON braces, so no str.format
    for key, text in (("{install}", install), ("{reload}", reload), ("{reach}", reach)):
        doc = doc.replace(key, text)
    return doc


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
    docs = Docs(hc.docs, (archive_dir or Path("extensions")).parent / "docs-cache")

    @mcp.tool(annotations=READ_ONLY)
    async def read_docs(query: str = "", section: str = "", source: str = "", limit: int = 8) -> str:
        """Search and read this program's documentation (the sources its host config lists).
        No arguments: list the sources. query: search; returns matching sections with their ids.
        section: the full text of one section, by the id a search returned. source: search one
        source only. limit: number of search results (1-25)."""
        args = {k: v for k, v in {"query": query, "section": section, "source": source}.items() if v}
        with log.call("read_docs", "docs", args) as res:
            text, summary = await anyio.to_thread.run_sync(answer_docs, docs, query, section, source,
                                                           max(1, min(int(limit), 25)))
            res.set("text", len(text), summary=summary)
            return text

    @mcp.tool(annotations=types.ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                                idempotentHint=False, openWorldHint=False),
              description=load_extension_doc(hc))
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

    @mcp.tool(annotations=types.ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                                idempotentHint=False, openWorldHint=False),
              description=scripts.DOC)
    async def run_script(code: str = "", file: str = "", args: dict | None = None, timeout_s: float = 300) -> str:
        logged = {k: v for k, v in {"code": code, "file": file, "args": args, "timeout_s": timeout_s}.items() if v}
        with log.call("run_script", "script", logged) as res:
            if bool(code) == bool(file):
                raise ToolError("give either code or file")
            if file.startswith(("/", "\\")) or ".." in file.replace("\\", "/").split("/"):
                raise ToolError("file is a path inside your workspace, relative to it")
            ok, text, calls = await scripts.run(ext, code, timeout_s, file, args, script_url,
                                                sorted(BUILTIN - SCRIPT_BLOCKED))
            for c in calls:   # each inner call is recorded like a direct one, for review and for costs
                log.write("tool_call", tool=c.get("tool"), channel="hook", args=c.get("args"), ok=c.get("ok"),
                          error=c.get("error"), duration_ms=c.get("ms"), result_kind="text",
                          result_bytes=c.get("bytes"), summary=c.get("summary"), via="script",
                          hook=ext.hook_info(c.get("tool", "")))
            res.set("text", len(text), summary=text.splitlines()[-1][:200], inner_calls=len(calls))
            if not ok:
                raise ToolError(text)
            return text

    # Scripts reach the built-in tools here (the MCP endpoint needs a session; this doesn't).
    in_container = isinstance(ext.executor, ContainerExecutor)
    script_url = f"http://{'host.docker.internal' if in_container else '127.0.0.1'}:{port}/script-call"

    @mcp.custom_route("/script-call", methods=["POST"])
    async def script_call(request: Request) -> JSONResponse:
        body = await request.json()
        name, call_args = body.get("name"), body.get("args") or {}
        if name not in BUILTIN or name in SCRIPT_BLOCKED:
            return JSONResponse({"ok": False, "error": f"{name} can't be called from a script"})
        token = VIA.set("script")
        try:
            result = await mcp.call_tool(name, call_args)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)[:2000]})
        finally:
            VIA.reset(token)
        if isinstance(result, tuple):   # (content blocks, structured): a typed result, e.g. a list
            content, structured = result
            if isinstance(structured, dict) and set(structured) == {"result"}:
                return JSONResponse({"ok": True, "text": json.dumps(structured["result"])})
        else:
            content = result
        return JSONResponse({"ok": True, "text": "\n".join(getattr(c, "text", "") for c in content)})

    run_dir = (archive_dir or Path("extensions")).parent

    @mcp.tool(annotations=READ_ONLY)
    def costs(minutes: float = 60) -> str:
        """Your own spending over the last `minutes`: tool calls per tool with failures, time and
        result size, the largest results, model tokens, and time between calls. Use it to decide
        which tools to improve first."""
        with log.call("costs", "meta", {"minutes": minutes}) as res:
            text = costs_mod.report(run_dir, max(1.0, min(float(minutes), 24 * 60)))
            res.set("text", len(text), summary=text.splitlines()[0])
            return text

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
    ap.add_argument("--no-spectator", action="store_true",
                    help="don't write <run>/live/latest.jpg (a host picture every 5 s for people watching)")
    args = ap.parse_args()

    host_config = HostConfig.load(args.host_config, args.run_dir)
    workspace = args.run_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    container = args.agent_container or f"harness-agent-{args.run_id}"
    executor = LocalExecutor(workspace) if container == "local" else ContainerExecutor(container)
    log = EventLog(args.run_dir / "events.jsonl", args.run_id)
    log.write("server_started", host=args.host, port=args.port, host_config=host_config.name)
    if not args.no_spectator:
        spectator.start(Target(title=args.window_title or host_config.window_title, process=host_config.process,
                               mode=host_config.capture), args.run_dir)
    build_server(log, args.host, args.port, args.window_title or "",
                 host_config=host_config, workspace=workspace, executor=executor,
                 archive_dir=args.run_dir / "extensions").run(transport="streamable-http")


if __name__ == "__main__":
    main()
