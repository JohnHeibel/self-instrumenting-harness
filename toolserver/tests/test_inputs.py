"""Input tools against a fake backend that records the SendInput events. No game, any OS."""

from __future__ import annotations

import json
from pathlib import Path

import anyio
import pytest

from toolserver import inputs as I
from toolserver.capture import Capture, NotFound, Region, Shot, Target
from toolserver.events import EventLog
from toolserver.inputs import Controller, InputError

ABS = I.MOUSEEVENTF_MOVE | I.MOUSEEVENTF_ABSOLUTE | I.MOUSEEVENTF_VIRTUALDESK
SC = I.KEYEVENTF_SCANCODE
UP = I.KEYEVENTF_KEYUP
US_SHIFTED = {'!': '1', '@': '2', '#': '3', '$': '4', '%': '5', '^': '6', '&': '7', '*': '8',
              '(': '9', ')': '0', '_': '-', '+': '=', '{': '[', '}': ']', ':': ';', '"': "'",
              '~': '`', '|': '\\', '<': ',', '>': '.', '?': '/'}


TARGET = Target(title="Untitled - Notepad", process="notepad.exe")


class FakeBackend:
    """Records SendInput events; the host is a fixed region on a fixed desktop."""

    def __init__(self, rect=None, vscreen=(0, 0, 2560, 1440), hwnd=42) -> None:
        self.hwnd = hwnd
        self.rect = rect if rect is not None else {"left": 0, "top": 0, "width": 2560, "height": 1440}
        self.vscreen = vscreen
        self.not_found: str | None = None
        self.located: list[Target] = []
        self.events: list[tuple] = []
        self.timeline: list[tuple] = []   # events and sleeps, in order
        self.focused = 0
        self.focus_ok = True
        self.fail_after: int | None = None

    def locate(self, target):
        self.located.append(target)
        if self.not_found:
            raise NotFound(self.not_found)
        r = self.rect
        ident = "monitor" if target.mode == "monitor" else ("window", self.hwnd)
        return Region(self.hwnd, ident, r["left"], r["top"], r["width"], r["height"])

    def virtual_screen(self):
        return self.vscreen

    def focus(self, hwnd):
        self.focused += 1
        return self.focus_ok

    def send(self, events):
        for ev in events:
            if self.fail_after is not None and len(self.events) >= self.fail_after:
                self.fail_after = None
                raise InputError("simulated SendInput failure")
            self.events.append(ev)
            self.timeline.append(ev)

    def char_key(self, ch):
        if ch.isalpha() and ch.isascii():
            return I._KEYS[ch.lower()][0], False, ch.isupper()
        if ch in US_SHIFTED:
            return I._KEYS[US_SHIFTED[ch]][0], False, True
        if ch in I._KEYS and len(ch) == 1:
            return I._KEYS[ch][0], False, False
        return None

    def sleep(self, s):
        self.timeline.append(("sleep", s))


def region(width=2560, height=1440, left=0, top=0, hwnd=42) -> Region:
    return Region(hwnd, ("window", hwnd), left, top, width, height)


def cap(width=2560, height=1440, image=(1280, 720), left=0, top=0, hwnd=42) -> Capture:
    return Capture(region(width, height, left, top, hwnd), *image)


def ctl(backend=None, capture=None) -> tuple[Controller, FakeBackend]:
    b = backend or FakeBackend()
    c = Controller(b, TARGET)
    c.capture = capture if capture is not None else cap()
    return c, b


def keys_of(events):
    return [(scan, flags) for kind, scan, flags in (e for e in events if e[0] == "key")]


def mouse_flags(events):
    return [e[4] for e in events if e[0] == "mouse"]


# Coordinate mapping -------------------------------------------------------------------


