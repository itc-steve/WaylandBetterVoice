"""pywhispercpp model wrapper — load once, keep VRAM-resident."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np
from pywhispercpp.model import Model

from waylandbettervoice.config import LOG_PATH, MODEL_DIR
from waylandbettervoice.models import short_name_for

log = logging.getLogger("wbv.stt")


class ModelMissingError(FileNotFoundError):
    """Raised when the configured ggml model is not on disk."""


def resolve_model_path(name: str) -> Path:
    """Resolve config model name to a file under MODEL_DIR only."""
    primary = MODEL_DIR / name
    if primary.is_file():
        log.info("using model: %s", primary)
        return primary
    short = short_name_for(name)
    raise ModelMissingError(
        f"model not found: {primary}\n"
        f"Download it with: wbv model download {short}"
    )


def load_model(config: dict) -> Model:
    """Load whisper model once at daemon start. language=en, translate=False."""
    name = config.get("model", "ggml-large-v3.bin")
    path = resolve_model_path(name)
    n_threads = int(config.get("n_threads", 8))
    beam_size = int(config.get("beam_size", 5))
    language = config.get("language", "en") or "en"

    # beam_size > 1 → BEAM_SEARCH strategy (params_sampling_strategy != 0)
    strategy = 1 if beam_size and beam_size > 1 else 0
    params: dict = {
        "language": language,
        "translate": False,
        "n_threads": n_threads,
        "print_realtime": False,
        "print_progress": False,
    }
    if strategy:
        # pywhispercpp accepts beam_search dict via **params
        params["beam_search"] = {"beam_size": beam_size, "patience": -1.0}

    log.info("loading whisper model %s (threads=%s beam=%s)", path, n_threads, beam_size)
    model = Model(
        model=str(path),
        models_dir=None,
        params_sampling_strategy=strategy,
        redirect_whispercpp_logs_to=str(LOG_PATH),
        **params,
    )
    log.info("model loaded")
    return model


# whisper.cpp's CUDA scheduler is NOT re-entrant. Two concurrent whisper_full calls
# abort the process (GGML_ASSERT(!sched->is_alloc) -> SIGABRT, verified with a core
# dump when a meeting's mic and system threads transcribed at the same time).
# Every path into the model goes through this lock.
_model_lock = threading.Lock()


def transcribe(model: Model, pcm_int16_bytes: bytes) -> str:
    """Convert int16 PCM → float32 [-1,1], run model.transcribe, join segment text.

    Serialized: concurrent calls queue instead of crashing the daemon.
    """
    if not pcm_int16_bytes:
        return ""
    n = len(pcm_int16_bytes) - (len(pcm_int16_bytes) % 2)
    if n == 0:
        return ""
    # ponytail: whole-utterance buffer in RAM; stream/VAD if dictation_max_seconds grows huge
    audio = np.frombuffer(pcm_int16_bytes[:n], dtype=np.int16).astype(np.float32) / 32768.0
    with _model_lock:
        segments = model.transcribe(audio)
    parts = []
    for seg in segments or []:
        t = (seg.text or "").strip()
        if t:
            parts.append(t)
    return " ".join(parts).strip()
