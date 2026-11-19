#!/usr/bin/python3
"""Monitor complete camera JPEGs and pause producers without closing the writer."""
import os
import json
import stat
from pathlib import Path
import re
import select
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from canon_webcam_jpeg import take_jpegs


FRAME_GAP_SECONDS = 0.5


def jpeg_size(data):
    """Read the main JPEG SOF header; skip APP segments and embedded thumbnails."""
    start = data.find(b'\xff\xd8')
    if start < 0:
        return None
    offset = start + 2
    while offset + 4 <= len(data):
        if data[offset] != 0xff:
            return None
        while offset + 1 < len(data) and data[offset + 1] == 0xff:
            offset += 1
        marker = data[offset + 1]
        if marker in (0xda, 0xd9):
            return None
        if marker in (0xd8, 0x01) or 0xd0 <= marker <= 0xd7:
            offset += 2
            continue
        length = int.from_bytes(data[offset + 2:offset + 4], 'big')
        if length < 2:
            return None
        if marker in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf):
            if length >= 8 and offset + 9 <= len(data):
                height = int.from_bytes(data[offset + 5:offset + 7], 'big')
                width = int.from_bytes(data[offset + 7:offset + 9], 'big')
                if width and height:
                    return width, height
            return None
        if offset + 2 + length > len(data):
            return None
        offset += 2 + length
    return None


def publish_stream_status(message, size=None):
    if os.environ.get('CANON_WEBCAM_IN_SERVICE') != '1' or not os.environ.get('XDG_RUNTIME_DIR'):
        return
    runtime = Path(os.environ['XDG_RUNTIME_DIR']) / 'canon-webcam'
    try:
        runtime.mkdir(parents=True, exist_ok=True)
        temporary = runtime / 'status.frame'
        temporary.write_text(message + '\n')
        temporary.replace(runtime / 'status')
        if size:
            root = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'canon-webcam'
            root.mkdir(parents=True, exist_ok=True)
            cache = root / 'native-size.json'
            known = {}
            try:
                known = json.loads(cache.read_text())
            except (OSError, ValueError):
                pass
            known.update(width=size[0], height=size[1], cached=False,
                         live_view_size=int(os.environ.get('CANON_WEBCAM_LIVE_VIEW_SIZE', '1')),
                         capture_backend=int(os.environ.get('CANON_WEBCAM_CAPTURE_BACKEND', '0')))
            write_json(cache, known)
            write_json(runtime / 'camera.json', known)
    except (OSError, ValueError):
        pass  # A preference-write failure must not interrupt live capture.

class JPEGFrames:
    """Recognize complete MJPEG envelopes, including markers split across reads."""
    def __init__(self):
        self.tail = b''
        self.started = False
        self.count = 0
        self.header = bytearray()
        self.size = None

    def feed(self, data):
        if self.size is None and len(self.header) < 262144:
            self.header.extend(data)
            self.size = jpeg_size(self.header)
            if self.size is not None:
                self.header.clear()
        complete = False
        for marker in re.finditer(b'\xff[\xd8\xd9]', self.tail + data):
            if marker[0] == b'\xff\xd8':
                self.started = True
            elif self.started:
                complete = True
                self.count += 1
                self.started = False
        self.tail = data[-1:]
        return complete


def activity(path):
    destination = Path(path)
    temporary = destination.with_suffix('.new')
    temporary.write_text(str(time.monotonic()))
    temporary.replace(destination)


def write_video(output, data):
    remaining = memoryview(data)
    while remaining:
        remaining = remaining[output.write(remaining):]


def show_lost_connection(output, card):
    write_video(output, card)
    publish_stream_status('Lost connection')
    print('[camera-bridge] Showing status card: Lost connection', flush=True)