@pytest.mark.parametrize("scale", [1.0, 1.25, 1.5, 2.0])
def test_mapping_follows_physical_capture_size_at_any_dpi_scale(scale):
    # A 1280x720 (logical) window at this DPI scale is this many physical pixels; the
    # agent always sees a 1280-wide image.
    w, h = round(1280 * scale), round(720 * scale)
    c = cap(w, h, image=(1280, 720), left=300, top=200)
    rect = region(w, h, 300, 200)
    assert I.to_screen(c, rect, 0, 0) == (300 + int(0.5 * scale), 200 + int(0.5 * scale))
    assert I.to_screen(c, rect, 640, 360) == (300 + int(640.5 * scale), 200 + int(360.5 * scale))
    sx, sy = I.to_screen(c, rect, 1279, 719)
    assert (sx, sy) == (300 + w - 1, 200 + h - 1)  # last image pixel -> last client pixel


def test_mapping_with_a_smaller_window_than_the_image_limit():
    c = cap(800, 600, image=(800, 600))  # no downscale: 1:1
    assert I.to_screen(c, region(800, 600, 10, 20), 799, 0) == (809, 20)


def test_window_moved_since_capture_uses_its_current_position():
    c, b = ctl(capture=cap(2560, 1440, left=0, top=0))
    b.rect = {"left": 100, "top": 50, "width": 2560, "height": 1440}
    b.vscreen = (0, 0, 2660 + 1, 1490 + 1)
    c.move(0, 0)
    nx, ny = b.events[0][1:3]
    assert (nx, ny) == I.to_absolute(101, 51, b.vscreen)


def test_absolute_coordinates_cover_a_virtual_desktop_with_a_monitor_left_of_primary():
    vs = (-1920, 0, 1920 + 2560, 1440)
    assert I.to_absolute(-1920, 0, vs) == (0, 0)
    assert I.to_absolute(2559, 1439, vs) == (65535, 65535)
    assert I.to_absolute(0, 0, vs) == (round(1920 * 65535 / 4479), 0)


def test_out_of_bounds_coordinates_fail_clearly():
    c, b = ctl()
    with pytest.raises(InputError, match=r"outside the last screenshot, which is 1280x720"):
        c.click(1280, 10)
    with pytest.raises(InputError, match="outside"):
        c.click(-1, 10)
    assert not b.events


# Window checks ------------------------------------------------------------------------


def test_needs_a_screenshot_first():
    c, b = ctl()
    c.capture = None
    with pytest.raises(InputError, match="take a screenshot first"):
        c.click(1, 1)


def test_host_missing_minimized_resized_or_replaced():
    c, b = ctl()
    b.not_found = "the host's window (process notepad.exe) is not open"
    with pytest.raises(InputError, match="is not open; input needs it on screen"):
        c.key("e")
    b.not_found = "the host's window (process notepad.exe) is minimized"
    with pytest.raises(InputError, match="minimized"):
        c.key("e")
    b.not_found = None
    b.rect = {"left": 0, "top": 0, "width": 1920, "height": 1080}
    with pytest.raises(InputError, match="changed size"):
        c.click(1, 1)
    c.key("e")  # keys don't need coordinates, so a size change doesn't block them
    b.rect = {"left": 0, "top": 0, "width": 2560, "height": 1440}
    b.hwnd = 43
    with pytest.raises(InputError, match="not the one in the last screenshot"):
        c.click(1, 1)
    assert all(t == TARGET for t in b.located)  # the host config's target, every time


def test_focus_failure_sends_nothing():
    c, b = ctl()
    b.focus_ok = False
    with pytest.raises(InputError, match="front"):
        c.key("e")
    assert not b.events


def test_repeated_focus_failures_mark_input_blocked_until_a_probe_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(I, "PROBE_S", 0.01)
    c, b = ctl()
    flag = tmp_path / "INPUT_BLOCKED"
    c.watch_blocked(flag)
    b.focus_ok = False
    for _ in range(I.BLOCK_AFTER - 1):
        with pytest.raises(InputError):
            c.key("e")
    assert not flag.exists()   # a failure or two isn't "blocked"
    with pytest.raises(InputError, match="pauses the run"):
        c.key("e")
    assert "detail" in json.loads(flag.read_text())
    b.focus_ok = True          # the probe notices and clears it
    c._probe.join(timeout=5)
    assert not flag.exists()
    c.key("e")
    assert b.events


