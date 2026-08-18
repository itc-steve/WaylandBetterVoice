# WaylandBetterVoice — Noctalia plugin

Bar widget + panel + top overlay for the [WaylandBetterVoice](https://github.com/) dictation daemon.
Targets Noctalia shell **4.7.7** / quickshell **0.0.12**, niri, strict Wayland.

## Install path

Packaging installs the plugin tree to:

```
/usr/share/waylandbettervoice/noctalia-plugin/
```

`install-local.sh` (repo packaging) then symlinks it into:

```
~/.config/noctalia/plugins/waylandbettervoice
```

Manual (dev) symlink from the repo:

```bash
ln -sfn "$PWD/noctalia-plugin/waylandbettervoice" \
  ~/.config/noctalia/plugins/waylandbettervoice
```

Folder name **must** match `manifest.json` `id` (`waylandbettervoice`).

## Enable

1. Start the daemon: `wbv daemon` (or the user systemd unit).
2. Open Noctalia settings → Plugins → enable **WaylandBetterVoice**.
3. Add the bar widget from the bar editor (mic icon).
4. Optional: niri binds `Super+Space` (dictate) / `Super+Alt+Space` (meeting) — see packaging `niri-keybinds.kdl`.

## Load check

```bash
qs -c noctalia-shell
```

Expect no QML errors mentioning `waylandbettervoice`. With the daemon up, `$XDG_RUNTIME_DIR/waylandbettervoice/state.json` updates drive the bar tint + overlay.

`Main.qml` also reloads once per second until first read, then only after its file watcher has been quiet for three seconds. This recovers state files created after Noctalia starts and daemon restarts without polling healthy 10 Hz updates.

IPC smoke (from another terminal while shell is running):

```bash
qs ipc call plugin:waylandbettervoice dictate
qs ipc call plugin:waylandbettervoice meeting
```

## FileView recovery harness

This reproduces boot order safely: start with target absent, then create it after `qs -p` starts. `watchChanges` alone misses this creation; timer calls `reload()` until `RECOVERED` prints.

```bash
rm -f "$XDG_RUNTIME_DIR/wbv-fileview-recovery.json"
(sleep 2; printf recovered >"$XDG_RUNTIME_DIR/wbv-fileview-recovery.json") &
timeout 6 qs -p FileViewRecovery.qml
```

Observed (2026-08-01): `WAITING` → `RELOAD` → `WAITING` → `RELOAD` → `RECOVERED recovered` (exit 124 is `timeout` ending the intentionally persistent harness).

## Behaviour

| Surface | Role |
|---|---|
| **Main.qml** | Watches and recovers `state.json`; owns overlay, IPC `dictate`/`meeting`, CLI helpers |
| **Orb.qml** | `PanelWindow` overlay — dim slow orbs while listening, level-reactive bright orbs while dictating, calm rotating orbs while transcribing, dim meeting pill |
| **BarWidget.qml** | Mic capsule; tint: offline muted, idle configured, listening tertiary, dictating primary, transcribing secondary, meeting/error error; LMB toggles dictation, RMB opens settings, MMB opens panel |
| **Panel.qml** | Status, last transcript (selectable + copy), meeting control, open meetings folder |
| **Settings.qml** | Native/Electron injection mode, overlay margin, orb scale, show-while-transcribing, meeting pill, bar icon color |

All daemon control goes through the `wbv` CLI via `Quickshell.Io.Process`. The plugin never opens the unix socket.

## Note

`preview.png` is not shipped in this tree — packaging/leader supplies it (16:9, 960×540) before any plugin-registry publish.
