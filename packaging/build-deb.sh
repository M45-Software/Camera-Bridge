#!/usr/bin/env bash
set -Eeuo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version="${1:-0.2.0}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Use a version such as 0.2.0' >&2; exit 1; }
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/DEBIAN" "$stage/usr/bin" "$stage/usr/share/canon-webcam" \
  "$stage/usr/share/applications" "$stage/usr/share/icons/hicolor/scalable/apps" \
  "$stage/usr/lib/systemd/user" "$stage/etc/modules-load.d" "$stage/etc/modprobe.d" \
  "$stage/usr/share/doc/canon-webcam" "$root/dist"
install -m 0755 "$root/canon-webcam.sh" "$stage/usr/bin/camera-bridge"
ln -s camera-bridge "$stage/usr/bin/canon-webcam"
install -m 0644 "$root"/assets/tray-*.svg "$stage/usr/share/canon-webcam/"
install -m 0644 "$root"/assets/status-*.png "$stage/usr/share/canon-webcam/"
install -m 0644 "$root/assets/warning.svg" "$stage/usr/share/canon-webcam/warning.svg"
install -m 0644 "$root/canon-webcam-runtime.py" "$stage/usr/share/canon-webcam/"
install -m 0644 "$root/canon-webcam-gui.py" "$stage/usr/share/canon-webcam/"
install -m 0644 "$root/assets/canon-webcam.svg" "$stage/usr/share/icons/hicolor/scalable/apps/camera-bridge.svg"
install -m 0644 "$root/README.md" "$stage/usr/share/doc/canon-webcam/"
install -d "$stage/usr/share/doc/canon-webcam/docs"
install -m 0644 "$root"/docs/*.md "$stage/usr/share/doc/canon-webcam/docs/"
mkdir -p "$stage/usr/share/doc/canon-webcam/assets/screenshots"
install -m 0644 "$root"/assets/screenshots/*.png "$root"/assets/screenshots/*.gif "$stage/usr/share/doc/canon-webcam/assets/screenshots/"
install -m 0644 "$root/packaging/canon-webcam.service" "$stage/usr/lib/systemd/user/"
cat > "$stage/usr/share/applications/canon-webcam.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Camera Bridge
Comment=Webcam settings and status for compatible Canon cameras
Exec=/usr/bin/camera-bridge settings
Icon=camera-bridge
Terminal=false
Categories=AudioVideo;Video;
Keywords=Canon;Camera;Webcam;
Actions=Tray;

[Desktop Action Tray]
Name=Show tray icon
Exec=/usr/bin/camera-bridge tray
EOF
printf 'v4l2loopback\n' > "$stage/etc/modules-load.d/canon-webcam.conf"
printf 'options v4l2loopback devices=1 video_nr=42 card_label=Camera-Bridge exclusive_caps=0 max_buffers=2\n' > "$stage/etc/modprobe.d/canon-webcam.conf"
cat > "$stage/DEBIAN/control" <<EOF
Package: camera-bridge
Provides: canon-webcam
Conflicts: canon-webcam
Replaces: canon-webcam
Version: $version
Section: video
Priority: optional
Architecture: all
Maintainer: Camera Bridge contributors <noreply@github.com>
Depends: bash, coreutils, procps, python3 (>= 3.10), python3-pyqt5, fonts-dejavu-core, gphoto2, ffmpeg, v4l2loopback-dkms, v4l2loopback-utils, v4l-utils, systemd, libnotify-bin, policykit-1
Recommends: linux-headers-generic
Homepage: https://github.com/M45-Software/Camera-Bridge
Description: Virtual webcam for compatible Canon cameras with KDE tray controls
 Persistent gphoto2 and ffmpeg virtual webcam with status cards,
 automatic app-driven capture, native resolution, and idle live-view shutdown, and Qt settings and tray controls.
EOF
printf '/etc/modules-load.d/canon-webcam.conf\n/etc/modprobe.d/canon-webcam.conf\n' > "$stage/DEBIAN/conffiles"
# Package install never starts capture, enables lingering, or changes user sessions.
cat > "$stage/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = configure ]; then
    modprobe v4l2loopback 2>/dev/null || true
fi
EOF
chmod 0755 "$stage/DEBIAN/postinst"
find "$stage" -type d -exec chmod 0755 {} +
find "$stage" -type f -exec chmod 0644 {} +
chmod 0755 "$stage/usr/bin/camera-bridge" "$stage/DEBIAN/postinst"
dpkg-deb --root-owner-group --build "$stage" "$root/dist/camera-bridge_${version}_all.deb"