def test_a_left_over_input_blocked_file_is_probed_on_start(tmp_path, monkeypatch):
    monkeypatch.setattr(I, "PROBE_S", 0.01)
    flag = tmp_path / "INPUT_BLOCKED"
    flag.write_text("{}")
    c, b = ctl()
    c.watch_blocked(flag)
    c._probe.join(timeout=5)
    assert not flag.exists()


def test_every_call_focuses_the_window_first():
    c, b = ctl()
    c.key("e")
    c.click(5, 5)
    assert b.focused == 2


# Mouse --------------------------------------------------------------------------------


def test_click_moves_settles_presses_and_releases():
    c, b = ctl()
    assert c.click(640, 360) == "Clicked left at (640, 360)"
    move, down, up = b.events
    assert move == ("mouse", *I.to_absolute(1281, 721, b.vscreen), 0, ABS)
    assert down == ("mouse", 0, 0, 0, I.MOUSEEVENTF_LEFTDOWN)
    assert up == ("mouse", 0, 0, 0, I.MOUSEEVENTF_LEFTUP)
    kinds = [t[0] for t in b.timeline]
    assert kinds == ["mouse", "sleep", "mouse", "sleep", "mouse"]  # hover, then a held press


def test_double_right_click():
    c, b = ctl()
    assert c.click(10, 10, button="right", double=True).startswith("Double-clicked right")
    assert mouse_flags(b.events)[1:] == [I.MOUSEEVENTF_RIGHTDOWN, I.MOUSEEVENTF_RIGHTUP] * 2


def test_shift_click_holds_shift_around_the_click():
    c, b = ctl()
    assert c.click(10, 10, modifiers="shift") == "Clicked shift+left at (10, 10)"
    ev = b.events
    assert ev[1] == ("key", 0x2A, SC)
    assert [e[4] for e in ev[2:4]] == [I.MOUSEEVENTF_LEFTDOWN, I.MOUSEEVENTF_LEFTUP]
    assert ev[4] == ("key", 0x2A, SC | UP)


def test_modifiers_reject_non_modifier_keys_and_bad_buttons():
    c, b = ctl()
    with pytest.raises(InputError, match="modifiers takes"):
        c.click(1, 1, modifiers="e")
    with pytest.raises(InputError, match="button must be"):
        c.click(1, 1, button="side")


def test_drag_presses_at_start_moves_in_steps_and_releases_at_end():
    c, b = ctl()
    c.drag(100, 100, 300, 200)
    flags = mouse_flags(b.events)
    assert flags[0] == ABS and flags[1] == I.MOUSEEVENTF_LEFTDOWN and flags[-1] == I.MOUSEEVENTF_LEFTUP
    moves = [e for e in b.events[2:-1]]
    assert len(moves) >= 5 and all(m[4] == ABS for m in moves)
    assert moves[-1][1:3] == I.to_absolute(601, 401, b.vscreen)


def test_scroll_sends_wheel_notches_with_sign():
    c, b = ctl()
    c.scroll(10, 10, -3)
    wheels = [e for e in b.events if e[4] == I.MOUSEEVENTF_WHEEL]
    assert [w[3] for w in wheels] == [(-120) & 0xFFFFFFFF] * 3
    with pytest.raises(InputError, match="whole number"):
        c.scroll(10, 10, 0)


# Keyboard -----------------------------------------------------------------------------


def test_single_key_is_a_scan_code_press():
    c, b = ctl()
    assert c.key("e") == "Pressed e"
    assert keys_of(b.events) == [(0x12, SC), (0x12, SC | UP)]


def test_combo_presses_in_order_and_releases_in_reverse():
    c, b = ctl()
    c.key("ctrl+shift+z")
    assert keys_of(b.events) == [(0x1D, SC), (0x2A, SC), (0x2C, SC),
                                 (0x2C, SC | UP), (0x2A, SC | UP), (0x1D, SC | UP)]


def test_extended_keys_and_aliases():
    c, b = ctl()
    c.key("Up")
    c.key("Escape")
    c.key("pgdn")
    ext = I.KEYEVENTF_EXTENDEDKEY
    assert keys_of(b.events) == [(0x48, SC | ext), (0x48, SC | ext | UP), (0x01, SC), (0x01, SC | UP),
                                 (0x51, SC | ext), (0x51, SC | ext | UP)]


