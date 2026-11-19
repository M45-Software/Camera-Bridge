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

    def test_stalled_live_feed_outputs_lost_card_then_resumes_after_complete_frame(self):
        first = b'\xff\xd8\xff\xd9'
        # An embedded end marker in an unfinished frame must leave the card visible.
        partial = b'\xff\xd8\xff\xe1\x00\x06\xff\xd9zz'
        end = b'\xff\xd9'
        card = b'\xff\xd8\xff\xfe\x00\x06lost\xff\xd9'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            activity = root / 'last-frame'
            output = root / 'video'
            (root / 'card').write_bytes(card)
            runtime = root / 'run'
            status = runtime / 'canon-webcam/status'
            resume = root / 'resume'
            capture = f'''import os, time
from pathlib import Path
os.write(1, {first + partial!r})
while not Path({str(resume)!r}).exists():
    time.sleep(0.01)
os.write(1, {end!r})
'''
            env = dict(os.environ, CANON_WEBCAM_ACTIVITY_FILE=str(activity),
                       CANON_WEBCAM_LOST_CONNECTION_CARD=str(root / 'card'),
                       CANON_WEBCAM_IN_SERVICE='1', XDG_RUNTIME_DIR=str(runtime))
            with subprocess.Popen([sys.executable, str(HELPER), 'relay', str(output),
                                   sys.executable, '-c', capture], env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE) as relay:
                try:
                    deadline = time.monotonic() + 4
                    while not status.exists() or status.read_text() != 'Lost connection\n':
                        self.assertIsNone(relay.poll())
                        self.assertLess(time.monotonic(), deadline, 'Lost connection card did not appear')
                        time.sleep(0.01)
                    last_frame = float(activity.read_text())
                    self.assertGreaterEqual(time.monotonic() - last_frame, monitor.FRAME_GAP_SECONDS)
                    self.assertEqual(output.read_bytes(), first + card)
                    resume.touch()
                    stdout, stderr = relay.communicate(timeout=4)
                    self.assertEqual(relay.returncode, 0, stderr.decode())
                    self.assertIn(b'Showing status card: Lost connection', stdout)
                    self.assertEqual(output.read_bytes(), first + card + partial + end)
                    self.assertEqual(status.read_text(), 'Capturing Canon live view\n')
                    self.assertGreater(float(activity.read_text()), last_frame)
                finally:
                    if relay.poll() is None:
                        relay.terminate()
                        relay.communicate(timeout=4)

    def test_live_frames_prevent_idle_then_standby_and_resume_keep_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = root / 'events'
            fake = root / 'camera.py'
            fake.write_text('''import os, signal, time
from pathlib import Path
state = Path(os.environ['CANON_WEBCAM_ACTIVITY_FILE'])
events = Path(os.environ['CAMERA_TEST_EVENTS'])
stall = events.with_name('stall')

def log(text):
    with events.open('a') as out: out.write(text + '\\n')

def resume(*_):
    stall.unlink(missing_ok=True)
    log('resumed')

signal.signal(signal.SIGUSR1, lambda *_: log('standby'))
signal.signal(signal.SIGUSR2, resume)
signal.signal(signal.SIGTERM, lambda *_: exit(0))
Path(os.environ['CANON_WEBCAM_CONTROL_READY']).touch()
log('ready')
while True:
    if not stall.exists():
        update = state.with_suffix('.new')
        update.write_text(str(time.monotonic()))
        update.replace(state)
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
                (root / 'stall').touch()
                wait_for(lambda: 'standby' in contents())
                self.assertIsNone(child.poll(), 'standby must not stop the service')
                resumes = contents().count('resumed')
                child.send_signal(signal.SIGUSR2)
                wait_for(lambda: contents().count('resumed') > resumes)
                time.sleep(0.65)
                self.assertEqual(contents().count('standby'), 1)
                (root / 'stall').touch()
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
