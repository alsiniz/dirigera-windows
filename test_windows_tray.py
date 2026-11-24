"""Regression checks for wheel filtering and signed Windows deltas."""

import ctypes
import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from windows_tray import HC_ACTION, WM_MOUSEWHEEL, MouseHookData, TrayWheelHook


class TrayHookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tray = QSystemTrayIcon()
        self.menu = QMenu()
        self.hook = TrayWheelHook(self.tray, self.menu)
        self.hook._api = Mock()
        self.hook._api.CallNextHookEx.return_value = 37
        self.hook._api.GetClassNameW.side_effect = self.window_class
        self.root_class = "Shell_TrayWnd"
        self.received = []
        self.hook.wheel.connect(self.received.append)

    def window_class(self, _root, output, _length):
        output.value = self.root_class
        return len(self.root_class)

    def dispatch(self, delta):
        event = MouseHookData()
        event.mouse_data = (delta & 0xFFFF) << 16
        with (
            patch.object(self.tray, "isVisible", return_value=True),
            patch.object(self.tray, "geometry", return_value=QRect(-20, 0, 20, 20)),
            patch("windows_tray.QCursor") as cursor,
        ):
            cursor.pos.return_value = self.cursor
            return self.hook._on_mouse(HC_ACTION, WM_MOUSEWHEEL, ctypes.addressof(event))

    def test_scroll_over_icon_is_signed_and_consumed(self):
        self.cursor = QPoint(-10, 10)
        self.assertEqual(self.dispatch(-120), 1)
        self.assertEqual(self.received, [-120])
        self.hook._api.CallNextHookEx.assert_not_called()

    def test_scroll_elsewhere_on_taskbar_passes_through(self):
        self.cursor = QPoint(10, 10)
        self.assertEqual(self.dispatch(120), 37)
        self.assertEqual(self.received, [])

    def test_scroll_over_app_window_passes_through(self):
        self.root_class = "AnotherApp"
        self.cursor = QPoint(-10, 10)
        self.assertEqual(self.dispatch(120), 37)
        self.assertEqual(self.received, [])
