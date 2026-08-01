# WaylandBetterVoice — round 2 fix plan (AUTHORITATIVE)

Three confirmed bugs from real dogfooding. Root causes below are VERIFIED on the
machine, not guesses. Do not re-diagnose; implement.

Repo: `/home/sdane/Projects/WaylandBetterVoice/`. Read `CONTRACT.md` first — it still
governs paths, IPC, and state schema, except where this file overrides it.

---

## BUG 1 — dictation should be continuous, not press-to-stop (agent A)

**Current (wrong):** `Mod+Alt+D` starts capture, buffers everything, and only transcribes
+ types when you press the hotkey again.

**Wanted:** the hotkey toggles *listening mode*. While listening, the daemon detects
natural pauses and types each sentence as it completes. You talk, it types, you keep
talking, it keeps typing. Second press stops listening entirely.

**Design (fixed, do not redesign):**
- Endpointing by RMS silence detection over the 20 ms frames already produced by
  `audio.Capture`. Speech starts when RMS > `vad_start_level` for ~120 ms; an utterance
  ENDS after `silence_seconds` of continuous sub-`vad_stop_level` frames.
- On utterance end: transcribe that utterance only, inject via `inject.type_text`, keep
  listening. Keep ~300 ms of pre-roll audio before speech onset so the first syllable is
  not clipped.
- Drop utterances shorter than `min_utterance_seconds` or below the existing silence gate
  (whisper hallucinates "Thank you." on silence — that gate stays).
- Transcription must NOT block capture. Capture thread feeds a `queue.Queue` of finished
  utterances; a single worker thread transcribes and injects in order. One worker only —
  ordering matters more than throughput.
- `dictation_max_seconds` now caps a single *utterance*, not the session. Add
  `listen_max_minutes` (default 30) as the session cap so a forgotten hotkey does not
  record forever.
- New mode value `listening` (mic open, no speech right now). `dictating` means speech is
  actively being captured. `transcribing` while a chunk is in whisper. The plugin uses
  these to drive the orbs, so all three must appear in state.json.
- `dictate.stop` flushes any in-progress utterance, then leaves listening mode.
  `dictate.cancel` discards it. `dictate.toggle` enters/leaves listening mode.

**New config keys (add to DEFAULTS + README table):**

```
"silence_seconds": 0.8,          # pause that ends an utterance
"vad_start_level": 0.015,        # RMS to consider speech started
"vad_stop_level": 0.008,         # RMS below this counts as silence
"min_utterance_seconds": 0.4,    # shorter -> discarded
"preroll_seconds": 0.3,          # audio kept before speech onset
"listen_max_minutes": 30,        # hard cap on a listening session
"dictation_max_seconds": 30,     # now caps ONE utterance
```

These thresholds are hardware-specific (Shure MV6 through EasyEffects). Leave a
`# ponytail:` note saying they are tunable and where.

Update `selftest.py` with an endpointer test: feed synthetic RMS frame sequences
(silence → speech → silence → speech) through the segmentation logic and assert the right
number of utterances with the right boundaries. Pure logic, no audio device, no model.
Factor the endpointer so it is testable without hardware.

---

## BUG 2 — plugin never sees the daemon (agent C)

**Root cause (VERIFIED, do not re-derive):** `FileView` with `watchChanges: true` only
tracks a file that existed when the shell started. Proved with a minimal quickshell
harness: when the watched path is created *after* startup, `onFileChanged` never fires and
`onLoaded` never runs. The daemon's `state.json` lives in `$XDG_RUNTIME_DIR`, which is
wiped on reboot, and the systemd unit starts after Noctalia — so the plugin is blind on
every single boot. This is why there is no orb and no bar-icon change. The QML is
otherwise correct; the overlay itself was verified working earlier when the daemon
happened to start first.

