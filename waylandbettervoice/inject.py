"""Type text into focused window via wtype; clipboard fallback."""
from __future__ import annotations

import logging
import shutil
import subprocess

log = logging.getLogger("wbv.inject")


def type_text(text: str, trailing_space: bool = True) -> None:
    """Inject text with wtype. On failure/missing → wl-copy + notify-send."""
    if text is None:
        return
    s = text
    if trailing_space and s and not s.endswith(" "):
        s = s + " "
    if not s:
        return

    wtype = shutil.which("wtype")
    if wtype:
        try:
            # "--" so leading dashes in text are not parsed as flags
            subprocess.run([wtype, "--", s], check=True, timeout=30)
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
            log.warning("wtype failed: %s — falling back to clipboard", e)

    # fallback: clipboard + notify (no synthetic Ctrl+V)
    _clipboard_fallback(s)


def _clipboard_fallback(s: str) -> None:
    wl_copy = shutil.which("wl-copy")
    if wl_copy:
        try:
            subprocess.run([wl_copy], input=s.encode("utf-8"), check=True, timeout=5)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
            log.error("wl-copy failed: %s", e)
            return
    else:
        log.error("neither wtype nor wl-copy available")
        return
    notify = shutil.which("notify-send")
    if notify:
        try:
            subprocess.run(
                [notify, "WaylandBetterVoice", "Transcription copied to clipboard (wtype unavailable)"],
                check=False,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass
