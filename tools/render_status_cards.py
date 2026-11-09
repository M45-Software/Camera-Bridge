#!/usr/bin/python3
"""Render real offline status frames, without opening any camera or video device."""
from pathlib import Path
import subprocess
import os
import tempfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'assets/screenshots'


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    source = (ROOT / 'canon-webcam.sh').read_text().rsplit('\nmain "$@"', 1)[0]
    with tempfile.TemporaryDirectory(prefix='canon-card-render-') as directory:
        root = Path(directory)
        script = root/'render.sh'
        script.write_text(source + '\nWIDTH=1024\nHEIGHT=576\nFPS=30\nCAMERA_FIFO="$1"\nrun_test_source_feeder_ffmpeg "$2"\n')
        for name, message in [('offline-idle.png', 'Waiting for a video app'),
                              ('offline-starting.png', 'Starting camera...'),
                              ('offline-preparing.png', 'Getting video ready...'),
                              ('offline-disconnected.png', 'Camera disconnected'),
                              ('offline-lost-connection.png', 'Lost connection'),
                              ('offline-not-responding.png', 'Camera not responding')]:
            jpeg = root/'card.mjpeg'
            subprocess.run(['bash', str(script), str(jpeg), message], check=True,
                           env={**os.environ, 'CANON_WEBCAM_ASSET_DIR': str(ROOT/'assets')})
            # Verify bounded output and actual motion using decoded-frame hashes.
            decoded = subprocess.check_output([
                'ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 'mjpeg',
                '-i', str(jpeg), '-f', 'framemd5', '-'])
            hashes = [line.split(b',')[-1].strip() for line in decoded.splitlines()
                      if line and not line.startswith(b'#')]
            animated = message == 'Starting camera...'
            assert len(hashes) == (12 if animated else 3), (message, len(hashes))
            assert len(set(hashes)) == (2 if animated else 1), (message, hashes)
            if animated:
                subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-framerate','6',
                                '-f','mjpeg','-i',str(jpeg),'-vf','scale=512:-1',
                                str(OUTPUT/'offline-starting.gif')],check=True)
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','mjpeg','-i',str(jpeg),
                            '-frames:v','1','-update','1',str(OUTPUT/name)],check=True)
    print(f'Rendered status cards to {OUTPUT}')


if __name__ == '__main__':
    main()
