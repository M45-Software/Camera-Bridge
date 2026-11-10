#!/usr/bin/env bash
set -Eeuo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version="${1:-0.3.1}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Use a version such as 0.2.0' >&2; exit 1; }
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/DEBIAN" "$stage/usr/bin" "$stage/usr/share/canon-webcam" \
  "$stage/usr/share/applications" "$stage/usr/share/icons/hicolor/scalable/apps" \
  "$stage/usr/lib/systemd/user" "$stage/etc/xdg/autostart" "$stage/etc/modules-load.d" "$stage/etc/modprobe.d" \
  "$stage/usr/share/doc/canon-webcam" "$root/dist"
install -m 0755 "$root/canon-webcam.sh" "$stage/usr/bin/camera-bridge"
ln -s camera-bridge "$stage/usr/bin/canon-webcam"
install -m 0644 "$root"/assets/tray-*.svg "$stage/usr/share/canon-webcam/"
install -m 0644 "$root"/assets/status-*.png "$stage/usr/share/canon-webcam/"
install -m 0644 "$root/assets/warning.svg" "$stage/usr/share/canon-webcam/warning.svg"
install -m 0644 "$root/canon-webcam-runtime.py" "$stage/usr/share/canon-webcam/"
install -m 0644 "$root/canon-webcam-capture.py" "$stage/usr/share/canon-webcam/"
install -m 0644 "$root/canon_webcam_controls.py" "$stage/usr/share/canon-webcam/"
install -D -m 0644 "$root/vendor/canon/linux-x86_64/libEDSDK.so" "$stage/usr/lib/camera-bridge/libEDSDK.so"
printf '%s  %s\n' '92aeae98d2d18f641df72695733d68a55993b4149a13a075983895775eaf91b5' "$stage/usr/lib/camera-bridge/libEDSDK.so" | sha256sum -c -
install -m 0644 "$root/THIRD_PARTY_NOTICES.md" "$stage/usr/share/doc/canon-webcam/"
install -m 0644 "$root/THIRD_PARTY_NOTICES.md" "$stage/usr/share/doc/canon-webcam/copyright"
install -m 0644 "$root/canon-webcam-gui.py" "$stage/usr/share/canon-webcam/"
install -m 0644 "$root/assets/canon-webcam.svg" "$stage/usr/share/icons/hicolor/scalable/apps/camera-bridge.svg"
install -m 0644 "$root/README.md" "$stage/usr/share/doc/canon-webcam/"
install -d "$stage/usr/share/doc/canon-webcam/docs"
install -m 0644 "$root"/docs/*.md "$stage/usr/share/doc/canon-webcam/docs/"
mkdir -p "$stage/usr/share/doc/canon-webcam/assets/screenshots"
install -m 0644 "$root"/assets/screenshots/*.png "$root"/assets/screenshots/*.gif "$stage/usr/share/doc/canon-webcam/assets/screenshots/"
install -m 0644 "$root/packaging/canon-webcam.service" "$stage/usr/lib/systemd/user/"
cat > "$stage/usr/share/applications/camera-bridge.desktop" <<'EOF'
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
# Per-user Hidden=true overrides let either login default be disabled independently.
for mode in tray webcam; do
  entry=canon-webcam-tray.desktop
  command=tray
  name="Camera Bridge Tray"
  if [[ "$mode" == webcam ]]; then
    entry=camera-bridge-webcam.desktop
    command=start
    name="Camera Bridge Webcam"
  fi
  cat > "$stage/etc/xdg/autostart/$entry" <<EOF
[Desktop Entry]
Type=Application
Name=$name
Exec=/usr/bin/camera-bridge $command
Icon=camera-bridge
Terminal=false
EOF
done
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
Architecture: amd64
Maintainer: Camera Bridge contributors <noreply@github.com>
Depends: bash, coreutils, procps, python3 (>= 3.10), python3-pyqt5, fonts-dejavu-core, gphoto2, ffmpeg, v4l2loopback-dkms, v4l2loopback-utils, v4l-utils, systemd, libnotify-bin, policykit-1, libc6, libstdc++6, libgcc-s1, libusb-1.0-0
Recommends: linux-headers-generic
Homepage: https://github.com/M45-Software/Camera-Bridge
Description: Virtual webcam for compatible Canon cameras with KDE tray controls
 Canon SDK capture with gphoto2 fallback and a persistent ffmpeg virtual webcam,
 automatic app-driven capture, native resolution, and idle live-view shutdown, and Qt settings and tray controls.
EOF
printf '/etc/modules-load.d/canon-webcam.conf\n/etc/modprobe.d/canon-webcam.conf\n/etc/xdg/autostart/canon-webcam-tray.desktop\n/etc/xdg/autostart/camera-bridge-webcam.desktop\n' > "$stage/DEBIAN/conffiles"
# Login defaults apply on the next graphical sign-in; installation does not start capture.
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
dpkg-deb --root-owner-group --build "$stage" "$root/dist/camera-bridge_${version}_amd64.deb"
