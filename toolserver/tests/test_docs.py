"""read_docs over all three source formats, local and fetched, through the real MCP tool."""

from __future__ import annotations

import functools
import http.server
import json
import threading
import textwrap
from pathlib import Path

import anyio
import pytest

from toolserver.docs import Docs, answer, parse_sources
from toolserver.events import EventLog
from toolserver.extensions import HostConfig
from toolserver.server import build_server

API = {"classes": [
    {"name": "Widget", "description": "A thing on screen.",
     "attributes": [{"name": "status", "description": "Whether the widget is working, idle or blocked.",
                     "type": "WidgetStatus"},
                    {"name": "position", "description": "Where it is."}],
     "methods": [{"name": "rotate", "description": "Turn it 90 degrees.", "parameters": [{"name": "reverse"}]}]},
    {"name": "Panel", "description": "Holds widgets. See Widget.status for health."}]}

GUIDE = """<html><head><style>.x{}</style><script>var a=1</script></head><body>
<nav>Home | About</nav><h1>Guide</h1><p>Start here.</p>
<h2>Reloading extensions</h2><p>Changes to scripts load when a save is reloaded.</p>
<h2>Ports</h2><p>The control port is 27015.</p></body></html>"""


@pytest.fixture
def sources(tmp_path: Path):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "setup.md").write_text("# Setup\nInstall it.\n## Shortcuts\nPress e for the inventory panel.\n")
    (notes / "plain.txt").write_text("No headings, just a tip about widgets.")
    (tmp_path / "api.json").write_text(json.dumps(API))
    web = tmp_path / "web"
    web.mkdir()
    (web / "guide.html").write_text(GUIDE)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(web))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    raw = {"sources": [
        {"name": "api", "path": "{config_dir}/api.json", "format": "json"},
        {"name": "notes", "path": "{config_dir}/notes", "format": "markdown"},
        {"name": "guide", "url": f"http://127.0.0.1:{httpd.server_address[1]}/guide.html", "format": "html"},
        {"name": "gone", "path": "{config_dir}/missing", "format": "markdown"},
    ]}
    yield parse_sources(raw, lambda s: s.replace("{config_dir}", str(tmp_path)))
    httpd.shutdown()


def test_sections_search_and_read(sources, tmp_path):
    d = Docs(sources, tmp_path / "cache")
    ids = {s.id for s in d.sections} if d.load() is None else set()
    assert {"api:Widget", "api:Widget.status", "api:Widget.rotate", "api:Panel"} <= ids
    assert {"notes:setup.md > Setup", "notes:setup.md > Shortcuts", "notes:plain.txt"} <= ids
    assert {"guide:Guide", "guide:Reloading extensions", "guide:Ports"} <= ids
    assert not any("var a" in s.text or "Home | About" in s.text for s in d.sections)  # scripts, nav dropped
    assert "gone" in d.errors and "api" not in d.errors
    assert (tmp_path / "cache" / "guide.html").exists()  # fetched once, cached

    top = d.search("widget status")[0][1]
    assert top.id == "api:Widget.status"
    assert d.search("reload")[0][1].id == "guide:Reloading extensions"  # stem-free, but the title word matches
    assert d.search("inventory", source="notes")[0][1].id == "notes:setup.md > Shortcuts"
    assert d.search("zzzz") == []

    text, summary = answer(d, "", "api:Widget.status", "", 8)
    assert "working, idle or blocked" in text and "type: WidgetStatus" in text and summary == "read api:Widget.status"
    assert answer(d, "", "widget.status", "", 8)[0].startswith("[api:Widget.status]")  # titles match case-insensitively
    with pytest.raises(ValueError, match="Closest"):
        answer(d, "", "Widget.statsu", "", 8)
    listing, _ = answer(d, "", "", "", 8)
    assert "api (json" in listing and "UNAVAILABLE" in listing
    hits, _ = answer(d, "port", "", "", 3)
    assert "guide:Ports" in hits and "27015" in hits


def test_bad_source_configs():
    fill = lambda s: s
    with pytest.raises(ValueError, match="format"):
        parse_sources({"sources": [{"name": "a", "path": "x", "format": "pdf"}]}, fill)
    with pytest.raises(ValueError, match="exactly one"):
        parse_sources({"sources": [{"name": "a", "path": "x", "url": "http://y", "format": "html"}]}, fill)
    with pytest.raises(ValueError, match="twice"):
        parse_sources({"sources": [{"name": "a", "path": "x", "format": "html"}] * 2}, fill)


def test_tool_through_mcp(tmp_path):
    (tmp_path / "doc.md").write_text("# Saving\nUse the save menu.\n")
    host = tmp_path / "host.toml"
    host.write_text(textwrap.dedent("""
        [host]
        name = "plainapp"
        [docs]
        sources = [{ name = "manual", path = "{config_dir}/doc.md", format = "markdown" }]
    """))
    run = tmp_path / "run"
    log = EventLog(run / "events.jsonl", "t")
    mcp = build_server(log, "127.0.0.1", 0, host_config=HostConfig.load(host, run), workspace=run / "workspace",
                       archive_dir=run / "extensions")

    async def go():
        listing = await mcp.call_tool("read_docs", {})
        found = await mcp.call_tool("read_docs", {"query": "save"})
        return listing, found
    listing, found = anyio.run(go)
    text = lambda r: (r[0] if isinstance(r, tuple) else r)[0].text
    assert "manual (markdown, 1 sections)" in text(listing)
    assert "manual:doc.md" in text(found) or "manual:Saving" in text(found)
    calls = [json.loads(line) for line in (run / "events.jsonl").read_text().splitlines()]
    docs_calls = [c for c in calls if c.get("tool") == "read_docs"]
    assert [c["channel"] for c in docs_calls] == ["docs", "docs"]
    assert docs_calls[1]["summary"] == "1 results for 'save'"
