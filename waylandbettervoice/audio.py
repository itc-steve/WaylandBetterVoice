"""PipeWire capture via pw-record subprocess. No Python audio libs."""
from __future__ import annotations

import json
import logging
import subprocess
from collections import deque
from typing import Optional

import numpy as np

log = logging.getLogger("wbv.audio")

# 16-bit mono @ 16 kHz, 20 ms frames → 320 samples → 640 bytes
SAMPLE_RATE = 16000
CHANNELS = 1
BYTES_PER_SAMPLE = 2
CHUNK_MS = 20
CHUNK_BYTES = SAMPLE_RATE * CHANNELS * BYTES_PER_SAMPLE * CHUNK_MS // 1000  # 640
FRAME_SECONDS = CHUNK_MS / 1000.0  # 0.02
# ~120 ms of consecutive loud frames before speech onset locks in
START_HANG_SECONDS = 0.12


def resolve_default_source() -> str:
    """pactl get-default-source → mic node name."""
    out = subprocess.check_output(
        ["pactl", "get-default-source"], text=True, stderr=subprocess.DEVNULL
    )
    return out.strip()


def resolve_default_sink() -> str:
    """Sink NODE name to record system audio from (no '.monitor').

    The default sink is not always where audio really plays: with EasyEffects (or any
    filter chain) apps land in the effects sink while output reaches another device.
    Prefer the sink carrying playback streams, including an idle EasyEffects sink before
    playback starts; fall back to a running or default sink.
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
    """Sink carrying playback streams, else any running sink.

    Record the application-facing sink (for example EasyEffects), not an unrelated
    running hardware sink. Filter routing may send that mix to a non-default device.
    """
    try:
        sinks = json.loads(subprocess.check_output(
            ["pactl", "-f", "json", "list", "sinks"], text=True, stderr=subprocess.DEVNULL
        ))
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

        # EasyEffects routes future app streams here even while the sink is suspended.
        effects = next((s for s in sinks if s.get("name") == "easyeffects_sink"), None)
        if effects:
            return effects.get("name")

        running = [s for s in sinks if str(s.get("state", "")).upper() == "RUNNING"]
        if running:
            return running[0].get("name")
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
        cmd = ["pw-record", "--raw"]
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


def _frames(seconds: float) -> int:
    return max(1, int(round(seconds / FRAME_SECONDS)))


class Endpointer:
    """RMS silence endpointing over 20 ms frames. Pure — no hardware.

    Speech starts after ~120 ms of frames above vad_start_level.
    Utterance ends after silence_seconds of frames below vad_stop_level,
    or when max_utterance_seconds is hit. Pre-roll is prepended at onset.
    """

    def __init__(
        self,
        silence_seconds: float = 0.8,
        vad_start_level: float = 0.015,
        vad_stop_level: float = 0.008,
        min_utterance_seconds: float = 0.4,
        preroll_seconds: float = 0.3,
        max_utterance_seconds: float = 30.0,
        start_hang_seconds: float = START_HANG_SECONDS,
        chunk_bytes: int = CHUNK_BYTES,
    ):
        # ponytail: fixed-frame VAD; replace with webrtcvad/silero if RMS blips mis-fire
        self.silence_seconds = float(silence_seconds)
        self.vad_start_level = float(vad_start_level)
        self.vad_stop_level = float(vad_stop_level)
        self.min_utterance_seconds = float(min_utterance_seconds)
        self.preroll_seconds = float(preroll_seconds)
        self.max_utterance_seconds = float(max_utterance_seconds)
        self.start_hang_seconds = float(start_hang_seconds)
        self.chunk_bytes = int(chunk_bytes)

        self._start_need = _frames(self.start_hang_seconds)
        self._silence_need = _frames(self.silence_seconds)
        self._preroll_max = _frames(self.preroll_seconds)
        self._max_frames = _frames(self.max_utterance_seconds)
        self._min_bytes = int(self.min_utterance_seconds * SAMPLE_RATE) * BYTES_PER_SAMPLE

        self._preroll: deque[bytes] = deque(maxlen=self._preroll_max)
        self._buf = bytearray()
        self._in_speech = False
        self._start_count = 0
        self._silence_count = 0
        self._speech_frames = 0

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def reset(self) -> None:
        self._preroll.clear()
        self._buf.clear()
        self._in_speech = False
        self._start_count = 0
        self._silence_count = 0
        self._speech_frames = 0

    def feed(self, rms: float, chunk: bytes) -> list[bytes]:
        """Feed one (rms, chunk) frame. Returns zero or more finished utterances."""
        if not chunk:
            return []
        if not self._in_speech:
            return self._feed_idle(rms, chunk)
        return self._feed_speech(rms, chunk)

    def flush(self) -> list[bytes]:
        """Force-end in-progress utterance (if long enough). Resets state."""
        out: list[bytes] = []
        if self._in_speech and self._buf:
            utt = bytes(self._buf)
            if len(utt) >= self._min_bytes:
                out.append(utt)
        self.reset()
        return out

    def _feed_idle(self, rms: float, chunk: bytes) -> list[bytes]:
        self._preroll.append(chunk)
        if rms > self.vad_start_level:
            self._start_count += 1
        else:
            self._start_count = 0
        if self._start_count < self._start_need:
            return []
        # onset: preroll already holds the hang frames + prior audio
        self._in_speech = True
        self._buf = bytearray(b"".join(self._preroll))
        self._preroll.clear()
        self._start_count = 0
        self._silence_count = 0
        self._speech_frames = max(1, len(self._buf) // self.chunk_bytes)
        return []

    def _feed_speech(self, rms: float, chunk: bytes) -> list[bytes]:
        self._buf.extend(chunk)
        self._speech_frames += 1
        if rms < self.vad_stop_level:
            self._silence_count += 1
        else:
            self._silence_count = 0

        hit_silence = self._silence_count >= self._silence_need
        hit_max = self._speech_frames >= self._max_frames
        if not (hit_silence or hit_max):
            return []

        utt = bytes(self._buf)
        self._buf.clear()
        self._in_speech = False
        self._silence_count = 0
        self._speech_frames = 0
        self._start_count = 0
        # if still loud after max-cap, next frames re-enter via hang
        if len(utt) >= self._min_bytes:
            return [utt]
        return []
