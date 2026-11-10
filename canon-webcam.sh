#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="camera-bridge"
SERVICE_NAME="${CANON_WEBCAM_SERVICE:-canon-webcam.service}"
INSTALL_PATH="/usr/local/bin/camera-bridge"
if [[ "$(readlink -f "${BASH_SOURCE[0]}")" == /usr/bin/camera-bridge ]]; then
  INSTALL_PATH="/usr/bin/camera-bridge"
fi
GUI_PATH="/usr/share/canon-webcam/canon-webcam-gui.py"
AUTO_TIMEOUT="${CANON_WEBCAM_AUTO_TIMEOUT:-60}"
CAMERA_IDLE="${CANON_WEBCAM_START_IDLE:-0}"
ON_DEMAND="${CANON_WEBCAM_ON_DEMAND:-1}"
NATIVE_RESOLUTION="${CANON_WEBCAM_NATIVE_RESOLUTION:-1}"
LIVE_VIEW_SIZE="${CANON_WEBCAM_LIVE_VIEW_SIZE:-1}"
AUTO_FPS="${CANON_WEBCAM_AUTO_FPS:-1}"
PASSTHROUGH="${CANON_WEBCAM_PASSTHROUGH:-0}"
CAPTURE_BACKEND="${CANON_WEBCAM_CAPTURE_BACKEND:-0}"
LIVE_VIEW_ACTIVE=0
CAMERA_RESPONSIVE=1
CAMERA_RECOVERY_WAIT=0
LAST_CAMERA_ID=""
END_LIVE_VIEW=0
CAMERA_READY=0

DEFAULT_VIDEO_NR="42"
DEFAULT_CARD_LABEL="Camera-Bridge"
DEFAULT_WIDTH="1280"
DEFAULT_HEIGHT="720"
DEFAULT_FPS="30"
DEFAULT_CAMERA_FPS="30"
DEFAULT_LOOPBACK_BUFFERS="2"
DEFAULT_CAMERA_RETRY_DELAY="3"
DEFAULT_CAMERA_FAILURE_DELAY="5"
DEFAULT_CAMERA_MAX_FAILURE_DELAY="5"
DEFAULT_CAMERA_SETTLE_DELAY="5"
DEFAULT_CAMERA_STABLE_CHECKS="2"
DEFAULT_CAMERA_SEGMENT_SECONDS="0"
DEFAULT_SET_VIEWFINDER="1"

VIDEO_NR="${CANON_WEBCAM_VIDEO_NR:-$DEFAULT_VIDEO_NR}"
CARD_LABEL="${CANON_WEBCAM_LABEL:-$DEFAULT_CARD_LABEL}"
WIDTH="${CANON_WEBCAM_WIDTH:-$DEFAULT_WIDTH}"
HEIGHT="${CANON_WEBCAM_HEIGHT:-$DEFAULT_HEIGHT}"
FPS="${CANON_WEBCAM_FPS:-$DEFAULT_FPS}"
CAMERA_FPS="${CANON_WEBCAM_CAMERA_FPS:-$DEFAULT_CAMERA_FPS}"
LOOPBACK_BUFFERS="${CANON_WEBCAM_LOOPBACK_BUFFERS:-$DEFAULT_LOOPBACK_BUFFERS}"
CAMERA_RETRY_DELAY="${CANON_WEBCAM_CAMERA_RETRY_DELAY:-$DEFAULT_CAMERA_RETRY_DELAY}"
CAMERA_FAILURE_DELAY="${CANON_WEBCAM_CAMERA_FAILURE_DELAY:-$DEFAULT_CAMERA_FAILURE_DELAY}"
CAMERA_MAX_FAILURE_DELAY="${CANON_WEBCAM_CAMERA_MAX_FAILURE_DELAY:-$DEFAULT_CAMERA_MAX_FAILURE_DELAY}"
CAMERA_SETTLE_DELAY="${CANON_WEBCAM_CAMERA_SETTLE_DELAY:-$DEFAULT_CAMERA_SETTLE_DELAY}"
CAMERA_STABLE_CHECKS="${CANON_WEBCAM_CAMERA_STABLE_CHECKS:-$DEFAULT_CAMERA_STABLE_CHECKS}"
CAMERA_SEGMENT_SECONDS="${CANON_WEBCAM_CAMERA_SEGMENT_SECONDS:-$DEFAULT_CAMERA_SEGMENT_SECONDS}"
SET_VIEWFINDER="${CANON_WEBCAM_SET_VIEWFINDER:-$DEFAULT_SET_VIEWFINDER}"
DEVICE="${CANON_WEBCAM_DEVICE:-}"
DEVICE_EXPLICIT=0
START_SERVICE=0
TEST_SOURCE_PID=""
LAST_STATUS_FRAME=""
CAMERA_GPHOTO_PID=""
LOOPBACK_WRITER_PID=""
CAMERA_FIFO=""
CAMERA_FIFO_DIR=""
CAMERA_FIFO_HOLD_FD=""

log() {
  printf '[%s] %s\n' "$APP_NAME" "$*"
}

warn() {
  printf '[%s] warning: %s\n' "$APP_NAME" "$*" >&2
}

die() {
  printf '[%s] error: %s\n' "$APP_NAME" "$*" >&2
  exit 1
}

