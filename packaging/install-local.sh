#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -eq 0 ]]; then
  echo "Refusing to run as root; use your desktop user." >&2
  exit 1
fi

check=false
case ${1:-} in
  '') ;;
  --check) check=true ;;
  *) echo "Usage: $0 [--check]" >&2; exit 2 ;;
esac

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
plugin_source=/usr/share/waylandbettervoice/noctalia-plugin
plugin_dest="${XDG_DATA_HOME:-${HOME}/.local/share}/noctalia/plugins/waylandbettervoice"
model_dir="${HOME}/.local/share/waylandbettervoice/models"
niri_source=/usr/share/waylandbettervoice/niri-keybinds.kdl
niri_snippet="${HOME}/.config/niri/cfg/waylandbettervoice.kdl"

announce() {
  printf '==> %s\n' "$*"
}

if "${check}"; then
  announce "Would build package: (cd ${script_dir} && makepkg --cleanbuild --clean --force)"
  announce "Would install resulting waylandbettervoice package with: sudo pacman -U <package>"
  announce "Would create ${plugin_dest} and copy ${plugin_source} (Noctalia v5)"
  announce "Would not enable the plugin or edit Noctalia config"
  announce "Would create ${model_dir}; if empty, run: wbv model download large-v3"
  announce "Would create ${HOME}/.config/niri/cfg and copy ${niri_source} to ${niri_snippet} if absent"
  printf '%s\n' "Would not enable service or edit niri config."
  exit 0
fi

announce "Building package in ${script_dir}"
(
  cd "${script_dir}"
  makepkg --cleanbuild --clean --force
)

package_file=$(cd "${script_dir}" && makepkg --packagelist | head -n 1)
if [[ -z ${package_file} ]]; then
  echo "Package build completed without a waylandbettervoice package." >&2
  exit 1
fi
announce "Installing ${package_file} with pacman"
sudo pacman -U "${package_file}"

if [[ -L ${plugin_dest} ]]; then
  if [[ $(readlink -f -- "${plugin_dest}") == ${plugin_source} ]]; then
    announce "Replacing package symlink with user-owned copy: ${plugin_dest}"
    rm -- "${plugin_dest}"
  else
    echo "Refusing to replace existing plugin path: ${plugin_dest}" >&2
    exit 1
  fi
elif [[ -e ${plugin_dest} && ! -d ${plugin_dest} ]]; then
  echo "Refusing to replace existing plugin path: ${plugin_dest}" >&2
  exit 1
fi
if [[ -e ${plugin_dest}/manifest.json || -e ${plugin_dest}/Main.qml ]]; then
  echo "Archive the old v4 plugin at ${plugin_dest} before installing v5." >&2
  exit 1
fi
announce "Creating Noctalia v5 plugin directory: ${plugin_dest}"
mkdir -p "${plugin_dest}"
announce "Copying plugin: ${plugin_source} -> ${plugin_dest}"
cp -r -- "${plugin_source}/." "${plugin_dest}/"

announce "Creating model directory: ${model_dir}"
mkdir -p "${model_dir}"
if [[ -z $(find "${model_dir}" -mindepth 1 -maxdepth 1 -print -quit) ]]; then
  printf '%s\n' "No model found. Run: wbv model download large-v3"
fi

if [[ -e ${niri_snippet} || -L ${niri_snippet} ]]; then
  announce "Niri snippet already exists; leaving it unchanged: ${niri_snippet}"
else
  announce "Creating niri snippet directory: ${HOME}/.config/niri/cfg"
  mkdir -p "${HOME}/.config/niri/cfg"
  announce "Copying niri snippet: ${niri_source} -> ${niri_snippet}"
  cp "${niri_source}" "${niri_snippet}"
fi

cat <<'EOF'

Next steps (not performed):
  wbv model download large-v3
  systemctl --user enable --now waylandbettervoice.service
  Add: include "./cfg/waylandbettervoice.kdl"
  to ~/.config/niri/config.kdl, then reload niri.
  noctalia msg config-reload
  noctalia msg plugins enable itc-steve/waylandbettervoice
  Add bar widget type: itc-steve/waylandbettervoice:mic
  Archive any old ~/.config/noctalia/plugins/waylandbettervoice v4 copy separately.
EOF
