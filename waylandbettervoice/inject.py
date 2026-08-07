"""Type text into focused window via wtype/ydotool; clipboard fallback.

inject_method (config):
  wtype     — Wayland virtual_keyboard. Fast, no daemon, but uploads its own
              keymap, which Electron/xterm.js apps (Termius, VS Code) ignore —
              they decode the raw keycodes against the system layout and you
              get garbage like "22345" for "Hello". Fine for native terminals.
  ydotool   — kernel uinput, so every app sees ordinary key events. Needs
              ydotoold running and access to /dev/uinput.
  clipboard — wl-copy only; paste manually.
"""
from __future__ import annotations

import logging
import shutil
import subprocess

log = logging.getLogger("wbv.inject")


def type_text(text: str, trailing_space: bool = True, method: str = "wtype") -> None:
    """Inject text with the configured method. On failure → wl-copy + notify-send."""
    if text is None:
        return
    s = text
    if trailing_space and s and not s.endswith(" "):
        s = s + " "
    if not s:
        return

    # "--" so leading dashes in text are not parsed as flags
    argv = {
        "wtype": lambda exe: [exe, "--", s],
        "ydotool": lambda exe: [exe, "type", "--", s],
    }.get(method)

    if argv:
        exe = shutil.which(method)
        if exe:
            try:
                subprocess.run(argv(exe), check=True, timeout=30)
                return
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
                log.warning("%s failed: %s — falling back to clipboard", method, e)
        else:
            log.warning("%s not installed — falling back to clipboard", method)
    elif method != "clipboard":
        log.warning("unknown inject_method %r — using clipboard", method)

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
                [notify, "WaylandBetterVoice", "Transcription copied to clipboard — paste to insert"],
                check=False,
                timeout=5,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass
