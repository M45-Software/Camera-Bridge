import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HELPER = Path(__file__).resolve().parents[1] / 'canon-webcam-runtime.py'
spec = importlib.util.spec_from_file_location('frame_monitor', HELPER)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)


class FrameMonitorTests(unittest.TestCase):
    def test_native_size_is_read_from_live_header_not_embedded_thumbnail(self):
        thumbnail = b'\xff\xd8\xff\xc0\x00\x0b\x08\x00\x30\x00\x40\x01\x01\x11\x00\xff\xd9'
        app = b'\xff\xe1' + (len(thumbnail) + 2).to_bytes(2, 'big') + thumbnail
        main = b'\xff\xd8' + app + b'\xff\xc0\x00\x0b\x08\x02\x40\x04\x00\x01\x01\x11\x00'
        frame = monitor.JPEGFrames()
        frame.feed(main[:10])
        self.assertIsNone(frame.size)
        frame.feed(main[10:])
        self.assertEqual(frame.size, (1024, 576))
        self.assertEqual(frame.header, b'')

    def test_only_complete_camera_jpeg_resets_activity(self):
        frames = monitor.JPEGFrames()
        self.assertFalse(frames.feed(b'noise\xff'))
        self.assertFalse(frames.feed(b'\xd8partial'))
        self.assertFalse(frames.feed(b'image\xff'))
        self.assertTrue(frames.feed(b'\xd9'))
        self.assertFalse(frames.feed(b'noise\xff\xd9'))
        self.assertTrue(frames.feed(b'\xff\xd8image\xff\xd9'))

    def test_relay_preserves_bytes_and_tracks_frames(self):
        for payload in (b'\xff\xd8image\xff\xd9', b'noise', b'\xff\xd8partial'):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                state = root / 'last-frame'
                output = root / 'out'
                result = subprocess.run([sys.executable, str(HELPER), 'relay', str(output),
                                         sys.executable, '-c', f'import sys; sys.stdout.buffer.write({payload!r})'],
                                        env=dict(os.environ, CANON_WEBCAM_ON_DEMAND='0', CANON_WEBCAM_NATIVE_RESOLUTION='0', CANON_WEBCAM_ACTIVITY_FILE=str(state)),
                                        capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output.read_bytes(), payload)
                self.assertEqual(state.exists(), payload.endswith(b'\xff\xd9'))

    def test_live_frames_prevent_idle_then_standby_and_resume_keep_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = root / 'events'
            fake = root / 'camera.py'
            fake.write_text('''import os, signal, time
from pathlib import Path
state = Path(os.environ['CANON_WEBCAM_ACTIVITY_FILE'])
events = Path(os.environ['CAMERA_TEST_EVENTS'])
until = time.monotonic() + 0.9

def log(text):
    with events.open('a') as out: out.write(text + '\\n')

def resume(*_):
    global until
    until = time.monotonic() + 0.9
    log('resumed')

signal.signal(signal.SIGUSR1, lambda *_: log('standby'))
signal.signal(signal.SIGUSR2, resume)
signal.signal(signal.SIGTERM, lambda *_: exit(0))
Path(os.environ['CANON_WEBCAM_CONTROL_READY']).touch()
log('ready')
while True:
    if time.monotonic() < until:
        state.write_text(str(time.monotonic()))
    time.sleep(0.02)
''')
            child = subprocess.Popen([sys.executable, str(HELPER), 'watch', '0.4', sys.executable, str(fake)],
                                     env=dict(os.environ, CANON_WEBCAM_ON_DEMAND='0', CANON_WEBCAM_NATIVE_RESOLUTION='0', CAMERA_TEST_EVENTS=str(events)),
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            def contents():
                return events.read_text() if events.exists() else ''
            def wait_for(predicate):
                deadline = time.monotonic() + 4
                while not predicate():
                    if time.monotonic() > deadline:
                        self.fail('monitor state change timed out: ' + contents())
                    time.sleep(0.02)
            try:
                wait_for(lambda: 'ready' in contents())
                time.sleep(0.65)  # Longer than idle limit, but images are still arriving.
                self.assertNotIn('standby', contents())
                wait_for(lambda: 'standby' in contents())
                self.assertIsNone(child.poll(), 'standby must not stop the service')
                child.send_signal(signal.SIGUSR2)
                wait_for(lambda: 'resumed' in contents())
                time.sleep(0.65)
                self.assertEqual(contents().count('standby'), 1)
                wait_for(lambda: contents().count('standby') == 2)
                self.assertIsNone(child.poll())
            finally:
                child.terminate()
                stdout, stderr = child.communicate(timeout=5)
            self.assertEqual(child.returncode, 0, stderr.decode())
            self.assertIn(b'keeping virtual webcam open', stdout)

    def test_relay_reaps_stalled_capture_on_pause(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid_file = root / 'pid'
            child = subprocess.Popen([sys.executable, str(HELPER), 'relay', str(root / 'out'),
                                      sys.executable, '-c',
                                      'import os,time,signal; from pathlib import Path; signal.signal(signal.SIGINT,lambda *_:exit(0)); '
                                      f'Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(30)'],
                                     env=dict(os.environ, CANON_WEBCAM_ON_DEMAND='0', CANON_WEBCAM_NATIVE_RESOLUTION='0', CANON_WEBCAM_ACTIVITY_FILE=str(root / 'activity')))
            try:
                deadline = time.monotonic() + 4
                while not pid_file.exists():
                    if time.monotonic() > deadline:
                        self.fail('fake capture did not start')
                    time.sleep(0.01)
                capture_pid = int(pid_file.read_text())
                child.terminate()
                child.wait(timeout=5)
                with self.assertRaises(ProcessLookupError):
                    os.kill(capture_pid, 0)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()


if __name__ == '__main__':
    unittest.main()
