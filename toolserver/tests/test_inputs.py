"""Input tools against a fake backend that records the SendInput events. No game, any OS."""

from __future__ import annotations

import json

import anyio
import pytest

from toolserver import inputs as I
from toolserver.capture import Capture, Shot
from toolserver.events import EventLog
from toolserver.inputs import Controller, InputError

ABS = I.MOUSEEVENTF_MOVE | I.MOUSEEVENTF_ABSOLUTE | I.MOUSEEVENTF_VIRTUALDESK
SC = I.KEYEVENTF_SCANCODE
UP = I.KEYEVENTF_KEYUP
US_SHIFTED = {'!': '1', '@': '2', '#': '3', '$': '4', '%': '5', '^': '6', '&': '7', '*': '8',
              '(': '9', ')': '0', '_': '-', '+': '=', '{': '[', '}': ']', ':': ';', '"': "'",
              '~': '`', '|': '\\', '<': ',', '>': '.', '?': '/'}


class FakeBackend:
    """Records SendInput events; a window with a fixed client rect on a fixed desktop."""

    def __init__(self, rect=None, vscreen=(0, 0, 2560, 1440), hwnd=42) -> None:
        self.hwnd = hwnd
        self.rect = rect if rect is not None else {"left": 0, "top": 0, "width": 2560, "height": 1440}
        self.vscreen = vscreen
        self.events: list[tuple] = []
        self.timeline: list[tuple] = []   # events and sleeps, in order
        self.focused = 0
        self.focus_ok = True
        self.fail_after: int | None = None

    def find_window(self, title):
        return self.hwnd

    def client_rect(self, hwnd):
        return self.rect

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


def cap(width=2560, height=1440, image=(1280, 720), left=0, top=0, hwnd=42) -> Capture:
    return Capture(hwnd, left, top, width, height, *image)


def ctl(backend=None, capture=None) -> tuple[Controller, FakeBackend]:
    b = backend or FakeBackend()
    c = Controller(b, "Factorio")
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
    rect = {"left": 300, "top": 200, "width": w, "height": h}
    assert I.to_screen(c, rect, 0, 0) == (300 + int(0.5 * scale), 200 + int(0.5 * scale))
    assert I.to_screen(c, rect, 640, 360) == (300 + int(640.5 * scale), 200 + int(360.5 * scale))
    sx, sy = I.to_screen(c, rect, 1279, 719)
    assert (sx, sy) == (300 + w - 1, 200 + h - 1)  # last image pixel -> last client pixel


def test_mapping_with_a_smaller_window_than_the_image_limit():
    c = cap(800, 600, image=(800, 600))  # no downscale: 1:1
    assert I.to_screen(c, {"left": 10, "top": 20, "width": 800, "height": 600}, 799, 0) == (809, 20)


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


def test_window_missing_minimized_resized_or_replaced():
    c, b = ctl()
    b.hwnd = None
    with pytest.raises(InputError, match="is not open"):
        c.key("e")
    b.hwnd = 42
    b.rect = None
    with pytest.raises(InputError, match="minimized"):
        c.key("e")
    b.rect = {"left": 0, "top": 0, "width": 1920, "height": 1080}
    with pytest.raises(InputError, match="resized"):
        c.click(1, 1)
    c.key("e")  # keys don't need coordinates, so a resize doesn't block them
    b.rect = {"left": 0, "top": 0, "width": 2560, "height": 1440}
    b.hwnd = 43
    with pytest.raises(InputError, match="changed since the last screenshot"):
        c.click(1, 1)


def test_focus_failure_sends_nothing():
    c, b = ctl()
    b.focus_ok = False
    with pytest.raises(InputError, match="front"):
        c.key("e")
    assert not b.events


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


def _server(tmp_path, backend=None):
    from toolserver.server import build_server
    b = backend or FakeBackend()
    controller = Controller(b, "Factorio")
    shot = Shot(b"\x89PNG fake", b"\xff\xd8 fake jpeg", {"source": "window", "captured_size": [2560, 1440],
                                                         "image_size": [1280, 720]}, cap())
    log = EventLog(tmp_path / "events.jsonl", "t")
    mcp = build_server(log, "127.0.0.1", 0, io={"controller": controller, "shoot": lambda: shot})
    return mcp, b


def _records(tmp_path):
    return [json.loads(l) for l in (tmp_path / "events.jsonl").read_text().splitlines()]


def test_tools_log_summaries_artifacts_and_goal(tmp_path):
    mcp, b = _server(tmp_path)

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
    assert (tmp_path / shot["artifact"]).read_bytes() == b"\xff\xd8 fake jpeg"
    assert shot["summary"] == "Screenshot of the game window"
    assert calls[2]["summary"] == "Pressed e" and calls[3]["summary"] == "Typed 'iron'"
    assert calls[5]["summary"].startswith("click failed: (5000.0, 1.0) is outside")
    goal = next(r for r in recs if r["type"] == "goal")
    assert (goal["writer"], goal["text"], goal["serves"]) == ("toolserver", "Automate iron", "Red science")
    assert [r["seq"] for r in recs] == list(range(1, len(recs) + 1))


def test_input_tools_are_listed_with_descriptions(tmp_path):
    mcp, _ = _server(tmp_path)
    tools = {t.name: t for t in anyio.run(mcp.list_tools)}
    assert {"screenshot", "click", "drag", "move", "scroll", "key", "type", "set_goal"} <= set(tools)
    assert all(tools[n].description for n in ("click", "drag", "key", "type", "set_goal"))
    assert "last screenshot" in tools["click"].description


def test_a_lost_thumbnail_keeps_the_record(tmp_path):
    log = EventLog(tmp_path / "events.jsonl", "t")

    def boom(seq):
        raise OSError("disk full")
    log.write("tool_call", tool="screenshot", _artifact=boom)
    rec = _records(tmp_path)[0]
    assert rec["artifact"] is None and "disk full" in rec["artifact_error"]
