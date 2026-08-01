"""PipeWire capture via pw-record subprocess. No Python audio libs."""
from __future__ import annotations

import json
import logging
import subprocess
from typing import Optional

import numpy as np

log = logging.getLogger("wbv.audio")

# 16-bit mono @ 16 kHz, 20 ms frames → 320 samples → 640 bytes
SAMPLE_RATE = 16000
CHANNELS = 1
BYTES_PER_SAMPLE = 2
CHUNK_MS = 20
CHUNK_BYTES = SAMPLE_RATE * CHANNELS * BYTES_PER_SAMPLE * CHUNK_MS // 1000  # 640


def resolve_default_source() -> str:
    """pactl get-default-source → mic node name."""
    out = subprocess.check_output(
        ["pactl", "get-default-source"], text=True, stderr=subprocess.DEVNULL
    )
    return out.strip()


def resolve_default_sink() -> str:
    """Sink NODE name to record system audio from (no '.monitor').

    The default sink is not always where audio really plays: with EasyEffects (or any
    filter chain) apps land in the effects sink and only its output reaches the device,
    so recording the default sink captures silence. Prefer a sink that currently has
    playback streams attached; fall back to the default sink when nothing is playing.
    """
    active = _sink_with_inputs()
    if active:
        return active
    out = subprocess.check_output(
        ["pactl", "get-default-sink"], text=True, stderr=subprocess.DEVNULL
    )
    sink = out.strip()
    if not sink:
        raise RuntimeError("pactl get-default-sink returned empty")
    return sink


def _sink_with_inputs() -> Optional[str]:
    """Sink that is actually carrying audio: RUNNING state, else most playback streams.

    On this machine the default sink is the Shure MV6 while EasyEffects re-routes apps
    to a different device, so 'default' and 'where sound is' are not the same node.
    """
    try:
        sinks = json.loads(subprocess.check_output(
            ["pactl", "-f", "json", "list", "sinks"], text=True, stderr=subprocess.DEVNULL
        ))
        running = [s for s in sinks if str(s.get("state", "")).upper() == "RUNNING"]
        if running:
            # Physical device wins over filter sinks: it carries the final mix.
            hardware = [s for s in running if str(s.get("name", "")).startswith("alsa_")]
            return (hardware or running)[0].get("name")

        inputs = json.loads(subprocess.check_output(
            ["pactl", "-f", "json", "list", "sink-inputs"], text=True, stderr=subprocess.DEVNULL
        ))
        counts: dict[int, int] = {}
        for stream in inputs:
            sink_id = stream.get("sink")
            if isinstance(sink_id, int):
                counts[sink_id] = counts.get(sink_id, 0) + 1
        if counts:
            busiest = max(counts, key=lambda k: counts[k])
            for sink in sinks:
                if sink.get("index") == busiest:
                    return sink.get("name")
    except (subprocess.CalledProcessError, OSError, ValueError, json.JSONDecodeError) as e:
        log.debug("sink probe failed: %s", e)
    return None


def resolve_default_sink_monitor() -> str:
    """Back-compat alias. Returns the sink node name; use Capture(..., monitor=True)."""
    return resolve_default_sink()


def rms_level(pcm_bytes: bytes) -> float:
    """RMS of int16 PCM → 0..1 (clamped). Empty → 0.0."""
    if not pcm_bytes:
        return 0.0
    # need even length for int16
    n = len(pcm_bytes) - (len(pcm_bytes) % 2)
    if n == 0:
        return 0.0
    samples = np.frombuffer(pcm_bytes[:n], dtype=np.int16).astype(np.float32)
    if samples.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(samples * samples)))
    # int16 full-scale ≈ 32768
    return max(0.0, min(1.0, rms / 32768.0))


class Capture:
    """pw-record → stdout raw s16le mono 16kHz."""

    def __init__(self, target: Optional[str] = None, monitor: bool = False):
        self.target = target  # None → resolve at start()
        self.monitor = monitor  # True → capture a sink's output (system audio)
        self._proc: Optional[subprocess.Popen] = None

    def start(self) -> None:
        if self._proc is not None:
            return
        if self.target:
            node = self.target
        else:
            node = resolve_default_sink() if self.monitor else resolve_default_source()
        # A '.monitor' target makes pw-record record silence; the sink node plus
        # stream.capture.sink=true is the working form. Strip the suffix defensively.
        if self.monitor and node.endswith(".monitor"):
            node = node[: -len(".monitor")]
        cmd = ["pw-record"]
        if self.monitor:
            cmd += ["-P", "{ stream.capture.sink=true }"]
        cmd += [
            "--target", node,
            "--rate", str(SAMPLE_RATE),
            "--channels", str(CHANNELS),
            "--format", "s16",
            "--latency", "20ms",
            "-",
        ]
        log.info("starting capture: %s", " ".join(cmd))
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )

    def read_chunk(self, nbytes: int = CHUNK_BYTES) -> bytes:
        """Blocking read of up to nbytes. b'' means EOF / stopped."""
        if self._proc is None or self._proc.stdout is None:
            return b""
        try:
            data = self._proc.stdout.read(nbytes)
            return data or b""
        except (OSError, ValueError):
            return b""

    def stop(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1.0)
        except OSError:
            pass
        # drain stderr for diagnostics (ponytail: ignore if already closed)
        try:
            if proc.stderr:
                err = proc.stderr.read()
                if err:
                    log.debug("pw-record stderr: %s", err.decode("utf-8", errors="replace")[:500])
        except OSError:
            pass
