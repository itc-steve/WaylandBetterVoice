"""Daemon orchestrator: load model, IPC, dictation flow. Meeting via lazy import."""
from __future__ import annotations

import logging
import shutil
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
_pcm_buf = bytearray()
_pcm_lock = threading.Lock()
_capturing = threading.Event()
_max_deadline = 0.0
_server: Optional[ipc.Server] = None
_quit_event = threading.Event()


def _setup_logging() -> None:
    mkdirs()
    root = logging.getLogger()
    if not root.handlers:
        root.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
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


def _capture_loop() -> None:
    """Read PCM chunks, push level updates, enforce dictation_max_seconds."""
    global _capture
    cap = _capture
    if cap is None:
        return
    max_s = float(_config.get("dictation_max_seconds", 300))
    deadline = time.monotonic() + max_s if max_s > 0 else float("inf")
    try:
        while _capturing.is_set():
            if time.monotonic() >= deadline:
                log.info("dictation_max_seconds reached — auto-stop")
                # ponytail: auto-stop via side thread; per-session cancel token if races appear
                threading.Thread(target=dictate_stop, name="wbv-auto-stop", daemon=True).start()
                break
            chunk = cap.read_chunk()
            if not chunk:
                if not _capturing.is_set():
                    break
                time.sleep(0.01)
                continue
            with _pcm_lock:
                _pcm_buf.extend(chunk)
            lvl = audio.rms_level(chunk)
            state.write_state(level=lvl)
    except Exception as e:  # noqa: BLE001
        log.exception("capture loop failed")
        _set_error(f"capture error: {e}")
    finally:
        _capturing.clear()


def dictate_start(_args: dict | None = None) -> dict:
    global _capture, _capture_thread
    st = state.get_state()
    if st.get("mode") in ("dictating", "transcribing"):
        return {"mode": st.get("mode"), "note": "already active"}
    if st.get("mode") == "meeting":
        return {"ok": False, "error": "meeting active — stop meeting first"}
    if _model is None:
        return {"ok": False, "error": "model not loaded"}

    with _pcm_lock:
        _pcm_buf.clear()
    try:
        cap = audio.Capture()
        cap.start()
    except Exception as e:  # noqa: BLE001
        _set_error(f"failed to start capture: {e}")
        return {"ok": False, "error": str(e)}

    _capture = cap
    _capturing.set()
    state.write_state(mode="dictating", error=None, level=0.0, last_text="")
    _capture_thread = threading.Thread(target=_capture_loop, name="wbv-capture", daemon=True)
    _capture_thread.start()
    return {"mode": "dictating"}


def dictate_stop(_args: dict | None = None) -> dict:
    global _capture, _capture_thread
    st = state.get_state()
    if st.get("mode") != "dictating" and not _capturing.is_set():
        return {"mode": st.get("mode"), "note": "not dictating"}

    _capturing.clear()
    cap = _capture
    _capture = None
    if cap is not None:
        cap.stop()
    thr = _capture_thread
    _capture_thread = None
    if thr is not None:
        thr.join(timeout=2.0)

    with _pcm_lock:
        pcm = bytes(_pcm_buf)
        _pcm_buf.clear()

    # ponytail: flat RMS gate — whisper hallucinates ("Thank you.") on pure silence.
    # Tuned for a Shure MV6 through EasyEffects; promote to config if a quiet mic gets eaten.
    if len(pcm) < audio.SAMPLE_RATE // 2 * 2 or audio.rms_level(pcm) < 0.004:
        log.info("discarding near-silent dictation (%d bytes)", len(pcm))
        state.write_state(mode="idle", level=0.0, error=None)
        return {"mode": "idle", "text": "", "note": "silence"}

    state.write_state(mode="transcribing", level=0.0)
    try:
        text = stt.transcribe(_model, pcm) if _model is not None else ""
    except Exception as e:  # noqa: BLE001
        _set_error(f"transcribe failed: {e}")
        return {"ok": False, "error": str(e)}

    try:
        inject.type_text(text, trailing_space=bool(_config.get("trailing_space", True)))
    except Exception as e:  # noqa: BLE001
        _set_error(f"inject failed: {e}")
        return {"ok": False, "error": str(e), "text": text}

    state.write_state(mode="idle", last_text=text, error=None, level=0.0)
    return {"mode": "idle", "text": text}


def dictate_cancel(_args: dict | None = None) -> dict:
    global _capture, _capture_thread
    _capturing.clear()
    cap = _capture
    _capture = None
    if cap is not None:
        cap.stop()
    thr = _capture_thread
    _capture_thread = None
    if thr is not None:
        thr.join(timeout=2.0)
    with _pcm_lock:
        _pcm_buf.clear()
    state.write_state(mode="idle", level=0.0, error=None)
    return {"mode": "idle", "cancelled": True}


def dictate_toggle(_args: dict | None = None) -> dict:
    st = state.get_state()
    mode = st.get("mode")
    if mode == "dictating":
        return dictate_stop(_args)
    if mode in ("idle", "error", None):
        return dictate_start(_args)
    if mode == "transcribing":
        return {"ok": False, "error": "busy transcribing"}
    if mode == "meeting":
        return {"ok": False, "error": "meeting active"}
    return dictate_start(_args)


def _meeting_mod():
    """Lazy-import meeting module (agent B)."""
    # ponytail: lazy import so core daemon starts without meeting.py present
    import waylandbettervoice.meeting as m  # type: ignore
    return m


def _meeting_call(fn_name: str, *a, **kw) -> dict:
    try:
        mod = _meeting_mod()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"meeting module unavailable: {e}"}
    fn = getattr(mod, fn_name, None)
    if fn is None:
        return {"ok": False, "error": f"meeting.{fn_name} missing"}
    try:
        return fn(*a, **kw) if fn_name != "info" else {"meeting": fn(*a, **kw)}
    except Exception as e:  # noqa: BLE001
        log.exception("meeting.%s failed", fn_name)
        return {"ok": False, "error": str(e)}


def _on_meeting_state(fields: dict) -> None:
    """Callback meeting module uses to push state updates."""
    state.write_state(**fields)


def meeting_start(_args: dict | None = None) -> dict:
    st = state.get_state()
    if st.get("mode") == "dictating":
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
    return {"reloaded": True, "config": {k: _config[k] for k in ("model", "language", "n_threads", "beam_size") if k in _config}}


def cmd_quit(_args: dict | None = None) -> dict:
    _quit_event.set()
    # stop capture if any
    if _capturing.is_set():
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
    global _config, _model, _server
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
    except Exception as e:  # noqa: BLE001
        log.exception("model load failed")
        state.write_state(model_loaded=False, mode="error", error=f"model load failed: {e}")
        _notify(f"model load failed: {e}")
        # still serve IPC so status/quit work; dictate will refuse
        _model = None

    _server = ipc.Server(path=SOCKET_PATH, dispatch=_build_dispatch())
    _server.start()
    log.info("daemon ready")

    try:
        while not _quit_event.is_set():
            _quit_event.wait(0.5)
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt — shutting down")
    finally:
        if _capturing.is_set():
            dictate_cancel()
        if _server is not None:
            _server.stop()
            _server = None
        state.write_state(mode="idle", level=0.0, model_loaded=False)
        log.info("daemon stopped")
    return 0
