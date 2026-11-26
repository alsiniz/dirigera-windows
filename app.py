#!/usr/bin/env python3
"""Local lighting and climate controls for IKEA DIRIGERA on Windows."""
from __future__ import annotations

import math
from hashlib import sha256
from html import escape
import queue
import sys
from pathlib import Path
from collections import defaultdict
from threading import Lock
from importlib.metadata import PackageNotFoundError, version as package_version
from version import APP_NAME, VERSION, KOFI_URL
from windows_tray import TrayWheelHook
from pairing import valid_hub_address, extract_generated_token, run_token_helper

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QPointF, QUrl, QProcess, QProcessEnvironment, QLockFile
from PySide6.QtGui import QColor, QPainter, QPen, QIcon, QImage, QCursor, QKeySequence, QShortcut, QDesktopServices
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QPushButton, QLineEdit, QDialog, QDialogButtonBox, QTreeWidget,
    QTreeWidgetItem, QMessageBox, QTabWidget, QScrollArea, QGroupBox, QSlider,
    QSystemTrayIcon, QMenu, QFrame, QSizePolicy, QComboBox, QToolTip, QStyleOptionSlider, QStyle,
)
from core import (apply, approved_climate, approved_visible, config_path, environment_snapshot, read_config, snapshot,
                  toggle_power, wheel_steps, stepped_brightness, tray_power_targets,
                  tray_room, tray_targets, write_config)


class HubWorker(QThread):
    loaded = Signal(object)
    completed = Signal(str)
    failed = Signal(str)

    def __init__(self, ip: str, token: str, approved: dict[str, str]):
        super().__init__()
        self.ip = ip
        self.token = token
        self._approved = dict(approved)
        self._lock = Lock()
        self._jobs: queue.Queue = queue.Queue()
        self._latest_preview = {}
        self._preview_queued = False

    def set_approved(self, approved: dict[str, str]):
        with self._lock:
            self._approved = dict(approved)

    def submit(self, kind: str, *args):
        self._jobs.put((kind, args))

    def submit_preview(self, ids: list[str], value: tuple[float, float]):
        """Keep only the most recent live wheel colour per group during drag."""
        with self._lock:
            self._latest_preview[tuple(ids)] = value
            if not self._preview_queued:
                self._preview_queued = True
                self._jobs.put(("preview", ()))

    def stop(self):
        self._jobs.put(("stop", ()))

    def _publish(self, hub):
        lights = snapshot(hub)
        warning = ""
        try:
            sensors = environment_snapshot(hub)
        except Exception as exc:
            # Unsupported sensor types must not break lighting functionality.
            sensors = []
            warning = f"Climate discovery unavailable: {type(exc).__name__}: {exc}"
        self.loaded.emit((lights, sensors, warning))

    def run(self):
        # Import in worker so all network I/O remains outside the UI thread.
        import dirigera
        hub = dirigera.Hub(token=self.token, ip_address=self.ip)
        while True:
            kind, args = self._jobs.get()
            if kind == "stop":
                break
            try:
                if kind == "refresh":
                    self._publish(hub)
                elif kind == "preview":
                    with self._lock:
                        previews = self._latest_preview
                        self._latest_preview = {}
                        self._preview_queued = False
                        approved = dict(self._approved)
                    for ids, value in previews.items():
                        apply(hub, approved, list(ids), "color", value)
                    # Refresh once when the wheel closes; don't read the whole
                    # hub on every intermediate pointer movement.
                elif kind == "toggle":
                    (ids,) = args
                    with self._lock:
                        approved = dict(self._approved)
                    count, turned_on = toggle_power(hub, approved, ids)
                    self.completed.emit(f"{'On' if turned_on else 'Off'} · {count} approved room light(s)")
                    self._publish(hub)
                elif kind == "control":
                    ids, action, value = args
                    with self._lock:
                        approved = dict(self._approved)
                    changed = apply(hub, approved, ids, action, value)
                    self.completed.emit(f"Updated {changed} approved light(s)")
                    self._publish(hub)
            except Exception as exc:
                self.failed.emit(f"{type(exc).__name__}: {exc}")


class Wheel(QWidget):
    selection_changed = Signal(float, float)
    selection_finished = Signal()

    def __init__(self, hue: float = 25, saturation: float = 1):
        super().__init__()
        self.setFixedSize(252, 252)
        self.hue = float(hue) % 360
        self.saturation = max(0.0, min(1.0, float(saturation)))
        self.r = 113
        self.center = 126
        self.wheel_image = QImage(self.size(), QImage.Format.Format_ARGB32)
        self.wheel_image.fill(Qt.GlobalColor.transparent)
        for y in range(self.height()):
            for x in range(self.width()):
                dx, dy = x - self.center, self.center - y
                distance = math.hypot(dx, dy)
                if distance <= self.r:
                    hue = math.degrees(math.atan2(dy, dx)) % 360
                    sat = distance / self.r
                    self.wheel_image.setPixelColor(x, y, QColor.fromHsv(round(hue) % 360, round(sat * 255), 255))
        self.setToolTip("Angle selects hue, distance from centre selects saturation")

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.drawImage(0, 0, self.wheel_image)
        x = self.center + self.r * self.saturation * math.cos(math.radians(self.hue))
        y = self.center - self.r * self.saturation * math.sin(math.radians(self.hue))
        p.setPen(QPen(QColor("#151515"), 3))
        p.setBrush(QColor.fromHsv(round(self.hue) % 360, round(self.saturation * 255), 255))
        p.drawEllipse(QPointF(x, y), 9, 9)
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(x, y), 11, 11)

    def _select_at(self, pos):
        dx, dy = pos.x() - self.center, self.center - pos.y()
        self.hue = math.degrees(math.atan2(dy, dx)) % 360
        self.saturation = min(1.0, math.hypot(dx, dy) / self.r)
        self.selection_changed.emit(self.hue, self.saturation)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._select_at(event.position())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._select_at(event.position())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.selection_finished.emit()