usage() {
  cat <<'USAGE'
Usage:
  camera-bridge install [options] [--start]
  camera-bridge stream [options]
  camera-bridge tray|settings|setup-user
  camera-bridge start|stop|restart|hard-reset|status|logs|doctor
  camera-bridge reset-loopback|test-source|install-launchers|remove-launchers|uninstall

Commands:
  install             Install packages, v4l2loopback config, launchers, and user service.
  stream              Run the Canon -> gphoto2 -> ffmpeg -> virtual webcam pipeline.
  tray                Show the KDE-compatible tray icon and controls.
  settings            Open the settings and status window.
  setup-user          Create or refresh per-user service and launchers.
  start               Enable the webcam; automatic mode waits for an app.
  force-start         Force capture until auto or restart clears the override.
  auto                Clear the temporary force-capture override.
  stop                Stop the systemd user service.
  restart             Restart the systemd user service.
  hard-reset          Stop service, clear stale capture processes, reload loopback, restart.
  status              Show systemd user service status.
  logs                Follow service logs.
  doctor              Check dependencies, loopback, service, and camera detection.
  reset-loopback      Reload v4l2loopback and verify the virtual webcam is writable.
  test-source         Stream a generated test pattern to the virtual webcam.
  install-launchers   Install or refresh Kubuntu/Plasma application launchers.
  remove-launchers    Remove Kubuntu/Plasma application launchers.
  uninstall           Remove installed files. Packages are left installed.

Options:
  --device /dev/videoN       Virtual webcam device. Install also accepts --video-nr N.
  --video-nr N               Virtual webcam number for install-time loopback config.
  --label NAME               Camera label shown to video apps.
  --width PX --height PX     Output frame size.
  --mjpeg-passthrough        Copy camera JPEG frames without decoding (requires native size).
  --raw-video                Decode to raw YUV420 for app compatibility (default).
  --auto-fps                 Follow camera frame-arrival timing (default).
  --manual-fps               Use fixed input/output FPS assumptions.
  --camera-fps N             Expected input FPS in manual timing mode.
  --fps N                    Nominal webcam FPS (default 30; actual camera rate varies).
  --loopback-buffers N       v4l2loopback queue size, minimum 2.
  --set-viewfinder           Prepare Canon EOS live view before capture.
  --no-set-viewfinder        Skip Canon EOS live-view preparation.
  --on-demand                Encode when apps open the webcam (default).
  --always-stream            Bypass app detection for compatibility/manual use.
  --native-resolution        Use the camera's native live-view size (default).
  --fixed-resolution         Use --width/--height instead of native size.
  --live-view-size SIZE       keep|large|medium|small; default large if supported.
  --backend NAME             auto (SDK preferred), edsdk, or gphoto2.
  --timeout SECONDS          Idle live-view / no-image timeout (default 60); 0 disables.
  --start                    Start the service after install.

Defaults:
  Virtual webcam: /dev/video42
  Camera label:   Camera-Bridge
  Stream size:    1280x720
  Webcam FPS:     30 nominal
  Camera FPS:     automatic arrival timing
  Loopback queue: 2 buffers

Run install as your regular desktop user. The script will ask for sudo only for
system package and kernel module setup.

Examples:
  ./canon-webcam.sh install --start
  camera-bridge doctor
  camera-bridge stream --width 640 --height 480
  camera-bridge hard-reset
USAGE
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --mjpeg-passthrough) PASSTHROUGH=1; shift ;;
      --raw-video) PASSTHROUGH=0; shift ;;
      --backend)
        [[ $# -ge 2 ]] || die "--backend requires auto, edsdk, or gphoto2"
        case "$2" in auto) CAPTURE_BACKEND=0 ;; edsdk) CAPTURE_BACKEND=1 ;; gphoto2) CAPTURE_BACKEND=2 ;; *) die "invalid capture backend" ;; esac
        shift 2 ;;
      --auto-fps) AUTO_FPS=1; shift ;;
      --manual-fps) AUTO_FPS=0; shift ;;
      --on-demand) ON_DEMAND=1; shift ;;
      --always-stream) ON_DEMAND=0; shift ;;
      --native-resolution) NATIVE_RESOLUTION=1; shift ;;
      --fixed-resolution) NATIVE_RESOLUTION=0; shift ;;
      --live-view-size)
        [[ $# -ge 2 ]] || die "--live-view-size requires keep, large, medium, or small"
        case "$2" in keep) LIVE_VIEW_SIZE=0 ;; large) LIVE_VIEW_SIZE=1 ;; medium) LIVE_VIEW_SIZE=2 ;; small) LIVE_VIEW_SIZE=3 ;; *) die "invalid live-view size" ;; esac
        shift 2
        ;;
      --timeout)
        [[ $# -ge 2 ]] || die "--timeout requires seconds"
        AUTO_TIMEOUT="$2"
        shift 2
        ;;
      --video-nr)
        [[ $# -ge 2 ]] || die "--video-nr requires a value"
        VIDEO_NR="$2"
        shift 2
        ;;
      --label)
        [[ $# -ge 2 ]] || die "--label requires a value"
        CARD_LABEL="$2"
        shift 2
        ;;
      --device)
        [[ $# -ge 2 ]] || die "--device requires a value"
        DEVICE="$2"
        DEVICE_EXPLICIT=1
        shift 2
        ;;
      --width)
        [[ $# -ge 2 ]] || die "--width requires a value"
        WIDTH="$2"
        shift 2
        ;;
      --height)
        [[ $# -ge 2 ]] || die "--height requires a value"
        HEIGHT="$2"
        shift 2
        ;;
      --fps)
        [[ $# -ge 2 ]] || die "--fps requires a value"
        FPS="$2"
        shift 2
        ;;
      --camera-fps)
        [[ $# -ge 2 ]] || die "--camera-fps requires a value"
        CAMERA_FPS="$2"
        shift 2
        ;;
      --loopback-buffers)
        [[ $# -ge 2 ]] || die "--loopback-buffers requires a value"
        LOOPBACK_BUFFERS="$2"
        shift 2
        ;;
      --set-viewfinder)
        SET_VIEWFINDER=1
        shift
        ;;
      --no-set-viewfinder)
        SET_VIEWFINDER=0
        shift
        ;;
      --enable)
        warn "--enable is no longer needed; install enables the user service by default"
        shift
        ;;
      --start)
        START_SERVICE=1
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        die "unknown option: $1"
        ;;
    esac
  done

  [[ "$PASSTHROUGH" =~ ^[01]$ ]] || die "invalid passthrough setting"
  [[ "$CAPTURE_BACKEND" =~ ^[0-2]$ ]] || die "invalid capture backend"
  (( PASSTHROUGH == 0 || NATIVE_RESOLUTION == 1 )) || die "MJPEG passthrough requires native resolution"
  [[ "$AUTO_FPS" =~ ^[01]$ && "$ON_DEMAND" =~ ^[01]$ && "$NATIVE_RESOLUTION" =~ ^[01]$ && "$LIVE_VIEW_SIZE" =~ ^[0-3]$ ]] || die "invalid automatic camera options"
  [[ "$AUTO_TIMEOUT" =~ ^(0|[1-9][0-9]{0,7})$ ]] || die "--timeout must be 0 or a positive number of seconds (maximum 99999999)"
  [[ "$VIDEO_NR" =~ ^[0-9]+$ ]] || die "--video-nr must be a number"
  [[ "$WIDTH" =~ ^[0-9]+$ ]] || die "--width must be a number"
  [[ "$HEIGHT" =~ ^[0-9]+$ ]] || die "--height must be a number"
  [[ "$FPS" =~ ^[0-9]+$ ]] || die "--fps must be a number"
  [[ "$CAMERA_FPS" =~ ^[0-9]+$ ]] || die "--camera-fps must be a number"
  [[ "$LOOPBACK_BUFFERS" =~ ^[0-9]+$ ]] || die "--loopback-buffers must be a number"
  (( WIDTH >= 16 && HEIGHT >= 16 && WIDTH % 2 == 0 && HEIGHT % 2 == 0 )) || die "video width and height must be even and at least 16"
  (( FPS >= 1 )) || die "--fps must be at least 1"
  (( CAMERA_FPS >= 1 )) || die "--camera-fps must be at least 1"
  (( LOOPBACK_BUFFERS >= 2 )) || die "--loopback-buffers must be at least 2"
  [[ "$CAMERA_RETRY_DELAY" =~ ^[0-9]+$ ]] || die "CANON_WEBCAM_CAMERA_RETRY_DELAY must be a number"
  [[ "$CAMERA_FAILURE_DELAY" =~ ^[0-9]+$ ]] || die "CANON_WEBCAM_CAMERA_FAILURE_DELAY must be a number"
  [[ "$CAMERA_MAX_FAILURE_DELAY" =~ ^[0-9]+$ ]] || die "CANON_WEBCAM_CAMERA_MAX_FAILURE_DELAY must be a number"
  [[ "$CAMERA_SETTLE_DELAY" =~ ^[0-9]+$ ]] || die "CANON_WEBCAM_CAMERA_SETTLE_DELAY must be a number"
  [[ "$CAMERA_STABLE_CHECKS" =~ ^[0-9]+$ ]] || die "CANON_WEBCAM_CAMERA_STABLE_CHECKS must be a number"
  [[ "$CAMERA_SEGMENT_SECONDS" =~ ^[0-9]+$ ]] || die "CANON_WEBCAM_CAMERA_SEGMENT_SECONDS must be a number"
  [[ "$SET_VIEWFINDER" =~ ^(0|1)$ ]] || die "CANON_WEBCAM_SET_VIEWFINDER must be 0 or 1"
  (( CAMERA_RETRY_DELAY >= 1 )) || die "CANON_WEBCAM_CAMERA_RETRY_DELAY must be at least 1"
  (( CAMERA_FAILURE_DELAY >= 1 )) || die "CANON_WEBCAM_CAMERA_FAILURE_DELAY must be at least 1"
  (( CAMERA_MAX_FAILURE_DELAY >= CAMERA_FAILURE_DELAY )) || die "CANON_WEBCAM_CAMERA_MAX_FAILURE_DELAY must be greater than or equal to CANON_WEBCAM_CAMERA_FAILURE_DELAY"
  (( CAMERA_STABLE_CHECKS >= 1 )) || die "CANON_WEBCAM_CAMERA_STABLE_CHECKS must be at least 1"
  (( CAMERA_SEGMENT_SECONDS >= 0 )) || die "CANON_WEBCAM_CAMERA_SEGMENT_SECONDS must be at least 0"
  [[ "$CARD_LABEL" =~ ^[A-Za-z0-9._-]+$ ]] || die "--label may only contain letters, numbers, dots, underscores, and dashes"

  DEVICE="${DEVICE:-/dev/video${VIDEO_NR}}"
}

require_not_root() {
  if [[ "${EUID}" -eq 0 ]]; then
    die "run this command as your regular desktop user, not with sudo"
  fi
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing command: $1"
}

systemctl_user() {
  systemctl --user "$@"
}

notify_desktop() {
  local summary="$1"
  local body="$2"

  if command -v notify-send >/dev/null 2>&1 && notify-send "$summary" "$body"; then
    return
  fi

  log "$summary: $body"
}

desktop_app_dir() {
  printf '%s/applications\n' "${XDG_DATA_HOME:-$HOME/.local/share}"
}

run_privileged() {
  local command_name="$1"
  local command_path
  shift

  command_path="$(command -v "$command_name" 2>/dev/null || true)"
  [[ -n "$command_path" ]] || die "missing command: $command_name"

  if [[ "${EUID}" -eq 0 ]]; then
    "$command_path" "$@"
    return
  fi

  if [[ -t 0 && -t 1 ]]; then
    sudo "$command_path" "$@"
    return
  fi

  if command -v pkexec >/dev/null 2>&1 && [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
    pkexec "$command_path" "$@"
    return
  fi

  sudo "$command_path" "$@"
}

warn_if_not_ubuntu_2404() {
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    source /etc/os-release
    if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "24.04" ]]; then
      warn "this script targets Kubuntu/Ubuntu 24.04; detected ${PRETTY_NAME:-unknown OS}"
    fi
  fi
}

ensure_universe_packages_visible() {
  if apt-cache show gphoto2 v4l2loopback-dkms >/dev/null 2>&1; then
    return
  fi

  log "Refreshing apt package metadata..."
  sudo apt-get update

  if apt-cache show gphoto2 v4l2loopback-dkms >/dev/null 2>&1; then
    return
  fi

  if command -v add-apt-repository >/dev/null 2>&1; then
    log "Enabling the Ubuntu universe repository..."
    sudo add-apt-repository -y universe
    sudo apt-get update
    return
  fi

  die "Ubuntu universe packages are not visible. Enable the universe repository, then rerun install."
}

install_packages() {
  local packages=(
    gphoto2
    libusb-1.0-0
    libstdc++6
    ffmpeg
    v4l2loopback-dkms
    v4l2loopback-utils
    v4l-utils
    desktop-file-utils
    libnotify-bin
    python3-pyqt5
    fonts-dejavu-core
    policykit-1
  )

  if [[ ! -e "/lib/modules/$(uname -r)/build" ]]; then
    packages+=("linux-headers-$(uname -r)")
  fi

  ensure_universe_packages_visible
  log "Installing packages: ${packages[*]}"
  sudo apt-get update
  sudo apt-get install -y "${packages[@]}"
}

install_binary() {
  local self
  [[ "$INSTALL_PATH" != /usr/bin/camera-bridge ]] || { log "Use apt to update the Debian package"; return; }
  self="$(readlink -f "${BASH_SOURCE[0]}")"

  if [[ "$self" == "$INSTALL_PATH" ]]; then
    log "Command already installed at $INSTALL_PATH"
    return
  fi

  log "Installing command to $INSTALL_PATH"
  run_privileged install -m 0755 "$self" "$INSTALL_PATH"
  if [[ -f "$(dirname "$self")/canon-webcam-gui.py" ]]; then
    run_privileged install -d /usr/share/canon-webcam
    run_privileged install -m 0644 "$(dirname "$self")/canon-webcam-gui.py" "$GUI_PATH"
    run_privileged install -m 0644 "$(dirname "$self")/canon-webcam-runtime.py" /usr/share/canon-webcam/canon-webcam-runtime.py
    run_privileged install -m 0644 "$(dirname "$self")/canon-webcam-capture.py" /usr/share/canon-webcam/canon-webcam-capture.py
    run_privileged install -m 0644 "$(dirname "$self")/canon_webcam_controls.py" /usr/share/canon-webcam/canon_webcam_controls.py
    if [[ -f "$(dirname "$self")/vendor/canon/linux-x86_64/libEDSDK.so" && "$(uname -m)" == x86_64 ]]; then
      run_privileged install -D -m 0644 "$(dirname "$self")/vendor/canon/linux-x86_64/libEDSDK.so" /usr/lib/camera-bridge/libEDSDK.so
    fi
    run_privileged install -m 0644 "$(dirname "$self")"/assets/status-*.png /usr/share/canon-webcam/
    run_privileged install -m 0644 "$(dirname "$self")/assets/warning.svg" /usr/share/canon-webcam/warning.svg
    run_privileged install -m 0644 "$(dirname "$self")/assets/tray-ready.svg" "$(dirname "$self")/assets/tray-live.svg" "$(dirname "$self")/assets/tray-live-dim.svg" "$(dirname "$self")/assets/tray-off.svg" "$(dirname "$self")"/assets/tray-{connected,awake,starting,warning}.svg /usr/share/canon-webcam/
    run_privileged install -D -m 0644 "$(dirname "$self")/assets/canon-webcam.svg" /usr/share/icons/hicolor/scalable/apps/camera-bridge.svg
  fi
}

write_root_file() {
  local destination="$1"
  local content="$2"
  local tmp

  tmp="$(mktemp)"
  printf '%s\n' "$content" >"$tmp"
  sudo install -m 0644 "$tmp" "$destination"
  rm -f "$tmp"
}

install_loopback_config() {
  log "Writing persistent v4l2loopback configuration for $DEVICE"
  write_root_file "/etc/modules-load.d/canon-webcam.conf" "v4l2loopback"
  write_root_file \
    "/etc/modprobe.d/canon-webcam.conf" \
    "options v4l2loopback devices=1 video_nr=${VIDEO_NR} card_label=${CARD_LABEL} exclusive_caps=0 max_buffers=${LOOPBACK_BUFFERS}"
}

loopback_modprobe_args() {
  printf '%s\n' \
    "devices=1" \
    "video_nr=${VIDEO_NR}" \
    "card_label=${CARD_LABEL}" \
    "exclusive_caps=0" \
    "max_buffers=${LOOPBACK_BUFFERS}"
}

loopback_loaded() {
  lsmod | awk '$1 == "v4l2loopback" { found = 1 } END { exit !found }'
}

loopback_info() {
  v4l2-ctl --device "$DEVICE" --info 2>/dev/null || true
}

loopback_writer_ready() {
  [[ -c "$DEVICE" ]] || return 1
  command -v v4l2-ctl >/dev/null 2>&1 || return 0
  loopback_info | grep -q 'Video Output'
}

configure_loopback_runtime_controls() {
  [[ -c "$DEVICE" ]] || return
  command -v v4l2-ctl >/dev/null 2>&1 || return

  v4l2-ctl --device "$DEVICE" --set-ctrl keep_format=0 >/dev/null 2>&1 || true
  v4l2-ctl --device "$DEVICE" --set-ctrl sustain_framerate=1 >/dev/null 2>&1 || true
  v4l2-ctl --device "$DEVICE" --set-parm="$FPS" >/dev/null 2>&1 || true
}

wait_for_video_device() {
  local attempt

  for attempt in {1..20}; do
    [[ -c "$DEVICE" ]] && return 0
    sleep 0.1
  done

  return 1
}

load_loopback_now() {
  local modprobe_args

  if [[ -c "$DEVICE" ]]; then
    log "Virtual webcam already exists at $DEVICE"
    return
  fi

  if loopback_loaded; then
    die "v4l2loopback is already loaded, but $DEVICE does not exist. Reboot, or stop apps using virtual cameras and run: sudo modprobe -r v4l2loopback && sudo modprobe v4l2loopback"
  fi

  log "Loading v4l2loopback as $DEVICE"
  mapfile -t modprobe_args < <(loopback_modprobe_args)
  if run_privileged modprobe v4l2loopback "${modprobe_args[@]}"; then
    wait_for_video_device || die "v4l2loopback loaded, but $DEVICE was not created"
    return
  fi

  if command -v mokutil >/dev/null 2>&1 && mokutil --sb-state 2>/dev/null | grep -qi enabled; then
    warn "Secure Boot is enabled. DKMS modules such as v4l2loopback may need Machine Owner Key enrollment before they can load."
  fi

  die "could not load v4l2loopback"
}

reset_loopback() {
  local modprobe_args was_active=0

  need_cmd sudo

  if systemctl_user is-active "$SERVICE_NAME" >/dev/null 2>&1; then
    if [[ "${CANON_WEBCAM_IN_SERVICE:-0}" == "1" ]]; then
      warn "$SERVICE_NAME is already active; resetting loopback from inside the running service"
    else
      was_active=1
      log "Stopping $SERVICE_NAME before resetting v4l2loopback"
      systemctl_user stop "$SERVICE_NAME"
    fi
  fi

  if [[ -c "$DEVICE" ]] && command -v fuser >/dev/null 2>&1 && fuser "$DEVICE" >/dev/null 2>&1; then
    warn "$DEVICE is in use. Close Zoom, OBS, browsers, and video settings windows, then run this again."
    fuser -v "$DEVICE" || true
    return 1
  fi

  if loopback_loaded; then
    log "Unloading v4l2loopback"
    if ! run_privileged modprobe -r v4l2loopback; then
      die "could not unload v4l2loopback. Close apps using virtual cameras, then rerun: camera-bridge reset-loopback"
    fi
  fi

  log "Loading v4l2loopback as writable $DEVICE"
  mapfile -t modprobe_args < <(loopback_modprobe_args)
  run_privileged modprobe v4l2loopback "${modprobe_args[@]}"

  if command -v udevadm >/dev/null 2>&1; then
    udevadm settle || true
  fi

  wait_for_video_device || die "v4l2loopback loaded, but $DEVICE was not created"

  if ! loopback_writer_ready; then
    warn "$(loopback_info)"
    die "$DEVICE is present but is not advertising Video Output. Reboot, then run: camera-bridge start"
  fi

  log "$DEVICE is ready for ffmpeg input"

  if [[ "$was_active" -eq 1 ]]; then
    log "Restarting $SERVICE_NAME"
    systemctl_user start "$SERVICE_NAME"
  fi
}

ensure_loopback_for_writer() {
  if [[ ! -c "$DEVICE" ]]; then
    if loopback_loaded; then
      warn "$DEVICE is missing while v4l2loopback is loaded; resetting loopback"
      reset_loopback
    else
      load_loopback_now
    fi
  fi

  if ! loopback_writer_ready; then
    warn "$DEVICE is not in writable Video Output mode; resetting loopback"
    reset_loopback
  fi

  configure_loopback_runtime_controls
}

ensure_video_group() {
  if getent group video >/dev/null 2>&1 && ! id -nG "$USER" | tr ' ' '\n' | grep -qx video; then
    log "Adding $USER to the video group for webcam device access"
    sudo usermod -aG video "$USER"
    warn "log out and back in for the video group change to apply"
  fi
}

enable_user_linger() {
  if command -v loginctl >/dev/null 2>&1; then
    log "Enabling lingering so $SERVICE_NAME can keep the virtual webcam populated after boot"
    if ! loginctl enable-linger "$USER" >/dev/null 2>&1; then
      run_privileged loginctl enable-linger "$USER" || warn "could not enable lingering; $SERVICE_NAME will start after login instead of boot"
    fi
  fi
}

install_user_service() {
  [[ "${CANON_WEBCAM_PREVIEW:-0}" != 1 ]] || die "preview setup is managed by tools/preview.sh"
  local service_dir service_file tmp

  service_dir="$HOME/.config/systemd/user"
  service_file="$service_dir/$SERVICE_NAME"
  mkdir -p "$service_dir"

  tmp="$(mktemp)"
  cat >"$tmp" <<SERVICE
[Unit]
Description=Canon camera virtual webcam

[Service]
Type=simple
KillMode=mixed
TimeoutStopSec=20
Environment=CANON_WEBCAM_IN_SERVICE=1
Environment=CANON_WEBCAM_CAMERA_RETRY_DELAY=${CAMERA_RETRY_DELAY}
Environment=CANON_WEBCAM_CAMERA_FAILURE_DELAY=${CAMERA_FAILURE_DELAY}
Environment=CANON_WEBCAM_CAMERA_MAX_FAILURE_DELAY=${CAMERA_MAX_FAILURE_DELAY}
Environment=CANON_WEBCAM_CAMERA_SETTLE_DELAY=${CAMERA_SETTLE_DELAY}
Environment=CANON_WEBCAM_CAMERA_STABLE_CHECKS=${CAMERA_STABLE_CHECKS}
Environment=CANON_WEBCAM_CAMERA_SEGMENT_SECONDS=${CAMERA_SEGMENT_SECONDS}
Environment=CANON_WEBCAM_CAMERA_FPS=${CAMERA_FPS}
Environment=CANON_WEBCAM_SET_VIEWFINDER=${SET_VIEWFINDER}
Environment=CANON_WEBCAM_DEVICE=${DEVICE}
Environment=CANON_WEBCAM_LABEL=${CARD_LABEL}
Environment=CANON_WEBCAM_WIDTH=${WIDTH}
Environment=CANON_WEBCAM_HEIGHT=${HEIGHT}
Environment=CANON_WEBCAM_FPS=${FPS}
Environment=CANON_WEBCAM_LOOPBACK_BUFFERS=${LOOPBACK_BUFFERS}
Environment=CANON_WEBCAM_AUTO_TIMEOUT=${AUTO_TIMEOUT}
Environment=CANON_WEBCAM_ON_DEMAND=${ON_DEMAND}
Environment=CANON_WEBCAM_AUTO_FPS=${AUTO_FPS}
Environment=CANON_WEBCAM_PASSTHROUGH=${PASSTHROUGH}
Environment=CANON_WEBCAM_CAPTURE_BACKEND=${CAPTURE_BACKEND}
Environment=CANON_WEBCAM_NATIVE_RESOLUTION=${NATIVE_RESOLUTION}
Environment=CANON_WEBCAM_LIVE_VIEW_SIZE=${LIVE_VIEW_SIZE}
ExecStart=${INSTALL_PATH} stream
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
SERVICE

  install -m 0644 "$tmp" "$service_file"
  rm -f "$tmp"

  log "Installed user service at $service_file"
  if ! systemctl_user daemon-reload; then
    warn "systemctl --user daemon-reload failed. Log into a graphical session and run it again."
  fi
}

ensure_runtime_installed_current() {
  local self

  self="$(readlink -f "${BASH_SOURCE[0]}")"
  if [[ "$self" == "$INSTALL_PATH" ]]; then
    return
  fi

  if [[ -x "$INSTALL_PATH" ]] && cmp -s "$self" "$INSTALL_PATH"; then
    install_user_service
    return
  fi

  warn "installed command is not current; updating $INSTALL_PATH before starting"
  install_binary
  install_user_service
  install_desktop_launchers
}

write_desktop_launcher() {
  local filename="$1"
  local name="$2"
  local comment="$3"
  local exec_args="$4"
  local icon="$5"
  local app_dir tmp

  app_dir="$(desktop_app_dir)"
  mkdir -p "$app_dir"

  tmp="$(mktemp)"
  cat >"$tmp" <<DESKTOP
[Desktop Entry]
Type=Application
Name=${name}
Comment=${comment}
Exec=${INSTALL_PATH} ${exec_args}
Icon=${icon}
Terminal=false
Categories=AudioVideo;Video;
StartupNotify=false
DESKTOP

  install -m 0644 "$tmp" "$app_dir/$filename"
  rm -f "$tmp"
}

refresh_desktop_launchers() {
  local app_dir
  app_dir="$(desktop_app_dir)"

  if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$app_dir" >/dev/null 2>&1 || true
  fi

  if command -v kbuildsycoca6 >/dev/null 2>&1; then
    kbuildsycoca6 >/dev/null 2>&1 || true
  elif command -v kbuildsycoca5 >/dev/null 2>&1; then
    kbuildsycoca5 >/dev/null 2>&1 || true
  fi
}

install_desktop_launchers() {
  if [[ ! -x "$INSTALL_PATH" ]]; then
    warn "launchers point to $INSTALL_PATH, but that command is not installed yet"
  fi

  write_desktop_launcher \
    "canon-webcam-start.desktop" \
    "Start Camera Bridge" \
    "Start the Canon camera virtual webcam service" \
    "launcher-start" \
    "camera-web"

  write_desktop_launcher \
    "canon-webcam-stop.desktop" \
    "Stop Camera Bridge" \
    "Stop the Canon camera virtual webcam service" \
    "launcher-stop" \
    "media-playback-stop"

  write_desktop_launcher \
    "canon-webcam-status.desktop" \
    "Camera Bridge Status" \
    "Show whether the Canon camera virtual webcam service is running" \
    "launcher-status" \
    "dialog-information"

  write_desktop_launcher "canon-webcam.desktop" "Camera Bridge" "Webcam settings and status" "settings" "camera-bridge"
  write_desktop_launcher "canon-webcam-tray.desktop" "Camera Bridge Tray" "Webcam tray controls" "tray" "camera-bridge"
  refresh_desktop_launchers
  log "Installed application launchers in $(desktop_app_dir)"
}

remove_desktop_launchers() {
  local app_dir
  app_dir="$(desktop_app_dir)"

  rm -f \
    "$app_dir/canon-webcam-start.desktop" \
    "$app_dir/canon-webcam-stop.desktop" \
    "$app_dir/canon-webcam-status.desktop" \
    "$app_dir/canon-webcam.desktop" \
    "$app_dir/canon-webcam-tray.desktop"

  refresh_desktop_launchers
  log "Removed application launchers from $app_dir"
}

install_all() {
  [[ "$INSTALL_PATH" != /usr/bin/camera-bridge ]] || die "use apt to update the installed Debian package"
  require_not_root
  warn_if_not_ubuntu_2404
  need_cmd sudo

  if [[ "$DEVICE_EXPLICIT" -eq 1 ]]; then
    [[ "$DEVICE" =~ ^/dev/video([0-9]+)$ ]] || die "install --device must look like /dev/video42"
    VIDEO_NR="${BASH_REMATCH[1]}"
  fi

  log "Requesting sudo once for package and kernel module setup"
  sudo -v

  install_packages
  install_binary
  install_loopback_config
  load_loopback_now
  ensure_video_group
  enable_user_linger
  install_user_service
  install_desktop_launchers

  log "Enabling $SERVICE_NAME"
  systemctl_user enable "$SERVICE_NAME"

  if [[ "$START_SERVICE" -eq 1 ]]; then
    service_start
  fi

  log "Install complete. Connect the camera by USB, set it to movie/live-view capable mode, then run: camera-bridge doctor"
  log "Kubuntu launchers are available as Start Camera Bridge, Stop Camera Bridge, and Camera Bridge Status."
}

stop_camera_mount_helpers() {
  local name name_pattern pattern
  local names=(
    gvfsd-gphoto2
    gvfs-gphoto2-volume-monitor
    PTPCamera
  )
  local patterns=(
    'kioworker.*(camera|gphoto|mtp|ptp)'
    'kio_.*(camera|gphoto|mtp|ptp)'
  )

  for name in "${names[@]}"; do
    name_pattern="(^|/)${name}([[:space:]]|$)"
    if pgrep -u "$USER" -f "$name_pattern" >/dev/null 2>&1; then
      log "Stopping $name so gphoto2 can claim the camera"
      pkill -u "$USER" -f "$name_pattern" || true
    fi
  done

  for pattern in "${patterns[@]}"; do
    if pgrep -u "$USER" -f "$pattern" >/dev/null 2>&1; then
      log "Stopping KDE camera worker matching: $pattern"
      pkill -u "$USER" -f "$pattern" || true
    fi
  done
}

camera_detected() {
  gphoto2 --auto-detect | awk 'NR > 2 && NF { found = 1 } END { exit !found }'
}

camera_detect_line() {
  gphoto2 --auto-detect | awk 'NR > 2 && NF { print; exit }'
}

camera_wait_stable() {
  local attempt current previous=""

  for (( attempt = 1; attempt <= CAMERA_STABLE_CHECKS; attempt++ )); do
    current="$(camera_detect_line)"
    if [[ -z "$current" ]]; then
      CAMERA_READY=0
      return 1
    fi

    if [[ -n "$previous" && "$current" != "$previous" ]]; then
      CAMERA_READY=0
      return 1
    fi

    # A previously stable, unchanged USB camera does not need another delayed
    # stability sample on every app open. New connections still settle normally.
    if (( CAMERA_READY == 1 )) && [[ "$current" == "$LAST_CAMERA_ID" ]]; then break; fi
    previous="$current"
    if (( attempt < CAMERA_STABLE_CHECKS )); then
      sleep 1
    fi
  done

  if (( CAMERA_READY == 0 && CAMERA_SETTLE_DELAY > 0 )); then
    log "Camera detected; waiting ${CAMERA_SETTLE_DELAY}s for USB/PTP to settle"
    sleep "$CAMERA_SETTLE_DELAY"
  fi
  CAMERA_READY=1
  LAST_CAMERA_ID="$current"
}

camera_set_config() {
  local config="$1"

  timeout --kill-after=1 5 gphoto2 --set-config "$config" >/dev/null 2>&1 || true
}

write_live_view_state() {
  [[ "${CANON_WEBCAM_IN_SERVICE:-0}" == 1 && -n "${XDG_RUNTIME_DIR:-}" ]] || return 0
  local target="$XDG_RUNTIME_DIR/canon-webcam/live-view"
  mkdir -p "$(dirname "$target")"
  printf '%s\n%s\n' "$1" "$LAST_CAMERA_ID" >"$target.tmp.$$"
  mv -f "$target.tmp.$$" "$target"
}

camera_prepare_live_view() {
  CAMERA_RESPONSIVE=1
  write_live_view_state unknown
  local capture_helper
  capture_helper="$(dirname "$(runtime_helper_path)")/canon-webcam-capture.py"
  if [[ -f "$capture_helper" ]] && CANON_WEBCAM_CAPTURE_BACKEND="$CAPTURE_BACKEND" /usr/bin/python3 "$capture_helper" --prefers-sdk; then
    # The SDK owns its camera session and enables live view when capturing.
    LIVE_VIEW_ACTIVE=1
    return
  fi
  if [[ "$SET_VIEWFINDER" -ne 1 ]]; then
    return
  fi

  log "Preparing Canon EOS live view"
  local configs=(--set-config capturetarget=0)
  case "$LIVE_VIEW_SIZE" in
    1) configs+=(--set-config liveviewsize=Large) ;;
    2) configs+=(--set-config liveviewsize=Medium) ;;
    3) configs+=(--set-config liveviewsize=Small) ;;
  esac
  configs+=(--set-config viewfinder=1)
  if ! timeout --kill-after=1 8 gphoto2 "${configs[@]}" >/dev/null 2>&1; then
    # Models without a size control still get the basic EOS live-view setup.
    if ! timeout --kill-after=1 5 gphoto2 --set-config capturetarget=0 --set-config viewfinder=1 >/dev/null 2>&1; then
      CAMERA_RESPONSIVE=0
      warn "Camera is detected but not responding. Turn it off and on; reconnect USB if needed."
      return 0
    fi
  fi
  LIVE_VIEW_ACTIVE=1
  write_live_view_state on
}

camera_end_live_view() {
  if (( SET_VIEWFINDER == 1 && LIVE_VIEW_ACTIVE == 1 )); then
    log "Leaving Canon EOS live view"
    if timeout --kill-after=1 5 gphoto2 --set-config viewfinder=0 >/dev/null 2>&1; then
      write_live_view_state off
    else
      write_live_view_state unknown
    fi
    LIVE_VIEW_ACTIVE=0
  fi
  END_LIVE_VIEW=0
}

resolve_native_resolution() {
  (( NATIVE_RESOLUTION == 1 )) || return 0
  local cache="${XDG_CONFIG_HOME:-$HOME/.config}/canon-webcam/native-size.json"
  local cached="" measured="" dimensions=""
  if [[ -r "$cache" ]]; then
    cached="$(/usr/bin/python3 -c 'import json,sys
try:
 d=json.load(open(sys.argv[1])); w,h=d["width"],d["height"]
 if d.get("live_view_size")==int(sys.argv[2]) and d.get("capture_backend",0)==int(sys.argv[3]) and 16<=w<=16384 and 16<=h<=16384 and not(w%2 or h%2): print(w,h)
except (OSError,ValueError,KeyError,TypeError): pass' "$cache" "$LIVE_VIEW_SIZE" "$CAPTURE_BACKEND")"
  fi
  if [[ "$cached" =~ ^([0-9]+)[[:space:]]([0-9]+)$ ]]; then
    WIDTH="${BASH_REMATCH[1]}"; HEIGHT="${BASH_REMATCH[2]}"
    log "Using last detected native size ${WIDTH}x${HEIGHT}"
    if [[ "${CANON_WEBCAM_IN_SERVICE:-0}" == 1 && -n "${XDG_RUNTIME_DIR:-}" ]]; then
      mkdir -p "$XDG_RUNTIME_DIR/canon-webcam"
      /usr/bin/python3 - "$cache" "$XDG_RUNTIME_DIR/canon-webcam/camera.json" <<'CACHED'
from pathlib import Path
import json,sys
value=json.loads(Path(sys.argv[1]).read_text()); value['cached']=True
Path(sys.argv[2]).write_text(json.dumps(value))
CACHED
    fi
    return 0
  fi
  if ! timeout --kill-after=1 3 gphoto2 --auto-detect | awk 'NR > 2 && NF { found = 1 } END { exit !found }'; then
    log "Waiting for a camera before detecting native size"
    return 0
  fi
  stop_camera_mount_helpers
  camera_prepare_live_view
  if (( CAMERA_RESPONSIVE == 1 )); then
    measured="$(/usr/bin/python3 "$(runtime_helper_path)" probe)"
    dimensions="$(printf '%s' "$measured" | /usr/bin/python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["width"], d["height"]) if d.get("width") else None')"
    if [[ "$dimensions" =~ ^([0-9]+)[[:space:]]([0-9]+)$ ]]; then
      WIDTH="${BASH_REMATCH[1]}"; HEIGHT="${BASH_REMATCH[2]}"
      CAMERA_READY=1
      mkdir -p "$(dirname "$cache")"
      printf '%s' "$measured" | /usr/bin/python3 -c 'import json,sys; d=json.load(sys.stdin); d["live_view_size"]=int(sys.argv[1]); d["capture_backend"]=int(sys.argv[2]); d["cached"]=False; print(json.dumps(d))' "$LIVE_VIEW_SIZE" "$CAPTURE_BACKEND" >"$cache.tmp"
      mv "$cache.tmp" "$cache"
      if [[ "${CANON_WEBCAM_IN_SERVICE:-0}" == 1 && -n "${XDG_RUNTIME_DIR:-}" ]]; then
        mkdir -p "$XDG_RUNTIME_DIR/canon-webcam"
        cp "$cache" "$XDG_RUNTIME_DIR/canon-webcam/camera.json"
      fi
      log "Detected native live-view size ${WIDTH}x${HEIGHT}"
    fi
  fi
  if (( ON_DEMAND == 1 )); then camera_end_live_view; fi
}

terminate_process() {
  local pid="$1"

  [[ -n "$pid" ]] || return
  kill "$pid" >/dev/null 2>&1 || true
  local check
  for (( check=0; check<40; check++ )); do
    if ! kill -0 "$pid" >/dev/null 2>&1; then break; fi
    sleep 0.1
  done
  kill -KILL "$pid" >/dev/null 2>&1 || true
  wait "$pid" >/dev/null 2>&1 || true
}

stop_orphaned_test_source_feeders() {
  local pattern

  command -v pkill >/dev/null 2>&1 || return
  pattern="ffmpeg .*testsrc2=size=${WIDTH}x${HEIGHT}:rate=${CAMERA_FPS}.* -f image2pipe /tmp/tmp"
  pkill -TERM -f "$pattern" >/dev/null 2>&1 || true
  sleep 0.5
  pkill -KILL -f "$pattern" >/dev/null 2>&1 || true
}

stop_orphaned_stream_processes() {
  local pattern
  local patterns=(
    "gphoto2 --stdout --capture-movie"
    "ffmpeg .* -f v4l2 ${DEVICE}"
    "ffmpeg .*testsrc2=size=${WIDTH}x${HEIGHT}:rate=.* -f image2pipe /tmp/tmp"
  )

  command -v pkill >/dev/null 2>&1 || return

  for pattern in "${patterns[@]}"; do
    pkill -TERM -f "$pattern" >/dev/null 2>&1 || true
  done

  sleep 0.5

  for pattern in "${patterns[@]}"; do
    pkill -KILL -f "$pattern" >/dev/null 2>&1 || true
  done
}

run_test_source_ffmpeg() {
  ffmpeg \
    -hide_banner \
    -loglevel warning \
    -re \
    -f lavfi \
    -i "testsrc2=size=${WIDTH}x${HEIGHT}:rate=${FPS}" \
    -vf format=yuv420p \
    -f v4l2 \
    "$DEVICE"
}

run_test_source_feeder_ffmpeg() {
  local message="${1:-Waiting for a video app}"
  local title_size=$(( WIDTH / 44 )) message_size=$(( WIDTH / 28 )) detail_size=$(( WIDTH / 48 ))
  local detail="Select Camera-Bridge in your video app to begin." icon=idle frames=3 rate="$FPS"
  local pacing=() animation="" icon_size=$(( HEIGHT / 5 ))
  local asset_dir="${CANON_WEBCAM_ASSET_DIR:-}"
  [[ -n "$asset_dir" ]] || asset_dir="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/assets"
  [[ -f "$asset_dir/status-idle.png" ]] || asset_dir=/usr/share/canon-webcam
  case "$message" in
    "Camera kept off") detail="Use the tray controls to start video." ;;
    "Getting video ready...") detail="Checking the camera connection..." ;;
    "Camera not responding") icon=warning; detail="Try video mode or turn camera off and on. Check USB." ;;
    "Camera disconnected") icon=disconnected; detail="Check the USB connection and camera power." ;;
    "Lost connection") icon=disconnected; detail="Check the connection or restart the camera." ;;
    "Starting camera...")
      icon=starting; detail="Preparing live view..."
      # A bounded two-second pulse, interrupted as soon as capture takes over.
      # Resting and failure cards remain still, including quiet recovery attempts.
      frames=12; rate=6; pacing=(-re)
      animation=",drawbox=x=iw/2-4:y=ih*0.77:w=8:h=8:color=0xa8c6b5:t=fill:enable='lt(mod(n,6),3)'"
      ;;
  esac
  (( detail_size >= 12 )) || detail_size=12
  (( title_size >= 14 )) || title_size=14
  (( message_size >= 16 )) || message_size=16
  # Symmetric symbols remain recognizable in mirrored local previews. Text is
  # intentionally normal: video apps usually mirror only the user's own preview.
  # After a bounded update, the kernel repeats the final image without encoding.
  ffmpeg \
    -hide_banner -y -loglevel error -nostdin \
    "${pacing[@]}" -f lavfi -i "color=c=0x161b22:size=${WIDTH}x${HEIGHT}:rate=${rate}" \
    -loop 1 -i "$asset_dir/status-${icon}.png" \
    -filter_complex_threads 1 \
    -filter_complex "[1:v]scale=${icon_size}:${icon_size}[icon];[0:v][icon]overlay=(W-w)/2:H*0.20,drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:text='Camera Bridge':fontcolor=0x9ca9bb:fontsize=${title_size}:x=(w-text_w)/2:y=h*0.45,drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:text='${message}':fontcolor=white:fontsize=${message_size}:x=(w-text_w)/2:y=h*0.53,drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:text='${detail}':fontcolor=0x9ca9bb:fontsize=${detail_size}:x=(w-text_w)/2:y=h*0.64${animation},format=yuvj420p" \
    -frames:v "$frames" -threads 1 -vcodec mjpeg -q:v 3 -f image2pipe "$CAMERA_FIFO"
}

