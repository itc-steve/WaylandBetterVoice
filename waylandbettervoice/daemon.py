"""Daemon orchestrator: load model, IPC, continuous dictation. Meeting via lazy import."""
from __future__ import annotations

import logging
import logging.handlers
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Optional

from waylandbettervoice import audio, inject, ipc, state, stt
from waylandbettervoice.config import LOG_PATH, SOCKET_PATH, load_config, mkdirs

log = logging.getLogger("wbv.daemon")

_config: dict = {}
_model = None
_capture: Optional[audio.Capture] = None
_capture_thread: Optional[threading.Thread] = None
_endpointer: Optional[audio.Endpointer] = None
_listening = threading.Event()  # continuous listen session active
_in_speech = False
_worker_busy = False
_mode_lock = threading.Lock()
# Serializes start/stop/cancel so a double-tapped hotkey cannot spawn two captures.
_session_lock = threading.RLock()
_utt_q: queue.Queue = queue.Queue()
_worker_thread: Optional[threading.Thread] = None
_server: Optional[ipc.Server] = None
_quit_event = threading.Event()

# whisper hallucinates ("Thank you.") on pure silence — keep per-utterance gate
_SILENCE_RMS = 0.004


def _setup_logging() -> None:
    mkdirs()
    root = logging.getLogger()
    if not root.handlers:
        root.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        # cap log growth: 5 × 2 MiB rotated files under DATA_DIR
        fh = logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=2 * 1024 * 1024, backupCount=4, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)


