"""Screenshots of the game window (Windows), or the primary monitor elsewhere.

Each window screenshot also returns the geometry it was taken with (`Capture`), so the
input tools can map coordinates in the image the agent saw back to screen pixels.
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
class Capture:
    """Where a screenshot came from: the window, its client area on screen (physical
    pixels), and the size of the image the agent received."""
    hwnd: int
    left: int
    top: int
    width: int
    height: int
    image_width: int
    image_height: int


@dataclass
class Shot:
    png: bytes
    thumb_jpeg: bytes
    meta: dict
    capture: Capture | None  # None: the whole monitor (window not found)


def screenshot(window_title: str, max_width: int, thumb_width: int = 640) -> Shot:
    import mss
    from PIL import Image

    win = _find_window(window_title) if sys.platform == "win32" else None
    rect = {k: win[k] for k in ("left", "top", "width", "height")} if win else None
    with mss.mss() as sct:
        shot = sct.grab(rect or sct.monitors[1])
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
    meta = {"source": "window" if win else "monitor", "captured_size": list(captured),
            "image_size": list(img.size)}
    cap = Capture(win["hwnd"], win["left"], win["top"], captured[0], captured[1],
                  img.width, img.height) if win else None
    return Shot(buf.getvalue(), tbuf.getvalue(), meta, cap)


def find_hwnd(title_substring: str) -> int | None:
    """A visible top-level window whose title contains the substring. A window whose
    program is named after it (factorio.exe) wins over others, such as a browser tab
    titled "Factorio Wiki", so input never lands in the wrong window."""
    user32 = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def on_window(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if title_substring.lower() in buf.value.lower():
                found.append(hwnd)
        return True

    user32.EnumWindows(on_window, 0)
    for hwnd in found:
        if _exe_name(hwnd).startswith(title_substring.lower()):
            return hwnd
    return found[0] if found else None


def _exe_name(hwnd: int) -> str:
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
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


def _find_window(title_substring: str) -> dict | None:
    """Client area (and hwnd) of the game window, or None when missing or minimized."""
    hwnd = find_hwnd(title_substring)
    if hwnd is None:
        return None
    rect = client_rect(hwnd)
    return {"hwnd": hwnd, **rect} if rect else None
