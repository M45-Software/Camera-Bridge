#!/usr/bin/python3
"""Canon SDK capture with process isolation and automatic gphoto2 fallback."""
import argparse
import ctypes as c
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from canon_webcam_controls import CameraControls, ControlServer, remote_request, safe_handle


class PropertyDesc(c.Structure):
    _fields_ = [('form', c.c_int32), ('access', c.c_int32),
                ('count', c.c_int32), ('values', c.c_int32 * 128)]


def library_path():
    override = os.environ.get('CANON_WEBCAM_EDSDK_LIBRARY')
    if override:
        return Path(override)
    source = ROOT / 'vendor/canon/linux-x86_64/libEDSDK.so'
    return source if source.exists() else Path('/usr/lib/camera-bridge/libEDSDK.so')


def backend():
    value = os.environ.get('CANON_WEBCAM_CAPTURE_BACKEND', '0')
    return {'0': 'auto', '1': 'edsdk', '2': 'gphoto2'}.get(value, value)


def log(message):
    print(f'Camera Bridge: {message}', file=sys.stderr, flush=True)


def jpeg_end(data):
    """Respect JPEG segment lengths, embedded thumbnails and entropy stuffing."""
    pos = 2
    while pos < len(data):
        if data[pos] != 255:
            raise ValueError('Invalid camera JPEG marker')
        while pos < len(data) and data[pos] == 255:
            pos += 1
        if pos >= len(data):
            return None
        marker = data[pos]
        pos += 1
        if marker == 0xd9:
            return pos
        if marker == 1 or 0xd0 <= marker <= 0xd8:
            continue
        if pos + 2 > len(data):
            return None
        length = int.from_bytes(data[pos:pos+2], 'big')
        if length < 2:
            raise ValueError('Invalid camera JPEG segment')
        pos += length
        if pos > len(data):
            return None
        if marker == 0xda:
            while True:
                found = data.find(b'\xff', pos)
                if found < 0 or found + 1 >= len(data):
                    return None
                following = data[found+1]
                if following == 0 or 0xd0 <= following <= 0xd7:
                    pos = found + 2
                elif following == 255:
                    pos = found + 1
                else:
                    pos = found
                    break
    return None


def write_all(data):
    remaining = memoryview(data)
    while remaining:
        remaining = remaining[os.write(sys.stdout.fileno(), remaining):]


