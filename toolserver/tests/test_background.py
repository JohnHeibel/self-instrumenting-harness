"""Background tasks, the hands lock, notices, wait, rules and status, end to end over real HTTP.

Same setup as test_dynamic_tools.py: no program, model or Docker; tools run locally.
"""

from __future__ import annotations

import textwrap

import anyio

from test_dynamic_tools import _session, events, server, tool, write_ext  # noqa: F401  (server is a fixture)


def text(res) -> str:
    return "\n".join(getattr(c, "text", "") for c in res.content)


def ext_with_reader_and_actor(ws) -> str:
    return write_ext(ws, "probe_kit", [tool("probe_act"), tool("probe_read", read_only=True)],
                     mod_text=None, reload=None)


def test_background_task_runs_reports_and_ends_with_a_notice(server):
    ws = server["ws"]
    m = ext_with_reader_and_actor(ws)
    code = textwrap.dedent("""
        import time
        for i in range(3):
            call("probe_act", q=str(i))
            log("did", i)
        notify("halfway")
        time.sleep(1.5)
        print("built 3")
    """)

    async def body(s, changes):
        assert not (await s.call_tool("load_extension", {"manifest": m})).isError
        started = await s.call_tool("run_script", {"code": code, "background": True, "minutes": 2})
        assert not started.isError and text(started).startswith("started t1"), text(started)
        seen = ""
        with anyio.fail_after(10):
            while "did 2" not in seen:
                seen += text(await s.call_tool("task", {"id": "t1"}))
                await anyio.sleep(0.2)
        with anyio.fail_after(20):
            while "ok: built 3" not in seen:      # notices arrive on footers or from wait, once each
                seen += text(await s.call_tool("wait", {"on": ["t1"], "timeout_s": 20}))
        assert "t1: halfway" in seen and seen.count("halfway") == 1, seen
        done = await s.call_tool("task", {"id": "t1"})
        assert "t1 code: ok" in text(done) and "built 3" in text(done)
        listing = await s.call_tool("task", {})
        assert "t1 code: ok" in text(listing)

    anyio.run(_session, server["url"], body)
    ev = events(server["run"])
    assert [e["type"] for e in ev if e["type"].startswith("task_")] == ["task_started", "task_ended"]
    inner = [e for e in ev if e["type"] == "tool_call" and e.get("via") == "task:t1"]
    assert len(inner) == 3 and inner[0]["hook"]["extension"] == "probe_kit"
    assert [e["text"] for e in ev if e["type"] == "notice"] == ["halfway", "ok: built 3"]


def test_one_pair_of_hands(server):
    ws = server["ws"]
    m = ext_with_reader_and_actor(ws)
    busy = 'import time\ncall("probe_act", q="start")\ntime.sleep(60)\n'
    fg = textwrap.dedent("""
        out = [call("probe_read", q="r")["echo"]["q"]]
        try:
            call("probe_act", q="a")
        except ToolFailed as e:
            out.append("refused" if "hands are busy" in str(e) else str(e))
        print(out)
    """)

    async def body(s, changes):
        assert not (await s.call_tool("load_extension", {"manifest": m})).isError
        assert text(await s.call_tool("run_script", {"code": busy, "background": True})).startswith("started t1")
        refused = await s.call_tool("probe_act", {"q": "x"})
        assert refused.isError and "t1" in text(refused) and "hands are busy" in text(refused)
        assert not (await s.call_tool("probe_read", {"q": "x"})).isError
        res = await s.call_tool("run_script", {"code": fg})
        assert "['r', 'refused']" in text(res), text(res)
        second = await s.call_tool("run_script", {"code": busy, "background": True})
        assert second.isError and "hands are busy" in text(second)
        # A reading task can run beside it, but can't act.
        reader = 'call("probe_read")\ntry:\n    call("probe_act")\nexcept ToolFailed:\n    print("read only")\n'
        assert text(await s.call_tool("run_script", {"code": reader, "background": True,
                                                     "hands": False})).startswith("started t2")
        assert "t2: ok: read only" in text(await s.call_tool("wait", {"on": ["t2"], "timeout_s": 20}))
        with anyio.fail_after(30):
            stopped = await s.call_tool("task", {"id": "t1", "stop": True})
        assert "t1 code: stopped" in text(stopped), text(stopped)
        assert not (await s.call_tool("probe_act", {"q": "after"})).isError

    anyio.run(_session, server["url"], body)