def relay(fifo, command):
    """Forward camera JPEGs and replace a stalled feed with a status card."""
    child = None
    interrupted = False
    def stop(_signum, _frame):
        nonlocal interrupted
        if interrupted:
            return
        interrupted = True
        if child is not None and child.poll() is None:
            child.send_signal(signal.SIGINT)
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    stats_file = os.environ.get('CANON_WEBCAM_FRAME_STATS')
    stats_path = Path(stats_file) if stats_file else None
    if stats_path is not None:
        stats_path.unlink(missing_ok=True)
    activity_path = os.environ.get('CANON_WEBCAM_ACTIVITY_FILE')
    card_path = os.environ.get('CANON_WEBCAM_LOST_CONNECTION_CARD')
    card = Path(card_path).read_bytes() if card_path else b''
    idle_card_path = os.environ.get('CANON_WEBCAM_IDLE_CARD')
    idle_card = Path(idle_card_path).read_bytes() if idle_card_path else b''
    sdk_active_file = os.environ.get('CANON_WEBCAM_SDK_ACTIVE_FILE')
    sdk_idle_file = os.environ.get('CANON_WEBCAM_SDK_IDLE_FILE')
    sdk_active = Path(sdk_active_file) if sdk_active_file else None
    sdk_idle = Path(sdk_idle_file) if sdk_idle_file else None
    follow_sdk_idle = sdk_active is not None and sdk_idle is not None
    framed = bool(card) or follow_sdk_idle
    pending = bytearray()
    frames = JPEGFrames()
    first_frame = None
    last_frame = None
    lost_connection = False
    paused = False
    last_report = 0
    size_reported = False
    try:
        with open(fifo, 'wb', buffering=0) as output:
            child = subprocess.Popen(command, stdout=subprocess.PIPE, bufsize=0)
            fd = child.stdout.fileno()
            while True:
                sdk_paused = follow_sdk_idle and sdk_idle.exists() and (paused or sdk_active.exists())
                if sdk_paused and not paused:
                    write_video(output, idle_card)
                    publish_stream_status('Standby: Waiting for a video app. Virtual webcam ready.')
                    print('[camera-bridge] Video paused; Canon SDK live view remains on', flush=True)
                    # Standby images and discarded camera frames never count as activity.
                    last_frame = None
                    last_report = frames.count = 0
                    lost_connection = False
                    if stats_path is not None:
                        stats_path.unlink(missing_ok=True)
                elif paused and not sdk_paused:
                    if first_frame is not None and sdk_active.exists():
                        last_frame = time.monotonic()
                    first_frame = None
                paused = sdk_paused
                delay = 0.1 if follow_sdk_idle else None
                if card and last_frame is not None and not lost_connection:
                    remaining = last_frame + FRAME_GAP_SECONDS - time.monotonic()
                    if remaining <= 0:
                        show_lost_connection(output, card)
                        lost_connection = True
                    else:
                        delay = remaining if delay is None else min(delay, remaining)
                if delay is not None and not select.select([fd], [], [], delay)[0]:
                    continue
                data = os.read(fd, 262144)
                if not data:
                    break
                packets = (data,)
                if framed:
                    # Switch between camera and status cards at JPEG boundaries.
                    pending.extend(data)
                    packets = take_jpegs(pending)
                for packet in packets:
                    if paused:
                        continue
                    write_video(output, packet)
                    if framed:
                        frames.count += 1
                        if frames.size is None:
                            frames.size = jpeg_size(packet)
                    elif not frames.feed(packet):
                        continue
                    if activity_path:
                        activity(activity_path)
                    now = time.monotonic()
                    starting = first_frame is None
                    if starting:
                        first_frame = now
                    if frames.size and not size_reported:
                        publish_stream_status('Capturing Canon live view', frames.size)
                        size_reported = True
                    elif starting or lost_connection:
                        publish_stream_status('Capturing Canon live view')
                    last_frame = now
                    lost_connection = False
                    if stats_path is not None and now - last_report >= 0.5:
                        elapsed = now - first_frame
                        write_json(stats_path,
                                   {'frames': frames.count, 'fps': (frames.count - 1) / elapsed if elapsed else 0,
                                    'last_frame': now})
                        last_report = now
            status = child.wait()
            if status and card and last_frame is not None and not lost_connection:
                show_lost_connection(output, card)
            return status
    finally:
        if child is not None:
            if child.poll() is None:
                if not interrupted:
                    child.send_signal(signal.SIGINT)
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            if child.stdout:
                child.stdout.close()