start_loopback_writer() {
  local input_timing=(-framerate "$CAMERA_FPS")
  local video_output=()
  local output_timing=(-r "$FPS")
  if (( AUTO_FPS == 1 )); then
    # Wallclock timing preserves variable camera arrival intervals.
    # Passthrough avoids creating catch-up frames after standby gaps.
    input_timing=(-use_wallclock_as_timestamps 1 -framerate "$FPS")
    output_timing=(-fps_mode passthrough -enc_time_base 1/1000000)
  fi
  if [[ -n "${LOOPBACK_WRITER_PID:-}" ]] && kill -0 "$LOOPBACK_WRITER_PID" >/dev/null 2>&1; then
    return
  fi

  stop_orphaned_test_source_feeders

  CAMERA_FIFO_DIR="$(mktemp -d)"
  CAMERA_FIFO="$CAMERA_FIFO_DIR/camera.mjpeg"
  mkfifo "$CAMERA_FIFO"

  exec {CAMERA_FIFO_HOLD_FD}<>"$CAMERA_FIFO"

  video_output=(-filter_threads 1 -vf "scale=${WIDTH}:${HEIGHT}:force_original_aspect_ratio=decrease,pad=${WIDTH}:${HEIGHT}:(ow-iw)/2:(oh-ih)/2,format=yuv420p" "${output_timing[@]}" -threads 1 -flush_packets 1 -vcodec rawvideo -pix_fmt yuv420p)
  if (( PASSTHROUGH == 1 )); then
    video_output=(-c:v copy -flush_packets 1)
  fi

  log "Starting persistent ffmpeg writer for $DEVICE at ${WIDTH}x${HEIGHT}/${FPS}fps output"
  ffmpeg \
    -hide_banner \
    -loglevel error \
    -nostdin \
    -fflags nobuffer \
    -flags low_delay \
    -probesize 32 \
    -analyzeduration 0 \
    -fpsprobesize 0 \
    -f mjpeg \
    -vcodec mjpeg \
    "${input_timing[@]}" \
    -threads 1 \
    -thread_queue_size 1 \
    -i "$CAMERA_FIFO" \
    "${video_output[@]}" \
    -f v4l2 \
    "$DEVICE" &
  LOOPBACK_WRITER_PID="$!"

  sleep 0.5
  if ! kill -0 "$LOOPBACK_WRITER_PID" >/dev/null 2>&1; then
    stop_camera_stream_processes
    die "ffmpeg writer exited before it could write to $DEVICE"
  fi
}

