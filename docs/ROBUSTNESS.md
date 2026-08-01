# WaylandBetterVoice — failure-mode audit (empirical)

**Date:** 2026-08-01  
**Auditor:** agent H  
**Target:** installed package `waylandbettervoice 0.1.0-1` (`/usr/bin/wbv`, unit `waylandbettervoice.service`) plus concurrent WIP continuous-dictation daemon from the repo (PYTHONPATH) when that process held the socket.  
**Installed code:** `/usr/lib/python3.14/site-packages/waylandbettervoice/` (daemon.py = 360 lines, push-to-talk).  
**Repo WIP at audit time:** `waylandbettervoice/daemon.py` = 464 lines (`listening` mode + endpointer). Races below apply to **both**.  
**Constraint:** report only; no production code edits. System left with systemd unit **active**, default sink/source restored, EasyEffects running, no orphan `pw-record`.

---

## Method

| Area | What ran |
|---|---|
| Lifecycle | `systemctl --user show waylandbettervoice`, `journalctl --user -b`, timestamps vs pipewire / graphical-session / EasyEffects |
| Device | `pactl set-default-sink` swap+restore mid-capture; `systemctl --user restart 'app-easyeffects\x2dservice@autostart.service'`; `kill -KILL` on live `pw-record` |
| Leaks | 20× `dictate start→cancel`, 20× `start→stop`; FD/task counts via `/proc/<pid>/fd` and `/task` |
| Concurrency | Parallel `dictate toggle`×2, `dictate start`×3; meeting during dictate; dictate during meeting; SIGTERM mid-meeting; dual `Transcribing` → core dump |
| Failure UX | `kill -KILL` daemon; garbage `state.json`; config `model=DOES_NOT_EXIST.bin` + restart; `wbv quit` mid-flow |
| Packaging | `pacman -Qi` / file-list intersection with vocalinux; pin of `python-pywhispercpp-cuda` |

---

## Findings

### F1 — CRITICAL — Concurrent `whisper_full` aborts the process (lost meeting)

**Observed**

```
2026-08-01 11:32:47,465 INFO pywhispercpp.model: Transcribing ...
2026-08-01 11:32:47,466 INFO pywhispercpp.model: Transcribing ...
.../ggml-backend.cpp:1719: GGML_ASSERT(!sched->is_alloc) failed
.../ggml-alloc.c:602: GGML_ASSERT(buffer_id >= 0) failed
Main process exited, code=dumped, status=6/ABRT
```

Core dump: `coredumpctl` PID 63320, SIGABRT, ~220 MB. systemd restarted once (`NRestarts=1`). Meeting dir left as raw only:

```
~/.local/share/waylandbettervoice/meetings/2026-08-01_11-32-46/
  .mic.raw  .system.raw     # no transcript.md, no mix.wav
```

**Root cause**

- `meeting.py` runs **two** capture threads (`mic`, `system`); each calls `_transcribe` → `stt.transcribe` on the **same** global `_model` with **no lock** (`meeting.py` `_transcribe` ~L145–156, `_process` ~L238–244).
- pywhispercpp / whisper.cpp CUDA scheduler is **not** re-entrant. Dual `whisper_full` → `ggml_abort`.
- Any second caller (orphaned dictation stop racing a meeting chunk, or future continuous worker + meeting) hits the same bomb. `stt.transcribe` has no mutex (`stt.py` L65–80).

**Expected**

Serialized model use; meeting always finishes `transcript.md` + `mix.wav` or clean error without process death.

**Minimal fix**

- `stt.py`: module-level `threading.Lock()` around `model.transcribe(...)`.
- Prefer one meeting process thread (queue PCM from both captures → single STT worker), same pattern as continuous-dictation worker in repo `daemon.py`.

**Repro**

```bash
# while anything else may also call STT, or simply:
wbv meeting start
# speak on mic AND play system audio for > meeting_chunk_seconds (default 12)
# or force short chunks via config meeting_chunk_seconds=2 and dual audio
# crash appears in: journalctl --user -u waylandbettervoice -f
```

(Direct crash this session co-occurred with residual concurrent dictation activity from races; dual-log line is definitive.)

---

### F2 — HIGH — `dictate.start` / toggle race orphans `pw-record` (mode idle, pipes live)

**Observed (installed + WIP)**

| Action | `pw-record` count | After `dictate cancel` |
|---|---|---|
| Sequential 20× start/cancel | 0 | 0 |
| Sequential 20× start/stop | 0 | 0 |
| 2× parallel `dictate toggle` | **2** | **1 orphan** |
| 3× parallel `dictate start` | **4** | **3 orphans** |

Mode returned to `idle` after cancel; orphans kept the mic node open.

**Root cause**

