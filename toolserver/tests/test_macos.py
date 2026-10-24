"""macOS capture targeting and input translation. Pure logic, so this runs on any OS."""

from __future__ import annotations

import pytest

from toolserver import inputs as I
from toolserver.capture import Capture, NotFound, Region, Target
from toolserver.macos import MacBackend, _State, choose, translate

DISPLAYS = [(0.0, 0.0, 1512.0, 982.0), (1512.0, 0.0, 1920.0, 1080.0)]


def win(pid, owner, title, x, y, w, h, layer=0, num=1, exe=""):
    return {"kCGWindowOwnerPID": pid, "kCGWindowOwnerName": owner, "kCGWindowName": title,
            "kCGWindowLayer": layer, "kCGWindowNumber": num, "exe": exe or owner.lower(),
            "kCGWindowBounds": {"X": x, "Y": y, "Width": w, "Height": h}}


WINDOWS = [
    win(10, "Safari", "Minecraft Wiki", 0, 0, 1400, 900, num=1),
    win(20, "java", "Minecraft 1.21.1", 100, 50, 1280, 720, num=2),
    win(20, "java", "", 120, 60, 300, 200, num=3),            # a dialog of the same program
    win(20, "java", "", 0, 0, 1512, 25, layer=25, num=4),      # a menu-bar item: not a normal window
    win(30, "TextEdit", "Untitled", 200, 200, 600, 400, num=5),
]


def test_window_mode_matches_title_and_process():
    r = choose(WINDOWS, Target(title="Minecraft", process="java", mode="window"), DISPLAYS)
    assert (r.hwnd, r.ident, r.left, r.top, r.width, r.height) == (20, ("window", 2), 100, 50, 1280, 720)
    # Title alone would also match the browser tab; the process narrows it to the game.
    assert choose(WINDOWS, Target(title="minecraft", mode="window"), DISPLAYS).hwnd in (10, 20)
    assert choose(WINDOWS, Target(process="TextEdit", mode="window"), DISPLAYS).hwnd == 30


def test_process_mode_covers_all_normal_windows():
    r = choose(WINDOWS, Target(process="java", mode="process"), DISPLAYS)
    assert (r.left, r.top, r.width, r.height) == (100, 50, 1280, 720)  # dialog inside; layer-25 item excluded
    assert r.ident == ("process", 20)


def test_monitor_mode_and_missing_host():
    r = choose(WINDOWS, Target(process="java", mode="monitor"), DISPLAYS)
    assert (r.hwnd, r.left, r.top, r.width, r.height) == (20, 0, 0, 1512, 982)
    with pytest.raises(NotFound):
        choose(WINDOWS, Target(process="factorio", mode="window"), DISPLAYS)


def test_mouse_translation():
    st, vs = _State(), (0, 0, 3432, 1080)
    nx, ny = I.to_absolute(1000, 500, vs)
    ops = translate([("mouse", nx, ny, 0, I.MOUSEEVENTF_MOVE | I.MOUSEEVENTF_ABSOLUTE)], vs, st, 0.0)
    assert ops[0][0] == "move" and abs(ops[0][1] - 1000) < 0.1 and abs(ops[0][2] - 500) < 0.1
    # Press, drag, release: the move while held is a drag.
    ops = translate([("mouse", 0, 0, 0, I.MOUSEEVENTF_LEFTDOWN),
                     ("mouse", nx + 100, ny, 0, I.MOUSEEVENTF_MOVE | I.MOUSEEVENTF_ABSOLUTE),
                     ("mouse", 0, 0, 0, I.MOUSEEVENTF_LEFTUP)], vs, st, 1.0)
    assert [o[0] for o in ops] == ["button", "move", "button"] and ops[1][3] == "left"
    # A second click on the same spot soon after is a double click; later it isn't.
    st2 = _State()
    seq = [("mouse", 0, 0, 0, I.MOUSEEVENTF_LEFTDOWN), ("mouse", 0, 0, 0, I.MOUSEEVENTF_LEFTUP)]
    first = translate(seq, vs, st2, 5.0)
    second = translate(seq, vs, st2, 5.1)
    third = translate(seq, vs, st2, 9.0)
    assert [o[5] for o in first + second + third] == [1, 1, 2, 2, 1, 1]
    # Wheel: SendInput's unsigned 32-bit delta back to signed lines (positive = up).
    down = translate([("mouse", 0, 0, (-I.WHEEL_DELTA) & 0xFFFFFFFF, I.MOUSEEVENTF_WHEEL)], vs, st, 2.0)
    assert down == [("scroll", -1)]


