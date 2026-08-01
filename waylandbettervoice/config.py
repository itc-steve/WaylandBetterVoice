"""Paths, defaults, load/save for waylandbettervoice config."""
from __future__ import annotations

import json
import os
from pathlib import Path

RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "waylandbettervoice"
SOCKET_PATH = RUNTIME_DIR / "wbv.sock"
STATE_PATH = RUNTIME_DIR / "state.json"
DATA_DIR = Path.home() / ".local/share/waylandbettervoice"
MODEL_DIR = DATA_DIR / "models"
MEETING_DIR = DATA_DIR / "meetings"
LOG_PATH = DATA_DIR / "wbv.log"
CONFIG_PATH = Path.home() / ".config/waylandbettervoice/config.json"

DEFAULTS: dict = {
    "model": "ggml-large-v3.bin",
    "language": "en",
    "n_threads": 8,
    "beam_size": 5,
    # ponytail: VAD thresholds tuned for Shure MV6 via EasyEffects — retune if quiet mic gets eaten
    "dictation_max_seconds": 30,  # cap on ONE utterance (not session)
    "listen_max_minutes": 30,  # hard cap on a continuous listening session
    "silence_seconds": 0.8,  # pause that ends an utterance
    "vad_start_level": 0.015,  # RMS to consider speech started
    "vad_stop_level": 0.008,  # RMS below this counts as silence
    "min_utterance_seconds": 0.4,  # shorter -> discarded
    "preroll_seconds": 0.3,  # audio kept before speech onset
    "meeting_max_minutes": 180,
    "inject_method": "wtype",
    "wtype_delay_ms": 0,
    "trailing_space": True,
    "meeting_diarization": True,
    "meeting_speakers": 0,
    "meeting_chunk_seconds": 12,
    "notify_on_error": True,
}


def mkdirs() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    MEETING_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """Merge user JSON over defaults. Missing/invalid file → defaults only."""
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.is_file():
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                user = json.load(f)
            if isinstance(user, dict):
                cfg.update(user)
        except (OSError, json.JSONDecodeError):
            pass
    return cfg


def save_config(cfg: dict) -> None:
    mkdirs()
    tmp = CONFIG_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    os.replace(tmp, CONFIG_PATH)
