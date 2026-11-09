#!/usr/bin/python3
"""Render the real Qt widgets in a dark palette with isolated demo service data."""
import importlib.util
import os
from pathlib import Path
import tempfile
import time

# Render without a display server, with enough resolution for HiDPI READMEs.
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_SCALE_FACTOR', '2')

from PyQt5.QtCore import QPoint
from PyQt5.QtGui import QColor, QFont, QIcon, QPalette
from PyQt5.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'assets/screenshots'


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='canon-ui-render-') as directory:
        temp = Path(directory)
        os.environ['XDG_CONFIG_HOME'] = str(temp / 'config')
        os.environ['XDG_RUNTIME_DIR'] = str(temp / 'runtime')
        os.environ['PATH'] = str(temp) + ':' + os.environ['PATH']
        runtime = temp / 'runtime/canon-webcam'
        runtime.mkdir(parents=True)
        runtime.parent.chmod(0o700)
        (runtime / 'status').write_text('Capturing Canon live view\n')
        (runtime / 'usage.json').write_text('{"clients":[{"pid":2048,"name":"Firefox"}],"uncertain":false,"camera_fps":16.0,"camera_frames":44}')
        (runtime / 'camera.json').write_text('{"width":1024,"height":576}')
        fake = temp / 'systemctl'
        fake.write_text('#!/bin/sh\n'
                        "printf 'LoadState=loaded\\nActiveState=active\\nUnitFileState=disabled\\n"
                        "Environment=CANON_WEBCAM_AUTO_TIMEOUT=60\\n'\n")
        fake.chmod(0o755)
        spec = importlib.util.spec_from_file_location('canon_gui', ROOT / 'canon-webcam-gui.py')
        gui = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gui)
        gui.COMMAND = str(fake)

        app = QApplication([])
        app.setStyle('Fusion')
        app.setFont(QFont('DejaVu Sans', 10))
        palette = QPalette()
        colors = {
            QPalette.Window: '#31363b', QPalette.WindowText: '#eff0f1',
            QPalette.Base: '#232629', QPalette.AlternateBase: '#31363b',
            QPalette.Text: '#eff0f1', QPalette.Button: '#31363b',
            QPalette.ButtonText: '#eff0f1', QPalette.Highlight: '#3daee9',
            QPalette.HighlightedText: '#ffffff', QPalette.ToolTipBase: '#232629',
            QPalette.ToolTipText: '#eff0f1', QPalette.Link: '#3daee9',
            QPalette.Light: '#56616b', QPalette.Midlight: '#454e56',
            QPalette.Mid: '#232629', QPalette.Dark: '#1b1e20',
            QPalette.Shadow: '#141719',
        }
        for role, value in colors.items():
            palette.setColor(role, QColor(value))
        for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
            palette.setColor(QPalette.Disabled, role, QColor('#9da3a8'))
        app.setPalette(palette)
        app.setWindowIcon(QIcon(str(ROOT / 'assets/canon-webcam.svg')))
        window = gui.Webcam(app)
        deadline = time.monotonic() + 5
        while not window.initialized:
            app.processEvents()
            if time.monotonic() > deadline:
                raise RuntimeError('Demo service status did not load')
            time.sleep(0.01)
        window.timer.stop()
        window.startup_ready = False  # Rendering never writes real login preferences.
        window.login.setChecked(True)
        window.resize(460, 480)
        window.show()
        app.processEvents()
        assert window.grab().save(str(OUTPUT / 'settings-dark.png'))

        window.busy = True
        window.pending_action = 'stop'
        window.operation_feedback()
        app.processEvents()
        assert window.grab().save(str(OUTPUT / 'turning-off-dark.png'))
        window.busy = False
        window.pending_action = None
        window.operation_feedback()
        window.refresh()
        deadline = time.monotonic() + 5
        while window.poll is not None:
            app.processEvents()
            if time.monotonic() > deadline:
                raise RuntimeError('Demo status did not refresh')
            time.sleep(0.01)
        window.open_settings()
        app.processEvents()
        assert window.preferences.grab().save(str(OUTPUT / 'settings-general-dark.png'))
        assert window.preferences.grab().save(str(OUTPUT / 'advanced-automation-dark.png'))
        window.preferences.hide()
        window.open_advanced()
        app.processEvents()
        assert window.advanced.grab().save(str(OUTPUT / 'advanced-video-dark.png'))
        window.tabs.setCurrentIndex(1)
        app.processEvents()
        assert window.advanced.grab().save(str(OUTPUT / 'connection-details-dark.png'))
        window.advanced.hide()
        dialog = window.reconnect_dialog()
        dialog.show()
        app.processEvents()
        assert dialog.grab().save(str(OUTPUT / 'apply-changes-dialog-dark.png'))
        dialog.hide()

        gui.connected_camera = lambda: 'usb:001,005'
        window.running = True
        window.update_tray_status('Camera in use', {'camera_frames': 44, 'clients': [{'name': 'OBS'}]})
        menu = window.tray.contextMenu()
        menu.popup(QPoint(0, 0))
        menu.resize(menu.sizeHint())
        menu.setActiveAction(next(action for action in menu.actions()
                                  if action.text() == 'Open Camera Bridge…'))
        app.processEvents()
        assert menu.grab().save(str(OUTPUT / 'tray-menu-dark.png'))
        menu.hide()
        window.tray.hide()
        window.hide()
    print(f'Rendered settings and tray menu to {OUTPUT}')


if __name__ == '__main__':
    main()