def key_ev(name, up=False):
    scan, ext = I._KEYS[name]
    return I._key(scan, ext, up)


def test_key_translation_with_modifiers():
    st, vs = _State(), (0, 0, 100, 100)
    ops = translate([key_ev("cmd"), key_ev("c"), key_ev("c", True), key_ev("cmd", True)], vs, st, 0.0)
    assert ops == [("key", 0x37, True, 0x100000), ("key", 0x08, True, 0x100000),
                   ("key", 0x08, False, 0x100000), ("key", 0x37, False, 0)]
    # Extended scan codes are the arrows, plain ones the numpad.
    assert translate([key_ev("up")], vs, st, 0.0)[0][1] == 0x7E
    assert translate([key_ev("numpad8")], vs, st, 0.0)[0][1] == 0x5B
    uni = translate([("key", ord("é"), I.KEYEVENTF_UNICODE)], vs, st, 0.0)
    assert uni == [("unicode", "é", True, 0)]


def test_us_layout_characters():
    b = MacBackend.__new__(MacBackend)  # char_key needs no macOS
    assert b.char_key("a") == (*I._KEYS["a"], False)
    assert b.char_key("A") == (*I._KEYS["a"], True)
    assert b.char_key("!") == (*I._KEYS["1"], True)
    assert b.char_key("é") is None


def test_cmd_key_rules(monkeypatch):
    with pytest.raises(I.InputError, match="not allowed"):
        I.parse_keys("cmd+q")
    monkeypatch.setattr(I.sys, "platform", "darwin")
    assert [n for n, _, _ in I.parse_keys("command+shift+z")] == ["cmd", "shift", "z"]
    monkeypatch.setattr(I.sys, "platform", "win32")
    with pytest.raises(I.InputError, match="Windows key"):
        I.parse_keys("win+d")


class FakeMac:
    """MacBackend's logic with Quartz replaced by a recorder."""

    def __init__(self, region: Region) -> None:
        self.region, self.ops, self.st, self.t = region, [], _State(), 0.0

    def locate(self, target):
        return self.region

    def virtual_screen(self):
        return (0, 0, 3432, 1080)

    def focus(self, pid):
        return True

    def send(self, events):
        self.t += 0.01
        self.ops += translate(events, self.virtual_screen(), self.st, self.t)

    char_key = MacBackend.char_key

    def sleep(self, s):
        self.t += s


def test_controller_clicks_land_in_points(monkeypatch):
    monkeypatch.setattr(I.sys, "platform", "darwin")  # cmd is refused on Windows
    region = Region(20, ("window", 2), 100, 50, 1280, 720)
    fake = FakeMac(region)
    ctl = I.Controller(fake, Target(process="java"))
    ctl.capture = Capture(region, 1280, 720)  # what the last screenshot covered
    ctl.click(640, 360)
    moves = [o for o in fake.ops if o[0] == "move"]
    assert abs(moves[-1][1] - 740.5) < 1 and abs(moves[-1][2] - 410.5) < 1
    presses = [o for o in fake.ops if o[0] == "button"]
    assert [(o[1], o[2]) for o in presses] == [("left", True), ("left", False)]
    fake.ops.clear()
    ctl.key("cmd+s")
    assert [o[1:3] for o in fake.ops if o[0] == "key"] == [(0x37, True), (0x01, True), (0x01, False), (0x37, False)]
