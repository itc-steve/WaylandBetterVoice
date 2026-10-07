"""Meeting capture, transcript writing, and optional remote-speaker labels."""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from . import audio, config, stt

_RATE = 16000
_SAMPLE_BYTES = 2
_LOG = logging.getLogger(__name__)
_lock = threading.Lock()
_session_lock = threading.Lock()
_session: _Meeting | None = None


def _value(settings: Any, name: str, default: Any) -> Any:
    if isinstance(settings, dict):
        return settings.get(name, default)
    return getattr(settings, name, default)


def _atomic_json(path: Path, value: Any) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _format_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _merge_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for segment in sorted(segments, key=lambda item: item["start"]):
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        item = {
            "start": float(segment["start"]),
            "end": float(segment["end"]),
            "speaker": str(segment["speaker"]),
            "source": str(segment["source"]),
            "text": text,
        }
        if merged and merged[-1]["speaker"] == item["speaker"] and merged[-1]["source"] == item["source"]:
            merged[-1]["end"] = max(merged[-1]["end"], item["end"])
            merged[-1]["text"] += " " + item["text"]
        else:
            merged.append(item)
    return merged


def _markdown(segments: list[dict[str, Any]]) -> str:
    lines: list[str] = ["# Meeting transcript", ""]
    speaker: str | None = None
    for segment in segments:
        if segment["speaker"] != speaker:
            speaker = segment["speaker"]
            lines.extend((f"## {speaker}", ""))
        lines.extend((f"[{_format_time(segment['start'])}] {segment['text']}", ""))
    return "\n".join(lines)


class _Diarizer:
    """Small online centroid clusterer around sherpa-onnx speaker embeddings."""

    def __init__(self, settings: Any) -> None:
        self._extractor: Any = None
        self._centroids: list[np.ndarray] = []
        self._fixed_count = max(0, int(_value(settings, "meeting_speakers", 0)))
        self._warned = False
        from waylandbettervoice.config import SPEAKER_MODEL_DIR

        model_dir = SPEAKER_MODEL_DIR
        model = next(iter(sorted(model_dir.rglob("*.onnx"))), None)
        if not _value(settings, "meeting_diarization", True) or model is None:
            self._warn("speaker diarization unavailable; system segments use Speaker ?")
            return
        try:
            import sherpa_onnx  # type: ignore[import-not-found]

            config_type = getattr(sherpa_onnx, "SpeakerEmbeddingExtractorConfig")
            extractor_type = getattr(sherpa_onnx, "SpeakerEmbeddingExtractor")
            try:
                extractor_config = config_type(model=str(model), num_threads=1, debug=False, provider="cpu")
            except TypeError:
                extractor_config = config_type(model=str(model))
            self._extractor = extractor_type(extractor_config)
        except Exception as error:
            self._warn(f"speaker diarization unavailable; system segments use Speaker ? ({error})")

    def _warn(self, message: str) -> None:
        if not self._warned:
            _LOG.warning(message)
            self._warned = True

    def label(self, pcm: bytes) -> str:
        if self._extractor is None:
            return "Speaker ?"
        try:
            samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
            stream = self._extractor.create_stream()
            stream.accept_waveform(_RATE, samples)
            embedding = np.asarray(self._extractor.compute(stream), dtype=np.float32)
            norm = float(np.linalg.norm(embedding))
            if not norm:
                raise ValueError("empty speaker embedding")
            embedding /= norm
        except Exception as error:
            self._warn(f"speaker diarization failed; system segments use Speaker ? ({error})")
            return "Speaker ?"

        if not self._centroids:
            self._centroids.append(embedding)
            return "Speaker 1"
        scores = [float(np.dot(embedding, centroid)) for centroid in self._centroids]
        closest = int(np.argmax(scores))
        # ponytail: chunk-level cosine clusters miss speaker changes inside 12 s; add VAD/turn segmentation if needed.
        if (self._fixed_count == 0 and scores[closest] < 0.72) or (
            self._fixed_count > len(self._centroids) and scores[closest] < 0.72
        ):
            self._centroids.append(embedding)
            closest = len(self._centroids) - 1
        else:
            self._centroids[closest] = (self._centroids[closest] + embedding) / 2
            self._centroids[closest] /= np.linalg.norm(self._centroids[closest])
        # ponytail: fixed count is a cap, not global k-means; re-cluster after recording if exact N matters.
        return f"Speaker {closest + 1}"

    @property
    def speakers(self) -> int:
        return len(self._centroids)


def _transcribe(pcm: bytes) -> str:
    """Reuse the daemon's VRAM-resident model; never load a second one."""
    try:
        return str(stt.transcribe(pcm)).strip()
    except Exception as error:
        _LOG.warning("meeting transcription failed: %s", error)
        return ""


