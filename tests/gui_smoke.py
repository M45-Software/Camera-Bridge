"""Exercise Qt UI with a fake user service, without touching the host service."""
import importlib.util
import os
from pathlib import Path
import tempfile
import time

from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QIcon

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as directory:
    temp = Path(directory)
    os.environ['XDG_CONFIG_HOME'] = str(temp / 'config')
    os.environ['PATH'] = str(temp) + ':' + os.environ['PATH']
    os.environ['CANON_TEST_CALLS'] = str(temp / 'calls')
    fake = temp / 'systemctl'
    fake.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$CANON_TEST_CALLS"
case "$*" in
  *show*) printf 'LoadState=loaded\\nActiveState=inactive\\nUnitFileState=disabled\\nEnvironment=CANON_WEBCAM_WIDTH=1920 CANON_WEBCAM_AUTO_TIMEOUT=600\\n';;
esac
''')
    fake.chmod(0o755)
    spec = importlib.util.spec_from_file_location('canon_gui', root / 'canon-webcam-gui.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.COMMAND = str(fake)
    app = QApplication([])
    app.setWindowIcon(QIcon(str(root / 'assets/canon-webcam.svg')))
    for name in ('ready', 'live', 'live-dim', 'off', 'connected', 'awake', 'starting', 'warning'):
        icon = module.tray_icon(name)
        assert not icon.isNull()
        assert {(size.width(), size.height()) for size in icon.availableSizes()} >= {(16,16), (22,22), (24,24), (32,32)}
    # USB presence must distinguish Canon cameras from other Canon devices.
    usb = temp / 'usb'
    device = usb / '1-2'
    device.mkdir(parents=True)
    for name, value in {'idVendor': '04a9', 'product': 'Canon Printer', 'busnum': '1', 'devnum': '5'}.items():
        (device / name).write_text(value)
    assert module.connected_camera(usb) is None
    interface = usb / '1-2:1.0'
    interface.mkdir()
    (interface / 'bInterfaceClass').write_text('06')
    assert module.connected_camera(usb) == 'usb:001,005'
    # Confirm independent live-view and video states; stale frames never win over unplug.
    present = module.tray_presentation
    assert present(False, True, 'on', True, False, False, ['OBS'])[0] == 'off'
    assert present(True, True, 'off', False, False, False, [])[1] == ('Camera connected', 'Camera resting', 'Video paused')
    assert present(True, True, 'on', False, False, False, [])[0] == 'awake'
    assert present(True, True, 'on', True, False, False, ['OBS'])[1][-1] == 'Sending video to OBS'
    assert present(True, True, 'unknown', False, True, False, [])[0] == 'starting'
    assert present(True, True, 'on', False, False, True, [])[0] == 'warning'
    window = module.Webcam(app)
    def until(predicate):
        deadline = time.monotonic() + 5
        while not predicate():
            app.processEvents()
            if time.monotonic() > deadline:
                raise AssertionError('Qt operation timed out')
            time.sleep(0.01)
    until(lambda: window.initialized)
    real_confirm_reconnect = window.confirm_reconnect
    window.confirm_reconnect = lambda: True
    assert window.fields['WIDTH'].value() == 1920
    assert window.fields['AUTO_TIMEOUT'].value() == 600
    assert window.status.text() == 'Off'
    assert 'inactive' in window.details.toPlainText()
    assert not window.advanced.isVisible()
    assert window.advanced.isAncestorOf(window.fields['WIDTH'])
    assert window.preferences.isAncestorOf(window.fields['AUTO_TIMEOUT'])
    assert window.tabs.count() == 2
    assert window.preferences_tabs.count() == 2
    window.open_settings()
    assert window.preferences.isVisible()
    assert not window.advanced.isVisible()
    window.open_advanced()
    assert window.advanced.isVisible()
    window.preferences.hide()
    window.advanced.hide()
    from PyQt5.QtWidgets import QPushButton
    assert not any('restart' in button.text().lower() for button in window.findChildren(QPushButton))
    assert window.on_demand.isChecked()
    assert window.native.isChecked()
    assert window.auto_fps.isChecked()
    assert not window.fields['WIDTH'].isEnabled()
    assert not window.fields['CAMERA_FPS'].isEnabled()
    window.native.setChecked(False)
    assert window.fields['WIDTH'].isEnabled()
    window.passthrough.setChecked(True)
    assert window.native.isChecked()
    window.passthrough.setChecked(False)
    window.auto_fps.setChecked(False)
    assert window.fields['CAMERA_FPS'].isEnabled()
    window.auto_fps.setChecked(True)
    window.fields['AUTO_TIMEOUT'].setValue(1200)
    window.login.setChecked(True)
    window.boot.setChecked(True)
    window.save(True)
    until(lambda: not window.busy)
    assert 'AUTO_TIMEOUT=1200' in module.CONFIG.read_text()
    assert module.AUTOSTART.exists()
    calls = (temp / 'calls').read_text()
    assert '--user enable canon-webcam.service' in calls
    assert 'restart' in calls
    assert window.auto_pause.isChecked()
    window.auto_pause.setChecked(False)
    assert not window.fields['AUTO_TIMEOUT'].isEnabled()
    window.save(False)
    until(lambda: not window.busy)
    assert 'AUTO_TIMEOUT=0' in module.CONFIG.read_text()
    window.auto_pause.setChecked(True)
    assert window.fields['AUTO_TIMEOUT'].isEnabled()
    window.save(False)
    until(lambda: not window.busy)
    assert 'AUTO_TIMEOUT=1200' in module.CONFIG.read_text()
    window.saved_values = window.current_values()
    before = len((temp / 'calls').read_text().splitlines())
    window.running = True
    window.notifications.setChecked(False)
    window.save_changed_settings()
    until(lambda: not window.busy)
    new_calls = (temp / 'calls').read_text().splitlines()[before:]
    assert 'restart' not in new_calls
    assert window.settings_feedback.text() == 'Saved.'
    window.fields['FPS'].setValue(31)
    window.running = True
    before = len((temp / 'calls').read_text().splitlines())
    saved_before = module.CONFIG.read_bytes()
    window.confirm_reconnect = real_confirm_reconnect
    def cancel_reconnect():
        dialog = app.activeModalWidget()
        assert isinstance(dialog, QMessageBox)
        assert 'Close your video apps' in dialog.text()
        assert 'Reopen those apps' in dialog.informativeText()
        assert len((temp / 'calls').read_text().splitlines()) == before
        dialog.button(QMessageBox.Cancel).click()
    QTimer.singleShot(0, cancel_reconnect)
    window.save_changed_settings()
    assert module.CONFIG.read_bytes() == saved_before
    assert len((temp / 'calls').read_text().splitlines()) == before
    assert 'Changes not applied' in window.settings_feedback.text()
    def accept_reconnect():
        dialog = app.activeModalWidget()
        assert isinstance(dialog, QMessageBox)
        assert len((temp / 'calls').read_text().splitlines()) == before
        dialog.apply_button.click()
    window.running = True
    QTimer.singleShot(0, accept_reconnect)
    window.save_changed_settings()
    until(lambda: not window.busy)
    assert 'restart' in (temp / 'calls').read_text().splitlines()[before:]
    assert 'Saved.' in window.settings_feedback.text()
    old_camera = module.connected_camera
    module.connected_camera = lambda: 'usb:001,005'
    window.running = True
    window.update_tray_status('Camera in use', {'camera_frames': 10, 'clients': [{'name': 'OBS'}]})
    assert window.menu_camera.text() == 'Camera connected'
    assert window.menu_live_view.text() == 'Live view on'
    assert window.menu_video.text() == 'Sending video to OBS'
    assert window.tray_state == 'live'
    module.connected_camera = lambda: None
    window.update_tray_status('Ready', {})
    assert window.menu_camera.text() == 'No camera connected'
    assert window.tray_state == 'off'
    module.connected_camera = old_camera
    window.set_tray_state('live')
    assert window.blink_timer.isActive()
    bright = window.tray.icon().cacheKey()
    window.animate_tray()
    assert window.tray.icon().cacheKey() != bright
    window.set_tray_state('ready')
    assert not window.blink_timer.isActive()
    window.notifications.setChecked(True)
    notices = []
    window.tray.showMessage = lambda *args: notices.append(args)
    window.report_problem('Camera not responding', 'Turn the camera off and on.')
    window.report_problem('Camera not responding', 'Turn the camera off and on.')
    assert len(notices) == 1
    window.notifications.setChecked(False)
    window.report_problem('Camera disconnected', 'Check USB and camera power.')
    assert len(notices) == 1
    window.save(False)
    until(lambda: not window.busy)
    assert 'NOTIFICATIONS=0' in module.CONFIG.read_text()
    window.notifications.setChecked(True)
    window.control('stop')
    assert window.busy
    assert window.status.text() == 'Turning off…'
    assert not window.toggle.isEnabled()
    window.refresh()
    assert window.status.text() == 'Turning off…'
    until(lambda: not window.busy)
    assert window.toggle.isEnabled()
    quit_calls = []
    original_quit = app.quit
    app.quit = lambda: quit_calls.append((not window.busy, (temp / 'calls').read_text().splitlines()[-1]))
    window.quit_and_stop()
    assert window.status.text() == 'Closing…'
    assert not window.quit_button.isEnabled()
    until(lambda: bool(quit_calls))
    assert quit_calls[0] == (True, 'stop')
    app.quit = original_quit
    window.quitting = False
    window.operation_feedback()
    window.open()
    app.processEvents()
    if os.environ.get('CANON_GUI_SCREENSHOT'):
        window.grab().save(os.environ['CANON_GUI_SCREENSHOT'])
    window.tray.hide()
    window.timer.stop()
    until(lambda: window.poll is None)
    print('Qt smoke test passed: status, defaults, settings, login tray, enable and restart')
