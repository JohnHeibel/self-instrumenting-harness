"""Mouse and keyboard input to the host, through Win32 SendInput.

Coordinates are in the pixel space of the last screenshot image the agent received.
They map to the region that screenshot was taken of (`capture.Capture`: the host's
window, all its windows, or the monitor, per the host config), then to SendInput's
absolute 0..65535 virtual-desktop range. The process is DPI aware (capture.py), so every
rect here is in physical pixels.

Keys are sent as scan codes: many programs read scan codes and ignore events that carry
only a virtual-key code.

Everything Win32 sits behind `Backend`, so the logic is tested with a fake that records
the SendInput events (tests/test_inputs.py). An event is one INPUT structure:
  ("mouse", dx, dy, mouse_data, flags)
  ("key", scan, flags)            (with KEYEVENTF_UNICODE, scan is the UTF-16 code unit)
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass
from typing import Protocol

from .capture import Capture, NotFound, Region, Target

# SendInput flags (winuser.h)
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP = 0x0020, 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008
WHEEL_DELTA = 120

BUTTONS = {"left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
           "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
           "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP)}

# Scan code set 1. (scan, extended)
_KEYS: dict[str, tuple[int, bool]] = {
    "esc": (0x01, False), "backspace": (0x0E, False), "tab": (0x0F, False), "enter": (0x1C, False),
    "ctrl": (0x1D, False), "shift": (0x2A, False), "rshift": (0x36, False), "alt": (0x38, False),
    "space": (0x39, False), "capslock": (0x3A, False),
    "rctrl": (0x1D, True), "ralt": (0x38, True),
    "up": (0x48, True), "down": (0x50, True), "left": (0x4B, True), "right": (0x4D, True),
    "insert": (0x52, True), "delete": (0x53, True), "home": (0x47, True), "end": (0x4F, True),
    "pageup": (0x49, True), "pagedown": (0x51, True),
    "numpad0": (0x52, False), "numpad1": (0x4F, False), "numpad2": (0x50, False),
    "numpad3": (0x51, False), "numpad4": (0x4B, False), "numpad5": (0x4C, False),
    "numpad6": (0x4D, False), "numpad7": (0x47, False), "numpad8": (0x48, False),
    "numpad9": (0x49, False), "numpad*": (0x37, False), "numpad-": (0x4A, False),
    "numpad+": (0x4E, False), "numpad.": (0x53, False), "numpad/": (0x35, True),
    "numpadenter": (0x1C, True),
    "f11": (0x57, False), "f12": (0x58, False),
}
for _i in range(10):
    _KEYS[f"f{_i + 1}"] = (0x3B + _i, False)
for _row, _start in (("1234567890-=", 0x02), ("qwertyuiop[]", 0x10), ("asdfghjkl;'`", 0x1E),
                     ("\\zxcvbnm,./", 0x2B)):
    for _j, _ch in enumerate(_row):
        _KEYS[_ch] = (_start + _j, False)
_ALIASES = {"escape": "esc", "return": "enter", "control": "ctrl", "lctrl": "ctrl",
            "lshift": "shift", "lalt": "alt", "del": "delete", "ins": "insert", "pgup": "pageup",
            "pgdn": "pagedown", "caps": "capslock", "spacebar": "space", "bksp": "backspace",
            "minus": "-", "equals": "=", "plus": "=", "comma": ",", "period": ".", "slash": "/",
            "backslash": "\\", "semicolon": ";", "quote": "'", "backtick": "`", "grave": "`",
            "tilde": "`", "arrowup": "up", "arrowdown": "down", "arrowleft": "left",
            "arrowright": "right"}
MODIFIERS = {"ctrl", "shift", "alt", "rctrl", "rshift", "ralt"}
# Combos that would switch away from the host or close it.
_BLOCKED = {frozenset({"alt", "f4"}), frozenset({"alt", "tab"}), frozenset({"ctrl", "esc"}),
            frozenset({"alt", "esc"})}

MAX_HOLD_MS = 10_000
MAX_TYPE_CHARS = 2000
PRESS_S = 0.05      # a key or button stays down this long, so a program polling once a frame sees it
SETTLE_S = 0.03     # after moving the cursor, before pressing
DOUBLE_GAP_S = 0.08
TYPE_GAP_S = 0.02


class InputError(Exception):
    """Shown to the agent as the tool error: say what is wrong and what to do."""


class Backend(Protocol):
    def locate(self, target: Target) -> Region: ...           # raises capture.NotFound
    def virtual_screen(self) -> tuple[int, int, int, int]: ...  # left, top, width, height
    def focus(self, hwnd: int) -> bool: ...
    def send(self, events: list[tuple]) -> None: ...
    def char_key(self, ch: str) -> tuple[int, bool, bool] | None: ...  # scan, extended, shift
    def sleep(self, seconds: float) -> None: ...


def parse_keys(keys: str) -> list[tuple[str, int, bool]]:
    """'ctrl+shift+z' -> [(name, scan, extended), ...] in press order."""
    raw = keys.strip().lower()
    if not raw:
        raise InputError("keys is empty; give a key such as 'e', 'ctrl+z' or 'escape'")
    parts = ["+"] if raw == "+" else [p.strip() for p in raw.split("+")]
    if raw.endswith("++"):
        parts = parts[:-2] + ["="]  # 'ctrl++': the + key is shift+= on US keyboards
    out = []
    for p in parts:
        name = _ALIASES.get(p, p)
        if name not in _KEYS:
            raise InputError(f"unknown key {p!r} in {keys!r}. Use names like a, 1, f5, space, enter, "
                             "escape, tab, backspace, delete, up, ctrl, shift, alt, joined with +")
        out.append((name, *_KEYS[name]))
    names = [n for n, _, _ in out]
    if len(set(names)) != len(names):
        raise InputError(f"a key appears twice in {keys!r}")
    if frozenset(n.lstrip("r") if n in MODIFIERS else n for n in names) in _BLOCKED:
        raise InputError(f"{keys!r} is not allowed: it would switch away from or close the program")
    return out


def to_screen(cap: Capture, region: Region, x: float, y: float) -> tuple[int, int]:
    """Pixel (x, y) of the screenshot image -> physical screen pixel in `region` (where the
    host is now), at the centre of the block of screen pixels that image pixel covers."""
    if not (0 <= x < cap.image_width and 0 <= y < cap.image_height):
        raise InputError(f"({x}, {y}) is outside the last screenshot, which is "
                         f"{cap.image_width}x{cap.image_height}; x must be 0..{cap.image_width - 1} "
                         f"and y 0..{cap.image_height - 1}")
    w, h = cap.region.width, cap.region.height
    cx = min(int((x + 0.5) * w / cap.image_width), w - 1)
    cy = min(int((y + 0.5) * h / cap.image_height), h - 1)
    return region.left + cx, region.top + cy


def to_absolute(sx: int, sy: int, vscreen: tuple[int, int, int, int]) -> tuple[int, int]:
    """Physical screen pixel -> SendInput absolute coordinates over the virtual desktop."""
    left, top, width, height = vscreen
    nx = round((sx - left) * 65535 / max(width - 1, 1))
    ny = round((sy - top) * 65535 / max(height - 1, 1))
    return nx, ny


def _key(scan: int, extended: bool, up: bool) -> tuple:
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_EXTENDEDKEY if extended else 0) | (KEYEVENTF_KEYUP if up else 0)
    return ("key", scan, flags)


class Controller:
    """The input tools' logic. One call at a time; whatever a call pressed is released
    even if it fails part way, so no key or button is left held down."""

    def __init__(self, backend: Backend, target: Target) -> None:
        self.b = backend
        self.target = target
        self.capture: Capture | None = None   # set by every window screenshot
        self._lock = threading.Lock()
        self._held: list[tuple] = []          # release events for what is down right now

    # Tools ------------------------------------------------------------------------

    def click(self, x: float, y: float, button: str = "left", double: bool = False,
              modifiers: str = "") -> str:
        down, up = self._button(button)
        mods = self._modifiers(modifiers)
        with self._session() as point:
            nx, ny = point(x, y)
            self._move(nx, ny)
            self._press_mods(mods)
            for i in range(2 if double else 1):
                if i:
                    self.b.sleep(DOUBLE_GAP_S)
                self._down(("mouse", 0, 0, 0, down), ("mouse", 0, 0, 0, up))
                self.b.sleep(PRESS_S)
                self._up()
            self._release_all()
        what = "Double-clicked" if double else "Clicked"
        return f"{what} {_mod_text(modifiers)}{button} at ({_n(x)}, {_n(y)})"

    def drag(self, x1: float, y1: float, x2: float, y2: float, button: str = "left",
             modifiers: str = "") -> str:
        down, up = self._button(button)
        mods = self._modifiers(modifiers)
        with self._session() as point:
            a, b = point(x1, y1), point(x2, y2)
            self._move(*a)
            self._press_mods(mods)
            self._down(("mouse", 0, 0, 0, down), ("mouse", 0, 0, 0, up))
            self.b.sleep(PRESS_S)
            steps = max(5, min(30, int(max(abs(b[0] - a[0]), abs(b[1] - a[1])) / 2000)))
            for i in range(1, steps + 1):
                self._move(round(a[0] + (b[0] - a[0]) * i / steps), round(a[1] + (b[1] - a[1]) * i / steps),
                           settle=0.015)
            self.b.sleep(PRESS_S)
            self._release_all()
        return f"Dragged {_mod_text(modifiers)}{button} from ({_n(x1)}, {_n(y1)}) to ({_n(x2)}, {_n(y2)})"

    def move(self, x: float, y: float) -> str:
        with self._session() as point:
            self._move(*point(x, y))
        return f"Moved the mouse to ({_n(x)}, {_n(y)})"

    def scroll(self, x: float, y: float, amount: int, modifiers: str = "") -> str:
        if not isinstance(amount, int) or amount == 0 or abs(amount) > 50:
            raise InputError("amount must be a whole number from -50 to 50, not 0: "
                             "positive scrolls up (away from you), negative scrolls down")
        mods = self._modifiers(modifiers)
        with self._session() as point:
            self._move(*point(x, y))
            self._press_mods(mods)
            step = 1 if amount > 0 else -1
            for _ in range(abs(amount)):
                self.b.send([("mouse", 0, 0, (step * WHEEL_DELTA) & 0xFFFFFFFF, MOUSEEVENTF_WHEEL)])
                self.b.sleep(TYPE_GAP_S)
            self._release_all()
        return f"Scrolled {_mod_text(modifiers)}{'up' if amount > 0 else 'down'} {abs(amount)} at ({_n(x)}, {_n(y)})"

    def key(self, keys: str, hold_ms: int = 0) -> str:
        parsed = parse_keys(keys)
        if not 0 <= hold_ms <= MAX_HOLD_MS:
            raise InputError(f"hold_ms must be 0..{MAX_HOLD_MS}")
        with self._session(need_capture=False):
            for _, scan, ext in parsed:
                self._down(_key(scan, ext, False), _key(scan, ext, True))
            self.b.sleep(max(hold_ms / 1000, PRESS_S))
            self._release_all()
        return f"Held {keys} for {hold_ms} ms" if hold_ms else f"Pressed {keys}"

    def type_text(self, text: str) -> str:
        if not text:
            raise InputError("text is empty")
        if len(text) > MAX_TYPE_CHARS:
            raise InputError(f"text is {len(text)} characters; the limit is {MAX_TYPE_CHARS}")
        with self._session(need_capture=False):
            for ch in text:
                self._type_char(ch)
                self.b.sleep(TYPE_GAP_S)
        shown = text if len(text) <= 40 else text[:37] + "..."
        return f"Typed {shown!r}"

    # Internals --------------------------------------------------------------------

    class _Session:
        def __init__(self, ctl: "Controller", need_capture: bool) -> None:
            self.ctl, self.need_capture = ctl, need_capture

        def __enter__(self):
            self.ctl._lock.acquire()
            try:
                return self.ctl._prepare(self.need_capture)
            except BaseException:
                self.ctl._lock.release()
                raise

        def __exit__(self, *exc):
            try:
                self.ctl._release_all()
            finally:
                self.ctl._lock.release()
            return False

    def _session(self, need_capture: bool = True) -> "_Session":
        return Controller._Session(self, need_capture)

    def _prepare(self, need_capture: bool):
        """Find and focus the host; return a mapper from image pixels to SendInput coords."""
        try:
            region = self.b.locate(self.target)
        except NotFound as e:
            raise InputError(f"{e}; input needs it on screen") from None
        cap = self.capture
        if need_capture:
            if cap is None:
                raise InputError("take a screenshot first: coordinates are pixels of the last screenshot")
            if cap.region.ident != region.ident:
                raise InputError("the program's window is not the one in the last screenshot (it was "
                                 "closed and reopened?); take a new screenshot")
            was, now = (cap.region.width, cap.region.height), (region.width, region.height)
            if was != now:
                raise InputError(f"what the screenshot shows changed size ({was[0]}x{was[1]} -> "
                                 f"{now[0]}x{now[1]}), e.g. a window was resized, opened or closed; "
                                 "take a new screenshot")
        if region.hwnd is not None and not self.b.focus(region.hwnd):
            raise InputError("could not bring the program's window to the front")
        vscreen = self.b.virtual_screen()

        def point(x: float, y: float) -> tuple[int, int]:
            assert cap is not None
            return to_absolute(*to_screen(cap, region, x, y), vscreen)
        return point

    @staticmethod
    def _button(button: str) -> tuple[int, int]:
        if button not in BUTTONS:
            raise InputError(f"button must be left, right or middle, not {button!r}")
        return BUTTONS[button]

    @staticmethod
    def _modifiers(modifiers: str) -> list[tuple[str, int, bool]]:
        if not modifiers:
            return []
        parsed = parse_keys(modifiers)
        bad = [n for n, _, _ in parsed if n not in MODIFIERS]
        if bad:
            raise InputError(f"modifiers takes ctrl, shift and alt (e.g. 'shift' or 'ctrl+shift'), not {bad}")
        return parsed

    def _press_mods(self, mods) -> None:
        for _, scan, ext in mods:
            self._down(_key(scan, ext, False), _key(scan, ext, True))
        if mods:
            self.b.sleep(SETTLE_S)

    def _move(self, nx: int, ny: int, settle: float = SETTLE_S) -> None:
        self.b.send([("mouse", nx, ny, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK)])
        self.b.sleep(settle)

    def _down(self, press: tuple, release: tuple) -> None:
        self._held.append(release)  # before sending: if send fails half way, still release
        self.b.send([press])

    def _up(self) -> None:
        self.b.send([self._held.pop()])

    def _release_all(self) -> None:
        while self._held:
            ev = self._held.pop()
            try:
                self.b.send([ev])
            except Exception:
                pass

    def _type_char(self, ch: str) -> None:
        if ch == "\n":
            ch = "\r"
        special = {"\r": "enter", "\t": "tab", " ": "space"}.get(ch)
        mapped = (*_KEYS[special], False) if special else self.b.char_key(ch)
        if mapped:
            scan, ext, shift = mapped
            if shift:
                self._down(_key(_KEYS["shift"][0], False, False), _key(_KEYS["shift"][0], False, True))
            self._down(_key(scan, ext, False), _key(scan, ext, True))
            self._release_all()
            return
        # Not on the keyboard layout: send it as a Unicode character.
        for unit in ch.encode("utf-16-le").hex(" ", 2).split():
            code = int.from_bytes(bytes.fromhex(unit), "little")
            self.b.send([("key", code, KEYEVENTF_UNICODE), ("key", code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)])


def _n(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def _mod_text(modifiers: str) -> str:
    return f"{modifiers}+" if modifiers else ""


# The real backend --------------------------------------------------------------------


class Win32Backend:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise InputError("input tools need Windows")
        import ctypes
        from ctypes import wintypes

        from . import capture  # sets DPI awareness
        self._capture = capture
        self.ct, self.wt = ctypes, wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)

        ulong_ptr = ctypes.c_size_t

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ulong_ptr)]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ulong_ptr)]

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

        class _U(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

        class INPUT(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", _U)]

        self.INPUT = INPUT
        self.user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        self.user32.SendInput.restype = wintypes.UINT
        self.user32.VkKeyScanW.argtypes = (wintypes.WCHAR,)
        self.user32.VkKeyScanW.restype = ctypes.c_short
        self.user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
        self.user32.MapVirtualKeyW.restype = wintypes.UINT

    def locate(self, target: Target) -> Region:
        return self._capture.locate(target)

    def virtual_screen(self) -> tuple[int, int, int, int]:
        m = self.user32.GetSystemMetrics
        return m(76), m(77), m(78), m(79)  # SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN

    def focus(self, hwnd: int) -> bool:
        u, k32 = self.user32, self.ct.windll.kernel32
        if u.GetForegroundWindow() == hwnd:
            return True
        # Windows only lets the foreground thread change the foreground window, so borrow
        # its input state for a moment.
        fg = u.GetForegroundWindow()
        fg_tid = u.GetWindowThreadProcessId(fg, None) if fg else 0
        me = k32.GetCurrentThreadId()
        attached = bool(fg_tid and fg_tid != me and u.AttachThreadInput(me, fg_tid, True))
        try:
            u.BringWindowToTop(hwnd)
            u.SetForegroundWindow(hwnd)
        finally:
            if attached:
                u.AttachThreadInput(me, fg_tid, False)
        for _ in range(20):
            if u.GetForegroundWindow() == hwnd:
                time.sleep(0.05)  # let the program process the activation before input arrives
                return True
            time.sleep(0.025)
        return False

    def send(self, events: list[tuple]) -> None:
        arr = (self.INPUT * len(events))()
        for i, ev in enumerate(events):
            if ev[0] == "mouse":
                _, dx, dy, data, flags = ev
                arr[i].type = 0
                arr[i].mi.dx, arr[i].mi.dy, arr[i].mi.mouseData, arr[i].mi.dwFlags = dx, dy, data, flags
            else:
                _, scan, flags = ev
                arr[i].type = 1
                arr[i].ki.wVk, arr[i].ki.wScan, arr[i].ki.dwFlags = 0, scan, flags
        sent = self.user32.SendInput(len(events), arr, self.ct.sizeof(self.INPUT))
        if sent != len(events):
            err = self.ct.get_last_error()
            raise InputError(f"Windows accepted {sent} of {len(events)} input events (error {err}); "
                             "input may be blocked, e.g. by a window running as administrator")

    def char_key(self, ch: str) -> tuple[int, bool, bool] | None:
        if len(ch) != 1:
            return None
        res = self.user32.VkKeyScanW(ch)
        if res == -1:
            return None
        vk, shift_state = res & 0xFF, (res >> 8) & 0xFF
        if shift_state & ~1:  # needs ctrl or alt (AltGr): send as Unicode instead
            return None
        scan = self.user32.MapVirtualKeyW(vk, 0)  # MAPVK_VK_TO_VSC
        if not scan:
            return None
        return scan, False, bool(shift_state & 1)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)
