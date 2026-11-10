"""Camera properties and same-user IPC, serviced on the SDK capture thread.

Property IDs, encodings and commands follow Canon's EDSDK API reference.
Only current camera descriptors authorize enumerated property writes.
"""
import errno
import json
import os
from pathlib import Path
import socket
import time


PROPERTIES = {
    'exposure_mode': (0x400, 'Exposure mode', 'Exposure'),
    'iso': (0x402, 'ISO', 'Exposure'),
    'shutter': (0x406, 'Shutter speed', 'Exposure'),
    'aperture': (0x405, 'Aperture', 'Exposure'),
    'exposure_compensation': (0x407, 'Exposure compensation', 'Exposure'),
    'white_balance': (0x106, 'White balance', 'Color'),
    'color_temperature': (0x107, 'Color temperature', 'Color'),
    'picture_style': (0x114, 'Picture Style', 'Color'),
    'af_mode': (0x404, 'Lens / autofocus mode', 'Focus'),
    'live_af_mode': (0x50e, 'Live-view AF method', 'Focus'),
    'movie_servo_af': (0x0100043e, 'Movie Servo AF', 'Focus'),
    'continuous_af': (0x01000433, 'Continuous AF', 'Focus'),
}
READ_ONLY = {'exposure_mode', 'af_mode'}
LABELS = {
    'iso': {0: 'Auto', **dict(zip(
        [0x40,0x48,0x4b,0x4d,0x50,0x53,0x55,0x58,0x5b,0x5d,0x60,0x63,0x65,0x68,0x6b,0x6d,0x70,0x73,0x75,0x78,0x7b,0x7d,0x80,0x83,0x85,0x88,0x90,0x98,0xa0,0xa8],
        '50 100 125 160 200 250 320 400 500 640 800 1000 1250 1600 2000 2500 3200 4000 5000 6400 8000 10000 12800 16000 20000 25600 51200 102400 204800 409600'.split()))},
    'exposure_mode': {0: 'Program', 1: 'Shutter priority (Tv)', 2: 'Aperture priority (Av)', 3: 'Manual', 4: 'Bulb', 9: 'Auto'},
    'white_balance': {0: 'Auto', 1: 'Daylight', 2: 'Cloudy', 3: 'Tungsten', 4: 'Fluorescent', 5: 'Flash', 6: 'Custom', 8: 'Shade', 9: 'Color temperature', 23: 'Auto (white priority)'},
    'picture_style': {0x81: 'Standard', 0x82: 'Portrait', 0x83: 'Landscape', 0x84: 'Neutral', 0x85: 'Faithful', 0x86: 'Monochrome', 0x87: 'Auto', 0x88: 'Fine Detail', 0x21: 'User 1', 0x22: 'User 2', 0x23: 'User 3'},
    'af_mode': {0: 'One-shot AF', 1: 'AI Servo AF', 2: 'AI Focus AF', 3: 'Manual focus (lens switch)'},
    'live_af_mode': {0: 'Quick AF', 1: 'Live / single-point AF', 2: 'Face detection / tracking', 3: 'Multi-point AF', 4: 'Zone AF', 5: 'Expanded point (cross)', 6: 'Expanded point (surround)', 7: 'Large horizontal zone', 8: 'Large vertical zone', 10: 'Spot AF'},
    'movie_servo_af': {0: 'Disabled', 1: 'Enabled'},
    'continuous_af': {0: 'Disabled', 1: 'Enabled'},
}
APERTURES = dict(zip(
    [0x08,0x0b,0x0c,0x0d,0x10,0x13,0x14,0x15,0x18,0x1b,0x1c,0x1d,0x20,0x23,0x24,0x25,0x28,0x2b,0x2c,0x2d,0x30,0x33,0x34,0x35,0x38,0x3b,0x3c,0x3d,0x40,0x43,0x44,0x45,0x48,0x4b,0x4c,0x4d,0x50,0x53,0x54,0x55,0x58,0x5b,0x5c,0x5d,0x60,0x63,0x64,0x65,0x68],
    '1 1.1 1.2 1.2 1.4 1.6 1.8 1.8 2 2.2 2.5 2.5 2.8 3.2 3.5 3.5 4 4.5 4.5 5 5.6 6.3 6.7 7.1 8 9 9.5 10 11 13 13 14 16 18 19 20 22 25 27 29 32 36 38 40 45 51 54 57 64'.split()))
