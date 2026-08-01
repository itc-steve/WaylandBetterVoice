# WaylandBetterVoice — build contract (AUTHORITATIVE)

Single Python daemon + one Noctalia legacy-v4 plugin. English only. Local GPU STT.
Everything below is fixed. Do NOT invent files, deps, or abstractions outside this doc.

## Target system (verified, do not re-probe destructively)

- CachyOS (Arch), niri 26.04, strict Wayland, no X11 fallback.
- Noctalia shell 4.7.7 + quickshell (noctalia-qs 0.0.12). Plugins live in `~/.config/noctalia/plugins/<id>/`.
- Python 3.14.6 system interpreter, **no pip, no venv** — system packages only.
- GPU: RTX PRO 6000 Blackwell, 97 GB VRAM, driver 610.43.03, CUDA 13.3, sm_120.
- `python-pywhispercpp-cuda` already installed (whisper.cpp 1.8.2, CUDA sm_120) from
  `/home/sdane/Projects/vocalinux-blackwell-packages/`. WE REUSE IT. Never rebuild it.
- Models already on disk: `~/.local/share/vocalinux/models/whispercpp/ggml-large-v3.bin`
  (also small, small.en). New model dir: `~/.local/share/waylandbettervoice/models/`
  — installer symlinks the existing files, does not re-download.
- Installed and usable: `wtype`, `wl-clipboard`, `pw-record`, `pw-cat`, `wpctl`, `pactl`,
  `python-numpy`, `python-evdev`, `python-requests`, `python-tqdm`, `python-psutil`.
- NOT installed, do not depend on: torch, onnxruntime, sounddevice, pyaudio, ydotool, dotool.
- Audio: PipeWire 1.6.8. Mic `alsa_input.usb-Shure_Inc_Shure_MV6_*.mono-fallback`,
  EasyEffects active (`easyeffects_sink`, `easyeffects_source`). Never hardcode node names —
  resolve default source via `pactl get-default-source` and monitor via
  `pactl get-default-sink` + `.monitor` suffix.
- Old `vocalinux` still installed + autostarting (`~/.config/autostart/vocalinux.desktop`,
  `app-vocalinux@autostart.service`). We DO NOT remove it. Packaging ships a documented
  opt-in `migrate-off-vocalinux.sh`; user runs it manually.

## Non-negotiable design rules

1. No global hotkey grabbing inside the app. Hotkeys are **niri binds** that run the CLI.
   niri → `wbv dictate` / `wbv meeting` → unix socket → daemon. No evdev, no pynput.
2. Daemon loads the whisper model into VRAM at startup and keeps it resident forever.
3. Text injection: `wtype` only. Fallback: `wl-copy` + notify user (no synthetic Ctrl+V).
4. Audio capture: `pw-record` subprocess writing raw PCM to stdout. No Python audio libs.
5. Stdlib first: `socket`, `subprocess`, `json`, `argparse`, `threading`, `wave`, `array`.
   Only 3rd-party import allowed in core: `pywhispercpp`, `numpy`.
   Meeting diarization only: `sherpa_onnx` (optional, degrade gracefully if absent).
6. One process. No D-Bus service, no systemd socket activation, no plugin daemon.
7. `ponytail:` comment on every deliberate shortcut with its ceiling.

## Repo layout (exact)