**Fix:** add a `Timer` in `Main.qml` (interval 1000 ms, `repeat: true`, running whenever
the plugin has never successfully loaded state OR the last successful read is older than
~3 s) that calls `stateFile.reload()`. Once reads succeed, `watchChanges` handles the fast
path; the timer is the recovery path for file-recreated-after-start and for daemon
restarts. Do not poll aggressively when healthy — state.json updates at up to 10 Hz on its
own via watch.

Also:
- Treat "file exists but daemon dead" correctly: if state has not changed for >5 s AND
  mode is not idle, do not invent state; just keep showing the last value. Only
  `onLoadFailed` / missing file means `offline`.
- `mode: "listening"` must render: orbs visible but calm/dim (mic open, waiting), then
  brighten and pulse with `level` on `dictating`, then the existing calm palette on
  `transcribing`. Idle/offline = hidden.
- Verify the bar widget tints for all of: offline, idle, listening, dictating,
  transcribing, meeting, error. Add i18n keys for `mode.listening`.
- Keep using the `wbv` CLI for commands.

**Verification you owe:** write a standalone `qs -p` harness proving the timer recovers
when the file appears after startup. Put it in the plugin README as the reproduction, and
report the harness output.

---

## BUG 3 — "no thinking orb animation" (agent C, same job as bug 2)

Mostly a symptom of bug 2 — the overlay never activated because the plugin never saw a
non-idle mode. But while you are in there, the orbs are also weaker than the reference:

- The current implementation is three flat translucent circles. Make them read as soft
  glowing orbs: use `MultiEffect` blur (verify it exists in the installed Qt6 —
  `/usr/lib/qt6/qml/QtQuick/Effects/`) or layered radial `Gradient` fills. Verify before
  using; if `MultiEffect` is unavailable, say so and keep the layered approach but add
  more layers with a smoother alpha falloff.
- Motion should be organic: independent per-orb phase offsets, slight radius variation,
  eased scale response to `level` (fast attack, slow release — a `Behavior` with different
  in/out easing), not a single linear rotation.
- `listening` = slow, dim, small. `dictating` = brighter, larger, pulsing with `level`.
  `transcribing` = calm palette, no level response, gentle rotation.
- Respect `orbScale` and keep it themed via `Color.mPrimary/mSecondary/mTertiary`.

Keep the meeting pill discreet and unchanged in spirit — screen-share safe.

---

## Cross-cutting review (agent R)

Full adversarial code review of the whole repo AFTER A and C land. Not a rewrite — a
findings report at `REVIEW.md` with severity, file:line, and the minimal fix. Focus:
races in the new threading, resource leaks (`pw-record` subprocesses, file handles,
threads on error paths), state-machine holes (what if `dictate.toggle` arrives mid-
transcription? meeting during listening?), error paths that leave `mode` stuck, and QML
that will throw at runtime. Call out anything that could wedge the daemon or spam the
user's screen. Do not rewrite files owned by others; report only.

---

## Rules for every agent

- Verified system facts: CachyOS, niri 26.04, Noctalia 4.7.7, quickshell 0.0.12,
  Python 3.14, no pip/venv, whisper large-v3 CUDA sm_120 resident in VRAM,
  `sherpa_onnx` NOW INSTALLED and diarization confirmed working.
- The package is INSTALLED (`/usr/bin/wbv`, systemd user unit `waylandbettervoice`).
  The Noctalia plugin is symlinked to the REPO, so QML edits are live after a shell
  restart. Python edits need a rebuild — the leader does that, not you.
- Do NOT run: `pacman`, `yay`, `makepkg`, `systemctl enable`, anything with `sudo`.
  You may run `systemctl --user status/restart waylandbettervoice` and read journals.
- Do NOT edit anything under `~/.config/niri`. You may READ it.
- Write only files you own, only inside the repo.
- Stdlib first. No new runtime dependencies. No new abstractions without a second caller.
- `# ponytail:` comments on deliberate shortcuts, naming the ceiling.
- One runnable check per change, assert-based, no test framework.
