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


class StandbyPipelineTests(unittest.TestCase):
    def test_stalled_camera_pauses_producers_but_preserves_open_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'runtime').mkdir()
            shutil.copy(ROOT / 'canon-webcam-runtime.py', root)
            camera = root / 'gphoto2'
            camera.write_text('''#!/usr/bin/python3
import sys, time
if '--auto-detect' in sys.argv:
    print('Model Port\\n----- ----\\nCanon usb:001,002')
else:
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
  for frame in 1 2 3; do printf fallback; done >"$CAMERA_FIFO"
}
stop_background_test_source() {
  if [[ -n "$TEST_SOURCE_PID" ]]; then terminate_process "$TEST_SOURCE_PID"; TEST_SOURCE_PID=""; fi
}
main "$@"
'''
            script = root / 'webcam.sh'
            script.write_text(source)
            script.chmod(0o755)
            environment = dict(os.environ, CANON_WEBCAM_ON_DEMAND='0', CANON_WEBCAM_NATIVE_RESOLUTION='0', HOME=str(root), XDG_CONFIG_HOME=str(root / 'config'),
                               XDG_RUNTIME_DIR=str(root / 'runtime'), CANON_WEBCAM_IN_SERVICE='1',
                               CANON_TEST_ROOT=str(root), PATH=str(root) + ':' + os.environ['PATH'])
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
                until(lambda: 'Capturing' in state())
                self.assertEqual(int((root / 'writer-pid').read_text()), writer_pid)
                os.kill(writer_pid, 0)
            finally:
                process.terminate()
                stdout, stderr = process.communicate(timeout=12)
            self.assertEqual(process.returncode, 0, stderr.decode())
            self.assertIn(b'Video paused', stdout)
            with self.assertRaises(ProcessLookupError):
                os.kill(writer_pid, 0)


if __name__ == '__main__':
    unittest.main()
