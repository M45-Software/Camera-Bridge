import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import canon_webcam_controls as controls

spec = importlib.util.spec_from_file_location('controls_capture', ROOT / 'canon-webcam-capture.py')
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


class CameraControlsTests(unittest.TestCase):
    def setUp(self):
        self.sdk = Mock()
        self.sdk.api.EdsGetEvent.return_value = 0
        self.sdk.get.return_value = 0
        self.sdk.describe.return_value = [0, 1]
        self.controls = controls.CameraControls(self.sdk)

    def test_setting_validates_current_descriptor_and_leaves_other_properties_alone(self):
        self.sdk.describe.return_value = [0x48, 0x50]
        self.controls.handle(dict(operation='set', name='iso', value=0x50))
        self.sdk.set.assert_called_once_with(0x402, 0x50)
        self.sdk.set.reset_mock()
        for name, value in [('iso', 0x58), ('iso', True), ('af_mode', 1), ('exposure_mode', 1), ('unknown', 1)]:
            with self.assertRaises(ValueError):
                self.controls.handle(dict(operation='set', name=name, value=value))
        self.sdk.set.assert_not_called()

    def test_unsupported_property_remains_visible_and_disabled(self):
        def get(prop):
            if prop == 0x0100043e:
                raise RuntimeError('Unsupported')
            return 0
        self.sdk.get.side_effect = get
        fields = {field['name']: field for field in self.controls.snapshot()['fields']}
        self.assertFalse(fields['movie_servo_af']['available'])
        self.assertEqual(fields['movie_servo_af']['choices'], [])
        self.assertTrue(fields['iso']['available'])
        self.assertEqual(fields['af_mode']['choices'], [])
        self.sdk.set.assert_not_called()

    def test_numeric_kelvin_fallback_requires_color_temperature_white_balance(self):
        self.sdk.describe.side_effect = RuntimeError('No descriptor')
        self.sdk.get.return_value = 0
        self.assertEqual(self.controls.choices('color_temperature'), [])
        self.sdk.get.return_value = 9
        self.assertIn(5200, self.controls.choices('color_temperature'))
        self.assertEqual(self.controls.choices('iso'), [])

    def test_focus_is_released_on_deadline_and_shutdown(self):
        with patch.object(controls.time, 'monotonic', return_value=10):
            self.controls.handle(dict(operation='focus', action='auto'))
        self.sdk.command.assert_called_once_with(0x102, 1)
        with patch.object(controls.time, 'monotonic', return_value=12):
            self.controls.tick()
        self.assertEqual(self.sdk.command.call_args.args, (0x102, 0))
        self.controls.handle(dict(operation='focus', action='far', step=2))
        self.assertEqual(self.sdk.command.call_args.args, (0x103, 0x8002))
        with self.assertRaises(ValueError):
            self.controls.handle(dict(operation='focus', action='near', step=4))

    def test_camera_rejection_becomes_error_not_success(self):
        self.sdk.set.side_effect = RuntimeError('Camera busy')
        result = controls.safe_handle(self.controls, dict(operation='set', name='iso', value=1))
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'Camera busy')

    def test_human_readable_exposure_values(self):
        for name, value, expected in [('iso', 0x65, '1250'), ('iso', 0x6d, '2500'), ('iso', 0x7d, '10000'),
                                      ('shutter', 0x68, '1/60'), ('aperture', 0x25, 'f/3.5'),
                                      ('exposure_compensation', 0xfd, '−⅓ EV'), ('exposure_compensation', 0xfffffffd, '−⅓ EV')]:
            self.assertEqual(controls.value_label(name, value), expected)

    def test_ipc_partial_request_errors_and_stale_socket_recovery(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, XDG_RUNTIME_DIR=directory):
            handler = Mock()
            handler.handle.return_value = dict(ok=True, fields=[])
            server = controls.ControlServer(handler)
            try:
                self.assertEqual(server.path.stat().st_mode & 0o777, 0o600)
                with socket.socket(socket.AF_UNIX) as client:
                    client.settimeout(1)
                    client.connect(str(server.path))
                    client.sendall(b'{"operation":')
                    server.poll()
                    handler.handle.assert_not_called()
                    client.sendall(b'"read"}\n')
                    server.poll()
                    self.assertTrue(json.loads(client.recv(65536))['ok'])
                with socket.socket(socket.AF_UNIX) as client:
                    client.settimeout(1)
                    client.connect(str(server.path))
                    client.sendall(b'not-json\n')
                    server.poll()
                    self.assertFalse(json.loads(client.recv(65536))['ok'])
            finally:
                server.close()
            self.assertFalse(controls.control_path().exists())
            stale = socket.socket(socket.AF_UNIX)
            stale.bind(str(controls.control_path()))
            stale.close()
            replacement = controls.ControlServer(handler)
            replacement.close()

    def test_idle_control_queries_do_not_enable_live_view(self):
        sdk = Mock()
        with patch.object(capture, 'remote_request', return_value=None), patch.object(capture, 'SDK', return_value=sdk), \
                patch.object(capture, 'safe_handle', return_value=dict(ok=True)):
            self.assertTrue(capture.control_request(dict(operation='read'))['ok'])
        sdk.open.assert_called_once_with(live_view=False)
        sdk.close.assert_called_once()
        sdk.set.assert_not_called()


if __name__ == '__main__':
    unittest.main()