`dictate_start` is check-then-act with **no lock** (`daemon.py` installed ~L94–118; repo ~L222–248): concurrent handlers all see `mode==idle`, each spawn `audio.Capture().start()` → `Popen(pw-record)`, only the last `_capture` is tracked. `cancel`/`stop` only terminates the tracked handle.

IPC spawns one thread per connection (`ipc.py` `_serve` ~L75–77), so rapid hotkey double-taps race for real.

**Expected**

At most one `pw-record` per session; second start returns `already active`.

**Minimal fix**

- Global `_mode_lock` (repo already has one for mode refresh — extend it) covering the whole start/stop/cancel critical section.
- On start failure path, `cap.stop()` if replace is rejected.
- Optional: track all child PIDs and reap on cancel.

**Repro**

```bash
wbv dictate cancel
wbv dictate start & wbv dictate start & wbv dictate start & wait
pgrep -a pw-record   # expect 1, observed 3–4
wbv dictate cancel
pgrep -a pw-record   # expect 0, observed 3
pkill -9 pw-record   # cleanup
```

---

### F3 — HIGH — `pw-record` death leaves mode stuck non-idle (zombie child)

**Observed (installed push-to-talk)**

```bash
wbv dictate start
# mode=dictating, pw-record PID=83820
kill -KILL 83820
# mode=dictating, pw-record = 83820 [pw-record] <defunct>
# still dictating after +1.5s
wbv dictate cancel   # recovers to idle
```

CPU jiffies delta over 1 s ≈ 0 (loop is `read→b""→sleep(0.01)`, light). Not a hot spin, but **mode stuck** → plugin orb/bar stay “live” forever until manual cancel.

**WIP continuous code:** same pattern in `_capture_loop` (`daemon.py` repo L162–167): empty chunk + `_listening` set → sleep/continue; mode stays `listening`.

**Root cause**

- `read_chunk` treats EOF as “try again” while session flag set (`audio.py` L142–150; daemon capture loops).
- No `proc.poll()` / returncode check; zombie until `Capture.stop()` waits.
- No auto-transition to `error`/`idle` and no notify.

**Expected**

EOF/nonzero exit → stop session, `mode=idle` or `error`, one notification, reap child.

**Minimal fix**

In capture loop, if `not chunk` and (`proc.poll() is not None` or repeated EOF): clear session, `_set_error("capture ended")` or silent idle + notify. Always `wait()` on child.

**Repro**

```bash
wbv dictate start; sleep 0.3
kill -KILL $(pgrep -n pw-record)
wbv status --json   # mode still dictating|listening
wbv dictate cancel
```

---

### F4 — HIGH — SIGTERM / crash mid-meeting loses transcript; SIGTERM does not restart unit

**Observed**

```bash
wbv meeting start; sleep 1.5
# dir has .mic.raw + .system.raw only (chunk window not flushed)
kill -TERM <daemon-pid>
# unit → inactive (dead); Restart=on-failure does NOT restart on SIGTERM
# meeting dir still only raws — no transcript.md, no mix.wav
```

Meeting path after SIGTERM: `.../meetings/2026-08-01_11-35-34/` raw-only.  
After SIGKILL of daemon during dictation: unit **did** restart (`NRestarts=1`), but `state.json` briefly stayed `mode=dictating` until new process rewrote idle.

**Root cause**

- No `signal.signal(SIGTERM, ...)` / `try/finally` that calls `meeting.stop()` before exit (`daemon.py` `run()` finally only cancels **dictation**, not meeting — installed L348–356; repo L448–462).
- systemd `Restart=on-failure` deliberately ignores SIGTERM/SIGINT (see systemd.service(5)). External TERM or `wbv quit` leaves service **down** until manual start or next graphical-session.
- `TimeoutStopUSec=10s` (default): a slow meeting finalize under `systemctl stop` can be SIGKILL’d mid-write.

**Expected**

Graceful finalize of meeting on TERM; session unit comes back after crash; raw→transcript best-effort.

**Minimal fix**

- In `run()` finally / SIGTERM handler: if `meeting.is_active(): meeting.stop()`.
- Unit: `Restart=on-failure` is OK for ABRT; document that `wbv quit` is intentional stop. Optionally `Restart=always` + `RestartSec=2` for a “always-on” session helper.
- Raise `TimeoutStopUSec=120s` if stop must drain STT.
- Client: `ipc.send(..., timeout=...)` default **5 s** (`ipc.py` L128) is too short for `meeting.stop` after a long session — raise for stop/quit (e.g. 120 s).

**Repro**

```bash
wbv meeting start; sleep 2
kill -TERM $(systemctl --user show waylandbettervoice -p MainPID --value)
systemctl --user is-active waylandbettervoice   # inactive
ls ~/.local/share/waylandbettervoice/meetings/<latest>/  # raw only
systemctl --user start waylandbettervoice       # required to recover
```

---