start_background_test_source() {
  local message="${1:-Waiting for a video app}"
  start_loopback_writer
  if [[ "$LAST_STATUS_FRAME" == "$message" ]]; then return 0; fi
  stop_background_test_source
  LAST_STATUS_FRAME="$message"
  write_stream_state "$message"
  log "Showing status card: $message"
  run_test_source_feeder_ffmpeg "$message" &
  TEST_SOURCE_PID="$!"
}

stop_background_test_source() {
  if [[ -n "${TEST_SOURCE_PID:-}" ]]; then
    if kill -0 "$TEST_SOURCE_PID" >/dev/null 2>&1; then terminate_process "$TEST_SOURCE_PID"; fi
    wait "$TEST_SOURCE_PID" >/dev/null 2>&1 || true
    TEST_SOURCE_PID=""
  fi
  LAST_STATUS_FRAME=""
}

stream_test_source() {
  need_cmd ffmpeg

  ensure_loopback_for_writer
  log "Streaming generated test pattern to $DEVICE at ${WIDTH}x${HEIGHT}/${FPS}fps"
  log "Open Zoom and select '${CARD_LABEL}' or '$DEVICE'. Press Ctrl+C to stop."

  run_test_source_ffmpeg
}

stop_camera_stream_processes() {
  stop_background_test_source

  if [[ -n "${CAMERA_GPHOTO_PID:-}" ]]; then
    terminate_process "$CAMERA_GPHOTO_PID"
  fi

  if [[ -n "${CAMERA_FIFO_HOLD_FD:-}" ]]; then
    eval "exec ${CAMERA_FIFO_HOLD_FD}>&-"
    CAMERA_FIFO_HOLD_FD=""
  fi

  if [[ -n "${LOOPBACK_WRITER_PID:-}" ]]; then
    terminate_process "$LOOPBACK_WRITER_PID"
  fi

  CAMERA_GPHOTO_PID=""
  LOOPBACK_WRITER_PID=""

  if [[ -n "${CAMERA_FIFO:-}" ]]; then
    rm -f "$CAMERA_FIFO"
    CAMERA_FIFO=""
  fi

  if [[ -n "${CAMERA_FIFO_DIR:-}" ]]; then
    rmdir "$CAMERA_FIFO_DIR" >/dev/null 2>&1 || true
    CAMERA_FIFO_DIR=""
  fi
}

