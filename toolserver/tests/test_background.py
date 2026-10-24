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
        second = await s.call_tool("run_script", {"code": 'print("second")', "background": True})
        assert not second.isError and text(second).startswith("queued t2"), text(second)   # waits for the hands
        # A reading task can run beside it, but can't act.
        reader = 'call("probe_read")\ntry:\n    call("probe_act")\nexcept ToolFailed:\n    print("read only")\n'
        assert text(await s.call_tool("run_script", {"code": reader, "background": True,
                                                     "hands": False})).startswith("started t3")
        assert "t3: ok: read only" in text(await s.call_tool("wait", {"on": ["t3"], "timeout_s": 20}))
        with anyio.fail_after(30):
            stopped = await s.call_tool("task", {"id": "t1", "stop": True})
        assert "t1 code: stopped" in text(stopped), text(stopped)
        assert "t2: ok: second" in text(await s.call_tool("wait", {"on": ["t2"], "timeout_s": 20}))
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


def test_queue_runs_in_order_and_holds_what_follows_a_failure(server):
    ws = server["ws"]
    m = ext_with_reader_and_actor(ws)
    step = 'import time\ncall("probe_act", q=args["q"])\ntime.sleep(0.5)\nprint("done", args["q"])\n'

    def bg(**kw):
        return {"code": step, "background": True, **kw}

    async def body(s, changes):
        assert not (await s.call_tool("load_extension", {"manifest": m})).isError
        first = text(await s.call_tool("run_script", bg(args={"q": "a"})))
        assert first.startswith("started t1") and 'after="t1"' in first, first
        q2 = await s.call_tool("run_script", bg(args={"q": "b"}, after="t1"))
        assert text(q2).startswith("queued t2") and "when t1 ends ok" in text(q2), text(q2)
        bad = await s.call_tool("run_script", {"code": "raise SystemExit(3)", "background": True, "after": "t2"})
        assert text(bad).startswith("queued t3")
        assert text(await s.call_tool("run_script", bg(args={"q": "d"}, after="t3"))).startswith("queued t4")
        assert text(await s.call_tool("run_script", bg(args={"q": "e"}, after="t4"))).startswith("queued t5")
        st = text(await s.call_tool("status", {}))
        assert "t2 code: queued after t1" in st and "t4 code: queued after t3" in st, st
        polled = text(await s.call_tool("task", {"id": "t4"}))
        assert 'wait(on=["t4"])' in polled and 'after="t4"' in polled, polled
        seen = ""
        with anyio.fail_after(40):
            while "Held t4, t5" not in seen:
                seen += text(await s.call_tool("wait", {"on": ["t1", "t2", "t3"], "timeout_s": 20}))
        assert "t1: ok: done a" in seen and "t2: ok: done b" in seen and "t3: failed" in seen, seen
        assert "t3 code: failed" in text(await s.call_tool("task", {"id": "t3"}))
        assert "t4 code: held (t3 ended failed)" in text(await s.call_tool("task", {"id": "t4"}))
        late = await s.call_tool("run_script", bg(after="t3"))
        assert late.isError and "already ended failed" in text(late)
        # Fix it, and run the held chain after the fix.
        assert text(await s.call_tool("run_script", bg(args={"q": "fix"}))).startswith("started t6")
        resumed = text(await s.call_tool("task", {"id": "t4", "resume": True, "after": "t6"}))
        assert "t4 code: queued after t6" in resumed and "t5 code: queued after t4" in resumed, resumed
        seen = ""
        with anyio.fail_after(40):
            while "t5: ok" not in seen:
                seen += text(await s.call_tool("wait", {"on": ["t4", "t5", "t6"], "timeout_s": 20}))
        assert seen.index("t6: ok") < seen.index("t4: ok") < seen.index("t5: ok"), seen
        # A plan that no longer fits: dropping a held task drops what's behind it.
        await s.call_tool("run_script", {"code": "raise SystemExit(1)", "background": True})            # t7
        await s.call_tool("run_script", bg(args={"q": "x"}, after="t7"))                                 # t8
        await s.call_tool("run_script", bg(args={"q": "y"}, after="t8"))                                 # t9
        with anyio.fail_after(20):
            while "held" not in text(await s.call_tool("task", {"id": "t9"})):
                await anyio.sleep(0.2)
        dropped = text(await s.call_tool("task", {"id": "t8", "stop": True}))
        assert "t8 code: stopped" in dropped and "t9 code: stopped" in dropped, dropped

    anyio.run(_session, server["url"], body)
    ev = events(server["run"])
    started = [e["task"] for e in ev if e["type"] == "task_started"]
    assert started[:3] == ["t1", "t2", "t3"] and "t8" not in started and "t9" not in started
    assert [e["task"] for e in ev if e["type"] == "task_held"][:2] == ["t4", "t5"]
    assert [e["task"] for e in ev if e["type"] == "task_resumed"] == ["t4", "t5"]


