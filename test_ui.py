"""Qt slider regression test. Run with QT_QPA_PLATFORM=offscreen on CI."""
from dataclasses import replace
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
try:
    from PySide6.QtCore import Qt, QPoint, QPointF, QCoreApplication, QEvent, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication, QWidget, QPushButton, QMenu, QScrollArea
    from shiboken6 import isValid
    from app import ClickToJumpSlider, TraySettingsDialog, ConnectionDialog, MainWindow
    from core import EnvironmentInfo, LightInfo
except ImportError:
    ClickToJumpSlider = None


@unittest.skipUnless(ClickToJumpSlider is not None, 'PySide6 not installed (non-UI test environment)')
class SliderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_wheel_notifies_window_for_debounced_commit(self):
        slider = ClickToJumpSlider(Qt.Orientation.Horizontal)
        slider.setRange(1, 100)
        slider.setValue(50)
        slider.resize(300, 32)
        slider.show()
        seen = []
        slider.wheelAdjusted.connect(seen.append)
        center = QPointF(150, 16)
        event = QWheelEvent(center, center, QPoint(0, 0), QPoint(0, 120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                            Qt.ScrollPhase.ScrollUpdate, False)
        QApplication.sendEvent(slider, event)
        self.assertGreater(slider.value(), 50)
        self.assertEqual(seen, [slider.value()])
        slider.close()

    def test_track_click_jumps_and_emits_commit(self):
        slider = ClickToJumpSlider(Qt.Orientation.Horizontal)
        slider.resize(300, 32)
        slider.setRange(1, 100)
        slider.setValue(10)
        slider.show()
        events = []
        slider.trackClicked.connect(events.append)
        QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=QPoint(260, 16))
        self.assertEqual(len(events), 1)
        self.assertGreater(slider.value(), 65)
        self.assertEqual(events[0], slider.value())
        slider.close()


@unittest.skipUnless(ClickToJumpSlider is not None, 'PySide6 not installed (non-UI test environment)')
class TraySettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_diagnostics_accessible_from_tray_settings_without_closing(self):
        class FakeMain(QWidget):
            def __init__(self):
                super().__init__()
                self.requested_from = None

            def show_tray_diagnostics(self, dialog_parent=None):
                self.requested_from = dialog_parent

        parent = FakeMain()
        dialog = TraySettingsDialog(parent, 'r1', {'r1': 'Room'})
        matches = [b for b in dialog.findChildren(QPushButton)
                   if b.text() == 'Input diagnostics…']
        self.assertEqual(len(matches), 1)
        matches[0].click()
        self.assertIs(parent.requested_from, dialog)
        self.assertEqual(dialog.result(), 0)
        dialog.close()
        parent.close()


@unittest.skipUnless(ClickToJumpSlider is not None, 'PySide6 not installed')
class ConnectionDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_generate_button_guides_user_when_address_is_missing(self):
        dialog = ConnectionDialog(None, {})
        self.assertTrue(dialog.generate_button.isEnabled())
        with patch('app.TokenPairingDialog') as pairing:
            dialog.generate_button.click()
            self.assertIn('Enter the hub', dialog.address_help.text())
            pairing.assert_not_called()
            dialog.ip.setText(' 192.168.1.20 ')
            dialog.generate_button.click()
            pairing.assert_called_once_with(dialog, '192.168.1.20')
        dialog.close()

    def test_dialog_has_minimum_size_for_dhcp_note(self):
        dialog = ConnectionDialog(None, {})
        self.assertGreaterEqual(dialog.minimumHeight(), 335)
        self.assertGreaterEqual(dialog.minimumWidth(), 620)
        dialog.close()



@unittest.skipUnless(ClickToJumpSlider is not None, 'PySide6 not installed')
class RefreshLifetimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        config = dict(ip='test', token='test', approved={'lamp': 'room'}, approved_sensors={'sensor': 'room'})
        self.enterContext(patch('app.read_config', return_value=config))
        self.enterContext(patch.object(MainWindow, 'start_worker'))
        self.enterContext(patch('app.TrayWheelHook.start'))
        self.enterContext(patch('app.QSystemTrayIcon.show'))
        self.window = MainWindow()
        self.addCleanup(self.flush_deletions)
        self.addCleanup(self.window.deleteLater)
        self.light = LightInfo('lamp', 'Desk', 'room', 'Study', True, ('isOn', 'lightLevel'),
                               True, 50, None, None, None, 0, 1)
        self.sensor = EnvironmentInfo('sensor', 'Climate', 'room', 'Study', True, 20, 50, 90)

    def flush_deletions(self):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_repeated_refresh_disposes_old_pages_and_reuses_menu(self):
        window = self.window
        welcome = window.tabs.widget(0)
        window.on_loaded(([self.light], [self.sensor], ''))
        room, climate = (window.tabs.widget(i) for i in range(2))
        window.tabs.setCurrentIndex(1)
        submenu, = window._tray_menu.findChildren(QMenu)
        automatic, study = submenu.actions()
        for _ in range(20):
            window.on_loaded(([self.light], [self.sensor], ''))
            self.flush_deletions()
        self.assertFalse(any(isValid(page) for page in (welcome, room, climate)))
        self.assertEqual(window.tabs.currentIndex(), 1)
        self.assertEqual(len(window.tabs.findChildren(QScrollArea)), 2)
        self.assertEqual(window._tray_menu.findChildren(QMenu), [submenu])
        self.assertEqual(submenu.actions(), [automatic, study])
        with patch('app.write_config'):
            study.trigger()
            self.assertEqual(window.config['tray_room'], 'room')
            self.assertTrue(study.isChecked())
            automatic.trigger()
            self.assertEqual(window.config['tray_room'], '')
            self.assertTrue(automatic.isChecked())
        window.on_loaded(([replace(self.light, room_name='Office')], [], ''))
        self.flush_deletions()
        self.assertEqual([a.text() for a in submenu.actions()], ['Automatic (one approved room)', 'Office'])
        window.on_loaded(([], [], ''))
        self.flush_deletions()
        self.assertEqual(len(submenu.actions()), 1)
        self.assertFalse(window.tabs.findChildren(QScrollArea))
        self.assertEqual(window.tabs.count(), 1)

    def test_tray_settings_is_disposed_after_save_and_cancel(self):
        for dismiss in ('accept', 'reject'):
            QTimer.singleShot(0, lambda: getattr(self.app.activeModalWidget(), dismiss)())
            with patch('app.write_config'):
                self.window.change_tray_settings()
            self.flush_deletions()
            self.assertFalse(self.window.findChildren(TraySettingsDialog))


if __name__ == '__main__':
    unittest.main()
