"""Harness tool server: MCP over streamable HTTP, one instance per run.

The agent's only interface to the host: screenshots, input, docs, and its own extensions
(extensions.py), each of whose tools is registered as a named MCP tool when loaded.
"""

from __future__ import annotations

import argparse
import asyncio
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
from . import costs as costs_mod, io_tools, scripts, spectator, tasks as tasks_mod
from .capture import Target
from .docs import Docs, answer as answer_docs
from .events import VIA, WHO, EventLog
from .extensions import ContainerExecutor, Executor, Extensions, HostConfig, LocalExecutor

READ_ONLY = types.ToolAnnotations(readOnlyHint=True, openWorldHint=False)
SCRIPT_BLOCKED = {"screenshot", "load_extension", "run_script", "wait", "call_hook"}
# Names agents can't give their own tools.
BUILTIN = {"screenshot", "load_extension", "list_hooks", "call_hook", "set_goal", "wait", "task", "watch",
           "status", "click", "drag", "move", "scroll", "key", "type", "read_docs", "costs", "run_script"}
ACTING_BUILTINS = {"click", "drag", "move", "scroll", "key", "type"}

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
- read_only: true for a tool that only reads. Without it a tool counts as acting, and acting needs your
  hands, which a background task may be holding; split reading from acting so your watchers and
  read-only tasks can keep reading while your hands are busy.
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
    run_dir = (archive_dir or Path("extensions")).parent
    hands = tasks_mod.Hands()
    board = tasks_mod.Board(log, run_dir)
    ext.hands = hands
    # screenshot, the input tools and set_goal: io_tools.py
    hc = host_config or HostConfig()
    target = Target(title=window_title or hc.window_title, process=hc.process, mode=hc.capture)
    io_tools.register(mcp, log, target, max_screenshot_width, hands=hands, **(io or {}))
    work = tasks_mod.Work(ext, log, run_dir, hands, board, lambda: script_url,
                          lambda: sorted(BUILTIN - SCRIPT_BLOCKED))
    _deliver_notices(mcp, board, work)
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
    async def run_script(code: str = "", keep: str = "", name: str = "", file: str = "", args: dict | None = None,
                         timeout_s: float = 300, background: bool = False, hands: bool = True,
                         minutes: float = 60, wake_on_end: bool = False, after: str = "") -> str:
        logged = {k: v for k, v in {"code": code, "keep": keep, "name": name, "file": file, "args": args,
                                    "timeout_s": timeout_s, "background": background,
                                    "hands": None if hands else False, "minutes": minutes if background else None,
                                    "wake_on_end": wake_on_end, "after": after}.items() if v}
        with log.call("run_script", "script", logged) as res:
            if not (code or name or file):
                text = scripts.listing(ext.workspace)
                res.set("text", len(text), summary=text.splitlines()[0][:200])
                return text
            if sum(map(bool, (code, name, file))) != 1:
                raise ToolError("give one of: code (with keep to keep it), name (a kept script) or file")
            if keep and not code:
                raise ToolError("keep goes with code: the script to keep")
            for n in (keep, name):
                if n and not scripts.NAME_RE.fullmatch(n):
                    raise ToolError(f"{n!r}: a script name is lower_snake_case, at most 49 characters")
            if keep and not scripts.purpose(code):
                raise ToolError("a kept script starts with a docstring whose first line says what it does")
            if name:
                if not (ext.workspace / scripts.KEPT_DIR / f"{name}.py").is_file():
                    raise ToolError(f"no kept script named {name!r}. " + scripts.listing(ext.workspace))
                file = f"{scripts.KEPT_DIR}/{name}.py"
            if file.startswith(("/", "\\")) or ".." in file.replace("\\", "/").split("/"):
                raise ToolError("file is a path inside your workspace, relative to it")
            if after and not background:
                raise ToolError("after goes with background=True: the task starts when that one ends ok")
            if background:
                if keep:
                    raise ToolError("keep a script by running it once in the foreground; start kept scripts "
                                    "in the background with name=")
                t = work.start(code, file, name or file or "code", args, minutes, hands, wake_on_end, after=after)
                if t.status == "queued":
                    text = (f"queued {t.id} ({t.name}): it starts by itself "
                            f"{'when ' + after + ' ends ok (held for you if it does not)' if after else 'when your hands are free'}"
                            f". Its end arrives as a notice.")
                else:
                    text = (f"started {t.id} ({t.name}{', holding your hands' if hands else ', reading only'}; "
                            f"ends within {t.deadline_s / 60:g} min). Its end arrives as a notice; "
                            f"queue what comes next with after=\"{t.id}\", or wait(on=[\"{t.id}\"]).")
                res.set("text", len(text), summary=f"{t.status} {t.id} {t.name}", task=t.id)
                return text
            free = hands_free = work.hands.free_for(None)
            if free:
                work.hands.take("script", name or file or "code")
            try:
                ok, text, calls = await scripts.run(
                    ext, code, timeout_s, file, args, script_url, sorted(BUILTIN - SCRIPT_BLOCKED), who="script",
                    can_act=hands_free, hands_note=work.hands.busy_note() if not hands_free else "",
                    on_event=lambda kind, d: board.post("script", d.get("text", ""), bool(d.get("wake")))
                    if kind == "notify" else None)
            finally:
                if free:
                    work.hands.release("script")
            if ok and keep:
                path = ext.workspace / scripts.KEPT_DIR / f"{keep}.py"
                replaced = path.exists()
                path.parent.mkdir(exist_ok=True)
                path.write_text(code.replace("\r\n", "\n"), encoding="utf-8", newline="\n")
                text += (f"\n[{'replaced' if replaced else 'kept as'} {scripts.KEPT_DIR}/{keep}.py; "
                         f"run it again with name=\"{keep}\"]")
            elif keep:
                text += f"\n[not kept: the run failed, so {scripts.KEPT_DIR}/{keep}.py is unchanged]"
            for c in calls:   # each inner call is recorded like a direct one, for review and for costs
                log.write("tool_call", tool=c.get("tool"), channel="hook", args=c.get("args"), ok=c.get("ok"),
                          error=c.get("error"), duration_ms=c.get("ms"), result_kind="text",
                          result_bytes=c.get("bytes"), summary=c.get("summary"), via="script",
                          hook=ext.hook_info(c.get("tool", "")))
            res.set("text", len(text), summary=text.splitlines()[-1][:200], inner_calls=len(calls),
                    held_hands=bool(free))
            if not ok:
                raise ToolError(text)
            return text

    # Scripts reach the built-in tools here (the MCP endpoint needs a session; this doesn't).
    in_container = isinstance(ext.executor, ContainerExecutor)
    script_url = f"http://{'host.docker.internal' if in_container else '127.0.0.1'}:{port}/script-call"

    @mcp.custom_route("/script-call", methods=["POST"])
    async def script_call(request: Request) -> JSONResponse:
        body = await request.json()
        name, call_args, who = body.get("name"), body.get("args") or {}, body.get("who") or "script"
        if name not in BUILTIN or name in SCRIPT_BLOCKED:
            return JSONResponse({"ok": False, "error": f"{name} can't be called from a script"})
        if name in ACTING_BUILTINS and not work.can_act(who):
            return JSONResponse({"ok": False, "error": f"{name} acts, and {who} may only read"})
        token = VIA.set("script" if who == "script" else f"task:{who}" if who.startswith("t") else who)
        who_token = WHO.set(who)
        try:
            result = await mcp.call_tool(name, call_args)
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)[:2000]})
        finally:
            VIA.reset(token)
            WHO.reset(who_token)
        if isinstance(result, tuple):   # (content blocks, structured): a typed result, e.g. a list
            content, structured = result
            if isinstance(structured, dict) and set(structured) == {"result"}:
                return JSONResponse({"ok": True, "text": json.dumps(structured["result"])})
        else:
            content = result
        return JSONResponse({"ok": True, "text": "\n".join(getattr(c, "text", "") for c in content)})

    @mcp.tool(annotations=types.ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                                idempotentHint=False, openWorldHint=False))
    async def task(id: str = "", wait_s: float = 0, stop: bool = False, resume: bool = False,
                   after: str = "") -> str:
        """Your background tasks (scripts started with run_script(..., background=True)).
        No arguments: list them. id: one task's status, its latest log() lines and, once it has ended,
        its result. wait_s: wait up to that long (at most 900 s) for it to end first.
        stop: stop it; for a queued or held task, also drops what's queued behind it (id "all" stops
        everything). When a task fails or is stopped, what was queued behind it is held: resume=True
        lets it run again, now or `after` another task (e.g. the fix you just started)."""
        logged = {k: v for k, v in {"id": id, "wait_s": wait_s, "stop": stop, "resume": resume,
                                    "after": after}.items() if v}
        with log.call("task", "meta", logged) as res:
            if not id:
                text = work.listing()
            elif stop:
                text = await work.stop(id)
            elif resume:
                text = work.resume(id, after)
            else:
                t = work.tasks.get(id)
                if t and t.status == "running" and wait_s > 0 and t.job:
                    try:
                        await asyncio.wait_for(asyncio.shield(t.job), min(float(wait_s), 900))
                    except asyncio.TimeoutError:
                        pass
                text = work.describe(id)
            res.set("text", len(text), summary=text.splitlines()[0][:200])
            return text

    @mcp.tool(annotations=READ_ONLY)
    async def wait(on: list[str] | None = None, timeout_s: float = 300) -> str:
        """Wait until something you started reports, instead of polling or ending your turn.
        on: task ids and rule names to wait for (empty: anything). Returns their notices as soon as
        one arrives, or says nothing came within timeout_s (at most 900)."""
        with log.call("wait", "meta", {"on": on or [], "timeout_s": timeout_s}) as res:
            hits = await board.wait(list(on or []), max(0.0, min(float(timeout_s), 900)))
            text = ("\n".join(f"{n.source}: {n.text}" for n in hits) if hits
                    else f"nothing from {', '.join(on) if on else 'your tasks and rules'} within {timeout_s:g} s")
            res.set("text", len(text), summary=text.splitlines()[0][:200])
            return text

    @mcp.tool(annotations=types.ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                                idempotentHint=False, openWorldHint=False))
    def watch(name: str = "", check: str = "", script: str = "", args: dict | None = None, every_s: float = 10,
              do: str = "tell", times: int = 0, cooldown_s: float = 60, remove: bool = False) -> str:
        """Set a rule the harness checks for you on a schedule, so you don't poll.
        check: Python (like run_script code) that calls your tools and prints or returns something
        only when the rule should fire, e.g. a short message; nothing (or None/False) means all is
        well. Or script: a kept script that does that, with args. It may only read.
        every_s: how often to check. do: what happens when it fires: "tell" (a notice on your next
        tool result), "wake" (also interrupt your thinking for it), or "run:<kept script>" (start
        that script as a task holding your hands, with args plus event=<the message>; no model call).
        times: remove the rule after firing that many times (0: never). cooldown_s: least time
        between firings. remove: delete the rule `name`. No arguments: list your rules.
        Rules survive restarts of the harness."""
        logged = {k: v for k, v in {"name": name, "check": check, "script": script, "args": args,
                                    "every_s": every_s, "do": do, "times": times, "remove": remove}.items() if v}
        with log.call("watch", "meta", logged) as res:
            if not name:
                text = work.rule_listing()
            elif remove:
                work.remove_rule(name)
                text = f"removed rule {name}"
            else:
                r = tasks_mod.parse_rule(name, check, script, args, every_s, do, times, cooldown_s, ext.workspace)
                work.set_rule(r)
                text = f"rule set: {r.brief()}"
            res.set("text", len(text), summary=text.splitlines()[0][:200])
            return text

    @mcp.tool(annotations=READ_ONLY)
    def status() -> str:
        """Where things stand, in one call: your current goal, running tasks, rules, notices you
        haven't seen, who holds your hands, recent cost, and your NOTES.md headings. Call it first
        after your context was compacted or the harness restarted."""
        with log.call("status", "meta", {}) as res:
            g = tasks_mod.tail_goal(log.path)
            lines = [f"Goal: {g['text']} (serves: {g.get('serves', '')})" if g else "Goal: not set (set_goal)"]
            running = [t for t in work.tasks.values() if t.status == "running"]
            lines.append("Tasks running: " + ("; ".join(t.brief() for t in running) if running else "none"))
            lines.append(work.rule_listing().replace("\n  ", "\n  - "))
            running += [t for t in work.tasks.values() if t.status == "queued"]
            lines[-1] = "Tasks running or queued: " + ("; ".join(t.brief() for t in running) if running else "none")
            lines.append(f"Hands: free for {work.hands.idle_s() / 60:.1f} min" if work.hands.holder is None
                         else f"Hands: {work.hands.busy_note()}")
            new = board.unseen()
            if new:
                board.mark_seen(new)
                lines.append("Notices: " + tasks_mod._join(new, 1500))
            try:
                lines += costs_mod.report(run_dir, 60).splitlines()[:4]
            except Exception:
                pass
            notes = ext.workspace / "NOTES.md"
            if notes.is_file():
                heads = [l.strip() for l in notes.read_text(encoding="utf-8", errors="replace").splitlines()
                         if l.startswith("#")][:20]
                lines.append("NOTES.md: " + " | ".join(heads))
            text = "\n".join(lines)
            res.set("text", len(text), summary=lines[0][:200])
            return text

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