def test_idle_note_after_a_while_without_acting(tmp_path):
    from toolserver import tasks as tm
    h = tm.Hands()
    w = tm.Work(None, None, tmp_path, h, None, lambda: "", lambda: [])
    assert w.idle_note() == ""                       # just started
    h.last_busy -= tm.IDLE_NOTE_AFTER_S + 30
    note = w.idle_note()
    assert note.startswith("[hands idle 2.0 min") and "background=True" in note, note
    assert w.idle_note() == ""                       # not again right away
    h.check(None)                                    # acting resets the clock
    assert h.idle_s() < 1


def test_stalled_task_holding_the_hands_is_reported_once_per_kind(tmp_path):
    from toolserver import tasks as tm

    class Log:
        def __init__(self):
            self.rows = []

        def write(self, typ, **kw):
            self.rows.append((typ, kw))

    class Board:
        def __init__(self):
            self.posts = []

        def post(self, source, text, urgent=False):
            self.posts.append((source, text, urgent))

    log, board = Log(), Board()
    w = tm.Work(None, log, tmp_path, tm.Hands(), board, lambda: "", lambda: [])
    t = tm.Task(id="t1", name="build", hands=True, started=1000.0, deadline_s=600, wake_on_end=False)
    w.tasks["t1"] = t
    for i in range(tm.STALL_REPEAT - 1):
        w.watch_call(t, "act", {"op": "take", "n": 1}, acts=True, now=1001.0 + i)
        w.watch_call(t, "look", {}, acts=False, now=1001.5 + i)
    assert board.posts == []
    w.watch_call(t, "act", {"n": 1, "op": "take"}, acts=True, now=1020.0)    # same call, keys reordered
    assert len(board.posts) == 1 and "same acting call (10 times" in board.posts[0][1] and board.posts[0][2]
    w.watch_call(t, "act", {"op": "take", "n": 1}, acts=True, now=1021.0)
    assert len(board.posts) == 1                                               # once per kind
    for i in range(5):
        w.watch_call(t, "look", {}, acts=False, now=1030.0 + i)
    assert len(board.posts) == 1                                               # reading, but not for long
    w.stall_check(now=1021.0 + tm.STALL_NO_ACT_S + 1)
    assert len(board.posts) == 2 and "no acting call for 2.0 min (5 reads since)" in board.posts[1][1]
    assert [kw["kind"] for typ, kw in log.rows if typ == "task_stalled"] == ["repeat", "quiet"]
    assert "the most repeated 11x" in w.describe("t1")


def test_waiting_on_a_task_ends_early_on_an_urgent_notice(tmp_path):
    import asyncio
    from toolserver import tasks as tm

    class Log:
        def write(self, *a, **kw):
            pass

    async def go():
        board = tm.Board(Log(), tmp_path)
        job = asyncio.ensure_future(asyncio.sleep(30))
        loop = asyncio.get_running_loop()
        loop.call_later(0.05, board.post, "t9", "plain")              # not urgent: keeps waiting
        loop.call_later(0.15, board.post, "t2", "stuck", True)        # urgent: ends the wait
        t0 = loop.time()
        assert await board.until_done_or_urgent(job, 10) is False
        assert 0.1 < loop.time() - t0 < 2
        board.mark_seen(board.unseen())
        assert await board.until_done_or_urgent(asyncio.ensure_future(asyncio.sleep(0.01)), 10) is True
        loop.call_later(0.05, board.post, "t9", "plain again")
        loop.call_later(0.1, board.post, "t2", "again", True)
        assert [n.text for n in await board.wait(["t7"], 5)] == ["again"]   # urgent ends a wait on others
        job.cancel()

    asyncio.run(go())


