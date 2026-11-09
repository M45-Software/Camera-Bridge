import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

HELPER = Path(__file__).resolve().parents[1] / 'canon-webcam-runtime.py'
spec = importlib.util.spec_from_file_location('consumer_monitor', HELPER)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class ConsumersTests(unittest.TestCase):
    def test_device_aliases_duplicates_and_own_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            device = root/'video'
            device.touch()
            other = root/'other'
            other.touch()
            proc = root/'proc'
            proc.mkdir()
            for pid, group, name, files in [(81001, 77, 'our writer', [device]),
                                             (81002, 88, 'Firefox', [device, device]),
                                             (81003, 99, 'unrelated', [other])]:
                process = proc/str(pid)
                (process/'fd').mkdir(parents=True)
                (process/'stat').write_text(f'{pid} ({name}) S 1 {group} 0')
                (process/'comm').write_text(name)
                for number, file in enumerate(files):
                    (process/'fd'/str(number)).symlink_to(file)
            clients, uncertain = runtime.consumers(device, 77, proc)
            self.assertEqual(clients, [{'pid': 81002, 'name': 'Firefox'}])
            self.assertFalse(uncertain)
            original = Path.iterdir
            def restricted(path):
                if path == proc/'81002/fd':
                    raise PermissionError('restricted')
                return original(path)
            with patch.object(Path, 'iterdir', restricted):
                clients, uncertain = runtime.consumers(device, 77, proc)
            self.assertEqual(clients, [])
            self.assertTrue(uncertain)

    def test_continuous_capture_can_be_forced_off_then_return_to_automatic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            device = root / 'video'
            device.touch()
            fake = root / 'camera.py'
            fake.write_text("""import os, signal, time
from pathlib import Path
root=Path(os.environ['TEST_ROOT'])
def log(message):
 with (root/'events').open('a') as out: out.write(message+'\\n')
signal.signal(signal.SIGUSR1,lambda *_:log('paused'))
signal.signal(signal.SIGUSR2,lambda *_:log('resumed'))
signal.signal(signal.SIGALRM,lambda *_:log('viewfinder-off'))
signal.signal(signal.SIGTERM,lambda *_:exit(0))
Path(os.environ['CANON_WEBCAM_CONTROL_READY']).touch()
while True: time.sleep(.05)
""")
            env=dict(os.environ, TEST_ROOT=str(root), CANON_WEBCAM_DEVICE=str(device),
                     CANON_WEBCAM_ON_DEMAND='0', CANON_WEBCAM_IN_SERVICE='1',
                     XDG_RUNTIME_DIR=str(root/'run'))
            monitor=subprocess.Popen([sys.executable,str(HELPER),'watch','10',sys.executable,str(fake)],env=env)
            def events(): return (root/'events').read_text() if (root/'events').exists() else ''
            def until(predicate):
                deadline=time.monotonic()+5
                while not predicate():
                    self.assertIsNone(monitor.poll())
                    if time.monotonic()>deadline: self.fail('Timed out: '+events())
                    time.sleep(.02)
            try:
                until(lambda:'resumed' in events())
                monitor.send_signal(signal.SIGUSR1)
                until(lambda:'paused' in events() and 'viewfinder-off' in events())
                monitor.send_signal(signal.SIGUSR2)
                until(lambda:events().count('resumed')==2)
                monitor.send_signal(signal.SIGCONT)
                until(lambda:events().count('paused')==2)
                usage=json.loads((root/'run/canon-webcam/usage.json').read_text())
                self.assertTrue(usage['automatic'])
                self.assertFalse(usage['manual_off'])
                self.assertFalse(usage['manual_override'])
            finally:
                monitor.terminate(); monitor.wait(timeout=5)

    def test_open_close_multiple_apps_and_idle_live_view_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            device = root/'video'
            device.touch()
            events = root/'events'
            fake = root/'writer.py'
            fake.write_text('''import os,signal,time
from pathlib import Path
writer = open(os.environ['CANON_WEBCAM_DEVICE'], 'wb')
events = Path(os.environ['TEST_EVENTS'])
def log(text):
    with events.open('a') as out: out.write(text+'\\n')
signal.signal(signal.SIGUSR1,lambda *_:log('paused'))
signal.signal(signal.SIGUSR2,lambda *_:log('resumed'))
signal.signal(signal.SIGALRM,lambda *_:log('viewfinder-off'))
signal.signal(signal.SIGTERM,lambda *_:exit(0))
Path(os.environ['CANON_WEBCAM_CONTROL_READY']).touch()
log('ready')
while True: time.sleep(.05)
''')
            environment = dict(os.environ, CANON_WEBCAM_DEVICE=str(device), CANON_WEBCAM_ON_DEMAND='1',
                               TEST_EVENTS=str(events), XDG_RUNTIME_DIR=str(root/'run'), CANON_WEBCAM_IN_SERVICE='1')
            monitor = subprocess.Popen([sys.executable,str(HELPER),'watch','0.8',sys.executable,str(fake)], env=environment)
            readers = []
            def text(): return events.read_text() if events.exists() else ''
            def until(predicate):
                deadline=time.monotonic()+5
                while not predicate():
                    self.assertIsNone(monitor.poll())
                    if time.monotonic()>deadline: self.fail('Timed out: '+text())
                    time.sleep(.02)
            def reader():
                process = subprocess.Popen([sys.executable,'-c',
                    'import sys,time; f=open(sys.argv[1]); print("ready",flush=True); time.sleep(30)', str(device)],
                    stdout=subprocess.PIPE)
                self.assertEqual(process.stdout.readline(), b'ready\n')
                readers.append(process)
                return process
            try:
                until(lambda:'ready' in text())
                time.sleep(.4)
                self.assertNotIn('resumed',text(), 'Own writer must not trigger capture')
                first=reader()
                until(lambda:'resumed' in text())
                second=reader()
                time.sleep(.3)
                first.terminate(); first.wait(); first.stdout.close()
                time.sleep(.4)
                self.assertNotIn('paused',text(), 'Another app still needs the webcam')
                second.terminate(); second.wait(); second.stdout.close()
                until(lambda:'paused' in text())
                self.assertNotIn('viewfinder-off', text(), 'Idle grace must precede camera shutdown')
                until(lambda:'viewfinder-off' in text())
                third=reader()
                until(lambda:text().count('resumed') == 2)
                usage=json.loads((root/'run/canon-webcam/usage.json').read_text())
                self.assertIn(third.pid,[client['pid'] for client in usage['clients']])
                self.assertNotIn(monitor.pid, [client['pid'] for client in usage['clients']])
                monitor.send_signal(signal.SIGUSR1)
                until(lambda:text().count('paused') == 2)
                until(lambda:text().count('viewfinder-off') == 2)
                usage=json.loads((root/'run/canon-webcam/usage.json').read_text())
                self.assertTrue(usage['manual_off'])
                self.assertIn(third.pid,[client['pid'] for client in usage['clients']])
                time.sleep(.4)
                self.assertEqual(text().count('resumed'),2, 'Forced off must hold while app stays open')
                monitor.send_signal(signal.SIGCONT)
                until(lambda:text().count('resumed') == 3)
                third.terminate(); third.wait(); third.stdout.close()
                until(lambda:text().count('paused') == 3)
                monitor.send_signal(signal.SIGUSR2)
                until(lambda:text().count('resumed') == 4)
                usage=json.loads((root/'run/canon-webcam/usage.json').read_text())
                self.assertTrue(usage['manual_override'])
                self.assertFalse(usage['manual_off'])
                monitor.send_signal(signal.SIGCONT)
                until(lambda:text().count('paused') == 4)
            finally:
                for process in readers:
                    if process.poll() is None: process.terminate(); process.wait()
                    if not process.stdout.closed: process.stdout.close()
                monitor.terminate(); monitor.wait(timeout=5)
            self.assertEqual(monitor.returncode,0)


if __name__ == '__main__': unittest.main()