def _notify(msg: str) -> None:
    if not _config.get("notify_on_error", True):
        return
    n = shutil.which("notify-send")
    if not n:
        return
    try:
        subprocess.run([n, "WaylandBetterVoice", msg], check=False, timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        pass


def _set_error(err: str) -> None:
    log.error("%s", err)
    state.write_state(mode="error", error=err, level=0.0)
    _notify(err)
    state.write_state(mode="idle", error=None, level=0.0)


def _session_active() -> bool:
    return _listening.is_set()


def _refresh_listen_mode() -> None:
    """Pick listening|dictating|transcribing from speech + worker flags."""
    if not _listening.is_set():
        return
    with _mode_lock:
        if not _listening.is_set():
            return
        if _in_speech:
            mode = "dictating"
        elif _worker_busy:
            mode = "transcribing"
        else:
            mode = "listening"
        state.write_state(mode=mode, error=None)


def _enqueue_utterance(pcm: bytes) -> None:
    if not pcm:
        return
    # ponytail: flat RMS gate — whisper hallucinates on silence; promote to config if needed
    if audio.rms_level(pcm) < _SILENCE_RMS:
        log.info("discarding near-silent utterance (%d bytes)", len(pcm))
        return
    _utt_q.put(pcm)


def _make_endpointer() -> audio.Endpointer:
    # ponytail: VAD thresholds hardware-specific (Shure MV6 + EasyEffects) — retune in config.json
    return audio.Endpointer(
        silence_seconds=float(_config.get("silence_seconds", 0.8)),
        vad_start_level=float(_config.get("vad_start_level", 0.015)),
        vad_stop_level=float(_config.get("vad_stop_level", 0.008)),
        min_utterance_seconds=float(_config.get("min_utterance_seconds", 0.4)),
        preroll_seconds=float(_config.get("preroll_seconds", 0.3)),
        max_utterance_seconds=float(_config.get("dictation_max_seconds", 30)),
    )


def _worker_loop() -> None:
    """Single ordered worker: transcribe + inject. One only — ordering > throughput."""
    global _worker_busy
    while True:
        item = _utt_q.get()
        if item is None:
            break
        pcm = item
        _worker_busy = True
        _refresh_listen_mode()
        try:
            text = stt.transcribe(_model, pcm) if _model is not None else ""
        except Exception as e:  # noqa: BLE001
            log.exception("transcribe failed")
            if _listening.is_set():
                _worker_busy = False
                _refresh_listen_mode()
                _notify(f"transcribe failed: {e}")
            else:
                _worker_busy = False
                _set_error(f"transcribe failed: {e}")
            continue
        if text:
            try:
                inject.type_text(text, trailing_space=bool(_config.get("trailing_space", True)))
                state.write_state(last_text=text)
            except Exception as e:  # noqa: BLE001
                log.exception("inject failed")
                _notify(f"inject failed: {e}")
        _worker_busy = False
        if _listening.is_set():
            _refresh_listen_mode()
        # if session ended mid-work, stop path owns final idle write after drain


def _capture_loop() -> None:
    """Read PCM, endpoint by pause, enqueue utterances. Never blocks on STT."""
    global _capture, _endpointer, _in_speech
    cap = _capture
    ep = _endpointer
    if cap is None or ep is None:
        return
    max_min = float(_config.get("listen_max_minutes", 30))
    deadline = time.monotonic() + max_min * 60.0 if max_min > 0 else float("inf")
    try:
        while _listening.is_set():
            if time.monotonic() >= deadline:
                log.info("listen_max_minutes reached — auto-stop")
                # ponytail: auto-stop via side thread; per-session cancel token if races appear
                threading.Thread(target=dictate_stop, name="wbv-listen-cap", daemon=True).start()
                break
            chunk = cap.read_chunk()
            if not chunk:
                if not _listening.is_set():
                    break
                time.sleep(0.01)
                continue
            lvl = audio.rms_level(chunk)
            state.write_state(level=lvl)
            finished = ep.feed(lvl, chunk)
            if ep.in_speech != _in_speech:
                _in_speech = ep.in_speech
                _refresh_listen_mode()
            for utt in finished:
                _enqueue_utterance(utt)
                # after utterance end, ep leaves speech — sync mode
                if ep.in_speech != _in_speech:
                    _in_speech = ep.in_speech
                    _refresh_listen_mode()
    except Exception as e:  # noqa: BLE001
        log.exception("capture loop failed")
        _listening.clear()
        _set_error(f"capture error: {e}")
    finally:
        _in_speech = False


def _stop_capture_hw() -> list[bytes]:
    """Stop pw-record + capture thread; return flushed utterances from endpointer."""
    global _capture, _capture_thread, _endpointer, _in_speech
    _listening.clear()
    cap = _capture
    _capture = None
    if cap is not None:
        cap.stop()
    thr = _capture_thread
    _capture_thread = None
    if thr is not None:
        thr.join(timeout=2.0)
    flushed: list[bytes] = []
    ep = _endpointer
    _endpointer = None
    if ep is not None:
        flushed = ep.flush()
    _in_speech = False
    return flushed


def _drain_worker(timeout: float = 30.0) -> None:
    """Wait until utterance queue empty and worker not busy."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _utt_q.empty() and not _worker_busy:
            return
        time.sleep(0.02)


def _listen_busy_modes() -> frozenset[str]:
    return frozenset({"listening", "dictating", "transcribing"})


def dictate_start(_args: dict | None = None) -> dict:
    global _capture, _capture_thread, _endpointer, _in_speech
    # IPC serves one thread per connection, so a double-tapped hotkey really does race
    # here. Without the lock both callers saw mode==idle and each spawned a pw-record,
    # leaving an orphan holding the mic open. Whole check-then-act must be atomic.
    with _session_lock:
        st = state.get_state()
        mode = st.get("mode")
        if mode in _listen_busy_modes() or _session_active():
            return {"mode": mode, "note": "already listening"}
        if mode == "meeting":
            return {"ok": False, "error": "meeting active — stop meeting first"}
        if _model is None:
            from waylandbettervoice.models import short_name_for

            want = _config.get("model", "ggml-large-v3.bin")
            return {
                "ok": False,
                "error": (
                    f"model not loaded ({want}). "
                    f"Run: wbv model download {short_name_for(want)}"
                ),
            }

        ep = _make_endpointer()
        try:
            cap = audio.Capture()
            cap.start()
        except Exception as e:  # noqa: BLE001
            _set_error(f"failed to start capture: {e}")
            return {"ok": False, "error": str(e)}

        _endpointer = ep
        _capture = cap
        _in_speech = False
        _listening.set()
        state.write_state(mode="listening", error=None, level=0.0, last_text=st.get("last_text", ""))
        _capture_thread = threading.Thread(target=_capture_loop, name="wbv-capture", daemon=True)
        _capture_thread.start()
        return {"mode": "listening"}


def dictate_stop(_args: dict | None = None) -> dict:
    with _session_lock:
        st = state.get_state()
        if not _session_active() and st.get("mode") not in _listen_busy_modes():
            return {"mode": st.get("mode"), "note": "not listening"}
        flushed = _stop_capture_hw()
    for utt in flushed:
        _enqueue_utterance(utt)

    # show transcribing while worker drains remaining utterances
    if not _utt_q.empty() or _worker_busy:
        state.write_state(mode="transcribing", level=0.0)
        _drain_worker(timeout=60.0)

    state.write_state(mode="idle", level=0.0, error=None)
    return {"mode": "idle"}


def dictate_cancel(_args: dict | None = None) -> dict:
    with _session_lock:
        _stop_capture_hw()
    # drop queued utterances without processing
    try:
        while True:
            _utt_q.get_nowait()
    except queue.Empty:
        pass
    state.write_state(mode="idle", level=0.0, error=None)
    return {"mode": "idle", "cancelled": True}


def dictate_toggle(_args: dict | None = None) -> dict:
    # RLock: dictate_start/stop re-acquire it. Decide and act atomically so two
    # rapid toggles cannot both read "idle" and both start a capture.
    with _session_lock:
        st = state.get_state()
        mode = st.get("mode")
        if _session_active() or mode in _listen_busy_modes():
            return dictate_stop(_args)
        if mode == "meeting":
            return {"ok": False, "error": "meeting active"}
        return dictate_start(_args)


def _meeting_mod():
    """Lazy-import meeting module (agent B)."""
    # ponytail: lazy import so core daemon starts without meeting.py present
    import waylandbettervoice.meeting as m  # type: ignore
    return m


def _on_meeting_state(fields: dict) -> None:
    """Callback meeting module uses to push state updates."""
    state.write_state(**fields)


def meeting_start(_args: dict | None = None) -> dict:
    with _session_lock:
        st = state.get_state()
        if _session_active() or st.get("mode") in _listen_busy_modes():
            return {"ok": False, "error": "dictation active — stop first"}
    try:
        mod = _meeting_mod()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"meeting module unavailable: {e}"}
    try:
        result = mod.start(_config, _on_meeting_state)
        return result if isinstance(result, dict) else {"ok": True, "data": result}
    except Exception as e:  # noqa: BLE001
        log.exception("meeting.start failed")
        return {"ok": False, "error": str(e)}


def meeting_stop(_args: dict | None = None) -> dict:
    try:
        mod = _meeting_mod()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"meeting module unavailable: {e}"}
    try:
        result = mod.stop()
    except Exception as e:  # noqa: BLE001
        log.exception("meeting.stop failed")
        _set_error(f"meeting stop failed: {e}")
        return {"ok": False, "error": str(e)}
    # meeting.py stops pushing state once it returns — the daemon owns the reset.
    info = result if isinstance(result, dict) else {"active": False, "file": None, "speakers": 0, "elapsed": 0}
    info["active"] = False
    state.write_state(mode="idle", level=0.0, error=None, meeting=info)
    return info


def meeting_toggle(_args: dict | None = None) -> dict:
    try:
        mod = _meeting_mod()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"meeting module unavailable: {e}"}
    try:
        active = bool(mod.is_active())
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    if active:
        return meeting_stop(_args)
    return meeting_start(_args)


def cmd_status(_args: dict | None = None) -> dict:
    st = state.get_state()
    # refresh meeting sub-dict if module present
    try:
        mod = _meeting_mod()
        info = mod.info()
        if isinstance(info, dict):
            st["meeting"] = info
            state.write_state(meeting=info)
    except Exception:
        pass
    return st


def cmd_reload(_args: dict | None = None) -> dict:
    global _config
    _config = load_config()
    state.write_state(model=_config.get("model", "ggml-large-v3.bin"))
    return {
        "reloaded": True,
        "config": {
            k: _config[k]
            for k in (
                "model",
                "language",
                "n_threads",
                "beam_size",
                "silence_seconds",
                "vad_start_level",
                "vad_stop_level",
            )
            if k in _config
        },
    }


def cmd_quit(_args: dict | None = None) -> dict:
    _quit_event.set()
    if _session_active():
        dictate_cancel()
    return {"quitting": True}


def _build_dispatch() -> dict:
    return {
        "dictate.toggle": dictate_toggle,
        "dictate.start": dictate_start,
        "dictate.stop": dictate_stop,
        "dictate.cancel": dictate_cancel,
        "meeting.toggle": meeting_toggle,
        "meeting.start": meeting_start,
        "meeting.stop": meeting_stop,
        "status": cmd_status,
        "reload": cmd_reload,
        "quit": cmd_quit,
    }


def run(foreground: bool = True) -> int:
    """Start daemon: mkdirs, load config+model, serve IPC until quit."""
    global _config, _model, _server, _worker_thread
    _setup_logging()
    mkdirs()
    _config = load_config()
    log.info("waylandbettervoice daemon starting (socket=%s)", SOCKET_PATH)

    state.write_state(
        mode="idle",
        model=_config.get("model", "ggml-large-v3.bin"),
        model_loaded=False,
        error=None,
        level=0.0,
        last_text="",
        meeting={"active": False, "file": None, "speakers": 0, "elapsed": 0},
    )

    try:
        _model = stt.load_model(_config)
        state.write_state(model_loaded=True)
    except stt.ModelMissingError as e:
        # actionable — no silent auto-download (a multi-GB surprise is hostile)
        msg = str(e)
        log.error("%s", msg)
        state.write_state(model_loaded=False, mode="error", error=msg)
        _notify(msg.splitlines()[0])
        _model = None
    except Exception as e:  # noqa: BLE001
        log.exception("model load failed")
        state.write_state(model_loaded=False, mode="error", error=f"model load failed: {e}")
        _notify(f"model load failed: {e}")
        # still serve IPC so status/quit work; dictate will refuse
        _model = None

    _worker_thread = threading.Thread(target=_worker_loop, name="wbv-stt-worker", daemon=True)
    _worker_thread.start()

    _server = ipc.Server(path=SOCKET_PATH, dispatch=_build_dispatch())
    _server.start()

    # systemd stop/logout sends SIGTERM. Default handling exits immediately and an
    # in-progress meeting loses its transcript (raw files only). Turn TERM/INT into
    # the normal shutdown path so the finally block can finalize the meeting.
    def _on_term(signum, _frame):
        log.info("signal %s — shutting down", signum)
        _quit_event.set()

    for _sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(_sig, _on_term)
        except (ValueError, OSError):  # not main thread / unsupported
            pass

    log.info("daemon ready")

    try:
        while not _quit_event.is_set():
            _quit_event.wait(0.5)
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt — shutting down")
    finally:
        if _session_active():
            dictate_cancel()
        # Finalize an active meeting so transcript.md/json and mix.wav get written.
        try:
            _mod = sys.modules.get("waylandbettervoice.meeting")
            if _mod is not None and _mod.is_active():
                log.info("finalizing active meeting before shutdown")
                _mod.stop()
        except Exception:  # noqa: BLE001
            log.exception("meeting finalize on shutdown failed")
        # stop worker
        _utt_q.put(None)
        if _worker_thread is not None:
            _worker_thread.join(timeout=5.0)
            _worker_thread = None
        if _server is not None:
            _server.stop()
            _server = None
        state.write_state(mode="idle", level=0.0, model_loaded=False)
        log.info("daemon stopped")
    return 0
