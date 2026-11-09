"""Exercise Qt controls with a fake service; never touches a real camera/service."""
import importlib.util
import os
from pathlib import Path
import tempfile
import time

from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication, QMessageBox, QPushButton

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as directory:
    temp = Path(directory)
    os.environ['XDG_CONFIG_HOME'] = str(temp/'config')
    os.environ['XDG_RUNTIME_DIR'] = str(temp/'runtime')
    os.environ['PATH'] = str(temp) + ':' + os.environ['PATH']
    os.environ['CANON_TEST_CALLS'] = str(temp/'calls')
    os.environ['CANON_TEST_STATE'] = str(temp/'state')
    (temp/'state').write_text('inactive')
    fake = temp/'systemctl'
    fake.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$CANON_TEST_CALLS"
case "$*" in
 *show*) printf 'LoadState=loaded\\nActiveState=%s\\nUnitFileState=disabled\\nEnvironment=CANON_WEBCAM_WIDTH=1920 CANON_WEBCAM_AUTO_TIMEOUT=600\\n' "$(cat "$CANON_TEST_STATE")";;
esac
''')
    fake.chmod(0o755)
    spec = importlib.util.spec_from_file_location('canon_gui', root/'canon-webcam-gui.py')
    gui = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gui)
    gui.COMMAND = str(fake)
    app = QApplication([])
    app.setWindowIcon(QIcon(str(root/'assets/canon-webcam.svg')))
    for name in ('ready','live','live-dim','off','connected','awake','starting','warning'):
        icon = gui.tray_icon(name)
        assert not icon.isNull()
        assert {(s.width(),s.height()) for s in icon.availableSizes()} >= {(16,16),(22,22),(24,24),(32,32)}
    usb = temp/'usb'
    device = usb/'1-2'
    device.mkdir(parents=True)
    for name,value in {'idVendor':'04a9','product':'Canon Printer','busnum':'1','devnum':'5'}.items():
        (device/name).write_text(value)
    assert gui.connected_camera(usb) is None
    interface = usb/'1-2:1.0'
    interface.mkdir()
    (interface/'bInterfaceClass').write_text('06')
    assert gui.connected_camera(usb) == 'usb:001,005'
    assert gui.startup_enabled(gui.AUTOSTART)
    window = gui.Webcam(app)
    def until(predicate):
        deadline = time.monotonic()+5
        while not predicate():
            app.processEvents()
            if time.monotonic()>deadline: raise AssertionError('Qt operation timed out')
            time.sleep(.01)
    def calls(): return (temp/'calls').read_text().splitlines()
    until(lambda:window.initialized and window.poll is None)
    assert window.status.text() == 'Off'
    assert window.fields['WIDTH'].value() == 1920
    assert window.fields['AUTO_TIMEOUT'].value() == 600
    assert window.boot.parent() is window and window.login.parent() is window
    assert window.boot.isChecked() and window.login.isChecked()
    assert window.preferences_tabs.count() == 1 and window.tabs.count() == 2
    assert window.preferences.isAncestorOf(window.fields['AUTO_TIMEOUT'])
    assert window.advanced.isAncestorOf(window.fields['WIDTH'])
    assert not any('Save changes' in b.text() or 'restart' in b.text().lower() for b in window.findChildren(QPushButton))
    assert window.native.isChecked() and not window.fields['WIDTH'].isEnabled()
    assert window.auto_fps.isChecked() and not window.fields['CAMERA_FPS'].isEnabled()
    window.open_settings(); assert window.preferences.isVisible()
    window.open_advanced(); assert window.advanced.isVisible()
    window.preferences.hide(); window.advanced.hide()
    # Startup changes persist independently, including explicit off overrides.
    before=len(calls())
    window.login.setChecked(False)
    assert not gui.startup_enabled(gui.AUTOSTART)
    window.legacy_login_enabled=True
    window.boot.setChecked(False)
    until(lambda:not window.busy)
    assert not gui.startup_enabled(gui.WEBCAM_AUTOSTART)
    assert '--user disable canon-webcam.service' in calls()[before:]
    assert 'restart' not in calls()[before:]
    window.login.setChecked(True); window.boot.setChecked(True)
    until(lambda:gui.startup_enabled(gui.AUTOSTART) and gui.startup_enabled(gui.WEBCAM_AUTOSTART))
    # General changes save after the debounce without a Save button.
    window.fields['AUTO_TIMEOUT'].setValue(65)
    until(lambda:gui.CONFIG.exists() and 'AUTO_TIMEOUT=65' in gui.CONFIG.read_text())
    window.auto_pause.setChecked(False)
    until(lambda:'AUTO_TIMEOUT=0' in gui.CONFIG.read_text())
    window.auto_pause.setChecked(True)
    until(lambda:'AUTO_TIMEOUT=65' in gui.CONFIG.read_text())
    (temp/'state').write_text('active')
    window.refresh(); until(lambda:window.poll is None and window.running)
    window.applied_values=window.current_values()
    before=len(calls())
    window.notifications.setChecked(False)
    until(lambda:'NOTIFICATIONS=0' in gui.CONFIG.read_text())
    assert 'restart' not in calls()[before:]
    assert window.apply_button.isHidden()
    # Picture choices auto-save; only the explicit Apply action reconnects video.
    window.fields['FPS'].setValue(31)
    until(lambda:'FPS=31\n' in gui.CONFIG.read_text())
    assert not window.apply_button.isHidden()
    assert 'restart' not in calls()[before:]
    def cancel_apply():
        dialog=app.activeModalWidget()
        assert isinstance(dialog,QMessageBox)
        assert 'Close your video apps' in dialog.text()
        dialog.button(QMessageBox.Cancel).click()
    QTimer.singleShot(0,cancel_apply)
    window.save_changed_settings()
    assert 'restart' not in calls()[before:]
    assert 'FPS=31\n' in gui.CONFIG.read_text(), 'Cancel keeps auto-saved preferences'
    def accept_apply():
        dialog=app.activeModalWidget()
        assert isinstance(dialog,QMessageBox)
        dialog.apply_button.click()
    QTimer.singleShot(0,accept_apply)
    window.save_changed_settings()
    until(lambda:not window.busy)
    assert 'restart' in calls()[before:]
    assert window.apply_button.isHidden()
    # Tray status and overrides remain explicit, including forced-off state.
    gui.connected_camera=lambda:'usb:001,005'
    window.running=True
    window.update_tray_status('Camera in use',{'camera_frames':10,'clients':[{'name':'OBS'}]})
    assert window.menu_video.text() == 'Sending video to OBS'
    assert window.tray_state == 'live' and window.blink_timer.isActive()
    bright=window.tray.icon().cacheKey(); window.animate_tray()
    assert window.tray.icon().cacheKey()!=bright
    window.update_tray_status('Ready',{'manual_off':True})
    assert window.menu_live_view.text() == 'Camera kept off'
    assert not window.blink_timer.isActive()
    for label,command in [('Keep camera on','force-start'),('Keep camera off','force-stop'),('Use automatic camera control','auto')]:
        action=next(a for a in window.tray.contextMenu().actions() if a.text()==label)
        action.trigger(); until(lambda:not window.busy)
        assert command in calls()
    notices=[]
    window.tray.showMessage=lambda *args:notices.append(args)
    window.notifications.setChecked(True)
    window.report_problem('Camera not responding','Check camera')
    window.report_problem('Camera not responding','Check camera')
    assert len(notices)==1
    window.notifications.setChecked(False)
    window.report_problem('Camera disconnected','Check USB')
    assert len(notices)==1
    # Closing hides; Cancel and Close window in the quit dialog never stop video.
    window.open(); before=len(calls()); window.close_button.click()
    assert not window.isVisible() and 'stop' not in calls()[before:]
    def select_quit_button(label):
        def click():
            dialog=app.activeModalWidget()
            assert isinstance(dialog,QMessageBox)
            next(b for b in dialog.buttons() if b.text()==label).click()
        return click
    QTimer.singleShot(0,select_quit_button('Cancel'))
    window.quit_and_stop()
    assert not window.quitting and 'stop' not in calls()[before:]
    window.open()
    QTimer.singleShot(0,select_quit_button('Close window'))
    window.quit_and_stop()
    assert not window.isVisible() and 'stop' not in calls()[before:]
    quit_calls=[]
    original_quit=app.quit
    app.quit=lambda:quit_calls.append((not window.busy,calls()[-1]))
    QTimer.singleShot(0,select_quit_button('Quit and stop webcam'))
    window.quit_and_stop()
    assert window.status.text()=='Closing…'
    until(lambda:bool(quit_calls))
    assert quit_calls==[(True,'stop')]
    app.quit=original_quit
    window.shutdown(); window.tray.hide()
    print('Qt smoke passed: autosave, login defaults, deferred apply, tray overrides, and quit confirmation')