### F5 — MEDIUM — Boot order: unit starts before EasyEffects; default source is `easyeffects_source`

**Timeline this boot (user session already up; package installed mid-session)**

| Time | Event |
|---|---|
| 11:17:19 | `pipewire.service` + `pipewire-pulse` active |
| 11:17:21 | `graphical-session.target` reached; EasyEffects autostart starts |
| 11:20:12 | `pacman -U waylandbettervoice` |
| 11:20:16 | unit Started (post-install), model load 11:20:17→18 (~1.1 s) |

**Unit as installed** (`packaging/waylandbettervoice.service` / `/usr/lib/systemd/user/...`):

```
After=pipewire.service
WantedBy=graphical-session.target
Restart=on-failure
```

`systemctl --user show waylandbettervoice` → `After=pipewire.service basic.target ...` only.  
`graphical-session.target` **Wants** wbv and is ordered **Before** `app-easyeffects\x2dservice@autostart.service` and `app-vocalinux@autostart.service`. So on a cold graphical start, **wbv becomes active before EasyEffects recreates `easyeffects_source`**.

First capture uses `pactl get-default-source` → today `easyeffects_source`. If user hotkeys dictate in the first seconds after login, `pw-record --target easyeffects_source` can fail or bind wrong.

Model preload itself is fine (PipeWire already up; CUDA load ~0.4–1.1 s observed).

**Expected**

Daemon starts after session audio graph is useful (wireplumber + pulse bridge; ideally after filter graph if default source is EasyEffects).

**Recommended unit (do not apply here)**

```ini
[Unit]
Description=WaylandBetterVoice daemon
After=pipewire.service pipewire-pulse.service wireplumber.service
Wants=pipewire.service
# Optional, name is XDG-autostart-generated and fragile:
# After=app-easyeffects\x2dservice@autostart.service

[Service]
Type=simple
ExecStart=/usr/bin/wbv daemon --foreground
Restart=on-failure
RestartSec=2
TimeoutStopUSec=120s
# Keep devices + home accessible (CUDA, models, PW).

[Install]
WantedBy=graphical-session.target
```

Plus **code** retry on capture start (3× backoff) when target node missing — more reliable than chasing autostart unit names.

---

### F6 — MEDIUM — Bad model path reports `model_loaded: true`

**Observed**

```bash
# config model=DOES_NOT_EXIST.bin; systemctl --user restart waylandbettervoice
WARNING wbv.stt: model not found at .../DOES_NOT_EXIST.bin or vocalinux fallback
ERROR pywhispercpp.utils: Invalid model name `.../DOES_NOT_EXIST.bin`, available models are: [...]
INFO wbv.stt: model loaded
# status → model_loaded: true, mode idle, model DOES_NOT_EXIST.bin
```

Daemon stays up (good) but lies about readiness. `dictate start` did not hard-fail cleanly in the window before config restore.

**Root cause**

`stt.load_model` does not verify file existence after resolve; `Model(...)` does not always raise; `daemon.run` sets `model_loaded=True` whenever no exception (`daemon.py` ~L332–340 / `stt.py` L17–30, L32–61).

**Minimal fix**

After resolve, `if not path.is_file(): raise FileNotFoundError(...)`. Only then set `model_loaded=True`. Keep IPC up with `model_loaded=False` (already intended).

**Repro**

```bash
printf '%s\n' '{"model":"DOES_NOT_EXIST.bin"}' > ~/.config/waylandbettervoice/config.json
systemctl --user restart waylandbettervoice
wbv status --json   # model_loaded should be false; was true
```

---

### F7 — MEDIUM — Stale `state.json` after hard kill (plugin UX)

**Observed**

```bash
wbv dictate start   # state mode=dictating
kill -KILL $MAINPID
# state.json still {"mode":"dictating",...} until new daemon rewrites idle (~1s with Restart)
# socket briefly present but Connection refused
```

Corrupt file with `echo garbage > state.json`: daemon ignored it (in-memory state); next `write_state` rewrote valid JSON. **Daemon: fine. Plugin: depends on FileView/timer** — garbage parse can throw until rewrite.

**Minimal fix**

- On startup, write idle **before** long model load (already mostly true) and on every boot path.
- Plugin: tolerate JSON parse failure as offline (FIXPLAN timer path helps when file missing; also catch `JSON.parse` errors).
- Optional: daemon writes `mode=idle` in a `systemd` `ExecStop=` helper — usually overkill if finally/SIGTERM handled (F4).

---

### F8 — LOW — Default sink switch mid-dictation

**Observed**

Dictation targets **source** (`easyeffects_source`), not sink. Switching default sink MV6 → Kiwi Ears mid-session:

- Installed daemon once dropped to idle / lost `pw-record` (graph reshuffle side effect).
- Source stayed `easyeffects_source`; no permanent sink change left behind (restored).

**Impact**

