# WaylandBetterVoice

Local GPU Whisper dictation and meeting transcription for niri/Wayland. Loads a
whisper.cpp model into VRAM at boot and keeps it resident so dictation starts
immediately. English only. Single Python daemon, one Noctalia overlay plugin, no
systemd socket activation, no D-Bus, no venv.

## Requirements

- CachyOS (Arch Linux)
- niri compositor
- Noctalia shell 4.7.7 (quickshell 0.0.12) — for the overlay plugin
- NVIDIA GPU with CUDA (model runs via `python-pywhispercpp-cuda`)
- PipeWire (audio capture)
- `wtype` (text injection)

## Install

```bash
cd packaging
./install-local.sh
```

The script builds the package locally and installs it. It is idempotent and
refuses to run as root.

Enable the daemon:

```bash
systemctl --user enable --now waylandbettervoice.service
```

Symlink the Noctalia plugin (also done by `install-local.sh`):

```bash
ln -s /usr/share/waylandbettervoice/noctalia-plugin \
      ~/.config/noctalia/plugins/waylandbettervoice
```

Add the niri keybinds. `install-local.sh` copies the snippet to
`~/.config/niri/cfg/waylandbettervoice.kdl` (only if absent) but never edits your
config, so add the include line yourself:

```kdl
// ~/.config/niri/config.kdl (add)
include "./cfg/waylandbettervoice.kdl"
```

Check it before reloading: `niri validate`.

## Usage

**Dictate** — `Mod+Alt+D` toggles push-to-talk dictation. Injected at
cursor via `wtype`.

**Meeting** — `Mod+Alt+M` toggles meeting recording. Transcripts saved to
`~/.local/share/waylandbettervoice/meetings/`.

**CLI**

| Command | Description |
|---|---|
| `wbv daemon [--foreground]` | Start daemon (systemd unit runs this) |
| `wbv dictate [toggle\|start\|stop\|cancel]` | Push-to-talk dictation (default: toggle) |
| `wbv meeting [toggle\|start\|stop]` | Meeting recorder (default: toggle) |
| `wbv status [--json]` | Current status |
| `wbv quit` | Shut down daemon |

Client commands exit non-zero with a clear stderr message if the daemon is
not running.

## Config

`~/.config/waylandbettervoice/config.json`

| Key | Default | Description |
|---|---|---|
| `model` | `ggml-large-v3.bin` | Whisper model filename in model dir |
| `language` | `en` | Transcription language (English only) |
| `n_threads` | `8` | CPU threads for decoding |
| `beam_size` | `5` | Beam search size |
| `dictation_max_seconds` | `300` | Hard stop for dictation sessions |
| `meeting_max_minutes` | `180` | Hard stop for meeting recordings |
| `inject_method` | `wtype` | Text injection method (`wtype`; falls back to `wl-copy`) |
| `wtype_delay_ms` | `0` | Reserved; per-character delay is not applied yet |
| `trailing_space` | `true` | Append trailing space after injection |
| `meeting_diarization` | `true` | Enable speaker diarization for meetings |
| `meeting_speakers` | `0` | Expected speaker count (0 = auto) |
| `meeting_chunk_seconds` | `12` | Audio chunk size for processing |
| `notify_on_error` | `true` | Show desktop notification on errors |

## Relationship to Vocalinux

Built for the same machine. Coexists with Vocalinux — no conflict, no
automatic replacement. Opt-in migration via `packaging/migrate-off-vocalinux.sh`.

## Troubleshooting

**Daemon not running.** Check journal: `journalctl --user -u waylandbettervoice`.
Ensure `python-pywhispercpp-cuda` is installed and the model file exists in
`~/.local/share/waylandbettervoice/models/`.

**No text injected.** Verify `wtype` is installed and working. Check
`inject_method` in config. Fallback (`wl-copy`) copies to clipboard and shows
a notification — paste manually.

**Model not found.** Installer symlinks from
`~/.local/share/vocalinux/models/whispercpp/`. Check symlinks in
`~/.local/share/waylandbettervoice/models/`.

**No GPU.** `python-pywhispercpp-cuda` requires a CUDA-capable GPU and driver.
The CPU build is not supported.

**Meeting speakers all "Speaker ?".** `sherpa_onnx` or the speaker model is
missing. Diarization degrades gracefully — see `docs/MEETING.md` for the install
and model download, then restart the daemon.

**Meeting captured silence.** System audio is read from the sink that is actually
playing, not the default sink (EasyEffects re-routes streams). If nothing was
playing when the meeting started, the fallback is the default sink. Check the
chosen node: `journalctl --user -u waylandbettervoice | grep 'starting capture'`.

**Dictation returns nothing.** Near-silent audio is discarded on purpose —
whisper hallucinates phrases like "Thank you." on silence. Check the mic is not
muted: `wpctl status`.

## Credits

Inspired by jatinkrmalik/vocalinux, Jakubantalik/thinking-orbs, digimata/quill.
All code is original.