runtime_helper_path() {
  local adjacent
  adjacent="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/canon-webcam-runtime.py"
  if [[ -f "$adjacent" ]]; then
    printf '%s\n' "$adjacent"
  else
    printf '%s\n' /usr/share/canon-webcam/canon-webcam-runtime.py
  fi
}

pause_encoding() {
  CAMERA_IDLE=1
  if [[ -n "${CAMERA_GPHOTO_PID:-}" ]]; then
    terminate_process "$CAMERA_GPHOTO_PID"
  fi
}

wait_for_camera_retry() {
  # Bash defers traps while waiting for a foreground sleep. Waiting on a child
  # instead lets an app-open/app-close signal interrupt this retry immediately.
  local delay_pid
  sleep "$CAMERA_RETRY_DELAY" &
  delay_pid=$!
  wait "$delay_pid" || true
  kill "$delay_pid" >/dev/null 2>&1 || true
  wait "$delay_pid" 2>/dev/null || true
}

standby_encoding() {
  local saw_absent=0 wants=1 current=""
  local retry_at=$(( SECONDS + CAMERA_FAILURE_DELAY ))
  # Give the persistent writer a fallback image, then stop generating frames.
  # sustain_framerate repeats that image in the kernel while ffmpeg waits on FIFO.
  local card="Waiting for a video app"
  if (( ON_DEMAND == 1 )); then wants=0; fi
  if [[ -r "${CANON_WEBCAM_DEMAND_FILE:-}" ]]; then
    read -r wants <"$CANON_WEBCAM_DEMAND_FILE" || true
  fi
  if [[ -f "${CANON_WEBCAM_PAUSE_FILE:-}" ]]; then
    card="Camera kept off"
  elif (( CAMERA_RECOVERY_WAIT == 1 )) || [[ "$wants" == 1 ]]; then
    if timeout --kill-after=1 3 gphoto2 --auto-detect | awk 'NR > 2 && NF { found = 1 } END { exit !found }'; then
      card="Camera not responding"
    else
      card="Camera disconnected"
    fi
  fi
  start_background_test_source "$card"
  if [[ -n "$TEST_SOURCE_PID" ]]; then wait "$TEST_SOURCE_PID" || true; fi
  stop_background_test_source
  write_stream_state "Standby: $card. Virtual webcam ready."
  log "Video paused; virtual webcam remains available. Waiting for an app."
  while (( CAMERA_IDLE == 1 )); do
    if (( END_LIVE_VIEW == 1 )); then camera_end_live_view; fi
    if [[ -f "${CANON_WEBCAM_PAUSE_FILE:-}" && "$card" != "Camera kept off" ]]; then
      card="Camera kept off"
      start_background_test_source "$card"
      write_stream_state "Standby: $card. Virtual webcam ready."
    elif [[ ! -f "${CANON_WEBCAM_PAUSE_FILE:-}" && "$card" == "Camera kept off" ]]; then
      card="Waiting for a video app"
      start_background_test_source "$card"
      write_stream_state "Standby: $card. Virtual webcam ready."
    fi
    wants=1
    if (( ON_DEMAND == 1 )); then
      wants=0
      if [[ -r "${CANON_WEBCAM_DEMAND_FILE:-}" ]]; then read -r wants <"$CANON_WEBCAM_DEMAND_FILE" || true; fi
    fi
    if [[ "$wants" == 1 ]]; then
      current="$(timeout --kill-after=1 3 gphoto2 --auto-detect | awk 'NR > 2 && NF { print; exit }')" || current=""
      if [[ -z "$current" ]]; then
        saw_absent=1
      elif (( saw_absent == 1 )) || [[ -n "$LAST_CAMERA_ID" && "$current" != "$LAST_CAMERA_ID" ]] || (( CAMERA_RECOVERY_WAIT == 1 && SECONDS >= retry_at )); then
        CAMERA_IDLE=0
        log "Retrying camera connection in the background"
        if [[ -n "${CANON_WEBCAM_ACTIVITY_FILE:-}" ]]; then
          /usr/bin/python3 -c 'import sys,time; from pathlib import Path; Path(sys.argv[1]).write_text(str(time.monotonic()))' "$CANON_WEBCAM_ACTIVITY_FILE"
        fi
      fi
    fi
    if (( CAMERA_IDLE == 1 )); then wait_for_camera_retry; fi
  done
}

