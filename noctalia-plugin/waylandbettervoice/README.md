# WaylandBetterVoice — Noctalia v5 plugin

Requires Noctalia v5 with plugin API **30+** (v5.0.1+, targeted v5.2.1), `wbv` on
Noctalia's PATH and the independently managed WBV daemon. No Quickshell/QML dependency.
The v4 plugin was removed from this repository; git history and existing backups retain it.
The Python app, CLI, offline models and niri keybinds are unchanged.

## Install from repository

From the repository root, validate before copying:

```bash
noctalia plugins lint noctalia-plugin/waylandbettervoice
lua noctalia-plugin/test_plugin.lua
plugin_dir="${XDG_DATA_HOME:-$HOME/.local/share}/noctalia/plugins/waylandbettervoice"
mkdir -p "$plugin_dir"
cp -r noctalia-plugin/waylandbettervoice/. "$plugin_dir/"
noctalia msg config-reload
noctalia msg plugins enable itc-steve/waylandbettervoice
noctalia msg plugins list
```

Do not merge into an old QML directory. Archive any old v4 user copy separately;
`settings.json` is not migrated. V5 settings live in Noctalia's configuration, not
inside the plugin directory. The package installer uses the same v5 data-home path.

Alternatively add a **path source** whose location is this repo's `noctalia-plugin`
directory (the directory containing `waylandbettervoice`):

```bash
noctalia msg plugins source add wbv-dev path "$PWD/noctalia-plugin"
noctalia msg config-reload
noctalia msg plugins enable itc-steve/waylandbettervoice
```

Choose copy **or** path source, not both. Add widget type
**`itc-steve/waylandbettervoice:mic`** using Noctalia's bar editor. Enabling alone does
not place a widget. Luau edits hot reload; manifest edits require config reload.

## Entries and controls

| Entry | Purpose |
|---|---|
| `itc-steve/waylandbettervoice:mic` | Bar widget. Left-click dictation toggle; right/middle-click controls. Orb/status label, meeting dot/timer. |
| `itc-steve/waylandbettervoice:panel` | Dictation/cancel, meeting toggle, model load/unload, typing method. Dictation toggle closes panel to return focus to app. |
| `itc-steve/waylandbettervoice:status` | Service polling `wbv status --json`, publishing only presentation fields and opening/closing recording indicator. |
| `itc-steve/waylandbettervoice:indicator` | Persistent, non-focus floating panel: pulsing recording orb or calm meeting dot/timer. |

Open controls directly:

```bash
noctalia msg panel-toggle itc-steve/waylandbettervoice:panel
```

Every action executes an argument array, never interpolated shell input. Cross-entry
busy state blocks overlapping UI commands; CLI/niri remain independent. Failed commands
produce a generic error notification and panel message; raw stdout/stderr are not logged.
Timeout does not mean recording stopped: check current state before retrying. Status
requests do not overlap; missing executable/daemon, failed or truncated status, malformed
JSON and invalid mode become offline until a later successful poll. No automatic model
load/download, daemon start/stop, transcript file reads or network requests.

## Surface limits

The indicator is a 240×56 logical-pixel host-sized panel, `top_center`, `overlay`,
`persistent = true`, `keyboard_focus = "none"`. Noctalia owns output selection,
position and styling. The service uses idempotent `panel-open`/`panel-close` IPC because
the documented in-process API only exposes toggle. Disable the floating indicator in
plugin settings if screen sharing; bar status remains. Disabling/uninstalling the
plugin closes indicator but **does not stop WBV or recording**.

Mic level/status refresh at 1 Hz; floating orb pulse uses coalesced frame ticks.
Meeting pill is static except for timer updates. Old QML shader/three-orb composition,
arbitrary top margin/orb scale, automatic copies on every monitor, transcript preview/
copy and live niri keybind scraping are not carried over. Panel shows documented default
binds, not a scan of personal config. This plugin displays no dictated text/meeting paths.

## Verify safely

```bash
lua noctalia-plugin/test_plugin.lua             # mocks only, no daemon
noctalia plugins lint noctalia-plugin/waylandbettervoice
python -m waylandbettervoice.selftest            # synthetic/mocked core checks
```

After installation, inspect enabled state/bar/control panel and shell health. Recording
and focus/injection tests require user consent and speech; offline checks do not prove
microphone capture, GPU transcription or typing into real apps. `wbv status --json`
contains private text: do not paste unredacted output into bug reports.

Official references: [development](https://docs.noctalia.dev/noctalia/plugins/development/),
[manifest](https://docs.noctalia.dev/noctalia/plugins/development/manifest/),
[entries](https://docs.noctalia.dev/noctalia/plugins/development/entries/),
[UI](https://docs.noctalia.dev/noctalia/plugins/development/declarative-ui/),
[runtime](https://docs.noctalia.dev/noctalia/plugins/development/runtime-api/),
[API levels](https://docs.noctalia.dev/noctalia/plugins/development/plugin-api/),
[workflow](https://docs.noctalia.dev/noctalia/plugins/development/workflow/).
