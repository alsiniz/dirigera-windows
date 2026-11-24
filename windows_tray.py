"""Wheel input for the Qt-owned Windows notification icon."""

import ctypes
from ctypes import wintypes
import sys

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QCursor


WH_MOUSE_LL = 14
HC_ACTION = 0
WM_MOUSEWHEEL = 0x020A
GA_ROOT = 2
LRESULT = ctypes.c_ssize_t


class MouseHookData(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouse_data", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("extra_info", ctypes.c_size_t),
    ]


def wheel_delta(mouse_data: int) -> int:
    """Windows puts the signed wheel delta in the high word of mouseData."""
    return ctypes.c_short(mouse_data >> 16).value


class TrayWheelHook(QObject):
    wheel = Signal(int)

    def __init__(self, tray, menu):
        super().__init__(tray)
        self.tray = tray
        self.menu = menu
        self.error = ""
        self.count = 0
        self._hook = None
        self._callback = None
        self._api = None
        self._taskbar_classes = {
            "Shell_TrayWnd",
            "Shell_SecondaryTrayWnd",
            "NotifyIconOverflowWindow",
            "TopLevelWindowForOverflowXamlIsland",
        }

    @property
    def installed(self):
        return bool(self._hook)

    def start(self):
        if self.installed:
            return
        if sys.platform != "win32":
            self.error = "The wheel hook is available on Windows."
            return

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

        user32.SetWindowsHookExW.argtypes = (ctypes.c_int, callback_type, wintypes.HINSTANCE, wintypes.DWORD)
        user32.SetWindowsHookExW.restype = wintypes.HANDLE
        user32.CallNextHookEx.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
        user32.CallNextHookEx.restype = LRESULT
        user32.UnhookWindowsHookEx.argtypes = (wintypes.HANDLE,)
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        user32.WindowFromPoint.argtypes = (wintypes.POINT,)
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
        user32.GetClassNameW.restype = ctypes.c_int
        kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        self._api = user32
        # Keep the ctypes callback alive for the entire registration.
        self._callback = callback_type(self._on_mouse)
        self._hook = user32.SetWindowsHookExW(
            WH_MOUSE_LL, self._callback, kernel32.GetModuleHandleW(None), 0
        )
        self.error = "" if self._hook else f"Windows error {ctypes.get_last_error()}"

    def stop(self):
        if self._hook:
            self._api.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def restart(self):
        self.stop()
        self.start()

    def _over_icon(self, point):
        if not self.tray.isVisible() or self.menu.isVisible():
            return False
        # Skip shell geometry queries for scrolling over ordinary desktop apps.
        window = self._api.WindowFromPoint(point)
        if not window:
            return False
        root = self._api.GetAncestor(window, GA_ROOT) or window
        name = ctypes.create_unicode_buffer(64)
        self._api.GetClassNameW(root, name, len(name))
        if name.value not in self._taskbar_classes:
            return False
        # Qt's Windows backend uses Shell_NotifyIconGetRect. Both Qt positions
        # use logical coordinates so this also works with per-monitor scaling.
        return self.tray.geometry().contains(QCursor.pos())

    def _on_mouse(self, code, message, address):
        if code == HC_ACTION and message == WM_MOUSEWHEEL and address:
            try:
                event = MouseHookData.from_address(address)
                delta = wheel_delta(event.mouse_data)
                if delta and self._over_icon(event.pt):
                    self.count += 1
                    self.wheel.emit(delta)
                    return 1
            except Exception as exc:
                self.error = f"Wheel input failed ({type(exc).__name__})"
        return self._api.CallNextHookEx(None, code, message, address)