run_camera_stream_once() {
  (( CAMERA_IDLE == 0 )) || return 0
  local status
  local capture=(gphoto2 --stdout --capture-movie)

  start_loopback_writer

  if (( CAMERA_SEGMENT_SECONDS > 0 )); then
    capture=(gphoto2 --stdout "--capture-movie=${CAMERA_SEGMENT_SECONDS}s")
  fi
  local capture_helper
  capture_helper="$(dirname "$(runtime_helper_path)")/canon-webcam-capture.py"
  if [[ -f "$capture_helper" ]]; then
    capture=(/usr/bin/python3 "$capture_helper" --seconds "$CAMERA_SEGMENT_SECONDS")
  fi
  # Only the relay can confirm that a complete camera frame has arrived.
  if (( CAMERA_RECOVERY_WAIT == 0 )); then write_stream_state "Starting camera..."; fi
  if [[ -n "${CANON_WEBCAM_ACTIVITY_FILE:-}" ]]; then
    CANON_WEBCAM_LIVE_VIEW_SIZE="$LIVE_VIEW_SIZE" /usr/bin/python3 "$(runtime_helper_path)" relay "$CAMERA_FIFO" "${capture[@]}" &
  else
    "${capture[@]}" >"$CAMERA_FIFO" &
  fi
  CAMERA_GPHOTO_PID="$!"
  if (( CAMERA_IDLE == 1 )); then terminate_process "$CAMERA_GPHOTO_PID"; fi

  # Control signals can interrupt Bash wait before the capture process exits.
  # Keep tracking that process until it really exits; otherwise it can retain USB.
  while true; do
    if wait "$CAMERA_GPHOTO_PID"; then status=0; else status="$?"; fi
    if ! kill -0 "$CAMERA_GPHOTO_PID" >/dev/null 2>&1; then break; fi
  done

  if [[ -r "${CANON_WEBCAM_FRAME_STATS:-}" ]]; then
    if /usr/bin/python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("frames",0)>0 else 1)' "$CANON_WEBCAM_FRAME_STATS"; then
      CAMERA_RECOVERY_WAIT=0
    fi
  fi
  CAMERA_GPHOTO_PID=""
  return "$status"
}