def test_hold_ms_keeps_the_key_down():
    c, b = ctl()
    assert c.key("w", hold_ms=2000) == "Held w for 2000 ms"
    assert b.timeline[1] == ("sleep", 2.0)
    with pytest.raises(InputError, match="hold_ms"):
        c.key("w", hold_ms=60_000)


def test_unknown_and_blocked_keys():
    c, b = ctl()
    with pytest.raises(InputError, match="unknown key 'foo'"):
        c.key("ctrl+foo")
    for combo in ("alt+f4", "alt+tab", "ctrl+escape"):
        with pytest.raises(InputError, match="not allowed"):
            c.key(combo)
    assert not b.events


def test_keys_are_released_when_sending_fails_part_way():
    c, b = ctl()
    b.fail_after = 2  # ctrl down, shift down, then z down fails
    with pytest.raises(InputError, match="simulated"):
        c.key("ctrl+shift+z")
    assert keys_of(b.events) == [(0x1D, SC), (0x2A, SC), (0x2C, SC | UP), (0x2A, SC | UP), (0x1D, SC | UP)]


def test_type_uses_scan_codes_with_shift_and_unicode_fallback():
    c, b = ctl()
    assert c.type_text("Hi!\né") == "Typed 'Hi!\\né'"
    sh = 0x2A
    assert keys_of(b.events) == [
        (sh, SC), (0x23, SC), (0x23, SC | UP), (sh, SC | UP),      # H
        (0x17, SC), (0x17, SC | UP),                              # i
        (sh, SC), (0x02, SC), (0x02, SC | UP), (sh, SC | UP),      # !
        (0x1C, SC), (0x1C, SC | UP),                              # newline -> enter
        (ord("é"), I.KEYEVENTF_UNICODE), (ord("é"), I.KEYEVENTF_UNICODE | UP),
    ]


# Through the MCP server: logging, artifacts, set_goal ----------------------------------


REPO = Path(__file__).resolve().parents[2]
HOSTS = {"notepad": REPO / "hosts" / "notepad.toml", "factorio": REPO / "hosts" / "factorio.toml"}


def _server(tmp_path, host="notepad", backend=None):
    from toolserver.extensions import HostConfig
    from toolserver.server import build_server
    b = backend or FakeBackend()
    shot = Shot(b"PNG fake", b"JPEG fake", {"source": "window", "captured_size": [2560, 1440],
                                                         "image_size": [1280, 720]}, cap())
    log = EventLog(tmp_path / "events.jsonl", "t")
    mcp = build_server(log, "127.0.0.1", 0, host_config=HostConfig.load(HOSTS[host], tmp_path),
                       workspace=tmp_path / "ws", archive_dir=tmp_path / "ext",
                       io={"backend": b, "shoot": lambda: shot})
    return mcp, b


def _records(tmp_path):
    return [json.loads(l) for l in (tmp_path / "events.jsonl").read_text().splitlines()]