```
/home/sdane/Projects/WaylandBetterVoice/
├── CONTRACT.md                  # this file (leader owns)
├── README.md                    # docs agent
├── waylandbettervoice/
│   ├── __init__.py              # __version__ = "0.1.0"
│   ├── __main__.py              # CLI entry: `python -m waylandbettervoice` (agent A)
│   ├── config.py                # paths, defaults, load/save JSON (agent A)
│   ├── ipc.py                   # unix socket server + client (agent A)
│   ├── audio.py                 # pw-record capture helpers (agent A)
│   ├── stt.py                   # whisper model wrapper, VRAM preload (agent A)
│   ├── inject.py                # wtype / wl-copy (agent A)
│   ├── state.py                 # state file writer for the plugin (agent A)
│   ├── daemon.py                # orchestrator, dictation flow (agent A)
│   └── meeting.py               # meeting capture + diarization (agent B)
├── noctalia-plugin/waylandbettervoice/   # agent C
│   ├── manifest.json  Main.qml  BarWidget.qml  Panel.qml  Settings.qml
│   ├── Orb.qml        i18n/en.json  README.md  preview.png
├── packaging/                   # agent D
│   ├── PKGBUILD  wbv.install
│   ├── waylandbettervoice.service      # systemd --user
│   ├── niri-keybinds.kdl               # snippet to include
│   ├── install-local.sh  migrate-off-vocalinux.sh
└── docs/ARCHITECTURE.md         # docs agent
```

## Interface contracts (agents must match EXACTLY)

### Paths (`config.py`, everyone imports from here)

```python
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "waylandbettervoice"
SOCKET_PATH = RUNTIME_DIR / "wbv.sock"
STATE_PATH  = RUNTIME_DIR / "state.json"      # plugin watches this
DATA_DIR    = Path.home() / ".local/share/waylandbettervoice"
MODEL_DIR   = DATA_DIR / "models"
MEETING_DIR = DATA_DIR / "meetings"
LOG_PATH    = DATA_DIR / "wbv.log"
CONFIG_PATH = Path.home() / ".config/waylandbettervoice/config.json"
```

### config.json defaults

```json
{
  "model": "ggml-large-v3.bin",
  "language": "en",
  "n_threads": 8,
  "beam_size": 5,
  "dictation_max_seconds": 300,
  "meeting_max_minutes": 180,
  "inject_method": "wtype",
  "wtype_delay_ms": 0,
  "trailing_space": true,
  "meeting_diarization": true,
  "meeting_speakers": 0,
  "meeting_chunk_seconds": 12,
  "notify_on_error": true
}
```

### IPC protocol (`ipc.py`) — newline-delimited JSON over `SOCKET_PATH`

Request: `{"cmd": "<name>", "args": {...}}`  Response: `{"ok": bool, "data": {...}, "error": str|null}`

| cmd | effect |
|---|---|
| `dictate.toggle` | start/stop push-to-talk dictation |
| `dictate.start` / `dictate.stop` / `dictate.cancel` | explicit |
| `meeting.toggle` / `meeting.start` / `meeting.stop` | meeting recorder |
| `status` | returns the state object below |
| `reload` | re-read config.json |
| `quit` | shut down |

### State file (`state.json`) — atomic write (tmp + `os.replace`) on EVERY transition

```json
{
  "mode": "idle|dictating|transcribing|meeting|error",
  "since": 1730000000.0,
  "level": 0.0,
  "model": "ggml-large-v3.bin",
  "model_loaded": true,
  "meeting": {"active": false, "file": null, "speakers": 0, "elapsed": 0},
  "last_text": "",
  "error": null
}
```

`level` = 0.0–1.0 RMS, updated ≤10 Hz while capturing. Plugin drives orb animation from
`mode` + `level`. Never write the file more than 10×/s.

### CLI (`__main__.py`)

```
wbv daemon [--foreground]      # start daemon (systemd user service runs this)
wbv dictate [toggle|start|stop|cancel]   # default: toggle
wbv meeting [toggle|start|stop]          # default: toggle
wbv status [--json]
wbv quit
```
Client commands must exit non-zero with a clear stderr message if daemon is not running.

### Meeting output (agent B)

`MEETING_DIR/<YYYY-mm-dd_HH-MM-SS>/` containing `transcript.md`, `transcript.json`, `mix.wav`.
`transcript.json`: `[{"start": float, "end": float, "speaker": "Me|Speaker 1|...", "source": "mic|system", "text": str}]`
Mic audio is always speaker `Me`. Diarization runs **only** on the system-audio stream to
split the N remote participants. If `sherpa_onnx` is missing → all system segments become
`Speaker ?` and a one-line warning goes in the log. Never crash.