class _Meeting:
    def __init__(self, settings: Any, on_state: Any) -> None:
        self.settings = settings
        self.on_state = on_state
        self.started = time.monotonic()
        self.directory = Path(config.MEETING_DIR) / datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
        self.directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        self.segments: list[dict[str, Any]] = []
        self.stop_event = threading.Event()
        self.diarizer = _Diarizer(settings)
        # System audio needs monitor=True: pw-record on a '.monitor' target yields silence,
        # the sink node with stream.capture.sink=true is what actually records output.
        self.captures = {
            "mic": audio.Capture(monitor=False),
            "system": audio.Capture(monitor=True),
        }
        self.raw = {source: (self.directory / f".{source}.raw").open("wb") for source in self.captures}
        self.threads: list[threading.Thread] = []
        self.worker: threading.Thread | None = None
        self.last_state = 0.0
        # ponytail: bounded queue drops transcription chunks when STT lags; raw files and mix keep the full audio.
        self._queue: "queue.Queue[Any]" = queue.Queue(maxsize=8)
        self._dropped = 0
        self._done_lock = threading.Lock()
        self._done_count = 0
        self._finalize_lock = threading.Lock()
        self._finalized = False

    def start(self) -> None:
        worker = threading.Thread(target=self._worker_loop, daemon=True, name="wbv-meeting-stt")
        worker.start()
        self.worker = worker
        for capture in self.captures.values():
            capture.start()
        self._state(0.0, force=True)
        for source in self.captures:
            thread = threading.Thread(target=self._capture, args=(source,), daemon=True, name=f"wbv-meeting-{source}")
            self.threads.append(thread)
            thread.start()

    def _state(self, level: float, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self.last_state >= 0.1:
            self.last_state = now
            fields = {"mode": "meeting", "level": max(0.0, min(1.0, float(level))), "meeting": self.info()}
            self.on_state(fields)

    def _capture(self, source: str) -> None:
        window = max(1, int(_value(self.settings, "meeting_chunk_seconds", 12))) * _RATE * _SAMPLE_BYTES
        buffered = bytearray()
        sample_offset = 0
        capture = self.captures[source]
        max_seconds = max(1, int(_value(self.settings, "meeting_max_minutes", 180))) * 60
        try:
            while not self.stop_event.is_set():
                if time.monotonic() - self.started >= max_seconds:
                    _LOG.warning("meeting maximum duration reached")
                    self.stop_event.set()
                    break
                chunk = capture.read_chunk()
                if not chunk:
                    if not self.stop_event.is_set():
                        _LOG.warning("meeting %s capture ended", source)
                        self.stop_event.set()
                    break
                if isinstance(chunk, np.ndarray):
                    chunk = chunk.astype("<i2", copy=False).tobytes()
                else:
                    chunk = bytes(chunk)
                self.raw[source].write(chunk)
                self._state(audio.rms_level(chunk))
                buffered.extend(chunk)
                while len(buffered) >= window:
                    pcm = bytes(buffered[:window])
                    del buffered[:window]
                    self._push(source, sample_offset / (_RATE * _SAMPLE_BYTES), pcm)
                    sample_offset += len(pcm)
        except Exception as error:
            if not self.stop_event.is_set():
                _LOG.warning("meeting %s capture stopped: %s", source, error)
            self.stop_event.set()
        finally:
            for active_capture in self.captures.values():
                try:
                    active_capture.stop()
                except Exception:
                    pass
            if buffered:
                self._push(source, sample_offset / (_RATE * _SAMPLE_BYTES), bytes(buffered))
            self._capture_done()

    def _push(self, source: str, start: float, pcm: bytes) -> None:
        """Queue one chunk for the transcription worker without blocking capture."""
        try:
            self._queue.put_nowait((source, start, pcm))
        except queue.Full:
            self._dropped += 1
            if self._dropped == 1 or self._dropped % 50 == 0:
                _LOG.warning("meeting transcription queue full; dropped %d chunk(s)", self._dropped)

    def _capture_done(self) -> None:
        with self._done_lock:
            self._done_count += 1
            last = self._done_count == len(self.captures)
        if last:
            self._finalize()

    def _worker_loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                break
            source, start, pcm = item
            try:
                self._process(source, start, pcm)
            except Exception as error:
                _LOG.warning("meeting segment processing failed: %s", error)

    def _process(self, source: str, start: float, pcm: bytes) -> None:
        # ponytail: RMS gate can discard very quiet speech; expose threshold only after users report it.
        if not pcm or float(audio.rms_level(pcm)) < 0.008:
            return
        text = _transcribe(pcm)
        if not text:
            return
        segment = {
            "start": start,
            "end": start + len(pcm) / (_RATE * _SAMPLE_BYTES),
            "speaker": "Me" if source == "mic" else self.diarizer.label(pcm),
            "source": source,
            "text": text,
        }
        with _lock:
            self.segments.append(segment)
            self._write_transcript()

    def _write_transcript(self) -> list[dict[str, Any]]:
        segments = _merge_segments(self.segments)
        _atomic_json(self.directory / "transcript.json", segments)
        markdown_path = self.directory / "transcript.md"
        temp = markdown_path.with_suffix(".md.tmp")
        temp.write_text(_markdown(segments), encoding="utf-8")
        os.replace(temp, markdown_path)
        return segments

    def _write_mix(self) -> None:
        paths = [self.directory / ".mic.raw", self.directory / ".system.raw"]
        output = self.directory / "mix.wav"
        with wave.open(str(output), "wb") as mixed:
            mixed.setnchannels(1)
            mixed.setsampwidth(_SAMPLE_BYTES)
            mixed.setframerate(_RATE)
            with paths[0].open("rb") as left, paths[1].open("rb") as right:
                while True:
                    first, second = left.read(65536), right.read(65536)
                    if not first and not second:
                        break
                    size = max(len(first), len(second))
                    first += b"\0" * (size - len(first))
                    second += b"\0" * (size - len(second))
                    a = np.frombuffer(first, dtype="<i2").astype(np.int32)
                    b = np.frombuffer(second, dtype="<i2").astype(np.int32)
                    mixed.writeframes(np.clip(a + b, -32768, 32767).astype("<i2").tobytes())
        for path in paths:
            path.unlink(missing_ok=True)

    def stop(self) -> dict[str, Any]:
        self.stop_event.set()
        for capture in self.captures.values():
            try:
                capture.stop()
            except Exception as error:
                _LOG.warning("meeting capture stop failed: %s", error)
        current = threading.current_thread()
        for thread in self.threads:
            if thread is not current:  # a capture thread must never join itself
                thread.join()
        self._finalize()
        return self.info(active=False)

    def _finalize(self) -> None:
        """Finalize exactly once across user stop, EOF, max duration, and capture errors.

        Whichever path arrives first (user stop or the last capture thread) does the
        work; the lock plus flag makes every other path a no-op. No thread joins
        itself: capture threads exit on their own, and the worker is separate.
        """
        global _session
        with self._finalize_lock:
            if self._finalized:
                return
            self._finalized = True
            self._queue.put(None)
            if self.worker is not None:
                self.worker.join()
            for raw in self.raw.values():
                try:
                    raw.close()
                except OSError:
                    pass
            finalization_error: Exception | None = None
            try:
                with _lock:
                    self._write_transcript()
            except Exception as error:  # keep raw audio recoverable on any writer failure
                finalization_error = error
                _LOG.warning("meeting transcript write failed: %s", error)
            try:
                self._write_mix()
            except Exception as error:  # malformed/truncated PCM may raise ValueError
                finalization_error = finalization_error or error
                _LOG.warning("meeting mix write failed: %s", error)
            with _lock:
                if _session is self:
                    _session = None
            self.on_state({
                "mode": "error" if finalization_error else "idle",
                "level": 0.0,
                "error": f"meeting finalization failed: {finalization_error}" if finalization_error else None,
                "meeting": self.info(active=False),
            })

    def info(self, active: bool = True) -> dict[str, Any]:
        return {
            "active": active,
            "file": str(self.directory),
            "speakers": self.diarizer.speakers,
            "elapsed": time.monotonic() - self.started,
        }


def start(settings: Any, on_state: Any) -> None:
    """Start one meeting; raises RuntimeError when one is already active."""
    global _session
    with _session_lock:
        with _lock:
            if _session is not None:
                raise RuntimeError("meeting already active")
            session = _session = _Meeting(settings, on_state)
        try:
            session.start()
        except Exception:
            try:
                session.stop()
            except Exception as error:
                _LOG.warning("meeting startup cleanup failed: %s", error)
            finally:
                with _lock:
                    if _session is session:
                        _session = None
            raise


def stop() -> dict[str, Any]:
    """Stop capture and block until transcript and mix are written."""
    global _session
    with _session_lock:
        with _lock:
            session = _session
        if session is None:
            return {"active": False, "file": None, "speakers": 0, "elapsed": 0.0}
        try:
            return session.stop()
        finally:
            with _lock:
                if _session is session:
                    _session = None


def is_active() -> bool:
    with _lock:
        return _session is not None


def info() -> dict[str, Any]:
    with _lock:
        return _session.info() if _session is not None else {"active": False, "file": None, "speakers": 0, "elapsed": 0.0}


if __name__ == "__main__":
    fake = [
        {"start": 4.0, "end": 5.0, "speaker": "Speaker 1", "source": "system", "text": "again"},
        {"start": 0.0, "end": 2.0, "speaker": "Me", "source": "mic", "text": "hello"},
        {"start": 2.0, "end": 4.0, "speaker": "Me", "source": "mic", "text": "world"},
    ]
    merged = _merge_segments(fake)
    assert [item["speaker"] for item in merged] == ["Me", "Speaker 1"]
    assert merged[0]["text"] == "hello world"
    assert "## Me" in _markdown(merged) and "[00:00] hello world" in _markdown(merged)
    print("meeting self-check: ok")
