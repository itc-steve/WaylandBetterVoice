#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -eq 0 ]]; then
  echo "Refusing to run as root; this changes your user session." >&2
  exit 1
fi

confirm() {
  local answer
  read -r -p "$1 [y/N] " answer
  [[ ${answer:-} =~ ^[Yy]$ ]]
}

autostart="${HOME}/.config/autostart/vocalinux.desktop"
backup="${autostart}.bak"

printf '%s\n' "Opt-in Vocalinux migration. Nothing runs without confirmation."
if confirm "Disable app-vocalinux@autostart.service?"; then
  printf '%s\n' "==> Disabling app-vocalinux@autostart.service"
  systemctl --user disable app-vocalinux@autostart.service
else
  printf '%s\n' "Skipped service disable."
fi

if confirm "Back up Vocalinux autostart desktop file to ${backup}?"; then
  if [[ -e ${backup} || -L ${backup} ]]; then
    echo "Refusing to overwrite existing backup: ${backup}" >&2
  elif [[ -e ${autostart} || -L ${autostart} ]]; then
    printf '%s\n' "==> Moving ${autostart} to ${backup}"
    mv "${autostart}" "${backup}"
  else
    printf '%s\n' "Autostart file already absent; nothing to back up."
  fi
else
  printf '%s\n' "Skipped autostart backup."
fi

if confirm "Print manual Vocalinux removal command?"; then
  cat <<'EOF'
WARNING: keep python-pywhispercpp-cuda installed. It provides python-pywhispercpp
for WaylandBetterVoice and must not be removed or replaced by a CPU package.

Run manually only after verifying WaylandBetterVoice:
  sudo pacman -Rns vocalinux
EOF
else
  printf '%s\n' "Skipped removal command."
fi
