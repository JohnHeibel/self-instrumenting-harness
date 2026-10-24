"""Screen capture targeting and input on macOS, behind the same interfaces as the Win32 code.

`locate` finds the host per the host config (`capture.Target`) in macOS's window list.
`MacBackend` implements `inputs.Backend`: it takes the same SendInput-shaped event tuples
the Controller builds and posts them as Quartz events.

Units: everything here is in macOS points (global display coordinates, origin at the top
left of the main display). `grab` captures a region given in points and returns an image
at the display's pixel density, and the Controller maps image pixels proportionally, so
Retina scaling needs no special handling.

Needs two permissions for the process running the tool server (System Settings > Privacy
& Security): Screen Recording (to capture, and to read window titles) and Accessibility
(to post input).

The translation from events to Quartz calls (`translate`) and the window choice
(`choose`) are pure functions, tested without macOS (tests/test_macos.py).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .capture import NotFound, Region, Target, union_rect

# Windows scan code set 1 (scan, extended) -> macOS virtual key code (Carbon kVK_*, ANSI layout).
_MAC_KEYS: dict[str, int] = {
    "a": 0x00, "s": 0x01, "d": 0x02, "f": 0x03, "h": 0x04, "g": 0x05, "z": 0x06, "x": 0x07, "c": 0x08,
    "v": 0x09, "b": 0x0B, "q": 0x0C, "w": 0x0D, "e": 0x0E, "r": 0x0F, "y": 0x10, "t": 0x11, "1": 0x12,
    "2": 0x13, "3": 0x14, "4": 0x15, "6": 0x16, "5": 0x17, "=": 0x18, "9": 0x19, "7": 0x1A, "-": 0x1B,
    "8": 0x1C, "0": 0x1D, "]": 0x1E, "o": 0x1F, "u": 0x20, "[": 0x21, "i": 0x22, "p": 0x23, "l": 0x25,
    "j": 0x26, "'": 0x27, "k": 0x28, ";": 0x29, "\\": 0x2A, ",": 0x2B, "/": 0x2C, "n": 0x2D, "m": 0x2E,
    ".": 0x2F, "`": 0x32,
    "enter": 0x24, "tab": 0x30, "space": 0x31, "backspace": 0x33, "esc": 0x35, "cmd": 0x37,
    "shift": 0x38, "capslock": 0x39, "alt": 0x3A, "ctrl": 0x3B, "rshift": 0x3C, "ralt": 0x3D,
    "rctrl": 0x3E, "delete": 0x75, "insert": 0x72, "home": 0x73, "end": 0x77, "pageup": 0x74,
    "pagedown": 0x79, "left": 0x7B, "right": 0x7C, "down": 0x7D, "up": 0x7E,
    "f1": 0x7A, "f2": 0x78, "f3": 0x63, "f4": 0x76, "f5": 0x60, "f6": 0x61, "f7": 0x62, "f8": 0x64,
    "f9": 0x65, "f10": 0x6D, "f11": 0x67, "f12": 0x6F,
    "numpad0": 0x52, "numpad1": 0x53, "numpad2": 0x54, "numpad3": 0x55, "numpad4": 0x56,
    "numpad5": 0x57, "numpad6": 0x58, "numpad7": 0x59, "numpad8": 0x5B, "numpad9": 0x5C,
    "numpad.": 0x41, "numpad*": 0x43, "numpad+": 0x45, "numpad-": 0x4E, "numpad/": 0x4B,
    "numpadenter": 0x4C,
}
# Quartz event flags for held modifiers (CGEventFlags).
_FLAGS = {"shift": 0x20000, "rshift": 0x20000, "ctrl": 0x40000, "rctrl": 0x40000,
          "alt": 0x80000, "ralt": 0x80000, "cmd": 0x100000}
# US layout: characters typed with shift, and the key they're on.
_SHIFTED = dict(zip('~!@#$%^&*()_+{}|:"<>?', "`1234567890-=[]\\;',./"))

DOUBLE_CLICK_S = 0.5


def _scan_names() -> dict[tuple[int, bool], str]:
    from .inputs import _KEYS
    return {v: k for k, v in reversed(list(_KEYS.items()))}  # first name wins for shared codes


# --------------------------------------------------------------------------- finding the host

def choose(windows: list[dict], target: Target, displays: list[tuple[float, float, float, float]]) -> Region:
    """Pick the host's region from a window list (CGWindowListCopyWindowInfo shape, plus
    `exe`: the owning process's executable name). Raises NotFound."""
    bounds_all = union_rect([(x, y, w, h) for x, y, w, h in displays]) or (0, 0, 0, 0)
    if target.mode == "monitor":
        main = displays[0] if displays else bounds_all
        named = _match(windows, target) if (target.title or target.process) else []
        return Region(named[0]["kCGWindowOwnerPID"] if named else None, "monitor", *[round(v) for v in main])
    matches = _match(windows, target)
    if not matches:
        raise NotFound(f"{target.describe()} is not open (or is minimized)")
    main = matches[0]
    pid = main["kCGWindowOwnerPID"]
    if target.mode == "process":
        rects = [_rect(w) for w in windows if w.get("kCGWindowOwnerPID") == pid and w.get("kCGWindowLayer", 0) == 0]
        box = union_rect([r for r in rects if r])
        if box is None:
            raise NotFound(f"{target.describe()} has no visible windows")
        return Region(pid, ("process", pid), *box)
    rect = _rect(main)
    if rect is None:
        raise NotFound(f"{target.describe()} has no visible window")
    return Region(pid, ("window", main.get("kCGWindowNumber")), *rect)