@pytest.mark.parametrize("host", ["notepad", "factorio"])
def test_tools_log_summaries_artifacts_and_goal(tmp_path, host):
    from toolserver.extensions import HostConfig
    mcp, b = _server(tmp_path, host)

    async def scenario():
        out = {}
        await mcp.call_tool("screenshot", {})
        out["click"] = await mcp.call_tool("click", {"x": 640, "y": 360, "modifiers": "shift"})
        out["key"] = await mcp.call_tool("key", {"keys": "e"})
        out["type"] = await mcp.call_tool("type", {"text": "iron"})
        out["goal"] = await mcp.call_tool("set_goal", {"text": "Automate iron", "serves": "Red science"})
        try:
            await mcp.call_tool("click", {"x": 5000, "y": 1})
        except Exception as e:
            out["bad"] = str(e)
        return out

    out = anyio.run(scenario)
    assert "Clicked shift+left at (640, 360)" in str(out["click"])
    assert "ok" in str(out["goal"])
    assert "outside the last screenshot" in out["bad"]

    recs = _records(tmp_path)
    calls = [r for r in recs if r["type"] == "tool_call"]
    assert [(c["tool"], c["channel"], c["ok"]) for c in calls] == [
        ("screenshot", "screenshot", True), ("click", "input", True), ("key", "input", True),
        ("type", "input", True), ("set_goal", "meta", True), ("click", "input", False)]
    shot = calls[0]
    assert shot["artifact"] == f"shots/{shot['seq']:06d}.jpg"
    assert (tmp_path / shot["artifact"]).read_bytes() == b"JPEG fake"
    assert shot["summary"] == "Screenshot of the program's window"
    assert calls[2]["summary"] == "Pressed e" and calls[3]["summary"] == "Typed 'iron'"
    assert calls[5]["summary"].startswith("click failed: (5000.0, 1.0) is outside")
    hc = HostConfig.load(HOSTS[host], tmp_path)
    assert b.located and all(t == Target(hc.window_title, hc.process, hc.capture) for t in b.located)
    goal = next(r for r in recs if r["type"] == "goal")
    assert (goal["writer"], goal["text"], goal["serves"]) == ("toolserver", "Automate iron", "Red science")
    assert [r["seq"] for r in recs] == list(range(1, len(recs) + 1))


def test_tool_descriptions_are_host_neutral(tmp_path):
    mcp, _ = _server(tmp_path)
    tools = {t.name: t for t in anyio.run(mcp.list_tools)}
    names = {"screenshot", "click", "drag", "move", "scroll", "key", "type", "set_goal"}
    assert names <= set(tools)
    assert all(tools[n].description for n in names)
    assert "last screenshot" in tools["click"].description
    for n in names:  # the generality rule: nothing assumes a game or names a host
        text = (tools[n].description + json.dumps(tools[n].inputSchema)).lower()
        assert not any(w in text for w in ("game", "factorio", "inventory", "craft")), n


def test_monitor_mode_with_no_window_named_sends_input_without_focusing():
    b = FakeBackend()
    b.hwnd = None
    c = Controller(b, Target(mode="monitor"))
    c.capture = Capture(Region(None, "monitor", 0, 0, 2560, 1440), 1280, 720)
    c.click(10, 10)
    assert b.focused == 0 and len(b.events) == 3


def test_process_mode_maps_through_the_bounding_box_of_its_windows():
    from toolserver.capture import union_rect
    main, dialog = (100, 100, 1200, 800), (1000, 50, 600, 400)
    box = union_rect([main, dialog])
    assert box == (100, 50, 1500, 850)
    r = Region(42, ("process", 7), *box)
    c = Capture(r, 1280, 725)
    # A click on the dialog's spot in the image lands on the dialog on screen.
    x, y = (1300 - 100) * 1280 / 1500, (200 - 50) * 725 / 850
    sx, sy = I.to_screen(c, r, x, y)
    assert abs(sx - 1300) <= 1 and abs(sy - 200) <= 1


def test_host_config_capture_fields(tmp_path):
    from toolserver.extensions import HostConfig
    hc = HostConfig.load(HOSTS["notepad"])
    assert (hc.process, hc.capture) == ("notepad.exe", "process")
    bad = tmp_path / "bad.toml"
    bad.write_text('[host]\nname = "x"\ncapture = "screen"\n')
    with pytest.raises(ValueError, match="capture must be one of window, process, monitor"):
        HostConfig.load(bad)
    assert HostConfig.load(None).capture == "window"


def test_a_lost_thumbnail_keeps_the_record(tmp_path):
    log = EventLog(tmp_path / "events.jsonl", "t")

    def boom(seq):
        raise OSError("disk full")
    log.write("tool_call", tool="screenshot", _artifact=boom)
    rec = _records(tmp_path)[0]
    assert rec["artifact"] is None and "disk full" in rec["artifact_error"]


# load_extension's description, generated from the host config ---------------------------

