#!/usr/bin/env bash
# Isolated settings and a runtime-only unit; installed files are never updated.
set -Eeuo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
unit=canon-webcam-preview.service
if systemctl --user is-active --quiet canon-webcam.service; then
  echo 'Stop the installed canon-webcam service before starting the preview.' >&2
  exit 1
fi
if [[ "$(systemctl --user show "$unit" --property=LoadState --value)" != not-found ]]; then
  echo 'A preview unit already exists. Close its tray before starting another preview.' >&2
  exit 1
fi
runtime="${XDG_RUNTIME_DIR:?A systemd user session is required}"
preview="$(mktemp -d "$runtime/canon-webcam-preview.XXXXXX")"
unit_file="$runtime/systemd/user/$unit"
cleanup() {
  systemctl --user stop "$unit" >/dev/null 2>&1 || true
  rm -f "$unit_file"
  systemctl --user daemon-reload >/dev/null 2>&1 || true
  rm -rf "$preview"
}
trap cleanup EXIT
trap 'exit 0' INT TERM
export CANON_WEBCAM_PREVIEW=1 CANON_WEBCAM_SERVICE="$unit"
export CANON_WEBCAM_COMMAND="$root/canon-webcam.sh"
export XDG_CONFIG_HOME="$preview/config"
# Preserve a measured format when the operator is refreshing the preview UI.
if [[ -n "${CANON_WEBCAM_PREVIEW_SEED:-}" ]]; then
  mkdir -p "$XDG_CONFIG_HOME/canon-webcam"
  cp "$CANON_WEBCAM_PREVIEW_SEED"/*.json "$XDG_CONFIG_HOME/canon-webcam/" 2>/dev/null || true
  if [[ -f "$CANON_WEBCAM_PREVIEW_SEED/settings.conf" ]]; then
    cp "$CANON_WEBCAM_PREVIEW_SEED/settings.conf" "$XDG_CONFIG_HOME/canon-webcam/"
  fi
fi
/usr/bin/python3 - "$root" "$preview" "$unit_file" <<'PY'
from pathlib import Path
import sys
root, preview, unit_file = sys.argv[1:]
def quote(text):
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'
settings = ['CANON_WEBCAM_PREVIEW=1', 'CANON_WEBCAM_SERVICE=canon-webcam-preview.service',
            'CANON_WEBCAM_IN_SERVICE=1', 'XDG_CONFIG_HOME=' + preview + '/config']
path = Path(unit_file)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text('# Camera Bridge temporary preview\n[Unit]\nDescription=Camera Bridge temporary preview\n'
                '[Service]\nType=simple\nKillMode=mixed\nTimeoutStopSec=20\n' + ''.join('Environment=' + quote(value) + '\n' for value in settings)
                + 'ExecStart=' + quote(root + '/canon-webcam.sh') + ' stream\nRestart=on-failure\nRestartSec=3\n')
PY
systemctl --user daemon-reload
systemctl --user start "$unit"
printf 'Preview ready. Open Camera-Bridge in a video app. Quit the tray to stop and remove the preview.\n'
/usr/bin/python3 "$root/canon-webcam-gui.py" settings
