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
plugin_link="${HOME}/.config/noctalia/plugins/waylandbettervoice"
model_source="${HOME}/.local/share/vocalinux/models/whispercpp"
model_dir="${HOME}/.local/share/waylandbettervoice/models"
niri_source=/usr/share/waylandbettervoice/niri-keybinds.kdl
niri_snippet="${HOME}/.config/niri/cfg/waylandbettervoice.kdl"

announce() {
  printf '==> %s\n' "$*"
}

if "${check}"; then
  announce "Would build package: (cd ${script_dir} && makepkg --cleanbuild --clean --force)"
  announce "Would install resulting waylandbettervoice package with: sudo pacman -U <package>"
  announce "Would create ${HOME}/.config/noctalia/plugins and symlink ${plugin_link} -> ${plugin_source}"
  announce "Would symlink existing Whisper models from ${model_source} into ${model_dir}"
  announce "Would create ${HOME}/.config/niri/cfg and copy ${niri_source} to ${niri_snippet} if absent"
  printf '%s\n' "Would not enable service or edit niri config."
  exit 0
fi

announce "Building package in ${script_dir}"
(
  cd "${script_dir}"
  makepkg --cleanbuild --clean --force
)

package_file=$(find "${script_dir}" -maxdepth 1 -type f -name 'waylandbettervoice-*.pkg.tar.zst' -print -quit)
if [[ -z ${package_file} ]]; then
  echo "Package build completed without a waylandbettervoice package." >&2
  exit 1
fi
announce "Installing ${package_file} with pacman"
sudo pacman -U "${package_file}"

if [[ -L ${plugin_link} ]] && [[ $(readlink -f -- "${plugin_link}") == ${plugin_source} ]]; then
  announce "Plugin link already correct: ${plugin_link}"
elif [[ -e ${plugin_link} || -L ${plugin_link} ]]; then
  echo "Refusing to replace existing plugin path: ${plugin_link}" >&2
  exit 1
else
  announce "Creating plugin directory: ${HOME}/.config/noctalia/plugins"
  mkdir -p "${HOME}/.config/noctalia/plugins"
  announce "Creating plugin symlink: ${plugin_link} -> ${plugin_source}"
  ln -s "${plugin_source}" "${plugin_link}"
fi

if [[ -d ${model_source} ]]; then
  announce "Creating model directory: ${model_dir}"
  mkdir -p "${model_dir}"
  shopt -s nullglob
  for model in "${model_source}"/*.bin; do
    target="${model_dir}/$(basename "${model}")"
    if [[ -L ${target} ]] && [[ $(readlink -f -- "${target}") == ${model} ]]; then
      announce "Model link already correct: ${target}"
    elif [[ -e ${target} || -L ${target} ]]; then
      echo "Refusing to replace existing model path: ${target}" >&2
    else
      announce "Linking existing model: ${target} -> ${model}"
      ln -s "${model}" "${target}"
    fi
  done
else
  announce "No existing Vocalinux model directory found; skipping model links"
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
  systemctl --user enable --now waylandbettervoice.service
  Add: include "./cfg/waylandbettervoice.kdl"
  to ~/.config/niri/config.kdl, then reload niri.
EOF