write_stream_state() {
  [[ "${CANON_WEBCAM_IN_SERVICE:-0}" == 1 && -n "${XDG_RUNTIME_DIR:-}" ]] || return 0
  mkdir -p "$XDG_RUNTIME_DIR/canon-webcam"
  printf '%s\n' "$1" >"$XDG_RUNTIME_DIR/canon-webcam/status"
}

cleanup_stream() {
  trap - EXIT INT TERM USR1 USR2 ALRM HUP
  stop_camera_stream_processes
  camera_end_live_view
  write_stream_state "Stopped"
}

stream_camera() {
  local backoff_delay="$CAMERA_FAILURE_DELAY"
  local normal_segment_min retry_after started_at status stream_duration

  trap cleanup_stream EXIT
  trap 'cleanup_stream; exit 0' INT TERM
  trap pause_encoding USR1
  trap 'CAMERA_IDLE=0; CAMERA_RECOVERY_WAIT=0' HUP
  trap 'CAMERA_IDLE=0; CAMERA_RECOVERY_WAIT=0' USR2
  trap 'END_LIVE_VIEW=1' ALRM

  need_cmd gphoto2
  need_cmd ffmpeg
  export CANON_WEBCAM_CAPTURE_BACKEND="$CAPTURE_BACKEND" CANON_WEBCAM_LIVE_VIEW_SIZE="$LIVE_VIEW_SIZE" CANON_WEBCAM_SET_VIEWFINDER="$SET_VIEWFINDER"
  write_live_view_state unknown
  write_stream_state "Getting webcam ready"

  [[ -c "$DEVICE" ]] || die "$DEVICE does not exist. Run 'camera-bridge install' and reboot if the loopback module was just built."
  ensure_loopback_for_writer
  resolve_native_resolution

  log "Select '${CARD_LABEL}' or '$DEVICE' in your video app. Press Ctrl+C to stop foreground streaming."
  start_background_test_source "Waiting for a video app"
  sleep 0.5
  kill -0 "$LOOPBACK_WRITER_PID" >/dev/null 2>&1 || die "virtual webcam writer failed after its first frame; check logs"
  v4l2-ctl --device "$DEVICE" --set-ctrl keep_format=1 >/dev/null 2>&1 || true
  if [[ -n "${CANON_WEBCAM_CONTROL_READY:-}" ]]; then touch "$CANON_WEBCAM_CONTROL_READY"; fi

  while true; do
    if (( CAMERA_IDLE == 1 )); then
      standby_encoding
    fi
    if (( CAMERA_IDLE == 0 && CAMERA_RECOVERY_WAIT == 0 )); then
      start_background_test_source "Getting video ready..."
    fi
    stop_camera_mount_helpers

    if ! camera_wait_stable; then
      log "Waiting for a camera; virtual webcam remains available"
      start_background_test_source "Camera disconnected"
      CAMERA_RECOVERY_WAIT=1
      CAMERA_IDLE=1
      continue
    fi

    (( CAMERA_IDLE == 0 )) || continue
    if (( CAMERA_RECOVERY_WAIT == 0 )); then start_background_test_source "Starting camera..."; fi
    stop_camera_mount_helpers
    camera_prepare_live_view
    if (( CAMERA_RESPONSIVE == 0 )); then
      CAMERA_RECOVERY_WAIT=1
      CAMERA_IDLE=1
      start_background_test_source "Camera not responding"
      continue
    fi
    stop_camera_mount_helpers

    (( CAMERA_IDLE == 0 )) || continue
    stop_background_test_source
    (( CAMERA_IDLE == 0 )) || continue

    if (( CAMERA_SEGMENT_SECONDS == 0 )); then
      log "Streaming Canon live view to $DEVICE at ${WIDTH}x${HEIGHT}/${CAMERA_FPS}fps camera input, ${FPS}fps webcam output"
      started_at="$(date +%s)"

      if run_camera_stream_once; then
        status=0
      else
        status="$?"
      fi

      stream_duration=$(( $(date +%s) - started_at ))
    else
      normal_segment_min=1
      if (( CAMERA_SEGMENT_SECONDS > 1 )); then
        normal_segment_min=$(( CAMERA_SEGMENT_SECONDS - 1 ))
      fi

      while (( CAMERA_IDLE == 0 )); do
        log "Streaming Canon live view to $DEVICE at ${WIDTH}x${HEIGHT}/${CAMERA_FPS}fps camera input, ${FPS}fps webcam output"
        started_at="$(date +%s)"

        if run_camera_stream_once; then
          status=0
        else
          status="$?"
        fi

        stream_duration=$(( $(date +%s) - started_at ))
        if [[ "$status" -eq 0 && "$stream_duration" -ge "$normal_segment_min" ]]; then
          backoff_delay="$CAMERA_FAILURE_DELAY"
          log "Canon segment ended after ${stream_duration}s; restarting capture immediately"
          continue
        fi

        break
      done
    fi

    (( CAMERA_IDLE == 0 )) || continue

    if (( stream_duration >= 60 )); then
      backoff_delay="$CAMERA_FAILURE_DELAY"
    fi

    retry_after="$backoff_delay"
    if [[ "$status" -eq 0 ]]; then
      warn "Canon stream ended after ${stream_duration}s; retrying in ${retry_after}s"
    else
      warn "Canon stream failed with status $status after ${stream_duration}s; retrying in ${retry_after}s"
    fi

    if (( stream_duration < 60 )); then
      backoff_delay=$(( backoff_delay * 2 ))
      if (( backoff_delay > CAMERA_MAX_FAILURE_DELAY )); then
        backoff_delay="$CAMERA_MAX_FAILURE_DELAY"
      fi
    fi

    if timeout --kill-after=1 3 gphoto2 --auto-detect | awk 'NR > 2 && NF { found = 1 } END { exit !found }'; then
      start_background_test_source "Camera not responding"
    else
      start_background_test_source "Camera disconnected"
    fi
    CAMERA_RECOVERY_WAIT=1
    CAMERA_IDLE=1
    log "Recovery active; retrying while an app needs the camera without changing the visible warning"
  done
}

service_start() {
  if [[ "${CANON_WEBCAM_PREVIEW:-0}" != 1 ]]; then ensure_runtime_installed_current; fi

  if systemctl_user is-active "$SERVICE_NAME" >/dev/null 2>&1; then
    if (( ON_DEMAND == 0 )); then
      log "Resuming encoding on the existing virtual webcam"
      systemctl_user kill --kill-whom=main --signal=SIGUSR2 "$SERVICE_NAME"
    else
      log "Automatic webcam is enabled; open it in an app to start capture"
    fi
    return 0
  fi

  ensure_loopback_for_writer
  systemctl_user reset-failed "$SERVICE_NAME" >/dev/null 2>&1 || true
  log "Starting $SERVICE_NAME"
  systemctl_user start "$SERVICE_NAME"
}

signal_camera_control() {
  local signal="$1" attempt pid ready_pid
  local marker="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/canon-webcam/control-pid"
  # A Type=simple service can be active before Python installs its handlers.
  # Wait for this exact main process, preventing an early signal from killing it.
  for (( attempt = 0; attempt < 50; attempt++ )); do
    pid="$(systemctl_user show "$SERVICE_NAME" --property=MainPID --value)"
    ready_pid=""
    if [[ -r "$marker" ]]; then read -r ready_pid <"$marker" || true; fi
    if [[ "$pid" =~ ^[1-9][0-9]*$ && "$pid" == "$ready_pid" ]]; then
      systemctl_user kill --kill-whom=main --signal="$signal" "$SERVICE_NAME"
      return
    fi
    sleep 0.1
  done
  die "Camera controls are not ready. Turn the webcam off and on, then try again."
}

service_stop() {
  log "Stopping $SERVICE_NAME"
  systemctl_user stop "$SERVICE_NAME"
}

service_restart() {
  if [[ "${CANON_WEBCAM_PREVIEW:-0}" == 1 ]]; then
    systemctl_user restart "$SERVICE_NAME"
    return
  fi
  service_stop >/dev/null 2>&1 || true
  service_start
}

service_hard_reset() {
  log "Stopping $SERVICE_NAME"
  service_stop >/dev/null 2>&1 || true

  log "Clearing stale Canon webcam capture processes"
  stop_orphaned_stream_processes

  log "Reloading v4l2loopback for $DEVICE"
  reset_loopback

  service_start
}

service_status() {
  systemctl_user status "$SERVICE_NAME" --no-pager
}

service_logs() {
  journalctl --user -u "$SERVICE_NAME" -f
}

launcher_start() {
  if service_start; then
    notify_desktop "Camera Bridge" "Started/resumed. Select ${CARD_LABEL}. Apps opened before the webcam started may need restarting."
  else
    notify_desktop "Camera Bridge" "Start failed. Run camera-bridge logs for details."
    return 1
  fi
}

launcher_stop() {
  if service_stop; then
    notify_desktop "Camera Bridge" "Stopped."
  else
    notify_desktop "Camera Bridge" "Stop failed. Run camera-bridge status for details."
    return 1
  fi
}