def _match(windows: list[dict], target: Target) -> list[dict]:
    title, proc = target.title.lower(), target.process.lower().removesuffix(".app").removesuffix(".exe")
    out = []
    for w in windows:
        if w.get("kCGWindowLayer", 0) != 0 or not _rect(w):
            continue
        names = {str(w.get("kCGWindowOwnerName", "")).lower(), str(w.get("exe", "")).lower()}
        if proc and proc not in names:
            continue
        if title and title not in str(w.get("kCGWindowName", "")).lower():
            continue
        out.append(w)
    # Largest first: a program's main window rather than a tooltip or palette.
    return sorted(out, key=lambda w: -(_rect(w)[2] * _rect(w)[3]))


def _rect(w: dict) -> tuple[int, int, int, int] | None:
    b = w.get("kCGWindowBounds") or {}
    width, height = round(b.get("Width", 0)), round(b.get("Height", 0))
    if width < 2 or height < 2:
        return None
    return round(b.get("X", 0)), round(b.get("Y", 0)), width, height


def _windows() -> list[dict]:
    import Quartz
    from AppKit import NSRunningApplication

    opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    raw = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []
    exe: dict[int, str] = {}
    out = []
    for w in raw:
        d = {k: w.get(k) for k in ("kCGWindowOwnerPID", "kCGWindowOwnerName", "kCGWindowName",
                                   "kCGWindowLayer", "kCGWindowNumber")}
        b = w.get("kCGWindowBounds")
        d["kCGWindowBounds"] = {k: b[k] for k in ("X", "Y", "Width", "Height")} if b else None
        pid = d["kCGWindowOwnerPID"]
        if pid not in exe:
            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            url = app.executableURL() if app else None
            exe[pid] = url.lastPathComponent() if url else ""
        d["exe"] = exe[pid]
        out.append(d)
    return out


def _displays() -> list[tuple[float, float, float, float]]:
    import Quartz
    err, ids, n = Quartz.CGGetActiveDisplayList(16, None, None)
    main = Quartz.CGMainDisplayID()
    ordered = sorted(ids[:n] if ids else [main], key=lambda d: d != main)
    return [(r.origin.x, r.origin.y, r.size.width, r.size.height)
            for r in (Quartz.CGDisplayBounds(d) for d in ordered)]


def locate(target: Target) -> Region:
    return choose(_windows(), target, _displays())


