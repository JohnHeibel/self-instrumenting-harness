"""Screenshots of the host (Windows), and where the host is on screen.

What gets captured comes from the host config (`Target`):
  window   the client area of the host's main window (default)
  process  the bounding box of every visible top-level window of the host's process, so
           dialogs, popups and file pickers that open as their own windows are included
  monitor  the whole primary monitor

The host's window is found by title and/or process (exe name). Input coordinates are
mapped through the same region (`Region`), so the agent always clicks in the image it saw.
Each screenshot also returns the region it was taken with (`Capture`).
"""

from __future__ import annotations

import io
import sys
from dataclasses import dataclass

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    # Report physical pixels, so window rects match what mss captures and SendInput
    # coordinates are physical too. Per-monitor v2, falling back for older Windows.
    try:
        if not ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            raise OSError
    except (OSError, AttributeError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except OSError:
            ctypes.windll.user32.SetProcessDPIAware()


@dataclass(frozen=True)
class Target:
    """Which program is the host, from the host config."""
    title: str = ""        # substring of its main window's title
    process: str = ""      # its exe name, e.g. "notepad.exe"
    mode: str = "window"   # window | process | monitor

    def describe(self) -> str:
        parts = [f"title containing '{self.title}'" if self.title else "",
                 f"process {self.process}" if self.process else ""]
        return "the host's window (" + ", ".join(p for p in parts if p) + ")" if any(parts) else "the host"


@dataclass(frozen=True)
class Region:
    """Where the host is on screen, in physical pixels. `hwnd` is the window to focus
    before input (None: nothing to focus, e.g. monitor mode with no window named);
    `ident` says which host instance it is, so a restarted host is noticed."""
    hwnd: int | None
    ident: object
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True)
class Capture:
    """The region a screenshot was taken of, and the size of the image the agent got."""
    region: Region
    image_width: int
    image_height: int


@dataclass
class Shot:
    png: bytes
    thumb_jpeg: bytes
    meta: dict
    capture: Capture | None  # None: the host wasn't found, so this is the whole monitor


class NotFound(Exception):
    """The host isn't on screen; the message says why (closed, minimized)."""


