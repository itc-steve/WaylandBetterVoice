# Architecture

## Data flow

```
niri keybind (Mod+Alt+D/M)
  │
  ▼
wbv CLI  ── unix socket (XDG_RUNTIME_DIR/waylandbettervoice/wbv.sock) ──►  daemon
                                                                 (model resident in VRAM)
  │                                                                    │
  │                                                                    ▼
  │                                                               pw-record
  │                                                               (raw PCM)
  │                                                                    │
  │                                                                    ▼
  │                                                               whisper.cpp
  │                                                               (pywhispercpp)
  │                                                                    │
  │                                                                    ▼
  ◄───────────────────────────────────────────────────────────────  wtype
  (injected text)
                                                                 │
                                                                 ▼
                                                          state.json
                                                                 │
                                                                 ▼
                                                          Noctalia plugin
                                                          (overlay, watches file)
```

## Modules

| Module | Responsibility |
|---|---|
| `__main__.py` | CLI entry (`wbv`), argparse dispatcher, IPC client |
| `config.py` | Path constants, config.json load/save/merge, defaults |
| `ipc.py` | Unix socket server (daemon) and client (CLI), newline-delimited JSON |
| `audio.py` | `pw-record` subprocess management, raw PCM streaming |
| `stt.py` | `pywhispercpp` wrapper, model preload into VRAM, transcription |
| `inject.py` | `wtype` text injection, `wl-copy` fallback |
| `state.py` | Atomic state.json writer (tmp + `os.replace`) |
| `daemon.py` | Orchestrator: model init, IPC listener, dictation flow |
| `meeting.py` | Meeting capture, diarization (`sherpa_onnx`), transcript output |

## IPC protocol

Newline-delimited JSON over `XDG_RUNTIME_DIR/waylandbettervoice/wbv.sock`.

Request: `{"cmd": "<name>", "args": {...}}`
Response: `{"ok": bool, "data": {...}, "error": str|null}`

| cmd | effect |
|---|---|
| `dictate.toggle` | Start or stop push-to-talk dictation |
| `dictate.start` | Start dictation explicitly |
| `dictate.stop` | Stop dictation, inject result |
| `dictate.cancel` | Stop dictation, discard result |
| `meeting.toggle` | Start or stop meeting recording |
| `meeting.start` | Start meeting recording explicitly |
| `meeting.stop` | Stop meeting, write transcripts |
| `status` | Return current state object |
| `reload` | Re-read config.json |
| `quit` | Shut down daemon |

## State file schema

`XDG_RUNTIME_DIR/waylandbettervoice/state.json` — atomic write on every
transition, max 10 Hz while capturing.

| Field | Type | Description |
|---|---|---|
| `mode` | `idle\|dictating\|transcribing\|meeting\|error` | Current mode |
| `since` | float | Unix timestamp of mode start |
| `level` | float | Audio RMS level, 0.0–1.0 |
| `model` | string | Loaded model filename |
| `model_loaded` | bool | Whether model is resident in VRAM |
| `meeting.active` | bool | Meeting recording active |
| `meeting.file` | string\|null | Current meeting output path |
| `meeting.speakers` | int | Detected speaker count |
| `meeting.elapsed` | int | Elapsed seconds |
| `last_text` | string | Last injected or transcribed text |
| `error` | string\|null | Last error message |

## Design decisions

- **No in-process hotkey grabbing.** niri owns all keybinds. The CLI forwards
  commands to the daemon via unix socket. No evdev, no pynput.
- **No venv.** System Python only. All dependencies installed via system
  packages.
- **Stdlib first.** `socket`, `subprocess`, `json`, `argparse`, `threading`,
  `wave`, `array`. Third-party imports limited to `pywhispercpp`, `numpy`,
  and optional `sherpa_onnx`.
- **Single process.** One daemon, no D-Bus, no systemd socket activation, no
  plugin daemon.
- **Model preloaded.** Whisper model loaded into VRAM at daemon startup and
  kept resident. Dictation latency is wall-clock audio capture + decode only.