def main_display() -> tuple[int, int, int, int]:
    return tuple(round(v) for v in _displays()[0])


def grab(left: int, top: int, width: int, height: int):
    """Screenshot of a screen region (points) at the display's pixel density, as a PIL image.
    Uses `screencapture`: the capture API mss relies on returns only the wallpaper on
    macOS 15, while Apple's tool goes through the supported capture path."""
    import subprocess
    import tempfile
    from pathlib import Path

    from PIL import Image

    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "shot.png"
        r = subprocess.run(["screencapture", "-x", "-t", "png", "-R", f"{left},{top},{width},{height}", str(out)],
                           capture_output=True, text=True, timeout=15)
        if r.returncode or not out.exists():
            raise RuntimeError("screencapture failed; give the tool server's process Screen Recording "
                               f"permission (System Settings > Privacy & Security). {r.stderr.strip()}")
        with Image.open(out) as img:
            return img.convert("RGB")


# --------------------------------------------------------------------------- input

@dataclass
class _State:
    x: float = 0.0
    y: float = 0.0
    buttons: set = field(default_factory=set)      # held: "left", "right", "middle"
    flags: int = 0                                  # held modifier flags
    last_up: tuple | None = None                    # (button, x, y, time, clicks) of the last release
    clicks: int = 1                                 # click count of the press in progress


def translate(events: list[tuple], vscreen: tuple[float, float, float, float], st: _State,
              now: float) -> list[tuple]:
    """SendInput-shaped events -> Quartz operations:
      ("move", x, y, dragging_button | None)
      ("button", name, down, x, y, click_count)
      ("scroll", lines)                     positive = up
      ("key", mac_keycode, down, flags)
      ("unicode", text, down, flags)
    """
    from . import inputs as I

    names = _scan_names()
    left, top, width, height = vscreen
    ops: list[tuple] = []
    for ev in events:
        if ev[0] == "mouse":
            _, dx, dy, data, fl = ev
            if fl & I.MOUSEEVENTF_MOVE:
                st.x = left + dx * max(width - 1, 1) / 65535
                st.y = top + dy * max(height - 1, 1) / 65535
                ops.append(("move", st.x, st.y, next(iter(sorted(st.buttons)), None)))
            if fl & I.MOUSEEVENTF_WHEEL:
                signed = data - (1 << 32) if data >= 1 << 31 else data
                ops.append(("scroll", int(signed / I.WHEEL_DELTA)))
            for name, (down_flag, up_flag) in I.BUTTONS.items():
                if fl & down_flag:
                    # macOS counts double clicks from a field on the event, not from timing.
                    prev = st.last_up
                    repeat = (prev and prev[0] == name and now - prev[3] < DOUBLE_CLICK_S
                              and abs(prev[1] - st.x) < 4 and abs(prev[2] - st.y) < 4)
                    st.clicks = prev[4] + 1 if repeat else 1
                    st.buttons.add(name)
                    ops.append(("button", name, True, st.x, st.y, st.clicks))
                if fl & up_flag:
                    st.buttons.discard(name)
                    ops.append(("button", name, False, st.x, st.y, st.clicks))
                    st.last_up = (name, st.x, st.y, now, st.clicks)
        else:
            _, code, fl = ev
            down = not fl & I.KEYEVENTF_KEYUP
            if fl & I.KEYEVENTF_UNICODE:
                ops.append(("unicode", chr(code), down, st.flags))
                continue
            name = names.get((code, bool(fl & I.KEYEVENTF_EXTENDEDKEY)))
            if name is None or name not in _MAC_KEYS:
                raise I.InputError(f"that key isn't available on macOS (scan code {code:#x})")
            if name in _FLAGS:
                st.flags = st.flags | _FLAGS[name] if down else st.flags & ~_FLAGS[name]
            ops.append(("key", _MAC_KEYS[name], down, st.flags))
    return ops