def consumers(device, own_group, proc=Path('/proc')):
    """Find same-user openers, excluding our complete pipeline process group.

    Open descriptors are a usage hint, not proof that an app is transmitting.
    Permission failures are explicit uncertainty, never proof of no consumers.
    """
    clients = []
    uncertain = False
    try:
        target = Path(device).stat()
        processes = list(proc.iterdir())
    except OSError:
        return [], True
    for process in processes:
        if not process.name.isdigit() or int(process.name) == os.getpid():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            fields = (process / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(fields[2]) == own_group:
                continue
            for descriptor in (process / 'fd').iterdir():
                try:
                    opened = descriptor.stat()
                    match = (stat.S_ISCHR(opened.st_mode) and opened.st_rdev == target.st_rdev
                             if stat.S_ISCHR(target.st_mode) else
                             (opened.st_dev, opened.st_ino) == (target.st_dev, target.st_ino))
                    if match:
                        name = (process / 'comm').read_text().strip()[:80]
                        clients.append({'pid': int(process.name), 'name': name})
                        break
                except PermissionError:
                    uncertain = True
                except OSError:
                    pass  # Process or descriptor closed during the scan.
        except PermissionError:
            uncertain = True
        except (OSError, ValueError, IndexError):
            pass
    return sorted(clients, key=lambda client: client['pid']), uncertain


def write_json(path, value):
    temporary = path.with_suffix('.new')
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


def probe():
    """Measure one brief MJPEG capture; no video is saved or encoded."""
    result = {'sizes': [], 'width': None, 'height': None}
    try:
        settings = subprocess.run(['gphoto2', '--get-config', 'liveviewsize'],
                                  capture_output=True, text=True, timeout=6)
        if 'Readonly: 0' in settings.stdout:
            result['sizes'] = re.findall(r'^Choice: [0-9]+ (.+)$', settings.stdout, re.M)
        helper = Path(__file__).with_name('canon-webcam-capture.py')
        command = ([sys.executable, str(helper), '--seconds', '1'] if helper.exists() else
                   ['gphoto2', '--stdout', '--capture-movie=1s'])
        camera = subprocess.Popen(command,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            measured = subprocess.run(['ffprobe', '-v', 'error', '-f', 'mjpeg',
                                       '-show_entries', 'stream=width,height', '-of', 'json', '-i', 'pipe:0'],
                                      stdin=camera.stdout, capture_output=True, timeout=30)
            streams = json.loads(measured.stdout).get('streams', [])
            if streams:
                width, height = streams[0]['width'], streams[0]['height']
                if 16 <= width <= 16384 and 16 <= height <= 16384 and not (width % 2 or height % 2):
                    result.update(width=width, height=height)
        finally:
            camera.stdout.close()
            if camera.poll() is None:
                camera.terminate()
            try:
                camera.wait(timeout=5)
            except subprocess.TimeoutExpired:
                camera.kill()
                camera.wait()
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        pass
    print(json.dumps(result))
    return 0


def watch(seconds, command):
    demand_mode = os.environ.get('CANON_WEBCAM_ON_DEMAND', '1') == '1'
    with tempfile.TemporaryDirectory(prefix='canon-frame-activity-') as directory:
        root = Path(directory)
        path = root / 'last-frame'
        ready = root / 'ready'
        wanted_file = root / 'wanted'
        stats_file = root / 'frames.json'
        sdk_active = root / 'sdk-active'
        sdk_idle = root / 'sdk-idle'
        reset_at = time.monotonic()
        paused_at = reset_at
        last_activity = reset_at
        no_clients_since = reset_at
        idle = demand_mode
        live_view_stopped = False
        wanted = None
        forced = False
        forced_off = False
        pause_file = root / "manual-pause"
        stopping = False
        child = None
        usage_path = None
        if os.environ.get('CANON_WEBCAM_IN_SERVICE') == '1' and os.environ.get('XDG_RUNTIME_DIR'):
            usage_path = Path(os.environ['XDG_RUNTIME_DIR']) / 'canon-webcam/usage.json'
            usage_path.parent.mkdir(parents=True, exist_ok=True)
        previous_usage = None
        control_pid = usage_path.parent / "control-pid" if usage_path else None

        def forward(signum, _frame):
            nonlocal reset_at, idle, stopping, forced, forced_off, wanted, demand_mode
            if signum == signal.SIGHUP:
                reset_at = time.monotonic()
                idle = False
                if child is not None and ready.exists() and not forced_off and (wanted or not demand_mode):
                    child.send_signal(signal.SIGUSR2)
                return
            if signum == signal.SIGCONT:
                demand_mode = True
                forced_off = False
                pause_file.unlink(missing_ok=True)
                forced = False
                wanted = None
                return
            if signum == signal.SIGUSR1:
                forced_off = True
                forced = False
                wanted = None
                pause_file.touch()
                return
            if signum == signal.SIGUSR2:
                forced = True
                forced_off = False
                pause_file.unlink(missing_ok=True)
                wanted = None
                reset_at = time.monotonic()
                return
            else:
                stopping = True
            if child is not None and child.poll() is None:
                if signum in (signal.SIGTERM, signal.SIGINT):
                    child.send_signal(signum)
                elif ready.exists():
                    child.send_signal(signum)

        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1, signal.SIGUSR2, signal.SIGCONT, signal.SIGHUP):
            signal.signal(signum, forward)
        if control_pid:
            control_pid.write_text(str(os.getpid()))
        environment = dict(os.environ, CANON_WEBCAM_ACTIVITY_FILE=str(path),
                           CANON_WEBCAM_CONTROL_READY=str(ready), CANON_WEBCAM_DEMAND_FILE=str(wanted_file),
                           CANON_WEBCAM_START_IDLE=str(int(demand_mode)), CANON_WEBCAM_WATCHED='1',
                           CANON_WEBCAM_FRAME_STATS=str(stats_file), CANON_WEBCAM_PAUSE_FILE=str(pause_file),
                           CANON_WEBCAM_SDK_ACTIVE_FILE=str(sdk_active), CANON_WEBCAM_SDK_IDLE_FILE=str(sdk_idle))
        child = subprocess.Popen(command, env=environment, start_new_session=True)
        try:
            while child.poll() is None:
                if stopping:
                    try:
                        return child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        return child.wait()
                now = time.monotonic()
                if ready.exists():
                    clients, uncertain = consumers(os.environ.get('CANON_WEBCAM_DEVICE', '/dev/video42'), child.pid) if demand_mode else ([], False)
                    now = time.monotonic()
                    desired = not forced_off and (bool(clients) or forced or not demand_mode)
                    usage = {'clients': clients, 'uncertain': uncertain, 'automatic': demand_mode,
                             'manual_override': forced, 'manual_off': forced_off}
                    if sdk_active.exists():
                        usage['sdk_live_view'] = True
                    try:
                        stats = json.loads(stats_file.read_text())
                        if desired and now - stats['last_frame'] < 2:
                            usage['camera_fps'] = round(stats['fps'], 1)
                            usage['camera_frames'] = stats['frames']
                    except (OSError, ValueError, KeyError):
                        pass
                    if usage_path and usage != previous_usage:
                        write_json(usage_path, usage)
                        previous_usage = usage
                    if desired != wanted:
                        if desired or forced_off:
                            sdk_idle.unlink(missing_ok=True)
                        else:
                            sdk_idle.touch()
                        write_json(wanted_file, int(desired))
                        wanted = desired
                        live_view_stopped = False
                        if desired:
                            reset_at = now
                            idle = False
                            child.send_signal(signal.SIGUSR2)
                        else:
                            no_clients_since = now
                            if not idle or forced_off:
                                idle = True
                                paused_at = now
                                child.send_signal(signal.SIGUSR1)
                    # Let Bash handle the pause before sending ALRM.
                    # Back-to-back standard signals can otherwise leave its
                    # interrupted wait handling only the live-view request.
                    rest_delay = 0.25 if forced_off else seconds
                    if not desired and not live_view_stopped and rest_delay > 0 and now - no_clients_since >= rest_delay:
                        live_view_stopped = True
                        child.send_signal(signal.SIGALRM)
                last_frame = max(reset_at, last_activity)
                try:
                    last_frame = max(last_frame, float(path.read_text()))
                except (OSError, ValueError):
                    pass
                last_activity = last_frame
                if idle and wanted and last_frame > paused_at:
                    idle = False
                if seconds > 0 and not idle and now - last_frame >= seconds:
                    idle = True
                    paused_at = now
                    print('[canon-webcam] No camera images; pausing encoding, keeping virtual webcam open', flush=True)
                    if ready.exists() or not demand_mode:
                        child.send_signal(signal.SIGUSR1)
                        if demand_mode:
                            child.send_signal(signal.SIGALRM)
                time.sleep(0.25 if demand_mode else 0.1)
            return child.returncode
        finally:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            if child.poll() is None:
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
            if usage_path:
                usage_path.unlink(missing_ok=True)
            if control_pid:
                try:
                    if control_pid.read_text() == str(os.getpid()):
                        control_pid.unlink(missing_ok=True)
                except FileNotFoundError:
                    pass


def main():
    if sys.argv[1:] == ['probe']:
        return probe()
    mode, value, *command = sys.argv[1:]
    if mode == 'watch':
        return watch(float(value), command)
    if mode == 'relay':
        return relay(value, command)
    raise SystemExit('Expected watch or relay')


if __name__ == '__main__':
    sys.exit(main())
