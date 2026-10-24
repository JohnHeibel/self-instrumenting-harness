"""End to end over real HTTP: agent-written extensions are installed, announced, callable and replaceable.

Needs no game, model or Docker: extension tools run locally (`--agent-container local`) and the
host config's reload actions are tiny Python commands. Runs anywhere: `uv run pytest` in toolserver/.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client

from conftest import SRC

PY = json.dumps(sys.executable)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path: Path):
    host_dir = tmp_path / "hostmods"
    host_toml = tmp_path / "host.toml"
    host_toml.write_text(textwrap.dedent(f"""
        [host]
        name = "testhost"
        [extensions]
        install_dir = {json.dumps(str(host_dir))}
        [extensions.reload]
        ok = [{PY}, "-c", "print('reloaded')"]
        broken = [{PY}, "-c", "import sys; print('mod failed to load'); sys.exit(3)"]
    """))
    run_dir = tmp_path / "run"
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(SRC), os.environ.get("PYTHONPATH")]))}
    proc = subprocess.Popen([sys.executable, "-m", "toolserver.server", "--port", str(port), "--run-dir", str(run_dir),
                             "--run-id", "test", "--host-config", str(host_toml), "--agent-container", "local"], env=env)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            break
        except OSError:
            time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("tool server did not start")
    yield {"url": f"http://127.0.0.1:{port}/mcp", "run": run_dir, "ws": run_dir / "workspace", "host": host_dir}
    proc.terminate()
    proc.wait(10)


def write_ext(ws: Path, name: str, tools: list[dict], *, mod_text: str | None = "-- v1", reload: str | None = "ok",
              health: dict | None = None, reason: str = "Read state the screen can't show.") -> str:
    d = ws / "extensions" / name
    (d / "mod").mkdir(parents=True, exist_ok=True)
    if mod_text is not None:
        (d / "mod" / "control.lua").write_text(mod_text)
    (d / "probe.py").write_text(textwrap.dedent("""
        import json, sys
        args = json.load(sys.stdin)
        if args.get("fail"):
            print("probe exploded", file=sys.stderr); sys.exit(2)
        print(json.dumps({"summary": f"probe saw {args.get('q', '')}", "echo": args}))
    """))
    manifest = {"name": name, "reason": reason, "tools": tools}
    if mod_text is not None:
        manifest["in_host"] = {"source": "mod", "install_as": name.replace("_", "-")}
    if reload:
        manifest["reload"] = reload
    if health:
        manifest["health_check"] = health
    (d / "extension.json").write_text(json.dumps(manifest))
    return f"extensions/{name}/extension.json"


def tool(name: str, **kw) -> dict:
    return {"name": name, "description": f"{name} returns the probe's view", "run": [sys.executable, "probe.py"],
            "input_schema": {"type": "object", "properties": {"q": {"type": "string"}, "fail": {"type": "boolean"}}}, **kw}


def events(run: Path) -> list[dict]:
    return [json.loads(line) for line in (run / "events.jsonl").read_text().splitlines()]


async def _session(url: str, body) -> None:
    changes: list[int] = []

    async def on_message(msg):
        if isinstance(msg, types.ServerNotification) and isinstance(msg.root, types.ToolListChangedNotification):
            changes.append(1)

    async with streamable_http_client(url) as (read, write, _), \
            ClientSession(read, write, message_handler=on_message) as session:
        init = await session.initialize()
        assert init.capabilities.tools and init.capabilities.tools.listChanged
        await body(session, changes)


async def names(session) -> set[str]:
    return {t.name for t in (await session.list_tools()).tools}


def test_extension_lifecycle(server):
    ws, host = server["ws"], server["host"]

    async def body(s, changes):
        assert "probe_state" not in await names(s)

        # v1: installed into the host, reloaded, registered with the agent's own schema, health-checked.
        m = write_ext(ws, "factory_probe", [tool("probe_state"), tool("probe_extra")],
                      health={"tool": "probe_state", "args": {"q": "health"}})
        res = await s.call_tool("load_extension", {"manifest": m})
        assert not res.isError, res.content[0].text
        assert "probe_state" in res.content[0].text and "passed" in res.content[0].text
        with anyio.fail_after(5):
            while not changes:
                await anyio.sleep(0.05)
        tools = {t.name: t for t in (await s.list_tools()).tools}
        assert {"probe_state", "probe_extra"} <= tools.keys()
        assert tools["probe_state"].inputSchema["properties"]["q"] == {"type": "string"}
        assert (host / "factory-probe" / "control.lua").read_text() == "-- v1"

        direct = await s.call_tool("probe_state", {"q": "ping"})
        assert not direct.isError and json.loads(direct.content[0].text)["echo"] == {"q": "ping"}
        fallback = await s.call_tool("call_hook", {"name": "probe_state", "args": {"q": "pong"}})
        assert not fallback.isError and "pong" in fallback.content[0].text
        err = await s.call_tool("probe_state", {"fail": True})
        assert err.isError and "probe exploded" in err.content[0].text

        # v2 whose reload fails: v1 stays installed and registered.
        m = write_ext(ws, "factory_probe", [tool("probe_state")], mod_text="-- v2", reload="broken")
        res = await s.call_tool("load_extension", {"manifest": m})
        assert res.isError and "mod failed to load" in res.content[0].text
        assert (host / "factory-probe" / "control.lua").read_text() == "-- v1"
        assert {"probe_state", "probe_extra"} <= await names(s)

        # v2 that reloads: replaces v1, drops the tool it no longer lists, health check fails.
        m = write_ext(ws, "factory_probe", [tool("probe_state")], mod_text="-- v2", reason="The update broke v1.",
                      health={"tool": "probe_state", "args": {"fail": True}})
        res = await s.call_tool("load_extension", {"manifest": m})
        assert not res.isError and "v2" in res.content[0].text and "FAILED" in res.content[0].text
        assert (host / "factory-probe" / "control.lua").read_text() == "-- v2"
        now = await names(s)
        assert "probe_state" in now and "probe_extra" not in now
        assert not (host / "factory-probe.previous").exists()

        # A broken manifest names every problem and changes nothing.
        bad = ws / "extensions" / "bad" / "extension.json"
        bad.parent.mkdir(parents=True)
        bad.write_text(json.dumps({"name": "Bad Name", "tools": [{"name": "screenshot", "run": [], "where": "host"}]}))
        res = await s.call_tool("load_extension", {"manifest": "extensions/bad/extension.json"})
        text = res.content[0].text
        assert res.isError and "name:" in text and "reason:" in text and "built-in" in text and "run:" in text
        assert 'only "sandbox" is supported' in text

        # Paths can't escape the workspace.
        res = await s.call_tool("load_extension", {"manifest": "../events.jsonl"})
        assert res.isError

    anyio.run(_session, server["url"], body)

    ev = events(server["run"])
    loaded = [e for e in ev if e["type"] == "hook_loaded"]
    assert [(e["hook"], e["version"]) for e in loaded] == [("probe_state", 1), ("probe_extra", 1), ("probe_state", 2)]
    assert loaded[0]["reason"] and loaded[0]["kind"] == "in_host" and loaded[0]["language"] in ("lua", "python")
    assert {f["path"] for f in loaded[0]["files"]} >= {"mod/control.lua", "probe.py", "extension.json"}
    status = [(e["hook"], e["status"]) for e in ev if e["type"] == "hook_status"]
    assert status == [("probe_state", "ok"), ("probe_state", "broken")]
    hook_calls = [e for e in ev if e["type"] == "tool_call" and e["channel"] == "hook"]
    assert {e.get("via") for e in hook_calls} == {"direct", "call_hook", "health_check"}
    assert any(e["summary"] == "probe saw ping" for e in hook_calls if e["ok"])
    assert all(e["hook"]["extension"] == "factory_probe" for e in hook_calls)
    assert (server["run"] / "extensions" / "factory_probe" / "v2" / "mod" / "control.lua").exists()
    assert [e["seq"] for e in ev] == list(range(1, len(ev) + 1))


def test_accepts_docker_host_header(server):
    """The agent container reaches the server as host.docker.internal (DNS-rebinding protection is on)."""
    import httpx

    url = server["url"]
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    port = url.split(":")[2].split("/")[0]
    ok = httpx.post(url, json=init, headers={**headers, "Host": f"host.docker.internal:{port}"})
    assert ok.status_code == 200, ok.text
    bad = httpx.post(url, json=init, headers={**headers, "Host": f"evil.example:{port}"})
    assert bad.status_code == 421