def test_the_same_failure_twice_gets_an_urgent_nudge(tmp_path):
    import asyncio
    from toolserver import tasks as tm

    class Log:
        def __init__(self):
            self.rows = []

        def write(self, typ, **kw):
            self.rows.append(typ)

    class Ext:
        def hook_info(self, name):
            return None

    async def go():
        log = Log()
        board = tm.Board(log, tmp_path)
        w = tm.Work(Ext(), log, tmp_path, tm.Hands(), board, lambda: "", lambda: [])
        for i, pos in enumerate(("1.5 ab12cd34ef", "7.25 0f9e8d7c6b", "3 deadbeef00")):
            t = w.start(f"raise Exception('No path to target at x={pos}')", "", "walker", None, 1, True, False)
            await t.job
        texts = [n.text for n in board.items if n.source.startswith("t")]
        assert "same error" not in texts[0] and "2nd time walker failed" in texts[1] and "3rd time" in texts[2]
        assert board.items[1].urgent and log.rows.count("task_repeat_failure") == 2

    asyncio.run(go())


def test_quiet_stall_uses_the_programs_own_action_counts(tmp_path):
    import json
    from datetime import datetime, timezone
    from toolserver import tasks as tm

    class Log:
        def write(self, *a, **kw):
            pass

    class Board:
        def __init__(self):
            self.posts = []

        def post(self, source, text, urgent=False):
            self.posts.append(text)

    def counts(ts):
        with (tmp_path / "grader.jsonl").open("a") as f:
            f.write(json.dumps({"ts": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
                                "type": "program_actions", "count": 2}) + "\n")

    board = Board()
    w = tm.Work(None, Log(), tmp_path, tm.Hands(), board, lambda: "", lambda: [])
    t0 = datetime.now(timezone.utc).timestamp()
    t = tm.Task(id="t1", name="direct", hands=True, started=t0, deadline_s=600, wake_on_end=False)
    w.tasks["t1"] = t
    w.stall_check(now=t0 + 300)
    assert board.posts == []                     # no calls through the harness and no counts: can't tell
    counts(t0 + 250)                             # the program says things are happening
    w._prog["checked"] = 0
    w.stall_check(now=t0 + 300)
    assert board.posts == []
    w._prog["checked"] = 0
    with (tmp_path / "grader.jsonl").open("a") as f:      # walking, moving items: busy, not actions
        f.write(json.dumps({"ts": datetime.fromtimestamp(t0 + 350, timezone.utc).isoformat(),
                            "type": "program_activity", "kinds": {"move": 30}}) + "\n")
    w.stall_check(now=t0 + 250 + tm.STALL_NO_ACT_S + 1)
    assert board.posts == []
    w._prog["checked"] = 0
    w.stall_check(now=t0 + 350 + tm.STALL_NO_ACT_S + 1)
    assert len(board.posts) == 1 and "the program saw no player activity" in board.posts[0]


def test_notices_the_runner_delivered_in_a_wake_prompt_count_as_seen(tmp_path):
    import json
    from toolserver import tasks as tm

    class Log:
        def write(self, *a, **kw):
            pass

    board = tm.Board(Log(), tmp_path)
    board.post("t2", "stuck", urgent=True)
    items = json.loads((tmp_path / "WAKE").read_text())
    (tmp_path / "WAKE").unlink()                                          # the runner took it...
    (tmp_path / "WAKE_TAKEN").write_text(json.dumps([items[0]["key"]]))   # ...and delivered it
    board.post("t9", "plain")
    assert not (tmp_path / "WAKE").exists()          # not written again for what was delivered
    assert "stuck" not in board.footer()
    board.post("t3", "new trouble", urgent=True)
    assert [i["text"] for i in json.loads((tmp_path / "WAKE").read_text())] == ["new trouble"]


def test_a_long_failure_keeps_its_start_and_end():
    from toolserver import tasks as tm
    long = "RuntimeError: {'state': " + "x" * 2000 + "}, 'error': 'blocked at 3,4 by tree'"
    s = tm._ends(long, "t7")
    assert s.startswith("RuntimeError: {'state':") and "blocked at 3,4 by tree" in s
    assert 'task("t7") shows all' in s and len(s) < 400
    assert tm._ends("short error", "t7") == "short error"
