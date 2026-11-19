"""Exercise the real shell state machine with a harmless FIFO writer and fake camera."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def fake_pipeline(root):
    (root / 'runtime').mkdir()
    shutil.copy(ROOT / 'canon-webcam-runtime.py', root)
    shutil.copy(ROOT / 'canon_webcam_jpeg.py', root)
    camera = root / 'gphoto2'
    camera.write_text('''#!/usr/bin/python3
import sys, time
if '--auto-detect' in sys.argv:
    print('Model Port\\n----- ----\\nCanon usb:001,002')
elif '--capture-movie' in sys.argv:
    time.sleep(30)
''')
    camera.chmod(0o755)
    source = (ROOT / 'canon-webcam.sh').read_text().rsplit('main "$@"', 1)[0]
    source += '''
ensure_loopback_for_writer() { :; }
stop_camera_mount_helpers() { :; }
camera_wait_stable() { return 0; }
camera_prepare_live_view() { :; }
stop_orphaned_test_source_feeders() { :; }
start_loopback_writer() {
  if [[ -n "$LOOPBACK_WRITER_PID" ]]; then return; fi
  CAMERA_FIFO_DIR="$(mktemp -d)"
  CAMERA_FIFO="$CAMERA_FIFO_DIR/camera.mjpeg"
  mkfifo "$CAMERA_FIFO"
  exec {CAMERA_FIFO_HOLD_FD}<>"$CAMERA_FIFO"
  cat "$CAMERA_FIFO" >>"$CANON_TEST_ROOT/frames" &
  LOOPBACK_WRITER_PID=$!
  echo "$LOOPBACK_WRITER_PID" >"$CANON_TEST_ROOT/writer-pid"
}
run_test_source_feeder_ffmpeg() {
  printf '%s' "$1" >"${2:-$CAMERA_FIFO}"
}
stop_background_test_source() {
  if [[ -n "$TEST_SOURCE_PID" ]]; then terminate_process "$TEST_SOURCE_PID"; TEST_SOURCE_PID=""; fi
}
main "$@"
'''
    script = root / 'webcam.sh'
    script.write_text(source)
    script.chmod(0o755)
    environment = dict(os.environ, CANON_WEBCAM_ON_DEMAND='0', CANON_WEBCAM_NATIVE_RESOLUTION='0',
                       HOME=str(root), XDG_CONFIG_HOME=str(root / 'config'),
                       XDG_RUNTIME_DIR=str(root / 'runtime'), CANON_WEBCAM_IN_SERVICE='1',
                       CANON_TEST_ROOT=str(root), PATH=str(root) + ':' + os.environ['PATH'])
    return script, environment


class StandbyPipelineTests(unittest.TestCase):
    def test_stalled_camera_pauses_producers_but_preserves_open_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script, environment = fake_pipeline(root)
            process = subprocess.Popen([str(script), 'stream', '--device', '/dev/null', '--timeout', '1'],
                                       env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            status = root / 'runtime/canon-webcam/status'
            def state():
                return status.read_text() if status.exists() else ''
            def until(predicate):
                deadline = time.monotonic() + 10
                while not predicate():
                    if process.poll() is not None:
                        self.fail('Pipeline exited: ' + repr(process.communicate()))
                    if time.monotonic() > deadline:
                        self.fail('Pipeline timed out: ' + state())
                    time.sleep(0.02)
            try:
                until(lambda: 'Standby:' in state())
                writer_pid = int((root / 'writer-pid').read_text())
                os.kill(writer_pid, 0)
                time.sleep(0.1)
                size = (root / 'frames').stat().st_size
                self.assertGreater(size, 0)
                time.sleep(0.35)
                self.assertEqual((root / 'frames').stat().st_size, size, 'Standby must stop producing fallback frames')
                process.send_signal(signal.SIGUSR2)
                # This fake camera never emits a frame; launching its process
                # must not be reported as successful live capture.
                until(lambda: 'Starting camera' in state())
                self.assertNotIn('Capturing', state())
                self.assertEqual(int((root / 'writer-pid').read_text()), writer_pid)
                os.kill(writer_pid, 0)
            finally:
                process.terminate()
                stdout, stderr = process.communicate(timeout=12)
            self.assertEqual(process.returncode, 0, stderr.decode())
            self.assertIn(b'Video paused', stdout)
            with self.assertRaises(ProcessLookupError):
                os.kill(writer_pid, 0)

    def test_sdk_session_survives_idle_and_reopen_until_rest_timer_expires(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script, environment = fake_pipeline(root)
            active = root / 'sessions'
            active.touch()
            (root / 'canon-webcam-capture.py').write_text('''import os, signal, sys, time
from pathlib import Path

if '--prefers-sdk' in sys.argv:
    sys.exit(0)

def record(event):
    with (Path(os.environ['CANON_TEST_ROOT']) / 'sessions').open('a') as out:
        print(event, time.monotonic(), file=out)

signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
sdk = Path(os.environ['CANON_WEBCAM_SDK_ACTIVE_FILE'])
record('opened')
sdk.touch()
try:
    while True:
        os.write(1, bytes.fromhex('ffd8ffd9'))
        time.sleep(0.02)
finally:
    record('closed')
    sdk.unlink(missing_ok=True)
''')
            # An isolated character device lets the real watcher detect app handles.
            master, slave = os.openpty()
            device = os.ttyname(slave)
            os.close(slave)
            status = root / 'runtime/canon-webcam/status'
            logfile = root / 'stream.log'
            rest = 2
            with logfile.open('w') as log:
                process = subprocess.Popen([str(script), 'stream', '--device', device, '--on-demand',
                                            '--backend', 'edsdk', '--timeout', str(rest)],
                                           env=environment, stdout=log, stderr=subprocess.STDOUT)

            def until(predicate):
                deadline = time.monotonic() + 5
                while not predicate():
                    self.assertIsNone(process.poll(), logfile.read_text())
                    self.assertLess(time.monotonic(), deadline, logfile.read_text())
                    time.sleep(0.02)

            def state():
                return status.read_text().strip() if status.exists() else ''

            def open_app():
                return os.fdopen(os.open(device, os.O_RDONLY | os.O_NOCTTY), 'rb', buffering=0)

            try:
                until(lambda: state().startswith('Standby:'))
                with open_app():
                    until(lambda: state() == 'Capturing Canon live view')
                first_close = time.monotonic()
                until(lambda: state().startswith('Standby:'))
                output = root / 'frames'
                until(lambda: output.read_bytes().endswith(b'Waiting for a video app'))
                size = output.stat().st_size
                time.sleep(0.6)
                self.assertEqual(output.stat().st_size, size, 'SDK grace must pause the outgoing video')
                self.assertNotIn('closed', active.read_text())

                with open_app():
                    until(lambda: state() == 'Capturing Canon live view')
                    time.sleep(max(0, first_close + rest + 0.35 - time.monotonic()))
                    self.assertEqual(active.read_text().count('opened'), 1)
                    self.assertNotIn('closed', active.read_text(), 'Reopening must cancel the old deadline')
                    second_close = time.monotonic()
                until(lambda: 'closed' in active.read_text())
                _, closed_at = active.read_text().splitlines()[-1].split()
                self.assertGreaterEqual(float(closed_at) - second_close, rest)
                self.assertIn('Canon SDK live-view rest delay elapsed', logfile.read_text())
                self.assertNotIn('Showing status card: Lost connection', logfile.read_text())
            finally:
                process.terminate()
                process.wait(timeout=12)
                os.close(master)


if __name__ == '__main__':
    unittest.main()
