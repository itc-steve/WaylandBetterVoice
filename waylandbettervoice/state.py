"""Atomic state.json writer for the Noctalia plugin."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from waylandbettervoice.config import STATE_PATH

log = logging.getLogger("wbv.state")

# contract defaults
_DEFAULT_STATE: dict[str, Any] = {
    "mode": "idle",
    "since": 0.0,
    "level": 0.0,
    "model": "ggml-large-v3.bin",
    "model_loaded": False,
    "inject_method": "wtype",
    "meeting": {"active": False, "file": None, "speakers": 0, "elapsed": 0},
    "last_text": "",
    "error": None,
}

_lock = threading.Lock()
_state: dict[str, Any] = dict(_DEFAULT_STATE)
_last_level_write = 0.0
_LEVEL_MIN_INTERVAL = 0.1  # 10 Hz
# ponytail: global lock + in-process state; multi-writer only if second process appears


def get_state() -> dict:
    with _lock:
        return json.loads(json.dumps(_state))  # deep-ish copy via json


def write_state(path: Path | None = None, **fields: Any) -> dict:
    """Merge fields into state and atomically write. Level-only updates ≤10 Hz."""
    global _last_level_write
    out = path or STATE_PATH
    with _lock:
        # throttle pure level-only updates
        keys = set(fields.keys())
        if keys == {"level"}:
            now = time.monotonic()
            if now - _last_level_write < _LEVEL_MIN_INTERVAL:
                _state["level"] = float(fields["level"])
                return dict(_state)
            _last_level_write = now

        if "mode" in fields and fields["mode"] != _state.get("mode"):
            fields.setdefault("since", time.time())

        _state.update(fields)
        # always stamp since on first write if still 0
        if not _state.get("since"):
            _state["since"] = time.time()

        snapshot = dict(_state)
        # meeting may be nested — keep a shallow copy of nested dict
        if isinstance(snapshot.get("meeting"), dict):
            snapshot["meeting"] = dict(snapshot["meeting"])

        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, separators=(",", ":"))
        os.replace(tmp, out)
        return snapshot