class MacBackend:
    """inputs.Backend for macOS. `Region.hwnd` holds the host's process id."""

    def __init__(self) -> None:
        import sys
        if sys.platform != "darwin":
            from .inputs import InputError
            raise InputError("the macOS input backend only runs on macOS")
        import Quartz  # noqa: F401  (fail early if pyobjc is missing)
        self._st = _State()

    def locate(self, target: Target) -> Region:
        return locate(target)

    def virtual_screen(self) -> tuple[int, int, int, int]:
        box = union_rect([tuple(round(v) for v in d) for d in _displays()])
        return box or (0, 0, 1, 1)

    def focus(self, pid: int) -> bool:
        from AppKit import NSApplicationActivateAllWindows, NSRunningApplication, NSWorkspace

        def front() -> bool:
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            return bool(app) and app.processIdentifier() == pid

        if front():
            return True
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is None:
            return False
        app.activateWithOptions_(NSApplicationActivateAllWindows)
        for _ in range(40):
            if front():
                time.sleep(0.05)  # let the program process the activation before input arrives
                return True
            time.sleep(0.025)
        return False

    def send(self, events: list[tuple]) -> None:
        import Quartz as Q

        for op in translate(events, self.virtual_screen(), self._st, time.monotonic()):
            kind = op[0]
            if kind == "move":
                _, x, y, held = op
                etype = {None: Q.kCGEventMouseMoved, "left": Q.kCGEventLeftMouseDragged,
                         "right": Q.kCGEventRightMouseDragged, "middle": Q.kCGEventOtherMouseDragged}[held]
                ev = Q.CGEventCreateMouseEvent(None, etype, (x, y), _cg_button(Q, held or "left"))
            elif kind == "button":
                _, name, down, x, y, clicks = op
                etype = {("left", True): Q.kCGEventLeftMouseDown, ("left", False): Q.kCGEventLeftMouseUp,
                         ("right", True): Q.kCGEventRightMouseDown, ("right", False): Q.kCGEventRightMouseUp,
                         ("middle", True): Q.kCGEventOtherMouseDown,
                         ("middle", False): Q.kCGEventOtherMouseUp}[(name, down)]
                ev = Q.CGEventCreateMouseEvent(None, etype, (x, y), _cg_button(Q, name))
                Q.CGEventSetIntegerValueField(ev, Q.kCGMouseEventClickState, clicks)
            elif kind == "scroll":
                ev = Q.CGEventCreateScrollWheelEvent(None, Q.kCGScrollEventUnitLine, 1, op[1])
            elif kind == "key":
                _, code, down, flags = op
                ev = Q.CGEventCreateKeyboardEvent(None, code, down)
                Q.CGEventSetFlags(ev, flags)
            else:  # unicode
                _, text, down, flags = op
                ev = Q.CGEventCreateKeyboardEvent(None, 0, down)
                Q.CGEventKeyboardSetUnicodeString(ev, len(text.encode("utf-16-le")) // 2, text)
                Q.CGEventSetFlags(ev, flags)
            if ev is None:
                from .inputs import InputError
                raise InputError("macOS refused to create an input event; give the tool server's process "
                                 "Accessibility permission (System Settings > Privacy & Security)")
            Q.CGEventPost(Q.kCGHIDEventTap, ev)

    def char_key(self, ch: str) -> tuple[int, bool, bool] | None:
        """US layout: (scan, extended, shift) for a typeable character, else None (sent as Unicode)."""
        from .inputs import _KEYS
        if len(ch) != 1:
            return None
        if ch in _SHIFTED:
            return (*_KEYS[_SHIFTED[ch]], True)
        if ch.isalpha() and ch.isascii() and ch.isupper():
            return (*_KEYS[ch.lower()], True)
        if ch in _KEYS and len(ch) == 1:
            return (*_KEYS[ch], False)
        return None

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def _cg_button(Q, name: str) -> int:
    return {"left": Q.kCGMouseButtonLeft, "right": Q.kCGMouseButtonRight, "middle": Q.kCGMouseButtonCenter}[name]