class WheelDialog(QDialog):
    """Modeless colour picker: preview on drag, stay open until Close."""

    def __init__(self, parent, hue, saturation, preview_callback, finished_callback):
        super().__init__(parent)
        self.setWindowTitle("Live light colour preview")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._preview_callback = preview_callback
        self._finished_callback = finished_callback
        self._dirty = False
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Drag around the wheel to preview colours on your approved lights."))
        self.wheel = Wheel(hue, saturation)
        layout.addWidget(self.wheel, alignment=Qt.AlignmentFlag.AlignCenter)
        self.readout = QLabel()
        self.readout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.readout)
        self.wheel.selection_changed.connect(self.update_readout)
        self.wheel.selection_finished.connect(self.flush_preview)
        self.update_readout(self.wheel.hue, self.wheel.saturation, schedule=False)
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(140)
        self.preview_timer.timeout.connect(self.flush_preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        note = QLabel("Colours apply as you move. Close keeps the last selected colour; there is no Cancel/undo.")
        note.setWordWrap(True)
        layout.addWidget(note)

    def update_readout(self, h, s, schedule=True):
        self.readout.setText(f"Hue {round(h)}°    ·    Saturation {round(s * 100)}%")
        if schedule:
            self._dirty = True
            self.preview_timer.start()

    def flush_preview(self):
        if self._dirty:
            self._dirty = False
            self.preview_timer.stop()
            self._preview_callback((round(self.wheel.hue, 1), round(self.wheel.saturation, 3)))

    def closeEvent(self, event):
        self.flush_preview()
        self._finished_callback()
        super().closeEvent(event)


class TokenPairingDialog(QDialog):
    """GUI front end to the upstream generate-token pairing handshake.

    Only the explicit helper child receives the hub IP. Its captured output is
    never shown or logged, since the token is a hub-wide credential.
    """

    def __init__(self, parent, ip: str):
        super().__init__(parent)
        self.setWindowTitle("Generate DIRIGERA access token")
        self.setMinimumSize(540, 240)
        self.resize(640, 285)
        self.generated_token = None
        self.ip = ip
        self._output = bytearray()
        self._retry = False

        layout = QVBoxLayout(self)
        instructions = QLabel(
            "1. The app will start a pairing request with your DIRIGERA hub.\n"
            "2. Press the physical action button underneath the hub.\n"
            "3. Click Continue here to complete pairing."
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        self.status = QLabel("Starting the local pairing request…")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch()
        row = QHBoxLayout()
        self.continue_button = QPushButton("Continue")
        self.continue_button.setEnabled(False)
        self.continue_button.clicked.connect(self.continue_pairing)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        row.addStretch()
        row.addWidget(self.continue_button)
        row.addWidget(cancel)
        layout.addLayout(row)

        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.process.started.connect(self._started)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.readyReadStandardError.connect(self._discard_error_output)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)
        QTimer.singleShot(0, self._start)

    def _start(self):
        self._retry = False
        self._output.clear()
        self.continue_button.setText("Continue")
        self.continue_button.setEnabled(False)
        self.status.setText("Starting the local pairing request…")
        # Launch the same app in helper mode, rather than assuming a shell or
        # an installed generate-token executable exists. Works with bundled apps.
        args = ["--token-helper", self.ip]
        executable = sys.executable
        if not getattr(sys, "frozen", False):
            args = ["-u", str(Path(__file__).resolve()), *args]
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUNBUFFERED", "1")
        self.process.setProcessEnvironment(environment)
        self.process.start(executable, args)

    def _started(self):
        self.status.setText(
            "Pairing request started. Press the action button underneath DIRIGERA, "
            "then click Continue."
        )
        self.continue_button.setEnabled(True)

    def _read_output(self):
        # Upstream prints the access token to stdout. Keep it private.
        self._output.extend(bytes(self.process.readAllStandardOutput()))

    def _discard_error_output(self):
        # Never copy child-process stderr into visible diagnostics: an upstream
        # exception could contain sensitive request information.
        self.process.readAllStandardError()

    def continue_pairing(self):
        if self._retry:
            self._start()
            return
        if self.process.state() != QProcess.ProcessState.Running:
            return
        self.continue_button.setEnabled(False)
        self.status.setText("Completing pairing with DIRIGERA…")
        self.process.write(b"\n")

    def _finished(self, exit_code, exit_status):
        self._read_output()
        token = extract_generated_token(self._output.decode("utf-8", errors="replace"))
        self._output.clear()
        if exit_code == 0 and exit_status == QProcess.ExitStatus.NormalExit and token:
            self.generated_token = token
            self.accept()
            return
        self._show_failure("Pairing was not completed. Check the hub address, press the action button, and try again.")

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._show_failure("Unable to start the pairing helper. Please reinstall the app or report this error.")

    def _show_failure(self, message):
        self.status.setText(message)
        self._retry = True
        self.continue_button.setText("Try again")
        self.continue_button.setEnabled(True)

    def reject(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
        self._output.clear()
        super().reject()

    def closeEvent(self, event):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
        self._output.clear()
        super().closeEvent(event)


class ConnectionDialog(QDialog):
    def __init__(self, parent, config):
        super().__init__(parent)
        self.setWindowTitle("Connect to IKEA DIRIGERA")
        self.setMinimumSize(620, 335)
        self.resize(720, 390)
        outer = QVBoxLayout(self)
        note = QLabel(
            "Enter the local IP address or hostname of your DIRIGERA hub. "
            "You can generate a new access token here without opening a terminal."
        )
        note.setWordWrap(True)
        outer.addWidget(note)
        form = QFormLayout()
        self.ip = QLineEdit(config.get("ip", ""))
        self.ip.setPlaceholderText("192.168.1.20")
        self.token = QLineEdit(config.get("token", ""))
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("Access token")
        self.generate_button = QPushButton("Generate token")
        self.generate_button.setToolTip("Pair locally with DIRIGERA using its physical action button")
        self.generate_button.clicked.connect(self.generate_token)
        token_row = QWidget(self)
        token_layout = QHBoxLayout(token_row)
        token_layout.setContentsMargins(0, 0, 0, 0)
        token_layout.addWidget(self.token, 1)
        token_layout.addWidget(self.generate_button)
        form.addRow("Hub address", self.ip)
        form.addRow("Access token", token_row)
        outer.addLayout(form)
        self.address_help = QLabel()
        self.address_help.setWordWrap(True)
        outer.addWidget(self.address_help)
        self.ip.textEdited.connect(self.reset_address_help)
        self.reset_address_help()
        ip_note = QLabel(
            "<b>Recommended: reserve an IP address for DIRIGERA.</b> "
            "Set a DHCP reservation in your router so the hub keeps the same local IP address. "
            "This helps Lysnav reconnect reliably. See your router's manual "
            "for instructions on configuring DHCP reservations."
        )
        ip_note.setWordWrap(True)
        outer.addWidget(ip_note)
        outer.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.check_accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def reset_address_help(self, *_):
        self.address_help.setText(
            "Find the hub's local IP address in your router's connected-device list."
        )

    def generate_token(self):
        ip = self.ip.text().strip()
        if not valid_hub_address(ip):
            self.address_help.setText(
                "Enter the hub's local IP address or hostname first, such as 192.168.1.20. "
                "Use the address by itself."
            )
            self.ip.setFocus()
            self.ip.selectAll()
            return
        pairing = TokenPairingDialog(self, ip)
        if pairing.exec() == QDialog.DialogCode.Accepted and pairing.generated_token:
            self.token.setText(pairing.generated_token)
            self.token.setFocus()
            # User must still choose Save before credentials are persisted.

    def check_accept(self):
        ip = self.ip.text().strip()
        token = self.token.text().strip()
        if not valid_hub_address(ip) or not token:
            QMessageBox.warning(self, "Invalid settings", "Enter a valid hub IP/hostname and an access token.")
            return
        self.accept()


class ApprovalDialog(QDialog):
    def __init__(self, parent, lights, sensors, approved_lights, approved_sensors):
        super().__init__(parent)
        self.setWindowTitle("Choose my lights and sensors")
        self.resize(620, 560)
        layout = QVBoxLayout(self)
        message = QLabel(
            "Only check devices that belong to you. Room headings cannot be checked, "
            "so selecting a whole shared room accidentally is impossible.\n"
            "New devices always start unapproved. Sensor readings are read-only."
        )
        message.setWordWrap(True)
        layout.addWidget(message)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Device", "Status"])
        self.tree.setColumnWidth(0, 400)
        layout.addWidget(self.tree)
        self._populate("LIGHTS", lights, approved_lights)
        self._populate("CLIMATE SENSORS (read-only)", sensors, approved_sensors)
        self.tree.expandAll()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.check_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.selection_lights = {}
        self.selection_sensors = {}

    def _populate(self, kind, devices, approvals):
        section = QTreeWidgetItem(self.tree, [kind, ""])
        grouped = defaultdict(list)
        for d in devices:
            grouped[(d.room_id, d.room_name)].append(d)
        for (rid, name), members in sorted(grouped.items(), key=lambda item: item[0][1].casefold()):
            room_item = QTreeWidgetItem(section, [f"{name} ({len(members)})", ""])
            # Deliberately no checkbox on room headings, even in shared rooms.
            for device in members:
                status = "Online" if device.reachable else "Offline"
                if kind.startswith("CLIMATE"):
                    values = []
                    if device.temperature is not None:
                        values.append(f"{device.temperature:.1f} °C")
                    if device.humidity is not None:
                        values.append(f"{device.humidity:g}% RH")
                    if values:
                        status += " · " + " / ".join(values)
                child = QTreeWidgetItem(room_item, [device.name, status])
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setData(0, Qt.ItemDataRole.UserRole, ("sensor" if kind.startswith("CLIMATE") else "light", device.id, device.room_id))
                child.setCheckState(0, Qt.CheckState.Checked if approvals.get(device.id) == device.room_id else Qt.CheckState.Unchecked)

    def check_save(self):
        lights, sensors, names = {}, {}, []
        for i in range(self.tree.topLevelItemCount()):
            section = self.tree.topLevelItem(i)
            for j in range(section.childCount()):
                room = section.child(j)
                for k in range(room.childCount()):
                    item = room.child(k)
                    if item.checkState(0) == Qt.CheckState.Checked:
                        kind, device_id, rid = item.data(0, Qt.ItemDataRole.UserRole)
                        (sensors if kind == "sensor" else lights)[device_id] = rid
                        names.append(f"• {kind}: {room.text(0)} / {item.text(0)}")
        summary = "\n".join(names[:18])
        if len(names) > 18:
            summary += f"\n…and {len(names) - 18} more"
        reply = QMessageBox.question(
            self, "Confirm selection",
            f"Approve {len(lights)} light(s) for control and {len(sensors)} sensor(s) for viewing?\n\n"
            f"{summary or 'Nothing selected: all controls remain disabled.'}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.selection_lights, self.selection_sensors = lights, sensors
            self.accept()


class ClickToJumpSlider(QSlider):
    """Click-to-jump track and notify the window about mouse-wheel edits.

    Qt changes the slider value on wheel input without emitting sliderReleased.
    The main window owns the debounce timer, so periodic tab re-rendering cannot
    destroy a pending approved-light command when this slider is replaced.
    """
    trackClicked = Signal(int)
    wheelAdjusted = Signal(int)

    def wheelEvent(self, event):
        was = self.value()
        super().wheelEvent(event)
        if self.isEnabled() and self.value() != was:
            self.wheelAdjusted.emit(self.value())

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            option = QStyleOptionSlider()
            self.initStyleOption(option)
            handle = self.style().subControlRect(
                QStyle.ComplexControl.CC_Slider, option,
                QStyle.SubControl.SC_SliderHandle, self)
            point = event.position().toPoint()
            if not handle.contains(point):
                groove = self.style().subControlRect(
                    QStyle.ComplexControl.CC_Slider, option,
                    QStyle.SubControl.SC_SliderGroove, self)
                if self.orientation() == Qt.Orientation.Horizontal:
                    span = max(1, groove.width() - handle.width())
                    position = point.x() - groove.x() - handle.width() // 2
                else:
                    span = max(1, groove.height() - handle.height())
                    position = point.y() - groove.y() - handle.height() // 2
                value = QStyle.sliderValueFromPosition(
                    self.minimum(), self.maximum(), position, span, option.upsideDown)
                self.setValue(value)
                self.trackClicked.emit(value)
                event.accept()
                return
        super().mousePressEvent(event)


class TraySettingsDialog(QDialog):
    def __init__(self, parent, room_id, rooms):
        super().__init__(parent)
        self.setWindowTitle("System tray settings")
        self.setMinimumSize(600, 340)
        self.resize(730, 380)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.room = QComboBox()
        self.room.addItem("Automatic (only if one approved room)", "")
        for rid, name in sorted(rooms.items(), key=lambda p: p[1].casefold()):
            self.room.addItem(name, rid)
        idx = self.room.findData(room_id)
        self.room.setCurrentIndex(max(0, idx))
        form.addRow("Tray control room", self.room)
        layout.addLayout(form)
        shortcuts = QGroupBox("System tray shortcuts")
        shortcut_layout = QVBoxLayout(shortcuts)
        intro = QLabel("Use the Lysnav icon by the clock. If it is hidden, drag it out of the system tray overflow.")
        intro.setWordWrap(True)
        shortcut_layout.addWidget(intro)
        scroll_tip = QLabel(
            "<b>Scroll over the icon:</b> Brighten or dim the approved lights "
            "that are already on, by 5% per wheel notch."
        )
        scroll_tip.setWordWrap(True)
        shortcut_layout.addWidget(scroll_tip)
        click_tip = QLabel(
            "<b>Middle-click the icon:</b> Turn all approved lights in the selected "
            "room on or off."
        )
        click_tip.setWordWrap(True)
        shortcut_layout.addWidget(click_tip)
        safety_tip = QLabel(
            "These shortcuts only control lights you explicitly approved. "
            "Scrolling never turns on a switched-off light."
        )
        safety_tip.setWordWrap(True)
        shortcut_layout.addWidget(safety_tip)
        layout.addWidget(shortcuts, stretch=1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        # Diagnostics belong with tray settings, not in an unrelated menu bar.
        # ActionRole keeps the settings dialog open after viewing diagnostics.
        inspect = buttons.addButton("Input diagnostics…", QDialogButtonBox.ButtonRole.ActionRole)
        inspect.clicked.connect(lambda: parent.show_tray_diagnostics(dialog_parent=self))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(800, 660)
        self.config = read_config()
        self.lights = []
        self.sensors = []
        self.sensor_warning = ""
        self.worker = None
        self.color_dialog = None
        self._tray_pending_level = None
        self._tray_pending_ids = ()
        self._tray_scroll_accumulator = 0
        self._tray_last_wheel = None
        self._tray_last_middle = False
        self._tray_brightness_timer = QTimer(self)
        self._tray_brightness_timer.setSingleShot(True)
        self._tray_brightness_timer.setInterval(600)  # feedback remains visible before sending
        self._tray_brightness_timer.timeout.connect(self.flush_tray_brightness)
        # This timer lives on the persistent main window rather than a slider
        # widget recreated by each hub refresh. Wheel edits cannot get dropped
        # merely because the tabs were rebuilt before the debounce elapsed.
        self._slider_wheel_pending = {}
        self._slider_wheel_timer = QTimer(self)
        self._slider_wheel_timer.setSingleShot(True)
        self._slider_wheel_timer.setInterval(250)
        self._slider_wheel_timer.timeout.connect(self.flush_slider_wheel)
        self._really_quit = False
        self._quit_shortcut = QShortcut(QKeySequence("Ctrl+Q"), self)
        self._quit_shortcut.activated.connect(self.quit_application)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(30_000)
        self.refresh_timer.timeout.connect(self.refresh)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        header = QHBoxLayout()
        heading = QLabel("Lysnav  /  My lights")
        heading.setStyleSheet("font-weight: 700; font-size: 19px; padding: 5px 0")
        header.addWidget(heading, stretch=1)
        self.approvals_btn = QPushButton("Choose devices")
        self.approvals_btn.clicked.connect(self.choose_devices)
        self.approvals_btn.setEnabled(False)
        header.addWidget(self.approvals_btn)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        header.addWidget(self.refresh_btn)
        self.tray_btn = QPushButton("Tray settings")
        self.tray_btn.clicked.connect(self.change_tray_settings)
        header.addWidget(self.tray_btn)
        self.connect_btn = QPushButton("Hub settings")
        self.connect_btn.clicked.connect(self.change_connection)
        header.addWidget(self.connect_btn)
        self.about_btn = QPushButton("About")
        self.about_btn.clicked.connect(self.show_about)
        header.addWidget(self.about_btn)
        # No extra top-level menu bar: tray diagnostics are in Tray settings.
        # Ctrl+Q remains an emergency quit shortcut if the tray is unavailable.
        layout.addLayout(header)

        self.explainer = QLabel("Only approved bulbs receive commands. Room controls never affect unapproved bulbs; climate sensors are read-only.")
        self.explainer.setWordWrap(True)
        layout.addWidget(self.explainer)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, stretch=1)
        welcome = QLabel("Connect to DIRIGERA and choose your lights to begin.")
        welcome.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tabs.addTab(welcome, "Start")
        self.status = QLabel("No hub configured")
        self.status.setObjectName("status")
        layout.addWidget(self.status)
        self._create_tray()
        if self.config.get("ip") and self.config.get("token"):
            self.start_worker()
        else:
            QTimer.singleShot(150, self.change_connection)

    def _create_tray(self):
        self._tray_menu = self.create_tray_menu()
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setContextMenu(self._tray_menu)
        self.tray.activated.connect(self.on_tray_activation)
        self.tray_wheel = TrayWheelHook(self.tray, self._tray_menu)
        # Deliver feedback and lighting work after the Windows hook returns.
        self.tray_wheel.wheel.connect(self.on_tray_scroll, Qt.ConnectionType.QueuedConnection)
        self.update_tray_menu()
        self.update_tray_tooltip()
        if sys.platform == "win32":
            self.tray.show()
            self.tray_wheel.start()

    def on_tray_activation(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_and_raise()
        elif reason == QSystemTrayIcon.ActivationReason.MiddleClick:
            self._tray_last_middle = True
            self.on_tray_middle()

    def _current_tray_room(self):
        return tray_room(self.lights, self.config["approved"], self.config.get("tray_room", ""))

    def _tray_tooltip(self):
        rid = self._current_tray_room()
        visible = approved_visible(self.lights, self.config["approved"])
        room_lights = [l for l in visible if l.room_id == rid] if rid else []
        name = room_lights[0].room_name if room_lights else "No wheel room selected"
        sensors = approved_climate(self.sensors, self.config["approved_sensors"])
        matching = [s for s in sensors if s.room_id == rid and s.reachable] if rid else []
        if not matching:
            matching = [s for s in sensors if s.reachable]
        temperature = next((s.temperature for s in matching if s.temperature is not None), None)
        humidity = next((s.humidity for s in matching if s.humidity is not None), None)
        summary = []
        if temperature is not None:
            summary.append(f"{temperature:.1f} °C")
        if humidity is not None:
            summary.append(f"{humidity:g}% RH")
        climate = " · ".join(summary) if summary else "No approved climate readings"
        # Keep climate readings before the room name within the Shell tooltip limit.
        return f"{climate}\n{name}"

    def update_tray_tooltip(self):
        self.tray.setToolTip(self._tray_tooltip())

    def _approved_rooms(self):
        return {light.room_id: light.room_name
                for light in approved_visible(self.lights, self.config['approved'])}

    def create_tray_menu(self):
        menu = QMenu(self)
        menu.addAction("Show controls", self.show_and_raise)
        self._room_power_action = menu.addAction("Toggle approved room lights (middle-click)", self.on_tray_middle)
        menu.addAction("Refresh", self.refresh)
        self._room_menu = menu.addMenu("Scroll brightness in…")
        self._room_menu.triggered.connect(self.room_action_triggered)
        self._menu_rooms = None
        menu.addAction("Tray settings", self.change_tray_settings)
        menu.addAction(f"About {APP_NAME}…", self.show_about)
        menu.addSeparator()
        menu.addAction(f"Quit {APP_NAME}", self.quit_application)
        return menu

    def room_action_triggered(self, action):
        self.set_tray_room(action.data())

    def update_tray_menu(self):
        selected = self.config.get("tray_room", "")
        power = tray_power_targets(self.lights, self.config["approved"], selected)
        self._room_power_action.setEnabled(bool(power))
        rooms = self._approved_rooms()
        if rooms != self._menu_rooms:
            self._room_menu.clear()
            choices = [("", "Automatic (one approved room)")]
            choices.extend(sorted(rooms.items(), key=lambda pair: pair[1].casefold()))
            for room_id, name in choices:
                action = self._room_menu.addAction(name)
                action.setData(room_id)
                action.setCheckable(True)
            self._menu_rooms = rooms
        for action in self._room_menu.actions():
            action.setChecked(action.data() == selected)

    def set_tray_room(self, rid):
        self._tray_brightness_timer.stop()
        self._tray_pending_level = None
        self._tray_pending_ids = ()
        self._tray_scroll_accumulator = 0
        self.config["tray_room"] = rid
        write_config(self.config)
        self.update_tray_tooltip()
        self.update_tray_menu()

    def change_tray_settings(self):
        rooms = self._approved_rooms()
        dialog = TraySettingsDialog(self, self.config.get("tray_room", ""), rooms)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.set_tray_room(dialog.room.currentData())
        dialog.deleteLater()

    def show_about(self):
        def installed_version(name):
            try:
                return package_version(name)
            except PackageNotFoundError:
                return 'not detected'

        from PySide6.QtWidgets import QTextBrowser
        dialog = QDialog(self)
        dialog.setWindowTitle(f'About {APP_NAME}')
        dialog.resize(560, 490)
        layout = QVBoxLayout(dialog)
        heading = QLabel(f'{APP_NAME}  ·  {VERSION}')
        heading.setStyleSheet('font-size: 20px; font-weight: bold')
        layout.addWidget(heading)
        text = QTextBrowser()
        text.setOpenExternalLinks(True)
        text.setHtml(f"""
            <p>A local controller for IKEA DIRIGERA on Windows.</p>
            <p>Lysnav is an independent third-party application and is not affiliated with, endorsed by, or sponsored by IKEA.</p>
            <p><b>Features:</b> approved-only lighting controls, live colour selection,
            Kelvin and brightness sliders, read-only climate sensors, and Windows
            system tray controls.</p>
            <p><b>Development:</b> <a href='https://github.com/alsiniz'>alsiniz</a> (creator of Lysnav for KDE Plasma), with implementation and troubleshooting assistance from <a href='https://openai.com/'>OpenAI ChatGPT</a>.</p>
            <p><b>Open-source components:</b></p>
            <ul>
            <li><a href='https://github.com/Leggin/dirigera'>dirigera</a>
                {installed_version('dirigera')}, MIT, by Leggin and contributors;
                reverse-engineered DIRIGERA local HTTPS API.</li>
            <li><a href='https://doc.qt.io/qtforpython-6/'>PySide6 / Qt for Python</a>
                {installed_version('PySide6-Essentials')}, LGPLv3/GPLv3/commercial options,
                by The Qt Company and Qt for Python contributors.</li>
            </ul>
            <p>This Windows port is based on Lysnav for KDE Plasma and shared under the <b>MIT License</b>.
            Free to use, modify, share, and sell, with
            preservation of the copyright and license notice. See the LICENSE file for exact terms.
            Upstream components keep their own licenses. The upstream library maintainers do not endorse this project.</p>
            <p><b>Keep your hub access token private.</b> This app only sends
            lighting commands to the bulbs you explicitly approve. This helps
            prevent accidentally changing other residents’ lights.</p>
            <p>However, the IKEA hub does not limit the access token to those
            approved bulbs. Anyone who obtains your token could use another
            program to control <b>any device connected to the hub</b>.
            Treat the token like a password and never share it.</p>
        """)
        layout.addWidget(text)
        # Use a native Qt control rather than Ko-fi's web-only JavaScript widgets.
        # It opens the user's browser only when clicked; no network access is
        # needed to open the About dialog or run the application.
        if KOFI_URL:
            support = QPushButton("Support on Ko-fi")
            support.setToolTip("Opens the original project's Ko-fi page in your default browser")
            support.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(KOFI_URL)))
            layout.addWidget(support, alignment=Qt.AlignmentFlag.AlignHCenter)
        licenses = Path(__file__).resolve().parent / "licenses"
        if getattr(sys, "frozen", False) and not licenses.is_dir():
            licenses = Path(sys.executable).parent / "licenses"
        if licenses.is_dir():
            notices = QPushButton("Licenses and notices")
            notices.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(licenses))))
            layout.addWidget(notices, alignment=Qt.AlignmentFlag.AlignHCenter)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.accept)
        layout.addWidget(buttons)
        dialog.exec()

    def show_tray_diagnostics(self, dialog_parent=None):
        room = self._current_tray_room()
        bulbs = tray_power_targets(self.lights, self.config["approved"], self.config.get("tray_room", ""))
        eligible = tray_targets(self.lights, self.config["approved"], self.config.get("tray_room", ""))
        registration = "Installed" if self.tray_wheel.installed else "Unavailable"
        message = (
            f"System tray available: {QSystemTrayIcon.isSystemTrayAvailable()}\n"
            f"Icon visible by the clock: {self.tray.geometry().isValid()}\n"
            f"Wheel hook: {registration}\n"
            f"Wheel events over the icon: {self.tray_wheel.count}\n"
            f"Last wheel delta: {self._tray_last_wheel}\n"
            f"Middle-click received: {self._tray_last_middle}\n"
            f"Wheel hook error: {self.tray_wheel.error or 'None'}\n"
            f"Selected room: {self._approved_rooms().get(room, 'None')}\n"
            f"Approved room bulbs: {len(bulbs)}\n"
            f"On and dimmable: {len(eligible)}\n\n"
            "If wheel input is unavailable, move the icon out of the system tray overflow "
            "and try reinstalling the wheel hook."
        )
        dialog = QMessageBox(QMessageBox.Icon.Information, "System tray diagnostics",
                             message, parent=dialog_parent or self)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        retry = dialog.addButton("Restart wheel hook", QMessageBox.ButtonRole.ActionRole)
        dialog.addButton(QMessageBox.StandardButton.Close)
        dialog.exec()
        if dialog.clickedButton() == retry and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
            self.tray_wheel.restart()
            self.status.setText(self.tray_wheel.error or "Tray wheel hook restarted")

    def _tray_feedback(self, text):
        """Show the new brightness next to the tray before sending it to the hub."""
        QToolTip.showText(QCursor.pos(), f"<p>{escape(text)}</p>", self, msecShowTime=1400)

    def on_tray_scroll(self, delta):
        self._tray_last_wheel = delta
        if not self.worker:
            self.status.setText("Tray scroll received, but the hub is not connected")
            return
        targets = tray_targets(self.lights, self.config["approved"],
                               self.config.get("tray_room", ""))
        if not targets:
            self.status.setText("Tray scroll received, but no approved ON bulbs in the selected room")
            self._tray_feedback("No approved ON bulbs available for dimming")
            return
        ids = tuple(l.id for l in targets)
        if self._tray_pending_ids != ids:
            self._tray_pending_level = None
            self._tray_scroll_accumulator = 0
        self._tray_pending_ids = ids
        steps, self._tray_scroll_accumulator = wheel_steps(int(delta), self._tray_scroll_accumulator)
        if not steps:
            self.status.setText(f"Tray scroll received: partial delta {delta}")
            return
        level = (self._tray_pending_level if self._tray_pending_level is not None
                 else stepped_brightness([l.brightness for l in targets], 0))
        self._tray_pending_level = max(1, min(100, level + steps * 5))
        self._tray_brightness_timer.start()  # debounce rapid wheel turns
        room_name = targets[0].room_name
        self.status.setText(f"{room_name} · target brightness {self._tray_pending_level}%")
        self._tray_feedback(f"{room_name} lights: {self._tray_pending_level}%")

    def flush_tray_brightness(self):
        if self._tray_pending_level is None or not self._tray_pending_ids:
            return
        ids = tuple(l.id for l in tray_targets(self.lights, self.config["approved"],
                                                self.config.get("tray_room", "")))
        if ids != self._tray_pending_ids:
            self._tray_pending_level = None
            self._tray_pending_ids = ()
            self.update_tray_tooltip()
            return
        self.control(ids, "brightness", self._tray_pending_level)

    def on_tray_middle(self):
        if not self.worker:
            self.status.setText("Middle-click received, but not connected")
            return
        self._tray_brightness_timer.stop()
        self._tray_pending_level = None
        self._tray_pending_ids = ()
        self._tray_scroll_accumulator = 0
        self.update_tray_tooltip()
        targets = tray_power_targets(self.lights, self.config["approved"],
                                     self.config.get("tray_room", ""))
        if not targets:
            self.status.setText("Middle-click received; no approved room lights")
            return
        if not all(l.reachable and l.can("power") for l in targets):
            self.status.setText("Room toggle refused: an approved bulb is offline or lacks on/off control")
            self._tray_feedback("Room toggle unavailable: an approved bulb is offline")
            return
        ids = [l.id for l in targets]
        self.worker.submit("toggle", ids)  # worker re-fetches and validates all IDs
        self.status.setText(f"Toggle requested for {len(ids)} approved room lights")

    def show_and_raise(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def change_connection(self):
        dialog = ConnectionDialog(self, self.config)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        old = self.worker
        if old:
            # Avoid leaving a system hook installed while the GUI thread waits.
            self.tray_wheel.stop()
            try:
                old.stop()
                stopped = old.wait(30_000)
            finally:
                if self.tray.isVisible():
                    self.tray_wheel.start()
            if not stopped:
                QMessageBox.warning(self, "Hub busy", "An existing request is still running. Try changing hubs again after it finishes.")
                return
        self.worker = None
        self._slider_wheel_timer.stop()
        self._slider_wheel_pending.clear()
        new_ip = dialog.ip.text().strip()
        same_hub = new_ip == self.config.get("ip", "")
        self.config = {"ip": new_ip, "token": dialog.token.text().strip(),
                       "approved": dict(self.config["approved"]) if same_hub else {},
                       "approved_sensors": dict(self.config["approved_sensors"]) if same_hub else {},
                       "tray_room": self.config.get("tray_room", "") if same_hub else ""}
        write_config(self.config)
        self.lights = []
        self.sensors = []
        self._tray_pending_level = None
        self._tray_pending_ids = ()
        self._tray_scroll_accumulator = 0
        self.update_tray_tooltip()
        self.clear_tab_pages()
        self.tabs.addTab(QLabel("Discovering lights. No devices have been approved yet."), "Start")
        self.start_worker()

    def start_worker(self):
        self.refresh_timer.stop()
        self.worker = HubWorker(self.config["ip"], self.config["token"], self.config["approved"])
        self.worker.loaded.connect(self.on_loaded)
        self.worker.completed.connect(self.on_completed)
        self.worker.failed.connect(self.on_failed)
        self.worker.start()
        self.status.setText(f"Connecting to {self.config['ip']}…")
        self.refresh()

    def refresh(self):
        if self.worker and not self.worker.isFinished():
            self.worker.submit("refresh")

    def on_loaded(self, result):
        lights, sensors, self.sensor_warning = result
        self.lights = lights
        self.sensors = sensors
        self.approvals_btn.setEnabled(True)
        self.refresh_timer.start()
        self.render_tabs()
        self.update_tray_menu()
        if self._tray_pending_level is not None and self._tray_pending_ids:
            current = {l.id: l for l in lights}
            if all(i in current and abs(current[i].brightness - self._tray_pending_level) <= 1
                   for i in self._tray_pending_ids):
                self._tray_pending_level = None
                self._tray_pending_ids = ()
        self.update_tray_tooltip()
        eligible = approved_visible(lights, self.config["approved"])
        moved_or_missing = len(self.config["approved"]) - len(eligible)
        eligible_sensors = approved_climate(sensors, self.config["approved_sensors"])
        self.status.setText(f"Connected · {len(eligible)} approved light(s) · {len(eligible_sensors)} visible sensor(s)" +
                            (f" · {moved_or_missing} unavailable/relocated light(s)" if moved_or_missing else "") +
                            (f" · {self.sensor_warning}" if self.sensor_warning else ""))

    def on_completed(self, message):
        self.status.setText(message)

    def on_failed(self, error):
        self.status.setText(f"Request failed: {error}")

    def choose_devices(self):
        if not self.lights and not self.sensors:
            QMessageBox.information(self, "Not connected", "No supported lights or sensors have been discovered yet.")
            return
        dialog = ApprovalDialog(self, self.lights, self.sensors,
                                self.config["approved"], self.config["approved_sensors"])
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._slider_wheel_timer.stop()
        self._slider_wheel_pending.clear()
        self.config["approved"] = dialog.selection_lights
        self.config["approved_sensors"] = dialog.selection_sensors
        write_config(self.config)
        if self.worker:
            self.worker.set_approved(dialog.selection_lights)
        self._tray_pending_level = None
        self._tray_pending_ids = ()
        self._tray_scroll_accumulator = 0
        self.render_tabs()
        self.update_tray_tooltip()
        self.update_tray_menu()
        self.status.setText(f"Saved: {len(dialog.selection_lights)} controllable light(s), "
                            f"{len(dialog.selection_sensors)} read-only sensor(s).")

    def queue_slider_wheel(self, ids, action, value):
        key = (tuple(ids), action)
        self._slider_wheel_pending[key] = value
        self._slider_wheel_timer.start()

    def cancel_slider_wheel(self, ids, action):
        self._slider_wheel_pending.pop((tuple(ids), action), None)
        if not self._slider_wheel_pending:
            self._slider_wheel_timer.stop()

    def flush_slider_wheel(self):
        pending = self._slider_wheel_pending
        self._slider_wheel_pending = {}
        # Every command passes through the exact same approved-device check
        # as slider dragging and clicking. No direct hub call from a widget.
        for (ids, action), value in pending.items():
            self.control(list(ids), action, value)

    def commit_slider(self, ids, action, value):
        self.cancel_slider_wheel(ids, action)
        self.control(list(ids), action, value)

    def control(self, ids, action, value):
        allowed = self.config["approved"]
        current = {l.id: l for l in approved_visible(self.lights, allowed)}
        if not ids or any(id_ not in current for id_ in ids):
            QMessageBox.warning(self, "Not approved", "This action includes an unapproved or relocated light. Nothing was changed.")
            return
        if self.worker:
            self.worker.submit("control", list(ids), action, value)
            self.status.setText(f"Sending {action} to {len(ids)} approved light(s)…")

    def preview_color(self, ids, value):
        current = {l.id: l for l in approved_visible(self.lights, self.config["approved"])}
        if not ids or any(id_ not in current for id_ in ids):
            self.status.setText("Live preview stopped: a bulb is no longer approved.")
            return
        if self.worker:
            self.worker.submit_preview(list(ids), value)

    def choose_color(self, ids, lights):
        if self.color_dialog is not None:
            self.color_dialog.close()  # Finish the previous target first.
        initial = lights[0]
        dialog = WheelDialog(self, initial.hue, initial.saturation,
                             lambda value, target=tuple(ids): self.preview_color(target, value),
                             lambda: self.refresh())
        self.color_dialog = dialog
        dialog.finished.connect(lambda result, d=dialog:
                                setattr(self, "color_dialog", None) if self.color_dialog is d else None)
        dialog.show()  # NOT exec(): the wheel remains open during live preview.
        dialog.raise_()

    def add_controls(self, layout, lights, is_group=False):
        ids = [l.id for l in lights]
        active = all(l.reachable for l in lights)
        # Only offer group actions supported by every approved bulb in that room.
        for action in ("power", "brightness", "temperature", "color"):
            supported = active and all(l.can(action) for l in lights)
            if action == "power":
                line = QHBoxLayout()
                on = QPushButton("Turn on")
                off = QPushButton("Turn off")
                on.setEnabled(supported)
                off.setEnabled(supported)
                on.clicked.connect(lambda checked=False, ids=ids: self.control(ids, "power", True))
                off.clicked.connect(lambda checked=False, ids=ids: self.control(ids, "power", False))
                line.addWidget(on)
                line.addWidget(off)
                line.addStretch()
                layout.addLayout(line)
            elif action in ("brightness", "temperature"):
                bounds = (1, 100) if action == "brightness" else (
                    max((l.temp_low or 0) for l in lights),
                    min((l.temp_high or 0) for l in lights)
                )
                if action == "temperature" and bounds[0] > bounds[1]:
                    supported = False
                line = QHBoxLayout()
                line.addWidget(QLabel("Brightness" if action == "brightness" else "Warm  ⇄  Cool"))
                slider = ClickToJumpSlider(Qt.Orientation.Horizontal)
                slider.setMinimum(bounds[0] if bounds[0] < bounds[1] else 1)
                slider.setMaximum(bounds[1] if bounds[0] < bounds[1] else 1)
                start = lights[0].brightness if action == "brightness" else (lights[0].temperature or bounds[0])
                slider.setValue(max(slider.minimum(), min(slider.maximum(), start)))
                slider.setEnabled(supported)
                label = QLabel()
                label.setMinimumWidth(68)
                label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                suffix = "%" if action == "brightness" else " K"
                label.setText(f"{slider.value()}{suffix}")
                slider.valueChanged.connect(lambda v, lbl=label, s=suffix: lbl.setText(f"{v}{s}"))
                slider.sliderPressed.connect(lambda ids=tuple(ids), a=action: self.cancel_slider_wheel(ids, a))
                slider.sliderReleased.connect(lambda sl=slider, ids=tuple(ids), a=action:
                                              self.commit_slider(ids, a, sl.value()))
                slider.trackClicked.connect(lambda value, ids=tuple(ids), a=action:
                                            self.commit_slider(ids, a, value))
                slider.wheelAdjusted.connect(lambda value, ids=tuple(ids), a=action:
                                             self.queue_slider_wheel(ids, a, value))
                line.addWidget(slider, stretch=1)
                line.addWidget(label)
                layout.addLayout(line)
            else:
                line = QHBoxLayout()
                button = QPushButton("Live colour wheel…")
                button.setEnabled(supported)
                button.clicked.connect(lambda checked=False, ids=ids, ls=lights: self.choose_color(ids, ls))
                if not supported:
                    button.setToolTip("RGB colour is available only when every target supports it and is online")
                line.addWidget(button)
                if is_group:
                    line.addWidget(QLabel("Only your approved lights"))
                line.addStretch()
                layout.addLayout(line)

    def clear_tab_pages(self):
        # Removing a tab leaves its page owned by Qt's internal stacked widget.
        while self.tabs.count():
            index = self.tabs.count() - 1
            page = self.tabs.widget(index)
            self.tabs.removeTab(index)
            page.deleteLater()

    def render_tabs(self):
        old_index = self.tabs.currentIndex()
        eligible = approved_visible(self.lights, self.config["approved"])
        self.clear_tab_pages()
        if not eligible:
            label = QLabel("No approved bulbs are currently available.\n\n"
                           "Choose devices to select the exact devices you own.")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            self.tabs.addTab(label, "My lights")
        rooms = defaultdict(list)
        for light in eligible:
            rooms[(light.room_id, light.room_name)].append(light)
        for (_, room_name), lights in sorted(rooms.items(), key=lambda pair: pair[0][1].casefold()):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            content = QWidget()
            column = QVBoxLayout(content)
            column.setSpacing(12)
            section_font = content.font()
            section_font.setBold(True)
            room_title = QLabel("Room controls")
            room_title.setObjectName("roomControlsHeading")
            room_title.setFont(section_font)
            column.addWidget(room_title)
            group = QGroupBox(f"{room_name} · {len(lights)} approved light(s)")
            group.setObjectName("approvedRoomControls")
            group.setToolTip("These controls affect only approved lights in this room.")
            group_layout = QVBoxLayout(group)
            self.add_controls(group_layout, lights, is_group=True)
            column.addWidget(group)
            divider = QFrame()
            divider.setObjectName("roomDeviceDivider")
            divider.setFrameShape(QFrame.Shape.HLine)
            divider.setFrameShadow(QFrame.Shadow.Sunken)
            column.addWidget(divider)
            devices_title = QLabel("Individual lights")
            devices_title.setObjectName("individualLightsHeading")
            devices_title.setFont(section_font)
            column.addWidget(devices_title)
            for light in lights:
                box = QGroupBox(light.name + ("  ·  offline" if not light.reachable else ""))
                box.setObjectName("individualLightControls")
                box.setToolTip("Controls only this approved light.")
                box_layout = QVBoxLayout(box)
                self.add_controls(box_layout, [light])
                column.addWidget(box)
            column.addStretch()
            scroll.setWidget(content)
            self.tabs.addTab(scroll, room_name)
        climate = approved_climate(self.sensors, self.config["approved_sensors"])
        if climate:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            content = QWidget()
            column = QVBoxLayout(content)
            note = QLabel("Live readings from your approved sensors. View-only: no sensor commands are sent.")
            note.setWordWrap(True)
            column.addWidget(note)
            # Matching relation_id groups multiple parts of one physical TIMMERFLOTTE.
            for sensor in climate:
                group = QGroupBox(f"{sensor.name}  ·  {sensor.room_name}")
                layout = QVBoxLayout(group)
                readings = []
                if sensor.temperature is not None:
                    readings.append(f"Temperature: {sensor.temperature:.1f} °C")
                if sensor.humidity is not None:
                    readings.append(f"Humidity: {sensor.humidity:g}% RH")
                if sensor.battery is not None:
                    readings.append(f"Battery: {sensor.battery}%")
                layout.addWidget(QLabel("   ·   ".join(readings) if readings else "No readings currently reported"))
                if not sensor.reachable:
                    layout.addWidget(QLabel("Sensor offline"))
                column.addWidget(group)
            column.addStretch()
            scroll.setWidget(content)
            self.tabs.addTab(scroll, "Climate")
        self.tabs.setCurrentIndex(min(max(0, old_index), self.tabs.count() - 1))

    def quit_application(self):
        self._really_quit = True
        self.close()

    def closeEvent(self, event):
        if not self._really_quit and self.tray.isVisible() and QSystemTrayIcon.isSystemTrayAvailable():
            self.hide()
            event.ignore()
            return
        self.refresh_timer.stop()
        self._tray_brightness_timer.stop()
        self._slider_wheel_timer.stop()
        self._slider_wheel_pending.clear()
        self.tray_wheel.stop()
        self.tray.hide()
        if self.worker:
            self.worker.stop()
            # The library uses 10-second HTTP timeouts; allow current calls to finish.
            self.worker.wait()
        event.accept()
        QApplication.instance().quit()


def main():
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(str(Path(__file__).resolve().parent / "assets" / "lysnav.ico")))
    if sys.argv[1:] == ["--check-runtime"]:
        # Exercise the frozen Qt platform and icon loading without opening a
        # window, installing the tray hook, or connecting to a hub.
        from dirigera.hub import auth

        if app.windowIcon().pixmap(16, 16).isNull() or not callable(auth.main):
            sys.exit(1)
        return

    app.setApplicationName("Lysnav")
    app.setApplicationDisplayName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    start_requested = "--tray" in sys.argv
    settings_dir = config_path().parent
    try:
        settings_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        QMessageBox.critical(None, APP_NAME, f"Could not open the settings directory: {error}")
        return

    # A Start menu launch should open the window of the existing tray process.
    server_name = "Lysnav-" + sha256(str(settings_dir).casefold().encode("utf-8")).hexdigest()[:24]
    instance = QLockFile(str(settings_dir / "instance.lock"))
    instance.setStaleLockTime(0)
    if not instance.tryLock(0):
        if instance.error() != QLockFile.LockError.LockFailedError:
            QMessageBox.critical(None, APP_NAME, "Could not lock the settings directory.")
        elif not start_requested:
            client = QLocalSocket()
            client.connectToServer(server_name)
            if not client.waitForConnected(1500):
                QMessageBox.information(None, APP_NAME, "Lysnav is already running. Open it from the system tray.")
        return

    QLocalServer.removeServer(server_name)
    server = QLocalServer(app)
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    listening = server.listen(server_name)
    window = MainWindow()

    def open_existing_window():
        while server.hasPendingConnections():
            client = server.nextPendingConnection()
            client.disconnectFromServer()
            client.deleteLater()
        window.show_and_raise()

    server.newConnection.connect(open_existing_window)
    start_in_tray = (
        start_requested
        and listening
        and bool(window.config["ip"] and window.config["token"])
        and QSystemTrayIcon.isSystemTrayAvailable()
    )
    if not start_in_tray:
        window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--token-helper":
        run_token_helper(sys.argv[2])
    else:
        main()