def _deliver_notices(mcp: FastMCP, board: "tasks_mod.Board", work: "tasks_mod.Work") -> None:
    """Every tool result the agent gets carries the notices it hasn't seen yet, as a last text block.
    (Not inside scripts: their results are parsed.) Also restores saved rules on the first call."""
    manager = mcp._tool_manager
    original = manager.call_tool
    restored = []

    async def call_tool(name, arguments, context=None, convert_result=False):
        if not restored:
            restored.append(True)
            work.restore_rules()
        result = await original(name, arguments, context=context, convert_result=convert_result)
        if VIA.get() is not None or not convert_result or name in ("wait", "status"):
            return result
        footer = "\n".join(x for x in (board.footer(), work.idle_note()) if x)
        if not footer:
            return result
        block = types.TextContent(type="text", text=footer)
        if isinstance(result, tuple):
            content, structured = result
            return list(content) + [block], structured
        if isinstance(result, list):
            return result + [block]
        return result

    manager.call_tool = call_tool


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
    ap.add_argument("--max-tool-timeout", type=float, default=None,
                    help="cap (seconds) on any extension tool's timeout_s; overrides [extensions] max_tool_timeout_s")
    ap.add_argument("--no-spectator", action="store_true",
                    help="don't write <run>/live/latest.jpg (a host picture every 5 s for people watching)")
    args = ap.parse_args()

    host_config = HostConfig.load(args.host_config, args.run_dir)
    if args.max_tool_timeout:
        host_config.max_tool_timeout_s = args.max_tool_timeout
    workspace = args.run_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    container = args.agent_container or f"harness-agent-{args.run_id}"
    executor = LocalExecutor(workspace) if container == "local" else ContainerExecutor(container)
    log = EventLog(args.run_dir / "events.jsonl", args.run_id)
    log.write("server_started", host=args.host, port=args.port, host_config=host_config.name)
    if host_config.forward_offset and host_config.agent_ports:
        from . import forward
        forward.start(log, host_config.agent_ports, host_config.forward_offset)
    if not args.no_spectator:
        spectator.start(Target(title=args.window_title or host_config.window_title, process=host_config.process,
                               mode=host_config.capture), args.run_dir)
    build_server(log, args.host, args.port, args.window_title or "",
                 host_config=host_config, workspace=workspace, executor=executor,
                 archive_dir=args.run_dir / "extensions").run(transport="streamable-http")


if __name__ == "__main__":
    main()
