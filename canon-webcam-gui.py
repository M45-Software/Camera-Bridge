#!/usr/bin/python3
"""KDE/Qt tray controls. All service operations run asynchronously."""
import os
import json
import shlex
import shutil
import sys
import time
from pathlib import Path

from PyQt5.QtCore import QProcess, QTimer, Qt
from PyQt5.QtGui import QIcon
from PyQt5.QtNetwork import QLocalServer, QLocalSocket
from PyQt5.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QScrollArea, QTabWidget,
                             QLabel, QMenu, QMessageBox, QPushButton, QSpinBox,
                             QSystemTrayIcon, QTextEdit, QVBoxLayout, QWidget)

SERVICE = os.environ.get('CANON_WEBCAM_SERVICE', 'canon-webcam.service')
PREVIEW = os.environ.get('CANON_WEBCAM_PREVIEW') == '1'
CONFIG_ROOT = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config'))
CONFIG = CONFIG_ROOT / 'canon-webcam/settings.conf'
AUTOSTART = CONFIG_ROOT / 'autostart/canon-webcam-tray.desktop'
WEBCAM_AUTOSTART = CONFIG_ROOT / 'autostart/camera-bridge-webcam.desktop'
COMMAND = os.environ.get('CANON_WEBCAM_COMMAND') or shutil.which('camera-bridge') or shutil.which('canon-webcam') or str(Path(__file__).with_name('canon-webcam.sh'))
DEFAULTS = dict(WIDTH=1280, HEIGHT=720, FPS=30, CAMERA_FPS=30, AUTO_TIMEOUT=60, SET_VIEWFINDER=1, ON_DEMAND=1, NATIVE_RESOLUTION=1, LIVE_VIEW_SIZE=1, AUTO_FPS=1, PASSTHROUGH=0, CAPTURE_BACKEND=0, NOTIFICATIONS=1)


def tray_icon(name):
    path = Path(__file__).parent / 'assets' / f'tray-{name}.svg'
    if not path.exists():
        path = Path('/usr/share/canon-webcam') / f'tray-{name}.svg'
    vector = QIcon(str(path))
    # KDE/XEmbed bridges can receive no pixmaps from an SVG-only QIcon,
    # whose availableSizes() is empty. Export explicit raster sizes to the tray.
    icon = QIcon()
    for size in (16, 22, 24, 32, 48, 64):
        pixmap = vector.pixmap(size, size)
        if not pixmap.isNull():
            icon.addPixmap(pixmap)
    return icon if not icon.isNull() else QIcon.fromTheme('camera-video')


def connected_camera(root=Path('/sys/bus/usb/devices')):
    """Read USB presence without opening the camera or disturbing capture."""
    for device in root.glob('*'):
        try:
            if (device / 'idVendor').read_text().strip().lower() != '04a9':
                continue
            product = (device / 'product').read_text().lower() if (device / 'product').exists() else ''
            interfaces = list(root.glob(device.name + ':*'))
            ptp = any((item / 'bInterfaceClass').read_text().strip() == '06'
                      for item in interfaces if (item / 'bInterfaceClass').exists())
            if ptp or any(name in product for name in ('camera', 'eos', 'powershot')):
                return f"usb:{int((device / 'busnum').read_text()):03d},{int((device / 'devnum').read_text()):03d}"
        except (OSError, ValueError):
            continue
    return None


def tray_presentation(connected, running, live_view, receiving, starting, problem, apps):
    connection = 'Camera connected' if connected else 'No camera connected'
    camera = 'Live view not checked'
    video = 'Video paused' if running else 'Webcam off'
    icon = 'connected' if connected else 'off'
    if not connected:
        camera = 'Live view off'
    elif running:
        if receiving:
            camera = 'Live view on'
            video = 'Sending video' + (' to ' + ', '.join(apps) if apps else '')
            icon = 'live'
        elif problem:
            camera = 'Camera needs attention'
            icon = 'warning'
        elif starting:
            camera = 'Starting live view…'
            video = 'Waiting for camera video'
            icon = 'starting'
        elif live_view == 'on':
            camera = 'Live view on'
            icon = 'awake'
        elif live_view == 'off':
            camera = 'Camera resting'
    return icon, (connection, camera, video)


def startup_enabled(path):
    try:
        return not any(line.strip().lower() == 'hidden=true' for line in path.read_text().splitlines())
    except FileNotFoundError:
        return True  # Packaged XDG entries enable both options by default.


def write_startup(path, enabled, tray):
    atomic_write(path, '[Desktop Entry]\nType=Application\nName=Camera Bridge' + (' Tray' if tray else ' Webcam') +
                 '\nExec=/usr/bin/camera-bridge ' + ('tray' if tray else 'start') +
                 '\nIcon=camera-bridge\nTerminal=false\nHidden=' + ('false' if enabled else 'true') + '\n')


def read_settings():
    values = {}
    if CONFIG.exists():
        for line in CONFIG.read_text().splitlines():
            key, sep, value = line.partition('=')
            if sep and key in DEFAULTS and value.isascii() and value.isdigit():
                values[key] = int(value)
    return values


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(text)
    temp.chmod(0o600)
    temp.replace(path)