def test_notices_ride_on_the_next_result_and_wake_writes_the_wake_file(server):
    run = server["run"]

    async def body(s, changes):
        await s.call_tool("run_script", {"code": 'notify("quiet one")\nnotify("loud one", wake=True)',
                                         "background": True, "hands": False})
        with anyio.fail_after(10):
            while not (run / "WAKE").exists():
                await anyio.sleep(0.1)
        await anyio.sleep(0.5)
        res = await s.call_tool("list_hooks", {})
        footer = res.content[-1].text
        assert footer.startswith("[notices:") and "quiet one" in footer and "loud one" in footer, footer
        assert not (run / "WAKE").exists()          # delivered, so the runner needn't wake the agent
        again = await s.call_tool("list_hooks", {})
        assert not any("[notices:" in getattr(c, "text", "") for c in again.content)

    anyio.run(_session, server["url"], body)


def test_rules_tell_and_run_a_reflex_script(server):
    ws = server["ws"]
    m = ext_with_reader_and_actor(ws)
    reflex = textwrap.dedent('''\
        """Fix the thing a rule saw."""
        def main(event="", **_):
            call("probe_act", q="fix")
            import os
            os.remove("flag")
            return f"fixed after: {event}"
    ''')
    check = 'import os\nif os.path.exists("flag"):\n    print("flag is up")\n'

    async def body(s, changes):
        assert not (await s.call_tool("load_extension", {"manifest": m})).isError
        (ws / "flag").write_text("1")
        kept = await s.call_tool("run_script", {"code": reflex, "keep": "fix_flag"})
        assert not kept.isError, text(kept)            # the first run removed the flag
        assert not (ws / "flag").exists()
        bad = await s.call_tool("watch", {"name": "flagwatch", "check": check, "do": "run:nope"})
        assert bad.isError
        res = await s.call_tool("watch", {"name": "flagwatch", "check": check, "every_s": 1,
                                          "do": "run:fix_flag", "cooldown_s": 0})
        assert not res.isError and "rule set" in text(res), text(res)
        assert "flagwatch" in text(await s.call_tool("watch", {}))
        (ws / "flag").write_text("1")
        got = await s.call_tool("wait", {"on": ["flagwatch"], "timeout_s": 20})
        assert "flag is up -> started t1 (fix_flag)" in text(got), text(got)
        got = await s.call_tool("wait", {"on": ["t1"], "timeout_s": 20})
        assert "fixed after: flag is up" in text(got), text(got)
        assert not (ws / "flag").exists()
        # A rule that only tells, once.
        res = await s.call_tool("watch", {"name": "always", "check": "print('hello')", "every_s": 1, "times": 1})
        assert not res.isError
        got = await s.call_tool("wait", {"on": ["always"], "timeout_s": 20})
        assert "always: hello" in text(got)
        await anyio.sleep(0.5)
        assert "always" not in text(await s.call_tool("watch", {}))     # removed after one firing
        # A check may only read.
        await s.call_tool("watch", {"name": "pushy", "check": "call('probe_act')", "every_s": 1})
        got = await s.call_tool("wait", {"on": ["pushy"], "timeout_s": 20})
        assert "check failed" in text(got) and "may only read" in text(got), text(got)
        assert not (await s.call_tool("watch", {"name": "pushy", "remove": True})).isError

    anyio.run(_session, server["url"], body)
    ev = events(server["run"])
    fired = [e for e in ev if e["type"] == "rule_fired"]
    assert fired[0]["rule"] == "flagwatch" and fired[0]["do"] == "run:fix_flag"
    assert any(e["type"] == "rule_removed" and e["rule"] == "always" for e in ev)


def test_status_says_where_things_stand(server):
    ws = server["ws"]
    (ws / "NOTES.md").write_text("# Notes\n## Now\nbuilding\n## Next\n")

    async def body(s, changes):
        await s.call_tool("set_goal", {"text": "build the line", "serves": "the goal"})
        await s.call_tool("run_script", {"code": 'notify("unseen")', "background": True, "hands": False})
        await s.call_tool("wait", {"on": [], "timeout_s": 0.01})   # nothing yet, or the notice
        st = text(await s.call_tool("status", {}))
        assert "Goal: build the line (serves: the goal)" in st
        assert "Hands: free" in st and "NOTES.md: # Notes | ## Now | ## Next" in st, st

    anyio.run(_session, server["url"], body)