launcher_status() {
  local service_file="$HOME/.config/systemd/user/$SERVICE_NAME"

  if systemctl_user is-active "$SERVICE_NAME" >/dev/null 2>&1; then
    notify_desktop "Camera Bridge" "Running. Select ${CARD_LABEL} in your video app."
  elif [[ -f "$service_file" ]]; then
    notify_desktop "Camera Bridge" "Stopped."
  else
    notify_desktop "Camera Bridge" "Not installed. Run camera-bridge install first."
    return 1
  fi
}

doctor() {
  local problems=0
  local service_file="$HOME/.config/systemd/user/$SERVICE_NAME"
  local service_active=0
  local self
  local v4l_output=""

  if systemctl_user is-active "$SERVICE_NAME" >/dev/null 2>&1; then
    service_active=1
  fi

  printf 'System:\n'
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    source /etc/os-release
    printf '  OS: %s\n' "${PRETTY_NAME:-unknown}"
  fi
  printf '  Kernel: %s\n' "$(uname -r)"

  printf '\nCommands:\n'
  for cmd in gphoto2 ffmpeg v4l2-ctl; do
    if command -v "$cmd" >/dev/null 2>&1; then
      printf '  ok: %s -> %s\n' "$cmd" "$(command -v "$cmd")"
    else
      printf '  missing: %s\n' "$cmd"
      problems=1
    fi
  done

  self="$(readlink -f "${BASH_SOURCE[0]}")"
  if [[ "$self" != "$INSTALL_PATH" ]]; then
    if [[ -x "$INSTALL_PATH" ]] && cmp -s "$self" "$INSTALL_PATH"; then
      printf '  ok: installed command is current -> %s\n' "$INSTALL_PATH"
    elif [[ -x "$INSTALL_PATH" ]]; then
      printf '  missing: installed command differs from this script; run ./canon-webcam.sh install\n'
      problems=1
    else
      printf '  missing: installed command is not present; run ./canon-webcam.sh install\n'
      problems=1
    fi
  fi

  printf '\nLoopback:\n'
  if loopback_loaded; then
    printf '  ok: v4l2loopback module is loaded\n'
  else
    printf '  missing: v4l2loopback module is not loaded\n'
    problems=1
  fi

  if [[ -c "$DEVICE" ]]; then
    printf '  ok: %s exists\n' "$DEVICE"
  else
    printf '  missing: %s does not exist\n' "$DEVICE"
    problems=1
  fi

  if [[ -c "$DEVICE" ]] && command -v v4l2-ctl >/dev/null 2>&1; then
    if v4l2-ctl --device "$DEVICE" --get-fmt-video >/dev/null 2>&1; then
      printf '  ok: %s has an active video format\n' "$DEVICE"
    elif loopback_writer_ready; then
      printf '  ok: %s is ready for ffmpeg input\n' "$DEVICE"
    else
      printf '  missing: %s is not writable by ffmpeg; run camera-bridge reset-loopback\n' "$DEVICE"
      problems=1
    fi
  fi

  if command -v v4l2-ctl >/dev/null 2>&1; then
    printf '\nVideo devices:\n'
    if v4l_output="$(v4l2-ctl --list-devices 2>&1)"; then
      printf '%s\n' "$v4l_output"
    else
      printf '  unavailable: %s\n' "$v4l_output"
    fi
  fi

  printf '\nCamera:\n'
  if command -v gphoto2 >/dev/null 2>&1; then
    if [[ "$service_active" -eq 1 ]]; then
      printf '  skipped: %s is active and may own the camera PTP session\n' "$SERVICE_NAME"
      printf '  hint: run camera-bridge logs to inspect live capture, or stop the service before direct gphoto2 checks\n'
    else
      gphoto2 --auto-detect || true
      if camera_detected; then
        printf '  ok: gphoto2 sees a camera\n'
        if timeout --kill-after=1 8 gphoto2 --summary >/dev/null 2>&1; then
          printf '  ok: camera responds to PTP commands\n'
        else
          printf '  missing: camera is detected but not responding to PTP commands; power-cycle it and reconnect USB\n'
          problems=1
        fi
      else
        printf '  missing: gphoto2 does not see a camera\n'
        problems=1
      fi
    fi
  else
    printf '  skipped: gphoto2 is not installed\n'
  fi

  printf '\nService:\n'
  if [[ -f "$service_file" ]]; then
    systemctl_user is-enabled "$SERVICE_NAME" >/dev/null 2>&1 && printf '  enabled: yes\n' || printf '  enabled: no\n'
    [[ "$service_active" -eq 1 ]] && printf '  active: yes\n' || printf '  active: no\n'
  else
    printf '  missing: %s is not installed\n' "$SERVICE_NAME"
  fi

  if [[ "$problems" -ne 0 ]]; then
    return 1
  fi
}

uninstall_all() {
  require_not_root
  need_cmd sudo
  [[ "$INSTALL_PATH" != /usr/bin/camera-bridge ]] || die "installed by Debian package; remove with sudo apt remove camera-bridge"

  systemctl_user stop "$SERVICE_NAME" >/dev/null 2>&1 || true
  systemctl_user disable "$SERVICE_NAME" >/dev/null 2>&1 || true
  rm -f "$HOME/.config/systemd/user/$SERVICE_NAME"
  systemctl_user daemon-reload >/dev/null 2>&1 || true
  remove_desktop_launchers
  rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/canon-webcam-tray.desktop"

  sudo rm -f \
    "$INSTALL_PATH" \
    "$GUI_PATH" \
    /usr/share/canon-webcam/canon-webcam-runtime.py \
    /usr/share/canon-webcam/canon-webcam-capture.py \
    /usr/share/canon-webcam/canon_webcam_controls.py \
    /usr/lib/camera-bridge/libEDSDK.so \
    /usr/share/canon-webcam/status-{idle,starting,warning,disconnected}.png \
    /usr/share/canon-webcam/warning.svg \
    /usr/share/canon-webcam/tray-ready.svg \
    /usr/share/canon-webcam/tray-live.svg \
    /usr/share/canon-webcam/tray-live-dim.svg \
    /usr/share/canon-webcam/tray-off.svg \
    /usr/share/canon-webcam/tray-{connected,awake,starting,warning}.svg \
    /usr/share/icons/hicolor/scalable/apps/camera-bridge.svg \
    /etc/modules-load.d/canon-webcam.conf \
    /etc/modprobe.d/canon-webcam.conf

  log "Removed installed files. Packages and any loaded v4l2loopback module were left in place."
}

main() {
  local command="${1:-help}"
  if [[ $# -gt 0 ]]; then
    shift
  fi

  # Read data only: never execute settings as shell code.
  local key value
  local config="${XDG_CONFIG_HOME:-$HOME/.config}/canon-webcam/settings.conf"
  if [[ -f "$config" && "$command" =~ ^(install|stream|setup-user|test-source)$ ]]; then
    while IFS='=' read -r key value; do
      case "$key" in
        WIDTH|HEIGHT|FPS|CAMERA_FPS|AUTO_TIMEOUT|SET_VIEWFINDER|ON_DEMAND|NATIVE_RESOLUTION|LIVE_VIEW_SIZE|AUTO_FPS|PASSTHROUGH|CAPTURE_BACKEND)
          [[ "$value" =~ ^(0|[1-9][0-9]{0,7})$ ]] || die "invalid setting: $key"
          printf -v "$key" '%s' "$value"
          ;;
      esac
    done <"$config"
  fi
  parse_args "$@"

  case "$command" in
    install)
      install_all
      ;;
    tray|settings)
      need_cmd python3
      local gui="$GUI_PATH"
      [[ -f "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/canon-webcam-gui.py" ]] && gui="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/canon-webcam-gui.py"
      exec /usr/bin/python3 "$gui" "$command"
      ;;
    setup-user)
      require_not_root
      install_user_service
      install_desktop_launchers
      ;;
    stream)
      if [[ "${CANON_WEBCAM_WATCHED:-0}" != 1 ]] && { (( ON_DEMAND == 1 || AUTO_TIMEOUT > 0 )) || [[ "${CANON_WEBCAM_IN_SERVICE:-0}" == 1 ]]; }; then
        need_cmd python3
        export CANON_WEBCAM_DEVICE="$DEVICE" CANON_WEBCAM_ON_DEMAND="$ON_DEMAND"
        log "Automatic webcam controls active; virtual webcam stays open while idle"
        exec /usr/bin/python3 "$(runtime_helper_path)" watch "$AUTO_TIMEOUT" "$(readlink -f "${BASH_SOURCE[0]}")" stream "$@"
      fi
      stream_camera
      ;;
    retry)
      service_start
      signal_camera_control SIGHUP
      ;;
    force-start)
      service_start
      signal_camera_control SIGUSR2
      ;;
    force-stop)
      if systemctl_user is-active "$SERVICE_NAME" >/dev/null 2>&1; then
        signal_camera_control SIGUSR1
      fi
      ;;
    auto)
      service_start
      signal_camera_control SIGCONT
      ;;
    start)
      service_start
      ;;
    stop)
      service_stop
      ;;
    restart)
      service_restart
      ;;
    hard-reset)
      service_hard_reset
      ;;
    status)
      service_status
      ;;
    logs)
      service_logs
      ;;
    doctor)
      doctor
      ;;
    reset-loopback)
      reset_loopback
      ;;
    test-source)
      stream_test_source
      ;;
    install-launchers)
      install_desktop_launchers
      ;;
    remove-launchers)
      remove_desktop_launchers
      ;;
    launcher-start)
      launcher_start
      ;;
    launcher-stop)
      launcher_stop
      ;;
    launcher-status)
      launcher_status
      ;;
    uninstall)
      uninstall_all
      ;;
    help|-h|--help)
      usage
      ;;
    *)
      usage >&2
      die "unknown command: $command"
      ;;
  esac
}

main "$@"