SHUTTERS = dict(zip(
    [0x0c,0x10,0x13,0x14,0x15,0x18,0x1b,0x1c,0x1d,0x20,0x23,0x24,0x25,0x28,0x2b,0x2c,0x2d,0x30,0x33,0x34,0x35,0x38,0x3b,0x3c,0x3d,0x40,0x43,0x44,0x45,0x48,0x4b,0x4c,0x4d,0x50,0x53,0x54,0x55,0x58,0x5b,0x5c,0x5d,0x60,0x63,0x64,0x65,0x68,0x6b,0x6c,0x6d,0x70,0x73,0x74,0x75,0x78,0x7b,0x7c,0x7d,0x80,0x83,0x84,0x85,0x88,0x8b,0x8c,0x8d,0x90,0x93,0x94,0x95,0x98,0x9b,0x9c,0x9d,0xa0],
    'Bulb 30s 25s 20s 20s 15s 13s 10s 10s 8s 6s 6s 5s 4s 3.2s 3s 2.5s 2s 1.6s 1.5s 1.3s 1s 0.8s 0.7s 0.6s 0.5s 0.4s 0.3s 0.3s 1/4 1/5 1/6 1/6 1/8 1/10 1/10 1/13 1/15 1/20 1/20 1/25 1/30 1/40 1/45 1/50 1/60 1/80 1/90 1/100 1/125 1/160 1/180 1/200 1/250 1/320 1/350 1/400 1/500 1/640 1/750 1/800 1/1000 1/1250 1/1500 1/1600 1/2000 1/2500 1/3000 1/3200 1/4000 1/5000 1/6000 1/6400 1/8000'.split()))


def value_label(name, value):
    if value == 0xffffffff:
        return 'Unavailable'
    if name == 'aperture' and value in APERTURES:
        return 'f/' + APERTURES[value]
    if name == 'shutter' and value in SHUTTERS:
        return SHUTTERS[value]
    if name == 'exposure_compensation':
        signed = (value & 255) - (256 if value & 128 else 0)
        whole, fraction = divmod(abs(signed), 8)
        part = {0: '', 3: '⅓', 4: '½', 5: '⅔'}.get(fraction)
        if part is not None:
            return ('−' if signed < 0 else '+' if signed else '') + (str(whole) if whole or not part else '') + part + ' EV'
    if name == 'color_temperature':
        return f'{value} K'
    return LABELS.get(name, {}).get(value, f'Camera value 0x{value:x}')


class CameraControls:
    def __init__(self, sdk):
        self.sdk = sdk
        self.focus_until = None

    def choices(self, name):
        if name in READ_ONLY:
            return []
        prop = PROPERTIES[name][0]
        try:
            return [value for value in self.sdk.describe(prop) if value != 0xffffffff]
        except RuntimeError:
            # Kelvin is a numeric property, not an enumerated descriptor.
            if name == 'color_temperature' and self.sdk.get(0x106) == 9:
                return list(range(2800, 10001, 100))
            return []

    def snapshot(self):
        fields = []
        self.sdk.check(self.sdk.api.EdsGetEvent(), 'Camera events')
        for name, (prop, label, group) in PROPERTIES.items():
            field = dict(name=name, label=label, group=group, choices=[])
            try:
                value = self.sdk.get(prop)
                choices = self.choices(name) if value != 0xffffffff else []
                field.update(value=value, display=value_label(name, value),
                             choices=[dict(value=v, label=value_label(name, v)) for v in choices],
                             available=value != 0xffffffff)
                if not choices:
                    field['reason'] = 'Set on the camera or lens; unavailable for remote changes in this mode.'
            except RuntimeError:
                field.update(available=False, display='Not available in this camera mode',
                             reason='This camera does not expose this setting in its current mode.')
            fields.append(field)
        try:
            output, af_mode = self.sdk.get(0x500), self.sdk.get(0x404)
            focusing = output != 0xffffffff and output & 0xe != 0 and af_mode not in (3, 0xffffffff)
        except RuntimeError:
            focusing = False
        return dict(ok=True, backend='Canon SDK', fields=fields, focus_available=focusing)

    def handle(self, request):
        if not isinstance(request, dict):
            raise ValueError('Invalid control request')
        operation = request.get('operation', 'read')
        if operation == 'set':
            name, value = request.get('name'), request.get('value')
            if name not in PROPERTIES or type(value) is not int or value not in self.choices(name):
                raise ValueError('This value is not currently available. Refresh or change the camera shooting mode.')
            self.sdk.set(PROPERTIES[name][0], value)
        elif operation == 'focus':
            action = request.get('action')
            if action == 'stop':
                self.stop_focus()
            elif action == 'auto':
                self.sdk.command(0x102, 1)
                self.focus_until = time.monotonic() + 1.5
            elif action in ('near', 'far'):
                step = request.get('step', 1)
                if type(step) is not int or step not in (1, 2, 3):
                    raise ValueError('Invalid focus step')
                self.sdk.command(0x103, step | (0x8000 if action == 'far' else 0))
            else:
                raise ValueError('Unknown focus action')
        elif operation != 'read':
            raise ValueError('Unknown camera control operation')
        return self.snapshot()

    def tick(self):
        if self.focus_until is not None and time.monotonic() >= self.focus_until:
            self.stop_focus()

    def stop_focus(self):
        if self.focus_until is not None:
            self.sdk.command(0x102, 0)
            self.focus_until = None


