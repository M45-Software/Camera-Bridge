import ctypes
import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('capture', ROOT / 'canon-webcam-capture.py')
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)
# APP payload deliberately includes an EOI; it must not truncate the frame.
JPEG = b'\xff\xd8\xff\xe1\x00\x06\xff\xd9zz\xff\xd9'


class CaptureTests(unittest.TestCase):
    def test_jpeg_boundaries_ignore_embedded_markers_and_partial_reads(self):
        self.assertEqual(capture.jpeg_end(JPEG + JPEG), len(JPEG))
        for cut in range(2, len(JPEG)):
            self.assertIsNone(capture.jpeg_end(JPEG[:cut]), cut)
        with self.assertRaises(ValueError):
            capture.jpeg_end(b'\xff\xd8\xff\xe1\x00\x01')

    def test_explicit_sdk_failure_never_uses_gphoto(self):
        with patch.object(capture, 'forward', return_value=False) as forward, patch.object(capture, 'gphoto_command') as gphoto:
            self.assertEqual(capture.capture(selected='edsdk'), 1)
            self.assertEqual(forward.call_count, 1)
            gphoto.assert_not_called()

    def test_auto_falls_back_after_sdk_failure(self):
        with patch.object(capture, 'forward', side_effect=[False, True]) as forward, patch.object(capture, 'gphoto_command', return_value=['fake-gphoto']):
            self.assertEqual(capture.capture(1, 'auto'), 0)
            self.assertEqual(forward.call_args.args[1], 'gphoto2')

    def test_explicit_gphoto_does_not_try_sdk(self):
        with patch.object(capture, 'forward', return_value=True) as forward, patch.object(capture, 'gphoto_command', return_value=['fake-gphoto']):
            self.assertEqual(capture.capture(1, 'gphoto2'), 0)
            self.assertEqual(forward.call_count, 1)
            self.assertEqual(forward.call_args.args[0], ['fake-gphoto'])

    def test_user_stop_is_not_a_fallback_failure(self):
        with patch.object(capture, 'forward', side_effect=KeyboardInterrupt), patch.object(capture, 'gphoto_command') as gphoto:
            with self.assertRaises(KeyboardInterrupt):
                capture.capture(selected='auto')
            gphoto.assert_not_called()

    def test_partial_sdk_open_still_releases_refs(self):
        sdk = capture.SDK.__new__(capture.SDK)
        sdk.api = Mock()
        sdk.camera, sdk.cameras = ctypes.c_void_p(1), ctypes.c_void_p(2)
        sdk.opened, sdk.initialized, sdk.restore = False, True, {}
        sdk.close()
        self.assertEqual(sdk.api.EdsRelease.call_count, 2)
        sdk.api.EdsCloseSession.assert_not_called()
        sdk.api.EdsTerminateSDK.assert_called_once()

    def test_sdk_settings_restored_before_session_close(self):
        sdk = capture.SDK.__new__(capture.SDK)
        sdk.api = Mock()
        sdk.camera, sdk.cameras = ctypes.c_void_p(1), ctypes.c_void_p(2)
        sdk.opened, sdk.initialized, sdk.restore = True, True, {0x500: 0}
        sdk.api.EdsSetPropertyData.return_value = 0
        sdk.close()
        self.assertEqual(sdk.api.method_calls[0][0], 'EdsSetPropertyData')
        self.assertEqual(sdk.api.method_calls[1][0], 'EdsCloseSession')

    def test_no_frame_timeout_reaps_child(self):
        with patch.object(capture, 'log'):
            self.assertFalse(capture.forward([sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGINT, lambda *_: exit(0)); time.sleep(60)'], 'fake SDK', 0, timeout=0.1))

    def test_missing_library_really_falls_back_and_stop_releases_child(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = root / 'gphoto2'
            marker = root / 'stopped'
            command.write_text('#!' + sys.executable + '\n' + f'''
import sys,time
from pathlib import Path
if '--stdout' not in sys.argv: raise SystemExit(0)
try:
    while True:
        sys.stdout.buffer.write({JPEG!r}); sys.stdout.buffer.flush(); time.sleep(0.01)
except KeyboardInterrupt:
    Path({str(marker)!r}).write_text('released')
''')
            command.chmod(0o755)
            env = dict(os.environ, PATH=str(root) + os.pathsep + os.environ['PATH'],
                       CANON_WEBCAM_EDSDK_LIBRARY=str(root / 'missing.so'), CANON_WEBCAM_CAPTURE_BACKEND='0')
            process = subprocess.Popen([sys.executable, str(ROOT / 'canon-webcam-capture.py')],
                                       env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                self.assertEqual(process.stdout.read(len(JPEG)), JPEG)
                process.send_signal(signal.SIGTERM)
                _, error = process.communicate(timeout=8)
                self.assertEqual(process.returncode, 0, error)
                self.assertIn(b'falling back to gphoto2', error)
                self.assertTrue(marker.exists())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()


if __name__ == '__main__':
    unittest.main()