Meeting **system** capture resolves sink at start (`audio.resolve_default_sink`); mid-meeting sink switch is not followed — system track may go silent until restart meeting. Not re-tested to crash.

**Minimal fix**

Document “don’t change default devices mid-meeting”; optional re-resolve is YAGNI until reported.

---

### F9 — LOW — EasyEffects restart mid-listen

**Observed**

```bash
wbv dictate start
systemctl --user restart 'app-easyeffects\x2dservice@autostart.service'
# ~2s later: mode still listening/dictating, same pw-record PID still listed,
# default source again easyeffects_source, EasyEffects PID new
```

Often survives (PipeWire node reappears with same name). Can still pair with F3 if node destruction EOFs the stream.

---

### F10 — LOW — Packaging / vocalinux coexistence

| Check | Result |
|---|---|
| `python-pywhispercpp-cuda 1.4.0-11` | Intact; `Provides: python-pywhispercpp`; required by both vocalinux + wbv |
| File collisions (files only) | **None** |
| `Conflicts`/`Replaces` on wbv | None (correct) |
| vocalinux autostart | Still `app-vocalinux@autostart.service` **active** |
| Simultaneous mic grab | No evidence of hard device lock fight in this session; both can open PW streams. UX confusion (two STT stacks) is user-level, not a packaging bug |

**Fine:** models are symlinks into vocalinux’s tree; no second download.

---

### F11 — LOW — CLI gaps

- `wbv reload` not exposed in argparse (`__main__.py`) though IPC cmd `reload` exists — config tweak for meeting_chunk required restart.
- `ipc.send` default timeout 5 s will false-fail long `meeting.stop` (see F4).

---

## What tested FINE (negative results)

| Test | Result |
|---|---|
| 20× sequential dictate start/cancel | FD **39→39**, tasks **5→5**, 0 `pw-record` |
| 20× sequential dictate start/stop (silence) | Same; mode always back to `idle` |
| Mutual exclusion meeting↔dictation | `meeting.start` during dictate → `dictation active — stop first`; dictate during meeting → `meeting active` |
| Clean `meeting.stop` (quiet 5 s session) | `transcript.md` + `transcript.json` + `mix.wav` written; mode idle |
| Corrupt `state.json` while daemon up | Daemon unaffected; next state write repairs file |
| SIGKILL daemon | `Restart=on-failure` brings unit back; model reload ~0.4–1 s |
| Model cold load with large-v3 CUDA | Success; VRAM resident; no start-timeout issue (`Type=simple`) |
| Package pins / no vocalinux file clash | OK |
| Default sink/source restored after tests | MV6 sink + `easyeffects_source` |
| EasyEffects still running after restart test | PID present, service-mode |

---

## Severity summary

| ID | Sev | One-liner |
|---|---|---|
| F1 | **critical** | Concurrent STT → GGML abort, core dump, lost meeting |
| F2 | **high** | Parallel start/toggle orphans `pw-record` |
| F3 | **high** | Dead capture → stuck `dictating`/`listening` + zombie |
| F4 | **high** | TERM mid-meeting loses transcript; unit stays down |
| F5 | **medium** | Unit before EasyEffects; fragile default source |
| F6 | **medium** | Missing model still `model_loaded: true` |
| F7 | **medium** | Stale/garbage `state.json` window for plugin |
| F8 | **low** | Sink switch mid-session |
| F9 | **low** | EasyEffects restart usually OK |
| F10 | **low** | vocalinux coexists (no file fight) |
| F11 | **low** | CLI reload missing; 5 s IPC timeout |

---

## Priority fix order (minimal diffs)

1. **Lock in `stt.transcribe`** (kills F1; unblocks safe meeting + continuous worker).  
2. **Lock dictate start/stop/cancel** (F2).  
3. **Treat `pw-record` EOF/exit as session failure** (F3).  
4. **SIGTERM/finally → `meeting.stop()`** + longer stop timeout (F4).  
5. **Unit After=wireplumber/pipewire-pulse + capture retry** (F5).  
6. **Verify model file before `model_loaded=True`** (F6).

---

## Environment snapshot (end of audit)

```
waylandbettervoice.service: active
MainPID: (running wbv daemon --foreground)
mode: idle, model_loaded: true, model: ggml-large-v3.bin
default sink: alsa_output.usb-Shure_Inc_Shure_MV6_...
default source: easyeffects_source
pw-record: none
easyeffects: running (--service-mode)
vocalinux: still installed + autostart active
python-pywhispercpp-cuda: 1.4.0-11 intact
```

**Note:** During the audit, agent A briefly replaced the systemd daemon with `PYTHONPATH=... python -m waylandbettervoice daemon`. Final restore is **systemd unit running installed `/usr/bin/wbv`**. Repo continuous-dictation work was in flight; re-test F2/F3 after it lands.