def control_path():
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    if not runtime:
        raise RuntimeError('Camera controls need a desktop user session (XDG_RUNTIME_DIR).')
    return Path(runtime) / 'canon-webcam/controls.sock'


def safe_handle(controller, request):
    try:
        return controller.handle(request)
    except (OSError, RuntimeError, ValueError, TypeError) as error:
        return dict(ok=False, error=str(error))


class ControlServer:
    """Nonblocking requests; all camera calls stay on the capture thread."""
    def __init__(self, controller):
        self.controller = controller
        self.path = control_path()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.clients = {}
        self.socket = socket.socket(socket.AF_UNIX)
        if self.path.exists():
            with socket.socket(socket.AF_UNIX) as probe:
                probe.settimeout(0.1)
                try:
                    probe.connect(str(self.path))
                except OSError as error:
                    if error.errno not in (errno.ECONNREFUSED, errno.ENOENT):
                        raise
                    self.path.unlink(missing_ok=True)
                else:
                    raise RuntimeError('Another camera capture session owns the controls.')
        self.socket.bind(str(self.path))
        self.inode = self.path.stat().st_ino
        self.path.chmod(0o600)
        self.socket.listen(4)
        self.socket.setblocking(False)

    def poll(self):
        self.controller.tick()
        try:
            client, _ = self.socket.accept()
            client.setblocking(False)
            if len(self.clients) < 4:
                self.clients[client] = (bytearray(), time.monotonic())
            else:
                client.close()
        except BlockingIOError:
            pass
        for client, (buffer, started) in list(self.clients.items()):
            done = time.monotonic() - started > 2
            try:
                data = client.recv(4096)
                buffer.extend(data)
                done |= not data or len(buffer) > 4096
                if b'\n' in buffer and len(buffer) <= 4096:
                    try:
                        result = safe_handle(self.controller, json.loads(buffer.split(b'\n', 1)[0]))
                    except (ValueError, UnicodeError):
                        result = dict(ok=False, error='Invalid control request')
                    client.sendall(json.dumps(result).encode() + b'\n')
                    done = True
            except BlockingIOError:
                pass
            except OSError:
                done = True
            if done:
                client.close()
                del self.clients[client]

    def close(self):
        for client in self.clients:
            client.close()
        self.socket.close()
        try:
            if self.path.stat().st_ino == self.inode:
                self.path.unlink()
        except FileNotFoundError:
            pass


def remote_request(request):
    """Return None only when no capture worker is serving controls."""
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(5)
        try:
            connection.connect(str(control_path()))
        except OSError as error:
            if error.errno in (errno.ENOENT, errno.ECONNREFUSED):
                return None
            raise
        connection.sendall(json.dumps(request).encode() + b'\n')
        data = bytearray()
        while b'\n' not in data:
            chunk = connection.recv(65536)
            if not chunk or len(data) > 262144:
                raise RuntimeError('Camera controls disconnected. Refresh to reconnect.')
            data.extend(chunk)
        return json.loads(data.split(b'\n', 1)[0])
