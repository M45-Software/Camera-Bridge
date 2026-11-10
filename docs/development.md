# Building and testing

[Back to README](../README.md)

## Build a Debian package

Run from the checkout on Ubuntu/Kubuntu 24.04:

```bash
./packaging/build-deb.sh 0.3.1
sudo apt install ./dist/camera-bridge_0.3.1_amd64.deb
```

Packaging needs Bash, Python 3, and `dpkg-deb`; it does not require root.
APT installs runtime dependencies when the package is installed.
The `amd64` package contains the Canon SDK runtime plus scripts, Qt controls, icons, status-card assets, user
service, virtual-device defaults, and documentation.

The GitHub Actions release workflow tests pushes to `main` and version tags.
Pushing a tag such as `v0.3.1` builds a tested package, creates its GitHub release,
and calls the APT publisher directly. Manually published GitHub releases also
trigger the publisher. Draft releases are rejected. Package versions are immutable:
change the version whenever package bytes change.

See [APT release automation](apt-repository.md) for deployment setup and retries.

## Test without replacing the installed app

Stop the installed webcam first, then run:

```bash
./tools/preview.sh
```

The preview uses isolated settings and a runtime-only user service. Startup
preferences are disabled. Quit the preview tray to stop its webcam and remove the
temporary service/settings. The installed files and saved preferences are retained.

Open **Camera-Bridge** in a video app. Check that capture starts, processing pauses
when the last preview closes, and live view stops after the rest delay. Try multiple
apps, USB disconnect/reconnect, camera mode changes, error guidance, and quitting
while video is active. Apply auto-saved picture changes when ready, then reopen video apps.
An existing loopback device may retain its earlier label until reloaded.

## Automated checks

These use fake camera/service operations and do not start the real webcam:

```bash
bash -n canon-webcam.sh packaging/build-deb.sh tools/preview.sh
shellcheck canon-webcam.sh packaging/build-deb.sh tools/preview.sh
python3 -m unittest discover -s tests -v
QT_QPA_PLATFORM=offscreen /usr/bin/python3 tests/gui_smoke.py
```

The Qt checks and render tools need `python3-pyqt5`. Status-card rendering also
needs FFmpeg and `fonts-dejavu-core`.

The connected EOS 7D Mark II has passed native-size, sustained raw/MJPEG capture,
reader-open/close, and idle live-view shutdown checks. Driver installation and
common-app compatibility require desktop testing separately.

## Refresh screenshots

```bash
/usr/bin/python3 tools/render_ui.py
/usr/bin/python3 tools/render_status_cards.py
```

The tools use a fake service and synthetic frames; they do not open a camera or
video device. Outputs go to `assets/screenshots/`. The status renderer also checks
that idle/error cards are static and starting animation has bounded output.

## Legacy source installation

The `.deb` is the recommended install method. For development of the original
installer, run it as the normal desktop user:

```bash
./canon-webcam.sh install --start
```

This installs dependencies through APT, copies `camera-bridge` to `/usr/local/bin`,
writes loopback configuration, installs/enables a per-user service, adds desktop
launchers, and adds video-group membership if needed. It also tries to enable user
lingering so the service can start before login. This is separate from Debian
package installation, which uses graphical-login entries and does not enable lingering.

Use `camera-bridge uninstall` for a source installation and APT removal for a
packaged installation. Remove a source installation before switching to the `.deb`;
see [migration instructions](installation.md#upgrading-from-the-script-installer).

Compatibility service/config names retain `canon-webcam`; source filenames and
`CANON_WEBCAM_*` environment overrides retain their existing spelling.
