from __future__ import annotations

import ctypes
import io
import os
from ctypes import wintypes

from PIL import Image, ImageGrab


class CaptureError(RuntimeError):
    pass


if os.name == "nt":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def list_windows() -> list[tuple[int, str]]:
    if os.name != "nt":
        return []
    out: list[tuple[int, str]] = []

    @EnumWindowsProc
    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if title:
            out.append((int(hwnd), title))
        return True

    user32.EnumWindows(cb, 0)
    return out


def find_window(title_contains: str) -> tuple[int, str] | None:
    needle = title_contains.casefold().strip()
    matches = [(h, t) for h, t in list_windows() if needle in t.casefold()]
    if matches:
        return matches[0]
    aliases = ["space rangers", "космические рейнджеры"]
    for alias in aliases:
        for h, t in list_windows():
            if alias in t.casefold():
                return h, t
    return None


def is_target_window_active(title_contains: str) -> bool:
    """Return true only while the configured game window has keyboard focus."""
    if os.name != "nt":
        return True
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return False
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    active_title = buf.value.casefold()
    needle = title_contains.casefold().strip()
    if needle and needle in active_title:
        return True
    return any(alias in active_title for alias in ("space rangers", "космические рейнджеры"))


def _window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        raise CaptureError("GetWindowRect failed")
    if rect.right <= rect.left or rect.bottom <= rect.top:
        raise CaptureError("Window has invalid size")
    return rect.left, rect.top, rect.right, rect.bottom


def _client_rect_on_screen(hwnd: int) -> tuple[int, int, int, int]:
    """Capture only the game client area so UI text gets maximum useful pixels."""
    rect = wintypes.RECT()
    if not user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return _window_rect(hwnd)
    top_left = wintypes.POINT(rect.left, rect.top)
    bottom_right = wintypes.POINT(rect.right, rect.bottom)
    if not user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(top_left)):
        return _window_rect(hwnd)
    if not user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(bottom_right)):
        return _window_rect(hwnd)
    if bottom_right.x <= top_left.x or bottom_right.y <= top_left.y:
        return _window_rect(hwnd)
    return top_left.x, top_left.y, bottom_right.x, bottom_right.y


def capture_window_jpeg(title_contains: str, max_width: int = 1920, max_height: int = 1080, quality: int = 85) -> bytes:
    if os.name != "nt":
        raise CaptureError("Screen capture is supported on Windows")
    found = find_window(title_contains)
    if not found:
        raise CaptureError(f"Окно игры не найдено: {title_contains}")
    hwnd, _ = found
    bbox = _client_rect_on_screen(hwnd)
    img = ImageGrab.grab(bbox=bbox, all_screens=True)
    img = img.convert("RGB")
    img.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=max(50, min(95, quality)), optimize=True)
    return buf.getvalue()