def screenshot(target: Target, max_width: int, thumb_width: int = 640) -> Shot:
    import mss
    from PIL import Image

    try:
        region = locate(target) if sys.platform in ("win32", "darwin") else None
    except NotFound:
        region = None
    if sys.platform == "darwin":
        # mss's capture call returns only the wallpaper on recent macOS; use Apple's tool.
        from .macos import grab, main_display
        img = grab(*((region.left, region.top, region.width, region.height) if region else main_display()))
    else:
        with mss.MSS() as sct:
            rect = ({"left": region.left, "top": region.top, "width": region.width, "height": region.height}
                    if region else sct.monitors[1])
            shot = sct.grab(rect)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    captured = img.size
    if img.width > max_width:
        img = img.resize((max_width, round(img.height * max_width / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    thumb = img
    if thumb.width > thumb_width:
        thumb = img.resize((thumb_width, round(img.height * thumb_width / img.width)), Image.LANCZOS)
    tbuf = io.BytesIO()
    thumb.save(tbuf, "JPEG", quality=70)
    source = target.mode if region else "monitor"
    meta = {"source": source, "captured_size": list(captured), "image_size": list(img.size)}
    cap = Capture(region, img.width, img.height) if region else None
    return Shot(buf.getvalue(), tbuf.getvalue(), meta, cap)


# Finding the host (Windows) -------------------------------------------------------------


def locate(target: Target) -> Region:
    """The host's region now, per the capture mode. Raises NotFound."""
    if sys.platform == "darwin":
        from .macos import locate as locate_macos
        return locate_macos(target)
    if target.mode == "monitor":
        hwnd = find_hwnd(target.title, target.process) if (target.title or target.process) else None
        m = ctypes.windll.user32.GetSystemMetrics
        return Region(hwnd, "monitor", 0, 0, m(0), m(1))  # primary monitor: SM_CXSCREEN, SM_CYSCREEN
    hwnd = find_hwnd(target.title, target.process)
    if hwnd is None:
        raise NotFound(f"{target.describe()} is not open")
    if ctypes.windll.user32.IsIconic(hwnd):
        raise NotFound(f"{target.describe()} is minimized")
    if target.mode == "process":
        pid = _pid(hwnd)
        rects = [_frame_rect(h) for h in _top_windows() if _pid(h) == pid and _shown(h)]
        box = union_rect([r for r in rects if r])
        if box is None:
            raise NotFound(f"{target.describe()} has no visible windows")
        return Region(hwnd, ("process", pid), *box)
    rect = client_rect(hwnd)
    if rect is None:
        raise NotFound(f"{target.describe()} is minimized")
    return Region(hwnd, ("window", hwnd), rect["left"], rect["top"], rect["width"], rect["height"])


def union_rect(rects: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int] | None:
    """Bounding box of (left, top, width, height) rects."""
    if not rects:
        return None
    left = min(r[0] for r in rects)
    top = min(r[1] for r in rects)
    right = max(r[0] + r[2] for r in rects)
    bottom = max(r[1] + r[3] for r in rects)
    return left, top, right - left, bottom - top


def find_hwnd(title: str = "", process: str = "") -> int | None:
    """The host's main window: a shown top-level window whose title contains `title` and
    whose program is `process` (either may be empty, not both). When several match, the
    largest wins, so a tooltip or splash doesn't."""
    if not title and not process:
        return None
    best, best_area = None, -1
    for hwnd in _top_windows():
        if not _shown(hwnd, allow_minimized=True):
            continue
        if title and title.lower() not in _title(hwnd).lower():
            continue
        if process and _exe_name(hwnd) != process.lower():
            continue
        r = _frame_rect(hwnd)
        area = r[2] * r[3] if r else 0
        if area > best_area:
            best, best_area = hwnd, area
    return best


def client_rect(hwnd: int) -> dict | None:
    """Client area on screen in physical pixels, or None when minimized or gone."""
    user32 = ctypes.windll.user32
    if not user32.IsWindow(hwnd) or user32.IsIconic(hwnd):
        return None
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    if rect.right <= 0 or rect.bottom <= 0:
        return None
    return {"left": origin.x, "top": origin.y, "width": rect.right, "height": rect.bottom}


def _top_windows() -> list[int]:
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def on_window(hwnd, _):
        found.append(hwnd)
        return True

    ctypes.windll.user32.EnumWindows(on_window, 0)
    return found


def _shown(hwnd: int, allow_minimized: bool = False) -> bool:
    """Visible, not cloaked (hidden UWP frames are 'visible' but cloaked), and has area."""
    user32 = ctypes.windll.user32
    if not user32.IsWindowVisible(hwnd):
        return False
    if not allow_minimized and user32.IsIconic(hwnd):
        return False
    cloaked = wintypes.DWORD(0)
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), 4)  # DWMWA_CLOAKED
    except OSError:
        pass
    if cloaked.value:
        return False
    r = _frame_rect(hwnd)
    return bool(r and r[2] > 1 and r[3] > 1) or (allow_minimized and bool(user32.IsIconic(hwnd)))


def _frame_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """The window's visible bounds (without the invisible resize border)."""
    rect = wintypes.RECT()
    if ctypes.windll.dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)):
        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):  # DWMWA_EXTENDED_FRAME_BOUNDS failed
            return None
    w, h = rect.right - rect.left, rect.bottom - rect.top
    return (rect.left, rect.top, w, h) if w > 0 and h > 0 else None


def _title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def _pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _exe_name(hwnd: int) -> str:
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, _pid(hwnd))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return ""
        return buf.value.replace("/", "\\").rsplit("\\", 1)[-1].lower()
    finally:
        k32.CloseHandle(h)


def host_up(target: Target) -> tuple[bool, str]:
    """For the runner's host check: is the host on screen?"""
    try:
        locate(target)
        return True, f"{target.describe()} is on screen"
    except NotFound as e:
        return False, str(e)