class SDK:
    def __init__(self, path):
        self.api = c.CDLL(str(path))
        self.camera, self.cameras = c.c_void_p(), c.c_void_p()
        self.initialized = self.opened = False
        self.restore = {}
        u, p = c.c_uint32, c.c_void_p
        signatures = {
            'EdsInitializeSDK': [], 'EdsTerminateSDK': [], 'EdsGetEvent': [],
            'EdsGetCameraList': [c.POINTER(p)], 'EdsGetChildCount': [p, c.POINTER(u)],
            'EdsGetChildAtIndex': [p, c.c_int32, c.POINTER(p)],
            'EdsOpenSession': [p], 'EdsCloseSession': [p], 'EdsRelease': [p],
            'EdsGetPropertyData': [p, u, c.c_int32, u, p],
            'EdsSetPropertyData': [p, u, c.c_int32, u, p],
            'EdsGetPropertyDesc': [p, u, c.POINTER(PropertyDesc)],
            'EdsSendCommand': [p, u, c.c_int32],
            'EdsCreateMemoryStream': [c.c_uint64, c.POINTER(p)],
            'EdsCreateEvfImageRef': [p, c.POINTER(p)], 'EdsDownloadEvfImage': [p, p],
            'EdsGetLength': [p, c.POINTER(c.c_uint64)], 'EdsGetPointer': [p, c.POINTER(p)],
        }
        for name, arguments in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, u

    @staticmethod
    def check(code, action):
        if code:
            raise RuntimeError(f'{action}: Canon SDK error 0x{code:08x}')

    def get(self, prop):
        value = c.c_uint32()
        self.check(self.api.EdsGetPropertyData(self.camera, prop, 0, 4, c.byref(value)), 'Read camera setting')
        return value.value

    def set(self, prop, value):
        value = c.c_uint32(value)
        self.check(self.api.EdsSetPropertyData(self.camera, prop, 0, 4, c.byref(value)), 'Change camera setting')

    def describe(self, prop):
        description = PropertyDesc()
        self.check(self.api.EdsGetPropertyDesc(self.camera, prop, c.byref(description)), 'Available camera settings')
        if not 0 <= description.count <= 128:
            raise RuntimeError('Invalid camera setting descriptor')
        return [value & 0xffffffff for value in description.values[:description.count]]

    def command(self, command, parameter):
        self.check(self.api.EdsSendCommand(self.camera, command, parameter), 'Focus command')

    def open(self, live_view=True):
        self.check(self.api.EdsInitializeSDK(), 'Initialize')
        self.initialized = True
        self.check(self.api.EdsGetCameraList(c.byref(self.cameras)), 'Find cameras')
        count = c.c_uint32()
        self.check(self.api.EdsGetChildCount(self.cameras, c.byref(count)), 'Count cameras')
        if count.value != 1:
            raise RuntimeError(f'Canon SDK requires one connected camera; found {count.value}')
        self.check(self.api.EdsGetChildAtIndex(self.cameras, 0, c.byref(self.camera)), 'Select camera')
        self.check(self.api.EdsOpenSession(self.camera), 'Open session')
        self.opened = True
        if not live_view:
            return
        self.restore[0x500] = self.get(0x500)
        mode = self.get(0x501)
        # Movie mode reports 2 on the 7D II and rejects writes to this property.
        if mode == 0 and os.environ.get('CANON_WEBCAM_SET_VIEWFINDER', '1') == '1':
            self.set(0x501, 1)
            self.restore[0x501] = mode
        size = os.environ.get('CANON_WEBCAM_LIVE_VIEW_SIZE', '1')
        output = {'1': 2, '2': 4, '3': 8}.get(size, self.restore[0x500] or 2)
        try:
            self.set(0x500, output)
        except RuntimeError:
            if output == 2:
                raise
            log('Requested live-view size unavailable; using standard Canon SDK output')
            self.set(0x500, 2)

    def frame(self):
        stream, evf = c.c_void_p(), c.c_void_p()
        try:
            self.check(self.api.EdsGetEvent(), 'Camera events')
            self.check(self.api.EdsCreateMemoryStream(0, c.byref(stream)), 'Create frame stream')
            self.check(self.api.EdsCreateEvfImageRef(stream, c.byref(evf)), 'Create frame')
            code = self.api.EdsDownloadEvfImage(self.camera, evf)
            if code in (0xa102, 0x81, 0x8d44, 0x2019):
                return None
            self.check(code, 'Capture live view')
            length, pointer = c.c_uint64(), c.c_void_p()
            self.check(self.api.EdsGetLength(stream, c.byref(length)), 'Read frame length')
            self.check(self.api.EdsGetPointer(stream, c.byref(pointer)), 'Read frame data')
            if not pointer or not 0 < length.value <= 32 * 1024 * 1024:
                raise RuntimeError('Invalid Canon SDK frame length')
            return c.string_at(pointer, length.value)
        finally:
            if evf:
                self.api.EdsRelease(evf)
            if stream:
                self.api.EdsRelease(stream)

    def close(self):
        if self.opened:
            for prop, value in self.restore.items():
                try:
                    self.set(prop, value)
                except RuntimeError as error:
                    log(str(error))
            self.api.EdsCloseSession(self.camera)
        if self.camera:
            self.api.EdsRelease(self.camera)
        if self.cameras:
            self.api.EdsRelease(self.cameras)
        if self.initialized:
            self.api.EdsTerminateSDK()


def sdk_worker():
    sdk = SDK(library_path())
    controls = CameraControls(sdk)
    server = None
    try:
        sdk.open()
        if os.environ.get('XDG_RUNTIME_DIR'):
            try:
                server = ControlServer(controls)
            except (OSError, RuntimeError) as error:
                log(f'Camera controls unavailable: {error}')
        while True:
            if server:
                server.poll()
            frame = sdk.frame()
            if frame:
                write_all(frame)
            else:
                time.sleep(0.001)
    finally:
        if server:
            server.close()
        try:
            controls.stop_focus()
        except RuntimeError:
            pass
        sdk.close()


def control_request(request):
    result = remote_request(request)
    if result is not None:
        return result
    # An idle camera can be queried without enabling live view or changing it.
    sdk = SDK(library_path())
    try:
        sdk.open(live_view=False)
        if request.get('operation') == 'focus':
            return dict(ok=False, error='Start camera video with the Canon SDK backend to use focus buttons.')
        return safe_handle(CameraControls(sdk), request)
    except RuntimeError as error:
        return dict(ok=False, error=f'{error}. Check camera power and USB. If video is running, use the updated Canon SDK capture backend; gphoto2 cannot share its USB session.')
    finally:
        sdk.close()


