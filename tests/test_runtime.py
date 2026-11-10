import os
import signal
import time
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'canon-webcam.sh').read_text().rsplit('main "$@"', 1)[0]


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.env = dict(os.environ, CANON_WEBCAM_ON_DEMAND='0', CANON_WEBCAM_NATIVE_RESOLUTION='0', HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / '.config'))
        self.script = self.home / 'runtime.sh'
        shutil.copy(ROOT / 'canon-webcam-runtime.py', self.home)

    def run_script(self, extra, *args):
        self.script.write_text(SOURCE + '\n' + extra + '\nmain "$@"\n')
        self.script.chmod(0o755)
        return subprocess.run([str(self.script), *args], env=self.env, text=True,
                              capture_output=True, timeout=8)

    def config(self, text):
        path = self.home / '.config/canon-webcam/settings.conf'
        path.parent.mkdir(parents=True)
        path.write_text(text)

    def test_app_signals_interrupt_retry_delay(self):
        self.script.write_text(SOURCE + """
CAMERA_RETRY_DELAY=3
trap 'CAMERA_IDLE=0' USR2
trap 'CAMERA_IDLE=1' USR1
sleep() { echo waiting; exec sleep "$@"; }
wait_for_camera_retry
printf 'idle=%s\\n' "$CAMERA_IDLE"
""")
        for event, expected in ((signal.SIGUSR2, 'idle=0'), (signal.SIGUSR1, 'idle=1')):
            process = subprocess.Popen(['bash', str(self.script)], env=self.env,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                       start_new_session=True)
            try:
                self.assertEqual(process.stdout.readline().strip(), 'waiting')
                time.sleep(0.02)
                started = time.monotonic()
                process.send_signal(event)
                output, error = process.communicate(timeout=1)
                self.assertEqual(process.returncode, 0, error)
                self.assertIn(expected, output)
                self.assertLess(time.monotonic() - started, 1)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()

    def test_controls_wait_for_matching_watcher_before_signaling(self):
        self.env['XDG_RUNTIME_DIR']=str(self.home/'run')
        marker=self.home/'run/canon-webcam/control-pid'
        marker.parent.mkdir(parents=True)
        marker.write_text('2222')
        result=self.run_script("""
service_start() { :; }
systemctl_user() {
  if [[ "$1" == show ]]; then echo 1111; else echo "control:$*"; fi
}
sleep() { printf '1111' >"$XDG_RUNTIME_DIR/canon-webcam/control-pid"; }
""", 'force-start')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('control:kill --kill-whom=main --signal=SIGUSR2',result.stdout)

    def test_config_and_cli_precedence(self):
        self.config('WIDTH=640\nHEIGHT=480\nAUTO_TIMEOUT=120\n')
        result = self.run_script('stream_camera() { echo "$WIDTH/$HEIGHT/$AUTO_TIMEOUT"; }',
                                 'stream', '--width', '800', '--timeout', '0')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('800/480/0', result.stdout)

    def test_settings_are_not_executed(self):
        marker = self.home / 'executed'
        self.config(f'WIDTH=$(touch {marker})\n')
        result = self.run_script('', 'stream', '--timeout', '0')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker.exists())

    def test_bad_config_does_not_prevent_stop(self):
        self.config('WIDTH=broken\n')
        result = self.run_script('service_stop() { echo stopped; }', 'stop')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('stopped', result.stdout)

    def test_invalid_timeout(self):
        for value in ['-1', 'abc', '08', '100000000']:
            result = self.run_script('', 'stream', '--timeout', value)
            self.assertNotEqual(result.returncode, 0, value)

    def test_backend_selection_config_and_cli(self):
        self.config('CAPTURE_BACKEND=1\n')
        result = self.run_script('stream_camera() { echo "$CAPTURE_BACKEND"; }', 'stream', '--backend', 'gphoto2')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1], '2')
        result = self.run_script('stream_camera() { echo "$CAPTURE_BACKEND"; }', 'stream')
        self.assertEqual(result.stdout.splitlines()[-1], '1')
        result = self.run_script('', 'stream', '--backend', 'bad')
        self.assertNotEqual(result.returncode, 0)

    def test_standby_preserves_writer_and_stops_fallback(self):
        result = self.run_script('''stream_camera() {
LOOPBACK_WRITER_PID=12345
CAMERA_IDLE=1
local sleeps=0
start_background_test_source() { echo fallback-start; }
stop_background_test_source() { echo fallback-stop; }
stop_camera_stream_processes() { echo writer-closed; }
timeout() { return 1; }
wait_for_camera_retry() { sleeps=$((sleeps + 1)); if (( sleeps >= 2 )); then CAMERA_IDLE=0; fi; }
standby_encoding
printf 'writer=%s\n' "$LOOPBACK_WRITER_PID"
}''', 'stream', '--timeout', '0')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('fallback-stop', result.stdout)
        self.assertIn('writer=12345', result.stdout)
        self.assertNotIn('writer-closed', result.stdout)

    def test_native_dimensions_are_used_without_upscaling(self):
        helper = self.home / 'canon-webcam-runtime.py'
        helper.write_text("print('{\"width\":1024,\"height\":576,\"sizes\":[\"Large\"]}')\n")
        result = self.run_script('''stream_camera() {
NATIVE_RESOLUTION=1
ON_DEMAND=0
timeout() { printf 'Model Port\n----- ----\nCanon usb\n'; }
stop_camera_mount_helpers() { :; }
camera_prepare_live_view() { :; }
resolve_native_resolution
echo "$WIDTH/$HEIGHT"
}''', 'stream', '--timeout', '0')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('1024/576', result.stdout)

    def test_capture_wait_ignores_control_signal_until_process_exits(self):
        result = self.run_script('''stream_camera() {
start_loopback_writer() { :; }
write_stream_state() { :; }
CAMERA_FIFO=/dev/null
# A harmless shell child stands in for gphoto2; it remains alive across USR2.
gphoto2() { sleep 0.6; return 7; }
trap ':' USR2
local parent_pid="$BASHPID"
(sleep 0.1; kill -USR2 "$parent_pid") &
run_camera_stream_once
}''', 'stream', '--timeout', '0')
        # The synthetic capture's actual exit status must survive an interrupted wait.
        self.assertEqual(result.returncode, 7, result.stderr)

    def test_preview_cannot_write_an_installed_user_service(self):
        self.env['CANON_WEBCAM_PREVIEW'] = '1'
        result = self.run_script('require_not_root() { :; }', 'setup-user')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.home / '.config/systemd/user').exists())

    def test_identical_offline_state_is_not_continuously_rendered(self):
        result = self.run_script('''stream_camera() {
start_loopback_writer() { :; }
stop_background_test_source() { LAST_STATUS_FRAME=""; }
run_test_source_feeder_ffmpeg() { echo card >>"$HOME/card-count"; }
start_background_test_source "Camera disconnected"
wait "$TEST_SOURCE_PID"
start_background_test_source "Camera disconnected"
start_background_test_source "Lost connection"
wait "$TEST_SOURCE_PID"
wc -l <"$HOME/card-count"
}''', 'stream', '--timeout', '0')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1], '2')

    def test_unresponsive_camera_waits_for_recovery_instead_of_looping(self):
        result = self.run_script('''cleanup_stream() { :; }
need_cmd() { :; }
ensure_loopback_for_writer() { :; }
resolve_native_resolution() { :; }
start_background_test_source() { LOOPBACK_WRITER_PID="$BASHPID"; echo "$1"; }
stop_camera_mount_helpers() { :; }
camera_wait_stable() { LAST_CAMERA_ID="Canon usb:1"; return 0; }
camera_prepare_live_view() { echo prepare >>"$HOME/prepares"; CAMERA_RESPONSIVE=0; }
standby_encoding() { echo recovery-paused; wc -l <"$HOME/prepares"; exit 0; }
''', 'stream', '--device', '/dev/null', '--timeout', '0')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Camera not responding', result.stdout)
        self.assertIn('recovery-paused', result.stdout)
        self.assertEqual(result.stdout.splitlines()[-1], '1')

    def test_recovery_retries_same_usb_device_without_changing_warning(self):
        result = self.run_script('''stream_camera() {
CAMERA_IDLE=1
CAMERA_RECOVERY_WAIT=1
CAMERA_FAILURE_DELAY=5
LAST_CAMERA_ID="Canon usb:1"
SECONDS=0
start_loopback_writer() { :; }
start_background_test_source() { echo "card:$1"; }
stop_background_test_source() { :; }
write_stream_state() { echo "state:$1"; }
timeout() { printf 'Model Port\n----- ----\nCanon usb:1\n'; }
wait_for_camera_retry() { SECONDS=$((SECONDS + 5)); }
standby_encoding
echo "idle=$CAMERA_IDLE recovery=$CAMERA_RECOVERY_WAIT"
}''', 'stream', '--timeout', '0')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Retrying camera connection in the background', result.stdout)
        self.assertIn('idle=0 recovery=1', result.stdout)
        self.assertNotIn('card:Waiting for a video app', result.stdout)
        self.assertNotIn('Starting camera', result.stdout)

    def test_default_is_one_minute(self):
        result = self.run_script('require_not_root() { :; }; systemctl_user() { :; }', 'setup-user')
        self.assertEqual(result.returncode, 0, result.stderr)
        service = (self.home / '.config/systemd/user/canon-webcam.service').read_text()
        self.assertIn('CANON_WEBCAM_AUTO_TIMEOUT=60', service)

    def test_start_resumes_without_restarting_service(self):
        result = self.run_script('''ensure_runtime_installed_current() { :; }
systemctl_user() { echo "$*"; }''', 'start')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('kill --kill-whom=main --signal=SIGUSR2', result.stdout)
        self.assertNotIn('restart canon-webcam.service', result.stdout)

    def test_capture_failure_is_preserved(self):
        result = self.run_script('stream_camera() { return 7; }', 'stream', '--timeout', '5')
        self.assertEqual(result.returncode, 7)

    def test_service_defaults_and_restart_policy(self):
        result = self.run_script('require_not_root() { :; }; systemctl_user() { :; }',
                                 'setup-user', '--width', '640', '--timeout', '900')
        self.assertEqual(result.returncode, 0, result.stderr)
        service = (self.home / '.config/systemd/user/canon-webcam.service').read_text()
        self.assertIn('Restart=on-failure', service)
        self.assertIn('CANON_WEBCAM_AUTO_TIMEOUT=900', service)
        self.assertIn('CANON_WEBCAM_WIDTH=640', service)
        self.assertNotIn('stream --', service)  # GUI config must override install defaults.


if __name__ == '__main__':
    unittest.main()