class Webcam(QWidget):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.setWindowTitle('Camera Bridge — Preview' if PREVIEW else 'Camera Bridge')
        self.setWindowIcon(app.windowIcon())
        self.resize(420, 450)
        self.running = False
        self.busy = False
        self.pending_action = None
        self.quitting = False
        self.action_buttons = []
        self.saved_values = None
        self.applied_values = None
        self.startup_ready = False
        self.legacy_login_enabled = False
        self.quit_requested = False
        self.last_problem = None
        self.last_notification_at = 0
        self.shutting_down = False
        app.aboutToQuit.connect(self.shutdown)
        self.initialized = False
        self.poll = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        brand = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(app.windowIcon().pixmap(48, 48))
        brand.addWidget(icon)
        title = QLabel('Camera Bridge')
        title.setStyleSheet('font-size: 20px; font-weight: 600;')
        brand.addWidget(title, 1)
        layout.addLayout(brand)
        self.status = QLabel('Checking…')
        self.status.setStyleSheet('font-size: 24px; font-weight: 600;')
        layout.addWidget(self.status)
        self.summary = QLabel('Your camera starts automatically when an app needs it.')
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.PlainText)
        layout.addWidget(self.summary)
        hint = QLabel('Select Camera-Bridge in your video app.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()
        self.toggle = QPushButton('Turn on')
        self.toggle.setMinimumHeight(36)
        self.toggle.setEnabled(False)
        self.toggle.clicked.connect(lambda: self.control('stop' if self.running else 'start'))
        layout.addWidget(self.toggle)
        self.retry = QPushButton('Try again')
        self.retry.clicked.connect(lambda: self.control('retry'))
        self.retry.hide()
        layout.addWidget(self.retry)
        footer = QHBoxLayout()
        settings = QPushButton('Settings…')
        settings.clicked.connect(self.open_settings)
        footer.addWidget(settings)
        self.close_button = QPushButton('Close window')
        self.close_button.setToolTip('Hide this window. The tray and webcam keep running.')
        self.close_button.clicked.connect(self.close_window)
        footer.addWidget(self.close_button)
        quit_button = QPushButton('Quit…')
        self.quit_button = quit_button
        quit_button.setToolTip('Stop the webcam and exit.')
        quit_button.clicked.connect(self.quit_and_stop)
        footer.addWidget(quit_button)
        layout.addLayout(footer)
        if PREVIEW:
            preview = QLabel('Temporary preview · your installation is unchanged')
            preview.setWordWrap(True)
            layout.addWidget(preview)
        self.boot = QCheckBox('Make webcam available at login')
        self.boot.setToolTip('Keep Camera-Bridge ready for video apps after signing in. The camera starts when needed.')
        self.login = QCheckBox('Show tray icon at login')
        self.login.setToolTip('Keep camera status and controls near the clock after signing in.')
        self.boot.setChecked(startup_enabled(WEBCAM_AUTOSTART))
        self.login.setChecked(startup_enabled(AUTOSTART))
        self.boot.setEnabled(not PREVIEW)
        self.login.setEnabled(not PREVIEW)
        layout.insertWidget(layout.count()-1, self.boot)
        layout.insertWidget(layout.count()-1, self.login)
        self.main_feedback = QLabel('Changes save automatically.')
        self.main_feedback.setWordWrap(True)
        layout.insertWidget(layout.count()-1, self.main_feedback)
        self.apply_button = QPushButton('Apply to current video…')
        self.apply_button.setToolTip('Reconnect the webcam to use saved picture and camera settings. You will be asked to close video apps.')
        self.apply_button.clicked.connect(self.save_changed_settings)
        self.apply_button.hide()
        layout.insertWidget(layout.count()-1, self.apply_button)
        self.action_buttons.append(self.apply_button)
        self.build_settings()
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.setInterval(500)
        self.autosave_timer.timeout.connect(self.autosave)
        for field in self.fields.values():
            field.valueChanged.connect(self.queue_autosave)
        for box in (self.native, self.auto_fps, self.passthrough, self.on_demand,
                    self.auto_pause, self.viewfinder, self.notifications):
            box.toggled.connect(self.queue_autosave)
        self.size.currentIndexChanged.connect(self.queue_autosave)
        self.backend.currentIndexChanged.connect(self.queue_autosave)
        self.boot.toggled.connect(self.save_startup)
        self.login.toggled.connect(self.save_startup)
        self.tray_icons = {name: tray_icon(name) for name in ('ready', 'live', 'live-dim', 'off', 'connected', 'awake', 'starting', 'warning')}
        self.tray = QSystemTrayIcon(self.tray_icons['off'], self)
        self.tray_state = 'off'
        self.blink_on = True
        self.blink_timer = QTimer(self)
        self.blink_timer.setInterval(750)
        self.blink_timer.timeout.connect(self.animate_tray)
        menu = QMenu(self)
        self.menu_status = menu.addAction('Checking…')
        self.menu_status.setEnabled(False)
        self.menu_camera = menu.addAction('Checking camera…')
        self.menu_live_view = menu.addAction('Checking live view…')
        self.menu_video = menu.addAction('Video paused')
        for action in (self.menu_camera, self.menu_live_view, self.menu_video):
            action.setEnabled(False)
        menu.addSeparator()
        menu.addAction('Open Camera Bridge…', self.open)
        menu.addSeparator()
        menu.addAction('Keep camera on', lambda: self.control('force-start'))
        menu.addAction('Keep camera off', lambda: self.control('force-stop'))
        menu.addAction('Use automatic camera control', lambda: self.control('auto'))
        menu.addSeparator()
        menu.addAction('Quit and stop webcam…', self.quit_and_stop)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.open() if reason in
                                   (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.tray.show()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(750)
        self.refresh()

    def build_settings(self):
        self.advanced = QDialog(self)
        self.advanced.setWindowTitle('Camera Bridge — Advanced settings')
        self.advanced.resize(630, 660)
        layout = QVBoxLayout(self.advanced)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        def section(title):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.NoFrame)
            page = QWidget()
            form = QFormLayout(page)
            form.setContentsMargins(16, 16, 16, 16)
            form.setVerticalSpacing(10)
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            scroll.setWidget(page)
            self.tabs.addTab(scroll, title)
            return form

        video = section('Video')
        automation = section('Automation')
        troubleshoot = section('Troubleshooting')
        self.fields = {}

        def checkbox(form, label, description, checked):
            box = QCheckBox(label)
            box.setChecked(checked)
            box.setToolTip(description)
            form.addRow(box)
            hint = QLabel(description)
            hint.setWordWrap(True)
            hint.setContentsMargins(22, 0, 0, 6)
            form.addRow(hint)
            box.description_label = hint
            return box

        def number(form, key, label, low, high):
            field = QSpinBox()
            field.setRange(low, high)
            field.setValue(DEFAULTS[key])
            self.fields[key] = field
            form.addRow(label, field)
            return field

        self.backend = QComboBox()
        for label, value in [('Automatic (Canon SDK preferred)', 0), ('Canon SDK', 1), ('gphoto2', 2)]:
            self.backend.addItem(label, value)
        self.backend.setToolTip('Automatic falls back to gphoto2 if Canon SDK capture fails. Explicit choices use only the selected backend.')
        video.addRow('Capture backend', self.backend)
        self.native = checkbox(video, 'Use camera native resolution',
                               'Keep the original live-view size. Turn off to resize the picture.', True)
        self.size = QComboBox()
        for label, value in [('Keep camera setting', 0), ('Large (recommended)', 1), ('Medium', 2), ('Small', 3)]:
            self.size.addItem(label, value)
        self.size.setCurrentIndex(1)
        self.size.setToolTip('Requested only when supported by the camera. Native size is detected automatically from camera images.')
        video.addRow('Camera picture size', self.size)
        self.manual_dimensions = QWidget()
        manual_form = QFormLayout(self.manual_dimensions)
        manual_form.setContentsMargins(0, 0, 0, 0)
        number(manual_form, 'WIDTH', 'Width', 16, 16384)
        number(manual_form, 'HEIGHT', 'Height', 16, 16384)
        video.addRow(self.manual_dimensions)
        self.native.toggled.connect(lambda checked: [self.fields[key].setEnabled(not checked)
                                                     for key in ('WIDTH', 'HEIGHT')])
        self.native_size = QLabel('Waiting for camera — native size is detected automatically.')
        self.native_size.setWordWrap(True)
        video.addRow(self.native_size)
        self.native.toggled.connect(lambda checked: self.manual_dimensions.setVisible(not checked))
        self.native.toggled.connect(self.native_size.setVisible)
        self.auto_fps = checkbox(video, 'Follow camera frame timing',
                                 'Use frames as they arrive. Turn off for manual timing.', True)
        number(video, 'FPS', 'Advertised webcam FPS', 1, 120).setToolTip(
            'The nominal rate apps see. It does not increase the number of distinct camera images.')
        number(video, 'CAMERA_FPS', 'Manual camera FPS', 1, 120)
        self.auto_fps.toggled.connect(lambda checked: self.fields['CAMERA_FPS'].setEnabled(not checked))
        self.passthrough = checkbox(video, 'Pass JPEG through unchanged',
                                    'Lower processing cost; needs native size and app MJPEG support. '
                                    'Turn off for raw-video compatibility. Apply the saved change, then reopen your video app.', False)
        warning_path = Path(__file__).parent / 'assets/warning.svg'
        if not warning_path.exists():
            warning_path = Path('/usr/share/canon-webcam/warning.svg')
        self.passthrough.setIcon(QIcon(str(warning_path)))
        self.passthrough.description_label.setText(
            'Experimental — some apps may show no camera or a black image. Raw video is recommended. '
            'To switch: close video apps, apply the saved change, then reopen them.')
        self.passthrough.description_label.setStyleSheet('color: #ef6666;')
        self.passthrough.toggled.connect(lambda checked: self.native.setChecked(True) if checked else None)
        self.on_demand = checkbox(automation, 'Use the camera automatically',
                                  'Start video when a video app needs it, and pause when it closes. '
                                  'If your app cannot start the camera, turn this off to keep video on.', True)
        self.auto_pause = checkbox(automation, 'Let the camera rest when not in use',
                                   'After the delay below, let the camera leave live view to save power. '
                                   'Camera-Bridge stays available to video apps.', True)
        delay = number(automation, 'AUTO_TIMEOUT', 'Rest after', 1, 99999999)
        delay.setSuffix(' seconds')
        self.auto_pause.toggled.connect(delay.setEnabled)
        self.viewfinder = checkbox(video, 'Manage Canon live view automatically',
                                   'Enter live view for capture and leave it when idle. Turn off to manage it on the camera yourself.', True)
        self.notifications = checkbox(automation, 'Camera problem notifications',
                                      'Show recovery instructions in a tray notification. '
                                      'The video feed shows them even when notifications are off.', True)
        message = QLabel('If an open app cannot find Camera-Bridge, restart that app. '
                         'After changing picture settings, save changes and reopen the camera in your video app.')
        message.setWordWrap(True)
        troubleshoot.addRow(message)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(140)
        troubleshoot.addRow('Technical status', self.details)
        logs = QPushButton('View logs')
        logs.clicked.connect(self.logs)
        troubleshoot.addRow(logs)
        self.setup = QPushButton('Set up webcam')
        self.setup.clicked.connect(lambda: self.run(COMMAND, ['setup-user']))
        troubleshoot.addRow(self.setup)
        note = QLabel('Changes save automatically. Apply to current video from the main window when ready; video apps may need reopening.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.advanced_feedback = QLabel('')
        self.advanced_feedback.setWordWrap(True)
        layout.addWidget(self.advanced_feedback)
        row = QHBoxLayout()
        close = QPushButton('Close')
        close.clicked.connect(self.advanced.hide)
        row.addWidget(close)
        layout.addLayout(row)

        # Everyday preferences open first; video formats and diagnostics are opt-in.
        self.preferences = QDialog(self)
        self.preferences.setWindowTitle('Camera Bridge — Settings')
        self.preferences.resize(530, 570)
        preferences_layout = QVBoxLayout(self.preferences)
        self.preferences_tabs = QTabWidget()
        preferences_layout.addWidget(self.preferences_tabs, 1)
        general_page = self.tabs.widget(1)
        self.tabs.removeTab(1)
        self.preferences_tabs.addTab(general_page, 'General')
        self.tabs.setTabText(1, 'Connection details')
        self.settings_feedback = QLabel('')
        self.settings_feedback.setWordWrap(True)
        preferences_layout.addWidget(self.settings_feedback)
        row = QHBoxLayout()
        advanced = QPushButton('Advanced…')
        advanced.clicked.connect(self.open_advanced)
        row.addWidget(advanced)
        row.addStretch()
        close = QPushButton('Close')
        close.clicked.connect(self.preferences.hide)
        row.addWidget(close)
        preferences_layout.addLayout(row)

    def open_settings(self):
        self.preferences.show()
        self.preferences.raise_()
        self.preferences.activateWindow()

    def open_advanced(self):
        self.advanced.show()
        self.advanced.raise_()
        self.advanced.activateWindow()

    def open(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def close_window(self):
        self.preferences.hide()
        self.advanced.hide()
        self.hide()

    def closeEvent(self, event):
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.preferences.hide()
            self.advanced.hide()
            self.hide()
            event.ignore()
        else:
            event.ignore()
            self.quit_and_stop()

    def shutdown(self):
        self.shutting_down = True
        self.timer.stop()
        self.blink_timer.stop()
        self.autosave_timer.stop()
        for process in self.findChildren(QProcess):
            try:
                process.finished.disconnect()
                process.errorOccurred.disconnect()
                if process.state() != QProcess.NotRunning:
                    process.terminate()
                    process.waitForFinished(1000)
            except (RuntimeError, TypeError):
                pass

    def confirm_quit(self):
        dialog = QMessageBox(self)
        dialog.setWindowTitle('Quit Camera Bridge?')
        dialog.setText('Quitting stops the webcam and turns off camera live view.')
        dialog.setInformativeText('To keep the webcam available, choose Close window instead.')
        quit_button = dialog.addButton('Quit and stop webcam', QMessageBox.DestructiveRole)
        close_button = dialog.addButton('Close window', QMessageBox.ActionRole)
        dialog.addButton(QMessageBox.Cancel)
        dialog.setDefaultButton(QMessageBox.Cancel)
        dialog.exec_()
        if dialog.clickedButton() == close_button:
            self.close_window()
        return dialog.clickedButton() == quit_button

    def quit_and_stop(self):
        if not self.quitting and not self.confirm_quit():
            return
        self.autosave_timer.stop()
        if self.initialized and not self.busy:
            self.save(False)
        self.quitting = True
        self.quit_requested = True
        self.operation_feedback()
        if not self.busy:
            self.quit_requested = False
            self.run(COMMAND, ['stop'], self.app.quit)

    def control(self, action):
        self.run(COMMAND, [action])

    def run(self, program, args, done=None):
        if self.busy:
            return
        self.busy = True
        self.pending_action = args[0] if program == COMMAND else 'settings'
        self.operation_feedback()
        self.operation = QProcess(self)
        self.operation.setProcessChannelMode(QProcess.MergedChannels)
        self.operation.finished.connect(lambda code, _: self.finished(code, done))
        self.operation.errorOccurred.connect(lambda error: self.failed_start(error))
        self.operation.start(program, args)

    def failed_start(self, error):
        if error == QProcess.FailedToStart:
            self.busy = False
            self.quitting = False
            self.pending_action = None
            self.operation_feedback()
            QMessageBox.warning(self, 'Camera Bridge', self.operation.errorString())

    def finished(self, code, done):
        output = bytes(self.operation.readAll()).decode(errors='replace')
        self.busy = False
        self.operation.deleteLater()
        if code:
            self.quitting = False
            self.quit_requested = False
            self.settings_message('Could not apply the change. Check the camera connection and try again.')
            self.open()
            QMessageBox.warning(self, 'Operation failed', output[-5000:] or 'Service operation failed.')
        elif done:
            done()
        if self.busy:
            return  # A save callback has started the reconnect step.
        if self.shutting_down:
            return
        self.pending_action = None
        self.operation_feedback()
        self.refresh()
        if self.quit_requested:
            self.quit_and_stop()

    def operation_feedback(self):
        self.toggle.setEnabled(self.initialized and not self.busy and not self.quitting)
        self.retry.setEnabled(not self.busy and not self.quitting)
        self.quit_button.setEnabled(not self.quitting)
        for button in self.action_buttons:
            button.setEnabled(not self.busy and not self.quitting)
        if self.quitting:
            headline, explanation = 'Closing…', 'Stopping the webcam and releasing the camera before quitting.'
            self.quit_button.setText('Closing…')
        elif self.busy:
            messages = {
                'start': ('Turning on…', 'Making your webcam available to apps.'),
                'stop': ('Turning off…', 'Releasing the camera connection. This can take a few seconds.'),
                'restart': ('Applying changes…', 'Reconnecting the webcam to apply your picture settings.'),
                'force-stop': ('Pausing camera…', 'Turning off live view while keeping the virtual webcam available.'),
                'force-start': ('Updating camera control…', 'Requesting manual camera control.'),
                'retry': ('Trying again…', 'Checking the connection and requesting a fresh attempt.'),
                'auto': ('Updating…', 'Returning to automatic camera control.'),
            }
            headline, explanation = messages.get(self.pending_action, ('Saving…', 'Applying your preferences.'))
        else:
            self.quit_button.setText('Quit…')
            self.toggle.setText('Turn off' if self.running else 'Turn on')
            return
        self.status.setText(headline)
        self.summary.setText(explanation)
        self.toggle.setText('Please wait…')
        self.menu_status.setText('Camera Bridge — ' + headline)
        self.tray.setToolTip('Camera Bridge — ' + headline + '\n' + explanation)

    def refresh(self):
        if self.shutting_down:
            return
        if self.poll is not None and self.poll.state() != QProcess.NotRunning:
            return
        self.poll = QProcess(self)
        self.poll.finished.connect(self.status_ready)
        self.poll.errorOccurred.connect(lambda _: self.status.setText('Cannot query systemd user service.'))
        self.poll.start('systemctl', ['--user', 'show', SERVICE, '--property=LoadState,ActiveState,UnitFileState,Environment'])

    def status_ready(self):
        output = bytes(self.poll.readAllStandardOutput()).decode(errors='replace')
        props = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
        self.legacy_login_enabled = props.get('UnitFileState') == 'enabled'
        state = props.get('ActiveState', 'unavailable')
        if self.poll.exitCode() != 0:
            state = 'unavailable (systemd user session required)'
        usage = {}
        text = 'Webcam service: ' + state
        if state == 'active':
            runtime = os.environ.get('XDG_RUNTIME_DIR')
            status = Path(runtime) / 'canon-webcam/status' if runtime else None
            if status and status.exists():
                try:
                    text += '\n' + status.read_text().strip()
                except OSError:
                    pass
        if state == 'active' and os.environ.get('XDG_RUNTIME_DIR'):
            info = Path(os.environ['XDG_RUNTIME_DIR']) / 'canon-webcam'
            try:
                usage = json.loads((info / 'usage.json').read_text())
                names = ', '.join(f"{client['name']} ({client['pid']})" for client in usage['clients'])
                text += '\nApps: ' + (names or 'none detected')
                if usage.get('camera_fps'):
                    text += f"\nMeasured camera: {usage['camera_fps']:.1f} FPS"
                if usage.get('manual_override'):
                    text += '\nCamera started manually'
                if usage['uncertain']:
                    text += ' — some apps are hidden; use Always stream if needed'
            except (OSError, ValueError, KeyError, TypeError):
                pass
            try:
                camera = json.loads((info / 'camera.json').read_text())
                if camera.get('width') and camera.get('height'):
                    text += f"\nCamera native: {camera['width']} × {camera['height']}"
            except (OSError, ValueError, TypeError):
                pass
        self.details.setPlainText(text)
        self.running = state in ('active', 'activating', 'reloading')
        self.toggle.setText('Turn off' if self.running else 'Turn on')
        if state == 'active':
            raw = text.splitlines()[1] if len(text.splitlines()) > 1 else ''
            if 'Getting webcam ready' in raw:
                headline = 'Getting ready…'
            elif 'Getting video ready' in raw:
                headline = 'Getting video ready…'
            elif 'not responding' in raw.lower():
                headline = 'Camera not responding'
            elif 'Capturing' in raw:
                headline = 'Camera in use' if usage.get('camera_frames', 0) > 0 else 'Starting camera…'
            elif 'Starting' in raw:
                headline = 'Starting camera…'
            elif 'Lost connection' in raw or 'disconnected' in raw.lower():
                headline = 'Camera disconnected'
            else:
                headline = 'Ready'
            app_names = []
            try:
                app_names = list(dict.fromkeys(client['name'] for client in usage['clients']))
            except (NameError, KeyError, TypeError):
                pass
            if usage.get('manual_off'):
                headline = 'Camera kept off'
                summary = 'Video is paused. Use the tray menu to start the camera or return to automatic control.'
            elif headline == 'Camera not responding':
                summary = 'Try video/movie mode. Check camera power and USB. Retrying automatically while an app needs it.'
            elif headline == 'Camera disconnected':
                summary = 'Check the USB connection and camera power.'
            elif headline == 'Getting ready…':
                summary = 'Preparing the virtual webcam for your apps.'
            elif headline == 'Getting video ready…':
                summary = 'Checking the camera connection before starting video.'
            elif headline == 'Starting camera…':
                summary = 'Preparing your camera. The app is waiting for an image.'
            elif app_names:
                summary = 'Used by ' + ', '.join(app_names)
            elif headline == 'Camera in use':
                summary = 'Video is on continuously. You can switch back to automatic use in Settings.'
            else:
                summary = 'Your camera starts automatically when an app needs it.'
        elif state == 'activating':
            headline, summary = 'Starting…', 'Getting your webcam ready.'
        elif state == 'inactive':
            headline, summary = 'Off', 'Turn on to make your webcam available to apps.'
        else:
            headline, summary = 'Needs attention', 'Open Settings → Troubleshooting for details.'
        self.status.setText(headline)
        self.summary.setText(summary)
        self.menu_status.setText('Camera Bridge — ' + headline)
        self.tray.setToolTip('Camera Bridge — ' + headline + '\n' + summary)
        self.update_tray_status(headline, usage)
        self.setup.setVisible(not PREVIEW and props.get('LoadState') == 'not-found')
        if not self.initialized:
            values = DEFAULTS.copy()
            for assignment in shlex.split(props.get('Environment', '')):
                key, _, value = assignment.partition('=')
                key = key.removeprefix('CANON_WEBCAM_')
                if key in values and value.isdigit():
                    values[key] = int(value)
            values.update(read_settings())
            for key, widget in self.fields.items():
                widget.setValue(values[key] or 60 if key == 'AUTO_TIMEOUT' else values[key])
            self.auto_pause.setChecked(values['AUTO_TIMEOUT'] > 0)
            self.fields['AUTO_TIMEOUT'].setEnabled(self.auto_pause.isChecked())
            self.viewfinder.setChecked(bool(values['SET_VIEWFINDER']))
            self.notifications.setChecked(bool(values['NOTIFICATIONS']))
            self.on_demand.setChecked(bool(values['ON_DEMAND']))
            self.auto_fps.setChecked(bool(values['AUTO_FPS']))
            self.passthrough.setChecked(bool(values['PASSTHROUGH']))
            self.fields['CAMERA_FPS'].setEnabled(not self.auto_fps.isChecked())
            self.native.setChecked(bool(values['NATIVE_RESOLUTION']))
            self.manual_dimensions.setVisible(not self.native.isChecked())
            self.native_size.setVisible(self.native.isChecked())
            for key in ('WIDTH', 'HEIGHT'):
                self.fields[key].setEnabled(not self.native.isChecked())
            self.size.setCurrentIndex(self.size.findData(values['LIVE_VIEW_SIZE']))
            self.backend.setCurrentIndex(max(0, self.backend.findData(values['CAPTURE_BACKEND'])))
            self.initialized = True
        if self.native.isChecked():
            try:
                path = (Path(os.environ['XDG_RUNTIME_DIR']) / 'canon-webcam/camera.json'
                        if state == 'active' and os.environ.get('XDG_RUNTIME_DIR') else CONFIG.parent / 'native-size.json')
                detected = json.loads(path.read_text())
                width, height = int(detected['width']), int(detected['height'])
                if width < 16 or height < 16:
                    raise ValueError('No usable size')
                self.fields['WIDTH'].setValue(width)
                self.fields['HEIGHT'].setValue(height)
                if state == 'active' and not detected.get('cached', False):
                    self.native_size.setText(f'Detected automatically: {width} × {height}')
                else:
                    self.native_size.setText(f'Last detected: {width} × {height} · waiting for camera images')
            except (OSError, ValueError, KeyError, TypeError):
                self.native_size.setText('Waiting for camera — native size is detected automatically.')
        if self.saved_values is None:
            self.saved_values = self.current_values()
        if self.applied_values is None:
            self.applied_values = self.current_values()
        self.startup_ready = True
        self.update_pending_settings()
        self.retry.setVisible(self.running and headline in ('Camera not responding', 'Camera disconnected'))
        if not self.busy and not self.quitting:
            self.report_problem(headline, summary)
        self.operation_feedback()
        self.poll.deleteLater()
        self.poll = None

    def update_tray_status(self, headline, usage):
        identity = connected_camera()
        live_view = 'unknown'
        if self.running and identity:
            try:
                lines = (Path(os.environ['XDG_RUNTIME_DIR']) / 'canon-webcam/live-view').read_text().splitlines()
                # Ignore a previous USB session's live-view state after reconnect.
                if len(lines) > 1 and identity in lines[1]:
                    live_view = lines[0]
            except (OSError, KeyError):
                pass
        apps = list(dict.fromkeys(client['name'] for client in usage.get('clients', [])))
        icon, rows = tray_presentation(bool(identity), self.running, live_view,
                                      usage.get('camera_frames', 0) > 0,
                                      headline == 'Starting camera…',
                                      headline == 'Camera not responding', apps)
        if identity and self.running and headline == 'Getting video ready…':
            icon, rows = 'starting', (rows[0], 'Checking camera…', 'Preparing video')
        if self.running and usage.get('manual_off'):
            icon, rows = ('connected' if identity else 'off'), (rows[0], 'Camera kept off', 'Video paused')
        elif self.running and usage.get('manual_override'):
            rows = (rows[0], rows[1], rows[2] + ' · kept on')
        for action, label in zip((self.menu_camera, self.menu_live_view, self.menu_video), rows):
            action.setText(label)
        self.menu_camera.setIcon(self.tray_icons['connected' if identity else 'off'])
        self.menu_live_view.setIcon(self.tray_icons['awake' if rows[1] == 'Live view on' else icon])
        self.menu_video.setIcon(self.tray_icons['live' if icon == 'live' else 'ready'])
        self.menu_status.setText('Camera Bridge' if self.running else 'Camera Bridge — Off')
        self.tray.setToolTip('Camera Bridge\n' + '\n'.join(rows))
        self.set_tray_state(icon)

    def set_tray_state(self, state):
        if state == self.tray_state:
            return
        self.tray_state = state
        self.blink_timer.stop()
        if state == 'live':
            self.blink_on = True
            self.tray.setIcon(self.tray_icons['live'])
            self.blink_timer.start()
        else:
            self.tray.setIcon(self.tray_icons[state])

    def animate_tray(self):
        if self.tray_state != 'live':
            return
        self.blink_on = not self.blink_on
        self.tray.setIcon(self.tray_icons['live' if self.blink_on else 'live-dim'])

    def report_problem(self, headline, guidance):
        if headline in ('Camera not responding', 'Camera disconnected'):
            now = time.monotonic()
            if self.notifications.isChecked() and (headline != self.last_problem or now - self.last_notification_at >= 300):
                self.tray.showMessage('Camera Bridge — ' + headline, guidance, QSystemTrayIcon.Warning, 10000)
                self.last_notification_at = now
            self.last_problem = headline
        elif headline in ('Camera in use', 'Off', 'Ready'):
            self.last_problem = None


    def current_values(self):
        values = {key: widget.value() for key, widget in self.fields.items()}
        if not self.auto_pause.isChecked():
            values['AUTO_TIMEOUT'] = 0
        values.update(SET_VIEWFINDER=int(self.viewfinder.isChecked()),
                      NOTIFICATIONS=int(self.notifications.isChecked()),
                      ON_DEMAND=int(self.on_demand.isChecked()), AUTO_FPS=int(self.auto_fps.isChecked()),
                      PASSTHROUGH=int(self.passthrough.isChecked()), NATIVE_RESOLUTION=int(self.native.isChecked()),
                      LIVE_VIEW_SIZE=self.size.currentData(), CAPTURE_BACKEND=self.backend.currentData())
        return values

    def settings_message(self, message):
        self.main_feedback.setText(message)
        self.settings_feedback.setText(message)
        self.advanced_feedback.setText(message)

    def camera_settings_changed(self):
        current = self.current_values()
        relevant = set(current) - {'NOTIFICATIONS'}
        if current['NATIVE_RESOLUTION']:
            relevant -= {'WIDTH', 'HEIGHT'}
        if current['AUTO_FPS']:
            relevant.discard('CAMERA_FPS')
        return any(current[key] != (self.applied_values or current).get(key) for key in relevant)

    def update_pending_settings(self):
        if not self.running:
            self.applied_values = self.current_values()
        self.apply_button.setVisible(self.running and self.camera_settings_changed())

    def queue_autosave(self, *_):
        if self.initialized and not self.quitting and not self.shutting_down:
            self.autosave_timer.start()

    def autosave(self):
        if self.busy:
            self.autosave_timer.start()
            return
        if self.current_values() != self.saved_values:
            self.save(False)

    def save_startup(self, *_):
        if not self.startup_ready or PREVIEW:
            return
        if self.busy:
            QTimer.singleShot(250, self.save_startup)
            return
        try:
            write_startup(AUTOSTART, self.login.isChecked(), True)
            write_startup(WEBCAM_AUTOSTART, self.boot.isChecked(), False)
        except OSError as error:
            self.settings_message('Could not save login options: ' + str(error))
            return
        self.settings_message('Login options saved.')
        # Disable any legacy per-user enablement when the user turns login off.
        if not self.boot.isChecked() and self.legacy_login_enabled:
            self.run('systemctl', ['--user', 'disable', SERVICE])

    def save_changed_settings(self):
        if self.busy or not self.initialized:
            return
        self.autosave_timer.stop()
        self.save(self.running and self.camera_settings_changed())

    def reconnect_dialog(self):
        parent = self.advanced if self.advanced.isVisible() else self.preferences if self.preferences.isVisible() else self
        dialog = QMessageBox(parent)
        dialog.setWindowTitle('Apply camera changes')
        dialog.setIcon(QMessageBox.Information)
        dialog.setText('Close your video apps before applying changes.')
        dialog.setInformativeText(
            '1. Close video apps using Camera-Bridge, such as OBS, Zoom, Discord, or browser camera tabs.\n\n'
            '2. Click Apply changes below. The webcam will briefly disconnect.\n\n'
            '3. Reopen those apps and select Camera-Bridge again.')
        dialog.apply_button = dialog.addButton('Apply changes', QMessageBox.AcceptRole)
        dialog.addButton(QMessageBox.Cancel)
        dialog.setDefaultButton(QMessageBox.Cancel)
        return dialog

    def confirm_reconnect(self):
        dialog = self.reconnect_dialog()
        dialog.exec_()
        return dialog.clickedButton() == dialog.apply_button

    def save(self, restart):
        if self.busy or not self.initialized:
            return
        values = self.current_values()
        if values['PASSTHROUGH'] and not values['NATIVE_RESOLUTION']:
            QMessageBox.warning(self, 'Native size required', 'Enable camera native resolution for JPEG passthrough.')
            return
        values['LIVE_VIEW_SIZE'] = self.size.currentData()
        if values['WIDTH'] % 2 or values['HEIGHT'] % 2:
            self.settings_message('Width and height must be even. This change has not been saved.')
            return
        if restart and not self.confirm_reconnect():
            self.settings_message('Saved preferences kept. Current video is unchanged; apply when ready.')
            return
        try:
            atomic_write(CONFIG, ''.join(f'{key}={value}\n' for key, value in values.items()))
        except OSError as error:
            QMessageBox.warning(self, 'Cannot save settings', str(error))
            return
        self.saved_values = values.copy()
        def saved():
            if restart or not self.running:
                self.applied_values = values.copy()
            self.update_pending_settings()
            self.settings_message('Saved. Apply to current video when ready; video apps will need reopening.'
                                  if self.running and self.camera_settings_changed() else 'Saved automatically.')
        if restart:
            self.run(COMMAND, ['restart'], saved)
        else:
            saved()

    def logs(self):
        self.log_window = QWidget()
        self.log_window.setWindowTitle('Camera Bridge — Recent logs')
        self.log_window.resize(780, 480)
        layout = QVBoxLayout(self.log_window)
        text = QTextEdit()
        text.setReadOnly(True)
        layout.addWidget(text)
        self.log_process = QProcess(self.log_window)
        self.log_process.setProcessChannelMode(QProcess.MergedChannels)
        self.log_process.readyRead.connect(lambda: text.insertPlainText(
            bytes(self.log_process.readAll()).decode(errors='replace')))
        self.log_process.start('journalctl', ['--user', '-u', SERVICE, '-n', '100', '--no-pager'])
        self.log_window.show()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName('Camera Bridge')
    app.setDesktopFileName('camera-bridge')
    app.setQuitOnLastWindowClosed(False)
    icon_path = Path(__file__).parent / 'assets/canon-webcam.svg'
    icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon.fromTheme('camera-bridge', QIcon.fromTheme('camera-web'))
    app.setWindowIcon(icon)
    # One tray per graphical user session. A second launcher opens its settings.
    name = 'canon-webcam-' + str(os.getuid()) + '-' + SERVICE
    socket = QLocalSocket()
    socket.connectToServer(name)
    if socket.waitForConnected(500):
        socket.write(b'open')
        socket.waitForBytesWritten(500)
        return 0
    QLocalServer.removeServer(name)
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.UserAccessOption)
    if not server.listen(name):
        return 1
    window = Webcam(app)
    def incoming():
        connection = server.nextPendingConnection()
        window.open()
        connection.disconnectFromServer()
    server.newConnection.connect(incoming)
    if 'settings' in sys.argv or not QSystemTrayIcon.isSystemTrayAvailable():
        window.open()
    return app.exec_()


if __name__ == '__main__':
    sys.exit(main())