def gphoto_command():
    configs = ['--set-config', 'capturetarget=0']
    size = {'1': 'Large', '2': 'Medium', '3': 'Small'}.get(os.environ.get('CANON_WEBCAM_LIVE_VIEW_SIZE', '1'))
    if size:
        configs += ['--set-config', f'liveviewsize={size}']
    if os.environ.get('CANON_WEBCAM_SET_VIEWFINDER', '1') == '1':
        configs += ['--set-config', 'viewfinder=1']
    try:
        result = subprocess.run(['gphoto2', *configs], stdout=subprocess.DEVNULL, timeout=8)
        if result.returncode:
            basic = ['gphoto2', '--set-config', 'capturetarget=0']
            if os.environ.get('CANON_WEBCAM_SET_VIEWFINDER', '1') == '1':
                basic += ['--set-config', 'viewfinder=1']
            subprocess.run(basic,
                           stdout=subprocess.DEVNULL, timeout=5)
    except subprocess.TimeoutExpired:
        log('gphoto2 preparation timed out; attempting capture')
    # The supervisor times segments from the first JPEG. gphoto2's own timer
    # includes camera startup and can finish well before that interval elapses.
    return ['gphoto2', '--stdout', '--capture-movie']


def forward(command, label, seconds, timeout=12):
    """Forward complete JPEGs only, bounding startup and stalled-camera waits."""
    child = subprocess.Popen(command, stdout=subprocess.PIPE, bufsize=0)
    buffer = bytearray()
    first = None
    last = time.monotonic()
    selector = selectors.DefaultSelector()
    selector.register(child.stdout, selectors.EVENT_READ)
    try:
        while True:
            now = time.monotonic()
            if first is not None and seconds and now - first >= seconds:
                return True
            if now - last > timeout:
                log(f'{label} produced no frames for {timeout}s')
                return False
            if not selector.select(0.2):
                if child.poll() is not None:
                    return False
                continue
            chunk = os.read(child.stdout.fileno(), 262144)
            if not chunk:
                return first is not None and seconds > 0 and time.monotonic() - first >= max(0, seconds - 0.5)
            buffer.extend(chunk)
            if len(buffer) > 32 * 1024 * 1024:
                raise ValueError('Camera frame buffer exceeded 32 MiB')
            while buffer:
                start = buffer.find(b'\xff\xd8')
                if start < 0:
                    buffer[:] = buffer[-1:]
                    break
                del buffer[:start]
                end = jpeg_end(buffer)
                if end is None:
                    break
                if first is None:
                    first = time.monotonic()
                    log(f'Capturing with {label}')
                write_all(buffer[:end])
                del buffer[:end]
                last = time.monotonic()
    finally:
        selector.close()
        if child.poll() is None:
            child.send_signal(signal.SIGINT)
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        child.stdout.close()


def capture(seconds=0, selected=None):
    selected = selected or backend()
    if selected not in ('auto', 'edsdk', 'gphoto2'):
        raise ValueError('Capture backend must be auto, edsdk, or gphoto2')
    if selected != 'gphoto2':
        try:
            if forward([sys.executable, str(Path(__file__).resolve()), '--sdk-worker'], 'Canon SDK', seconds):
                return 0
        except (OSError, ValueError) as error:
            log(str(error))
        if selected == 'edsdk':
            log('Canon SDK capture failed; explicit backend selection disables fallback')
            return 1
        log('Canon SDK unavailable or stopped; falling back to gphoto2')
    return 0 if forward(gphoto_command(), 'gphoto2', seconds) else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=float, default=0)
    parser.add_argument('--backend', choices=('auto', 'edsdk', 'gphoto2'))
    parser.add_argument('--sdk-worker', action='store_true')
    parser.add_argument('--prefers-sdk', action='store_true')
    parser.add_argument('--control-request', help='JSON camera control request')
    args = parser.parse_args()
    if args.control_request is not None:
        try:
            request = json.loads(args.control_request)
            if not isinstance(request, dict):
                raise ValueError('Invalid camera control request')
            result = control_request(request)
        except (OSError, RuntimeError, ValueError) as error:
            result = dict(ok=False, error=str(error))
        print(json.dumps(result))
        return 0
    if args.prefers_sdk:
        return 0 if backend() == 'edsdk' or (backend() == 'auto' and library_path().is_file()) else 1
    if args.seconds < 0:
        parser.error('--seconds cannot be negative')
    def stop(_signal, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        if args.sdk_worker:
            sdk_worker()
            return 0
        return capture(args.seconds, args.backend)
    except (KeyboardInterrupt, BrokenPipeError):
        return 0
    except (OSError, RuntimeError, ValueError, AttributeError) as error:
        log(str(error))
        return 1


if __name__ == '__main__':
    sys.exit(main())