def test_load_extension_doc_lists_this_hosts_reload_actions_and_ports(tmp_path):
    from toolserver.extensions import HostConfig
    from toolserver.server import load_extension_doc
    cfg = tmp_path / "h.toml"
    cfg.write_text('[host]\nname = "h"\nagent_ports = [27015]\n[extensions]\ninstall_dir = "x"\n'
                   '[extensions.reload]\nnone = []\nrestart = ["echo"]\n')
    doc = load_extension_doc(HostConfig.load(cfg))
    assert 'reload actions: "none", "restart".' in doc
    assert "port(s) 27015" in doc and "{" + "reach}" not in doc
    bare = load_extension_doc(HostConfig())
    assert "no reload actions" in bare and "no extension folder" in bare and "up to you" in bare
    assert '"where"' not in doc


def test_agent_ports_must_be_port_numbers(tmp_path):
    from toolserver.extensions import HostConfig
    cfg = tmp_path / "h.toml"
    cfg.write_text('[host]\nagent_ports = ["27015"]\n')
    with pytest.raises(ValueError, match="agent_ports"):
        HostConfig.load(cfg)


def test_host_config_placeholders_and_reload_runs_from_the_config_dir(tmp_path):
    import asyncio
    import sys
    from types import SimpleNamespace
    from toolserver.extensions import Extensions, HostConfig
    cfg_dir = tmp_path / "hosts"
    cfg_dir.mkdir()
    cfg = cfg_dir / "h.toml"
    cfg.write_text('[host]\nup_check = ["{python}", "check.py", "{run_dir}"]\n'
                   'start = ["{python}", "ctl.py", "start", "--run-dir", "{run_dir}"]\n'
                   'notes = "state is in {run_dir}/out"\n'
                   '[extensions]\ninstall_dir = "{run_dir}/mods"\n'
                   '[extensions.reload]\nhere = ["{python}", "-c", "import os; print(os.getcwd())"]\n')
    run = tmp_path / "run"
    hc = HostConfig.load(cfg, run)
    assert hc.install_dir == run.resolve() / "mods" and hc.config_dir == cfg_dir.resolve()
    assert hc.up_check == [sys.executable, "check.py", str(run.resolve())]
    assert hc.start[-1] == str(run.resolve()) and hc.stop == []
    assert hc.notes == f"state is in {run.resolve()}/out"
    ok, out = asyncio.run(Extensions._reload(SimpleNamespace(host=hc, archive_dir=run / "extensions"), "here"))
    assert ok and out.strip() == str(cfg_dir.resolve())
    # While a reload runs, RELOADING in the run dir tells the runner the host may go down on purpose.
    run.mkdir(exist_ok=True)
    hc.reload_actions["peek"] = [sys.executable, "-c", f"import os; print(os.path.exists(r'{run / 'RELOADING'}'))"]
    ok, out = asyncio.run(Extensions._reload(SimpleNamespace(host=hc, archive_dir=run / "extensions"), "peek"))
    assert ok and out.strip() == "True" and not (run / "RELOADING").exists()
    with pytest.raises(ValueError, match="no run directory"):
        HostConfig.load(cfg)
    cfg.write_text('[host]\nstart = "ctl.py start"\n')
    with pytest.raises(ValueError, match="start must be a command"):
        HostConfig.load(cfg)


def test_install_as_cannot_take_a_folder_the_extension_does_not_own(tmp_path):
    from types import SimpleNamespace
    from toolserver.extensions import Extensions, HostConfig
    ext = object.__new__(Extensions)
    ext.host = HostConfig(install_dir=tmp_path / "mods")
    ext.archive_dir = tmp_path / "extensions"
    (tmp_path / "mods" / "placed-by-harness").mkdir(parents=True)
    (tmp_path / "src" / "p").mkdir(parents=True)

    def m(name, folder):
        return SimpleNamespace(name=name, in_host={"source": "p", "install_as": folder})
    with pytest.raises(ValueError, match="isn't one of your extensions"):
        ext._check_install_as(m("probe", "placed-by-harness"))
    ext._check_install_as(m("probe", "probe-mod"))
    ext._install(m("probe", "probe-mod"), tmp_path / "src")
    ext._check_install_as(m("probe", "probe-mod"))            # its own folder: fine to replace
    with pytest.raises(ValueError, match="already used by your extension 'probe'"):
        ext._check_install_as(m("other", "PROBE-MOD"))
