"""Screenshots of the game window (Windows), or the primary monitor elsewhere."""

from __future__ import annotations

import io
import sys

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    # Report physical pixels, so window rects match what mss captures.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except OSError:
        ctypes.windll.user32.SetProcessDPIAware()


def screenshot_png(window_title: str, max_width: int) -> tuple[bytes, dict]:
    import mss
    from PIL import Image

    rect = _find_window(window_title) if sys.platform == "win32" else None
    with mss.mss() as sct:
        shot = sct.grab(rect or sct.monitors[1])
    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    captured = img.size
    if img.width > max_width:
        img = img.resize((max_width, round(img.height * max_width / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    meta = {"source": "window" if rect else "monitor", "captured_size": list(captured),
            "image_size": list(img.size)}
    return buf.getvalue(), meta


def _find_window(title_substring: str) -> dict | None:
    """Client area of the first visible top-level window whose title contains the substring."""
    user32 = ctypes.windll.user32
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def on_window(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if title_substring.lower() in buf.value.lower():
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(on_window, 0)
    if not found:
        return None
    rect = wintypes.RECT()
    user32.GetClientRect(found[0], ctypes.byref(rect))
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(found[0], ctypes.byref(origin))
    if rect.right <= 0 or rect.bottom <= 0:
        return None  # minimized
    return {"left": origin.x, "top": origin.y, "width": rect.right, "height": rect.bottom}
