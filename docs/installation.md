# Installation and updates

[Back to README](../README.md)

Camera Bridge targets Kubuntu/Ubuntu 24.04 with a graphical user session. It needs
a camera with working USB live-view/movie capture through `gphoto2`, a USB data
cable, and the `v4l2loopback` kernel driver. Camera detection alone does not prove
live-view support. For long calls, consider AC power and disable camera auto power-off.

## Local Debian package

```bash
sudo apt install ./camera-bridge_0.3.0_amd64.deb
```

The architecture-independent package installs the app, icon, systemd user unit,
and virtual-device defaults. APT resolves native dependencies. Installation tries
to load the virtual webcam driver but does not start capture during installation.
At the next graphical sign-in, the webcam becomes available and the tray opens by
default. Both login options are on the main window and save immediately.
Open Camera Bridge and click **Turn on** to use it without signing out.

If device permissions prevent access, add yourself to the video group and log out/in:

```bash
sudo usermod -aG video "$USER"
```

## M45 Software signed APT repository

Public archive and setup information: <https://m45software.com/apt/>.
Suite: `stable`; component: `main`. The archive currently targets Ubuntu 24.04
on amd64, including architecture-independent packages.

### Add the repository once

The setup package contains the public signing key and an APT source scoped to
that key with `Signed-By`; it contains no maintainer scripts.

```bash
sudo apt install curl ca-certificates
curl -fLO https://m45software.com/apt/m45-software-archive-keyring_1.0_all.deb
printf '%s  %s\n' '64c664ec99234f244ec051e945ff604d8390966ef0d47df28c3ee39edc3d0fd1' 'm45-software-archive-keyring_1.0_all.deb' | sha256sum -c -
sudo apt install ./m45-software-archive-keyring_1.0_all.deb
sudo apt update
```

Confirm the checksum succeeds before installing the setup package. Its public-key
fingerprint is `F8BD 2FC4 4347 3EB7 1D87 003B F8A7 E75C 58D7 C0A3`.

The package installs `/usr/share/keyrings/m45-software-archive-keyring.asc` and
`/etc/apt/sources.list.d/m45-software.sources`.

### Install and update

```bash
sudo apt update
sudo apt install camera-bridge
```

Later updates can arrive through normal system updates, or explicitly:

```bash
sudo apt update
sudo apt install --only-upgrade camera-bridge
```

Quit the tray before updating and reopen it afterward. To remove the repository
setup while retaining installed apps:

```bash
sudo apt purge m45-software-archive-keyring
```

## Upgrading from the script installer

Before switching from the older `/usr/local/bin/canon-webcam` script installation,
stop it and run its uninstaller:

```bash
canon-webcam stop
canon-webcam uninstall
```

Then install the new `.deb`. This prevents an old per-user unit from overriding
the packaged service. Saved settings are retained. Older installed `.deb` packages
named `canon-webcam` are replaced by the `camera-bridge` package through APT.

The packaged command is `camera-bridge`; `canon-webcam` remains an alias.
Existing `canon-webcam.service` and configuration paths are retained for
compatibility. The packaged application entry uses `camera-bridge.desktop` so an
old user launcher cannot hide it. Use APT to update/remove packaged installations,
rather than the script's `install` or `uninstall` commands.

## Remove

Disable both login options on the main window, quit the app, then run:

```bash
sudo apt remove camera-bridge
```

Per-user preferences remain in `~/.config/canon-webcam/settings.conf` unless
`XDG_CONFIG_HOME` points elsewhere.
