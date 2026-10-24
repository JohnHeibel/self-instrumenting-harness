"""End to end over real HTTP: a tool loaded mid-session is announced and callable.

Needs no game and no model, so it runs anywhere (`uv run pytest` in toolserver/).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client

from conftest import SRC


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path: Path):
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(SRC), os.environ.get("PYTHONPATH")]))}
    proc = subprocess.Popen([sys.executable, "-m", "toolserver.server", "--port", str(port),
                             "--run-dir", str(tmp_path), "--run-id", "test"], env=env)
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
    yield f"http://127.0.0.1:{port}/mcp", tmp_path / "events.jsonl"
    proc.terminate()
    proc.wait(10)


def test_tool_added_mid_session(server):
    url, events_path = server
    anyio.run(_scenario, url)

    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    calls = [e for e in events if e["type"] == "tool_call"]
    assert [(c["tool"], c["ok"]) for c in calls] == [
        ("load_extension", True), ("probe_hello", True), ("probe_hello", True), ("load_extension", False)]
    assert [c.get("via") for c in calls[1:3]] == ["direct", "call_hook"]
    assert all(c["channel"] == "hook" for c in calls[1:3])
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert any(e["type"] == "hook_loaded" and e["hook"] == "probe_hello" for e in events)


async def _scenario(url: str) -> None:
    notified = anyio.Event()

    async def on_message(msg):
        if isinstance(msg, types.ServerNotification) and isinstance(msg.root, types.ToolListChangedNotification):
            notified.set()

    async with streamable_http_client(url) as (read, write, _), \
            ClientSession(read, write, message_handler=on_message) as session:
        init = await session.initialize()
        assert init.capabilities.tools and init.capabilities.tools.listChanged

        before = {t.name for t in (await session.list_tools()).tools}
        assert {"screenshot", "load_extension", "list_hooks", "call_hook"} <= before
        assert "probe_hello" not in before

        res = await session.call_tool("load_extension", {"name": "probe_hello", "description": "Probe tool"})
        assert not res.isError
        with anyio.fail_after(5):
            await notified.wait()

        tools = {t.name: t for t in (await session.list_tools()).tools}
        assert "probe_hello" in tools
        assert tools["probe_hello"].annotations.readOnlyHint

        direct = await session.call_tool("probe_hello", {"query": "ping"})
        assert not direct.isError and "ping" in direct.content[0].text
        fallback = await session.call_tool("call_hook", {"name": "probe_hello", "query": "pong"})
        assert not fallback.isError and "pong" in fallback.content[0].text

        bad = await session.call_tool("load_extension", {"name": "screenshot", "description": "x"})
        assert bad.isError