### Plugin ↔ daemon (agent C)

- Read state: `Quickshell.Io.FileView` on `$XDG_RUNTIME_DIR/waylandbettervoice/state.json`
  with `watchChanges: true`. Parse with `JSON.parse`.
- Send commands: `Quickshell.Io.Process` running `wbv dictate toggle` etc. Never write the socket from QML.
- Overlay: `PanelWindow` (Quickshell.Wayland) anchored top, `exclusionMode: ExclusionMode.Ignore`,
  `WlrLayershell.layer: WlrLayer.Overlay`, `WlrKeyboardFocus.None`, horizontally centered,
  `margins.top` = configurable (default 46) so it clears the Noctalia bar.
- Dictation visual: 3 thinking-orb style animated circles (spirit of Jakubantalik/thinking-orbs,
  original code — soft blurred gradient circles orbiting, scale pulsing with `level`).
  Idle → hidden entirely.
- Meeting visual: NOT the orbs. A small discreet dock pill (~ 110×26 px) with a dim red dot +
  elapsed timer, low opacity (0.55), no animation beyond a 2 s dot fade. Screen-share safe.
- BarWidget: mic icon; left click = `wbv dictate toggle`, right click = context menu (settings).
  Tint by `mode`. Panel: status, model, last text, meeting start/stop, open meetings folder.

### Packaging (agent D)

- PKGBUILD `pkgname=waylandbettervoice`, `arch=('any')`, `pkgver=0.1.0`.
  depends: `python`, `python-numpy`, `python-pywhispercpp`, `wtype`, `wl-clipboard`,
  `pipewire`, `libpulse`, `libnotify`.
  optdepends: `python-sherpa-onnx: meeting speaker diarization`, `noctalia-shell: overlay plugin`.
  Must NOT conflict with or replace `vocalinux`. Must not pull `python-pywhispercpp-cpu`
  (the CUDA build already `provides=python-pywhispercpp`).
- Build with `python -m build --wheel --no-isolation`; install with `python -m installer`.
- Package must also install the plugin to `/usr/share/waylandbettervoice/noctalia-plugin/`;
  `install-local.sh` symlinks it into `~/.config/noctalia/plugins/waylandbettervoice`.
- systemd user unit: `Type=simple`, `ExecStart=/usr/bin/wbv daemon --foreground`,
  `Restart=on-failure`, `WantedBy=graphical-session.target`, `After=pipewire.service`.
  Preloads the model at boot → fast everyday transcription.
- niri snippet: `Mod+Alt+D` → `spawn-sh "wbv dictate toggle"`, `Mod+Alt+M` → `spawn-sh "wbv meeting toggle"`.
  Snippet is a separate file the user *includes*; installer must never edit existing niri config in place.
- `install-local.sh` is idempotent, uses `makepkg`+`pacman -U`, and refuses to run as root.
- Nothing in packaging may touch `/etc/pacman.conf`, the vocalinux packages, or existing pinned builds.

## Verification each agent owes

One runnable check, stdlib `assert`, no test framework:
- A: `waylandbettervoice/selftest.py` — protocol round-trip + state atomic write + config merge.
- B: assert block in `meeting.py` `__main__` — segment merge/labeling logic on fake data.
- C: `qs -c noctalia-shell` load check documented in plugin README (manual).
- D: `bash -n` clean on all scripts; `install-local.sh --check` dry-run mode.

Never run `pacman -U`, `pacman -S`, `systemctl enable`, or modify anything under
`~/.config/niri` or `~/.config/noctalia`. Leader installs. Write files only inside
`/home/sdane/Projects/WaylandBetterVoice/`.
