"""Assert-based selftest — no framework. `python -m waylandbettervoice.selftest`."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path


def test_config_merge() -> None:
    from waylandbettervoice import config as cfg

    # defaults present
    assert cfg.DEFAULTS["model"] == "ggml-large-v3.bin"
    assert cfg.DEFAULTS["language"] == "en"
    assert cfg.DEFAULTS["trailing_space"] is True
    assert cfg.SOCKET_PATH.name == "wbv.sock"
    assert cfg.STATE_PATH.name == "state.json"

    # merge: write a temp config, point CONFIG_PATH at it
    orig = cfg.CONFIG_PATH
    try:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "config.json"
            p.write_text(json.dumps({"n_threads": 3, "model": "ggml-small.bin"}), encoding="utf-8")
            cfg.CONFIG_PATH = p
            loaded = cfg.load_config()
            assert loaded["n_threads"] == 3
            assert loaded["model"] == "ggml-small.bin"
            # untouched keys keep defaults
            assert loaded["language"] == "en"
            assert loaded["beam_size"] == 5
            assert loaded["dictation_max_seconds"] == 30
            assert loaded["silence_seconds"] == 0.8
            assert loaded["listen_max_minutes"] == 30
            assert loaded["vad_start_level"] == 0.015
            assert loaded["preroll_seconds"] == 0.3
    finally:
        cfg.CONFIG_PATH = orig
    print("  config merge: ok")


def test_state_atomic() -> None:
    from waylandbettervoice import state as st

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "state.json"
        # reset module state-ish by writing known fields
        out = st.write_state(path=path, mode="idle", level=0.0, model_loaded=True, inject_method="ydotool", last_text="hi")
        assert path.is_file()
        assert not path.with_suffix(".tmp").exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["mode"] == "idle"
        assert data["model_loaded"] is True
        assert data["inject_method"] == "ydotool"
        assert data["last_text"] == "hi"
        assert "since" in data

        # level throttle: many rapid level-only updates should not always rewrite
        # (we only assert final level is stored + file still valid JSON)
        for i in range(20):
            st.write_state(path=path, level=i / 20.0)
        data2 = json.loads(path.read_text(encoding="utf-8"))
        assert 0.0 <= data2["level"] <= 1.0
        assert data2["mode"] == "idle"
    print("  state atomic: ok")


def test_rms_level() -> None:
    import numpy as np
    from waylandbettervoice.audio import rms_level

    assert rms_level(b"") == 0.0
    silence = np.zeros(320, dtype=np.int16).tobytes()
    assert rms_level(silence) == 0.0
    loud = (np.ones(320, dtype=np.int16) * 16000).tobytes()
    lvl = rms_level(loud)
    assert 0.0 < lvl <= 1.0
    # full scale ~1.0
    full = (np.ones(320, dtype=np.int16) * 32767).tobytes()
    assert abs(rms_level(full) - 1.0) < 0.01
    print("  rms_level: ok")


def test_ipc_roundtrip() -> None:
    from waylandbettervoice.ipc import Server, send

    with tempfile.TemporaryDirectory() as td:
        sock = Path(td) / "test.sock"
        seen = {}

        def echo(args: dict) -> dict:
            seen["args"] = args
            return {"echo": args.get("x", None), "pong": True}

        def boom(args: dict) -> dict:
            raise RuntimeError("deliberate")

        srv = Server(path=sock, dispatch={"echo": echo, "boom": boom, "status": lambda a: {"mode": "idle"}})
        srv.start()
        try:
            # give thread a moment
            for _ in range(50):
                if sock.exists():
                    break
                time.sleep(0.01)
            assert sock.exists(), "socket not created"
            # mode bits
            mode = os.stat(sock).st_mode & 0o777
            assert mode == 0o600, f"expected 0600 got {oct(mode)}"

            resp = send("echo", {"x": 42}, path=sock)
            assert resp["ok"] is True
            assert resp["data"]["echo"] == 42
            assert resp["data"]["pong"] is True
            assert seen["args"]["x"] == 42

            resp2 = send("status", path=sock)
            assert resp2["ok"] is True
            assert resp2["data"]["mode"] == "idle"

            resp3 = send("nope", path=sock)
            assert resp3["ok"] is False
            assert "unknown" in (resp3.get("error") or "")

            resp4 = send("boom", path=sock)
            assert resp4["ok"] is False
            assert "deliberate" in (resp4.get("error") or "")
        finally:
            srv.stop()

        # missing socket → clear ConnectionError
        try:
            send("status", path=sock)
            raise AssertionError("expected ConnectionError for missing socket")
        except ConnectionError as e:
            assert "daemon not running" in str(e)
    print("  ipc roundtrip: ok")


def _fake_chunk(level_rms: float) -> bytes:
    """Synthetic 20 ms int16 mono frame at approx RMS level (0..1)."""
    import numpy as np
    from waylandbettervoice.audio import CHUNK_BYTES

    n = CHUNK_BYTES // 2
    amp = int(max(0.0, min(1.0, level_rms)) * 32767)
    return (np.ones(n, dtype=np.int16) * amp).tobytes()


def test_endpointer_two_utterances() -> None:
    """silence → speech → silence → speech → silence yields exactly 2 utterances."""
    from waylandbettervoice.audio import CHUNK_BYTES, Endpointer, FRAME_SECONDS

    ep = Endpointer(
        silence_seconds=0.2,  # 10 frames
        vad_start_level=0.015,
        vad_stop_level=0.008,
        min_utterance_seconds=0.1,
        preroll_seconds=0.06,  # 3 frames
        max_utterance_seconds=30.0,
        start_hang_seconds=0.06,  # 3 frames
    )
    loud = _fake_chunk(0.05)
    quiet = _fake_chunk(0.0)
    assert len(loud) == CHUNK_BYTES

    outs: list[bytes] = []

    def feed_n(chunk: bytes, n: int, rms: float) -> None:
        for _ in range(n):
            outs.extend(ep.feed(rms, chunk))

    # lead-in silence
    feed_n(quiet, 10, 0.0)
    assert outs == []
    assert ep.in_speech is False

    # utterance 1: speech then silence to endpoint
    feed_n(loud, 20, 0.05)  # 400 ms speech (+ hang)
    assert ep.in_speech is True
    feed_n(quiet, 10, 0.0)  # 200 ms silence → end
    assert len(outs) == 1
    assert ep.in_speech is False

    # gap silence
    feed_n(quiet, 5, 0.0)
    assert len(outs) == 1

    # utterance 2
    feed_n(loud, 15, 0.05)
    assert ep.in_speech is True
    feed_n(quiet, 10, 0.0)
    assert len(outs) == 2
    assert ep.in_speech is False

    # each utterance includes preroll + speech; must be longer than min
    min_bytes = int(0.1 * 16000) * 2
    for i, utt in enumerate(outs):
        assert len(utt) >= min_bytes, f"utt {i} too short: {len(utt)}"
        # boundaries: multiple of frame size
        assert len(utt) % CHUNK_BYTES == 0

    # duration sanity: utt1 ~ preroll(if any before hang) + hang + speech + trailing silence frames
    # at least the 20 speech frames worth
    assert len(outs[0]) >= 20 * CHUNK_BYTES
    assert len(outs[1]) >= 15 * CHUNK_BYTES

    # flush idle → nothing
    assert ep.flush() == []
    print("  endpointer two utterances: ok")


def test_endpointer_short_blip_discarded() -> None:
    """Speech shorter than min_utterance_seconds is dropped."""
    from waylandbettervoice.audio import CHUNK_BYTES, Endpointer

    ep = Endpointer(
        silence_seconds=0.1,  # 5 frames
        vad_start_level=0.015,
        vad_stop_level=0.008,
        min_utterance_seconds=0.4,  # 20 frames
        preroll_seconds=0.04,
        max_utterance_seconds=30.0,
        start_hang_seconds=0.04,  # 2 frames
    )
    loud = _fake_chunk(0.05)
    quiet = _fake_chunk(0.0)
    outs: list[bytes] = []
    for _ in range(3):
        outs.extend(ep.feed(0.0, quiet))
    # 2 hang + 2 more speech frames = ~80 ms total speech body — under 0.4 s even with preroll
    for _ in range(4):
        outs.extend(ep.feed(0.05, loud))
    assert ep.in_speech is True
    for _ in range(5):
        outs.extend(ep.feed(0.0, quiet))
    assert outs == [], f"expected no utterances, got {len(outs)} lens={[len(o) for o in outs]}"
    assert ep.in_speech is False

    # now a long enough one must pass
    for _ in range(25):
        outs.extend(ep.feed(0.05, loud))
    for _ in range(5):
        outs.extend(ep.feed(0.0, quiet))
    assert len(outs) == 1
    assert len(outs[0]) % CHUNK_BYTES == 0
    print("  endpointer short blip: ok")


def test_endpointer_flush_in_progress() -> None:
    """flush() emits in-progress utterance when long enough."""
    from waylandbettervoice.audio import Endpointer

    ep = Endpointer(
        silence_seconds=2.0,
        vad_start_level=0.015,
        vad_stop_level=0.008,
        min_utterance_seconds=0.1,
        preroll_seconds=0.04,
        max_utterance_seconds=30.0,
        start_hang_seconds=0.04,
    )
    loud = _fake_chunk(0.05)
    for _ in range(15):
        assert ep.feed(0.05, loud) == []
    assert ep.in_speech is True
    flushed = ep.flush()
    assert len(flushed) == 1
    assert ep.in_speech is False
    # second flush empty
    assert ep.flush() == []
    print("  endpointer flush: ok")


def test_known_models_table() -> None:
    from waylandbettervoice.models import KNOWN_MODELS, resolve_known, short_name_for

    names = {m.name for m in KNOWN_MODELS}
    for required in ("tiny.en", "base.en", "small.en", "medium.en", "large-v3"):
        assert required in names, f"missing {required} in KNOWN_MODELS"
    for m in KNOWN_MODELS:
        assert m.size > 0
        assert m.filename.startswith("ggml-") and m.filename.endswith(".bin")
        assert resolve_known(m.name).filename == m.filename
        assert resolve_known(m.filename).name == m.name
    assert short_name_for("ggml-large-v3.bin") == "large-v3"
    print("  known models table: ok")


def test_model_download_atomic_part() -> None:
    """Fake download via local file:// — .part never left as final, atomic replace."""
    import io
    from waylandbettervoice import models as M

    # tiny.en catalog entry — forge a body of exact expected size
    m = M.resolve_known("tiny.en")
    body = b"W" * m.size

    with tempfile.TemporaryDirectory() as td:
        src_dir = Path(td) / "src"
        dest_dir = Path(td) / "dest"
        src_dir.mkdir()
        dest_dir.mkdir()
        src = src_dir / m.filename
        src.write_bytes(body)

        class _Resp:
            def __init__(self, data: bytes):
                self._data = data
                self._pos = 0
                self.headers = {"Content-Length": str(len(data))}

            def read(self, n: int = -1) -> bytes:
                if self._pos >= len(self._data):
                    return b""
                if n < 0:
                    chunk = self._data[self._pos :]
                    self._pos = len(self._data)
                    return chunk
                chunk = self._data[self._pos : self._pos + n]
                self._pos += len(chunk)
                return chunk

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def opener(url: str):
            assert m.filename in url
            return _Resp(body)

        sink = io.StringIO()
        out = M.download(
            "tiny.en",
            directory=dest_dir,
            base_url="file:///fake",
            opener=opener,
            progress_stream=sink,
        )
        assert out == dest_dir / m.filename
        assert out.is_file()
        assert out.stat().st_size == m.size
        assert out.read_bytes() == body
        # no leftover .part
        assert not (dest_dir / (m.filename + ".part")).exists()
        # second call skips (no overwrite)
        out2 = M.download(
            "tiny.en",
            directory=dest_dir,
            base_url="file:///fake",
            opener=opener,
            progress_stream=sink,
        )
        assert out2 == out

        # interrupted mid-write leaves no final file and cleans .part
        dest2 = Path(td) / "dest2"
        dest2.mkdir()

        class _BoomResp(_Resp):
            def read(self, n: int = -1) -> bytes:
                raise KeyboardInterrupt

        try:
            M.download(
                "tiny.en",
                directory=dest2,
                base_url="file:///fake",
                opener=lambda url: _BoomResp(body),
                progress_stream=sink,
            )
            raise AssertionError("expected KeyboardInterrupt")
        except KeyboardInterrupt:
            pass
        assert not (dest2 / m.filename).exists()
        assert not (dest2 / (m.filename + ".part")).exists()
    print("  model download atomic: ok")


def test_xdg_path_resolution() -> None:
    """DATA/CONFIG honour XDG_* env; runtime under XDG_RUNTIME_DIR."""
    import importlib
    import waylandbettervoice.config as cfg

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        data = base / "data"
        conf = base / "conf"
        run = base / "run"
        env = {
            "XDG_DATA_HOME": str(data),
            "XDG_CONFIG_HOME": str(conf),
            "XDG_RUNTIME_DIR": str(run),
        }
        old = {k: os.environ.get(k) for k in env}
        try:
            os.environ.update(env)
            importlib.reload(cfg)
            assert cfg.DATA_DIR == data / "waylandbettervoice"
            assert cfg.MODEL_DIR == data / "waylandbettervoice" / "models"
            assert cfg.CONFIG_PATH == conf / "waylandbettervoice" / "config.json"
            assert cfg.RUNTIME_DIR == run / "waylandbettervoice"
            assert cfg.SOCKET_PATH == run / "waylandbettervoice" / "wbv.sock"
            assert cfg.LOG_PATH == data / "waylandbettervoice" / "wbv.log"
            cfg.mkdirs()
            assert cfg.MODEL_DIR.is_dir()
            assert cfg.SPEAKER_MODEL_DIR.is_dir()
            assert cfg.MEETING_DIR.is_dir()
            assert cfg.CONFIG_DIR.is_dir()
            assert cfg.RUNTIME_DIR.is_dir()
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            importlib.reload(cfg)
    print("  xdg path resolution: ok")


def test_model_missing_error_message() -> None:
    """stt.resolve_model_path names the exact download command."""
    import importlib
    import waylandbettervoice.config as cfg
    from waylandbettervoice import stt

    with tempfile.TemporaryDirectory() as td:
        data = Path(td) / "data"
        old = os.environ.get("XDG_DATA_HOME")
        try:
            os.environ["XDG_DATA_HOME"] = str(data)
            importlib.reload(cfg)
            importlib.reload(stt)
            try:
                stt.resolve_model_path("ggml-large-v3.bin")
                raise AssertionError("expected ModelMissingError")
            except stt.ModelMissingError as e:
                msg = str(e)
                assert "wbv model download large-v3" in msg, msg
                assert "ggml-large-v3.bin" in msg
        finally:
            if old is None:
                os.environ.pop("XDG_DATA_HOME", None)
            else:
                os.environ["XDG_DATA_HOME"] = old
            importlib.reload(cfg)
            importlib.reload(stt)
    print("  model missing error: ok")



def test_hf_token_resolution_order() -> None:
    """explicit arg > HF_TOKEN > HUGGING_FACE_HUB_TOKEN > cli login token file."""
    import tempfile
    from waylandbettervoice import models as M

    saved = {k: os.environ.get(k) for k in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HF_HOME")}
    try:
        for k in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
            os.environ.pop(k, None)
        home = tempfile.mkdtemp()
        os.environ["HF_HOME"] = home

        assert M.read_token() is None, "no token anywhere should give None"
        assert M.read_token("  ") is None, "whitespace-only token must be ignored"
        assert M.read_token("explicit") == "explicit"

        os.environ["HF_TOKEN"] = "env"
        assert M.read_token() == "env"
        assert M.read_token("explicit") == "explicit", "explicit must beat env"

        del os.environ["HF_TOKEN"]
        os.environ["HUGGING_FACE_HUB_TOKEN"] = "hub"
        assert M.read_token() == "hub"

        del os.environ["HUGGING_FACE_HUB_TOKEN"]
        Path(home, "token").write_text("fromfile\n", encoding="utf-8")
        assert M.read_token() == "fromfile", "should read huggingface-cli token file"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print("  hf token resolution order: ok")


def test_hf_token_not_leaked_on_redirect() -> None:
    """HF redirects downloads to a CDN; the token must not follow it there."""
    import urllib.request
    from waylandbettervoice import models as M

    handler = M._TokenSafeRedirectHandler()
    req = urllib.request.Request("https://huggingface.co/r/resolve/main/f.bin")
    req.add_header("Authorization", "Bearer SECRET")

    class _FP:
        def read(self, *a):
            return b""

        def close(self):
            pass

    def auth_of(r):
        if r is None:
            return None
        return {k.lower(): v for k, v in r.headers.items()}.get("authorization")

    same = handler.redirect_request(req, _FP(), 302, "Found", {}, "https://huggingface.co/other.bin")
    assert auth_of(same) == "Bearer SECRET", "token should survive same-host redirect"

    for foreign in (
        "https://cdn-lfs-us-1.hf.co/repos/a/b.bin",
        "https://evil.example.com/steal",
    ):
        away = handler.redirect_request(req, _FP(), 302, "Found", {}, foreign)
        assert auth_of(away) is None, f"token leaked to {foreign}"
    print("  hf token not leaked on redirect: ok")


def test_inject_mode_command_persists_and_reloads() -> None:
    """CLI mode switch preserves config and reloads a running daemon."""
    import contextlib
    import io
    from unittest.mock import call, patch
    from waylandbettervoice import config as cfg
    from waylandbettervoice.__main__ import main as cli_main

    original_config_path = cfg.CONFIG_PATH
    original_state_path = cfg.STATE_PATH
    try:
        with tempfile.TemporaryDirectory() as td:
            cfg.CONFIG_PATH = Path(td) / "config.json"
            cfg.STATE_PATH = Path(td) / "state.json"
            cfg.save_config({"n_threads": 3, "inject_method": "wtype"})

            cfg.STATE_PATH.write_text("{}", encoding="utf-8")
            with patch("waylandbettervoice.__main__.ipc.send", side_effect=[{"ok": True}, {"ok": True}]) as send, contextlib.redirect_stdout(io.StringIO()):
                assert cli_main(["inject", "ydotool"]) == 0
            assert send.call_args_list == [call("status"), call("reload")]
            saved = json.loads(cfg.CONFIG_PATH.read_text(encoding="utf-8"))
            assert saved["inject_method"] == "ydotool"
            assert saved["n_threads"] == 3

            cfg.STATE_PATH.unlink()
            with patch("waylandbettervoice.__main__.ipc.send") as send, contextlib.redirect_stdout(io.StringIO()):
                assert cli_main(["inject", "clipboard"]) == 0
            send.assert_not_called()
            assert json.loads(cfg.CONFIG_PATH.read_text(encoding="utf-8"))["inject_method"] == "clipboard"

            cfg.save_config({"inject_method": "wtype"})
            cfg.STATE_PATH.write_text("{}", encoding="utf-8")
            with patch("waylandbettervoice.__main__.ipc.send", side_effect=ConnectionError), contextlib.redirect_stderr(io.StringIO()):
                assert cli_main(["inject", "ydotool"]) == 1
            assert json.loads(cfg.CONFIG_PATH.read_text(encoding="utf-8"))["inject_method"] == "wtype"

            broken = '{"n_threads": 3,'
            cfg.CONFIG_PATH.write_text(broken, encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                assert cli_main(["inject", "ydotool"]) == 1
            assert cfg.CONFIG_PATH.read_text(encoding="utf-8") == broken

            cfg.STATE_PATH.unlink()
            cfg.CONFIG_PATH.write_text("{}", encoding="utf-8")
            with patch("waylandbettervoice.config.save_config", side_effect=OSError("read-only")), contextlib.redirect_stderr(io.StringIO()):
                assert cli_main(["inject", "ydotool"]) == 1
    finally:
        cfg.CONFIG_PATH = original_config_path
        cfg.STATE_PATH = original_state_path
    print("  inject mode command: ok")


def test_meeting_capture_targets_playback_stream() -> None:
    """Record sink carrying app audio, not unrelated running hardware; request raw PCM."""
    from unittest.mock import patch
    from waylandbettervoice import audio

    sinks = [
        {"index": 1, "name": "alsa_output.unrelated", "state": "RUNNING"},
        {"index": 2, "name": "easyeffects_sink", "state": "RUNNING"},
    ]
    inputs = [{"sink": 2}]

    def output(command, **_kwargs):
        if command[-1] == "sinks":
            return json.dumps(sinks)
        if command[-1] == "sink-inputs":
            return json.dumps(inputs)
        raise AssertionError(command)

    with patch.object(audio.subprocess, "check_output", side_effect=output):
        assert audio._sink_with_inputs() == "easyeffects_sink"

    inputs.clear()
    sinks[1]["state"] = "SUSPENDED"
    with patch.object(audio.subprocess, "check_output", side_effect=output):
        assert audio._sink_with_inputs() == "easyeffects_sink"

    class Process:
        stdout = None
        stderr = None

    with patch.object(audio, "resolve_default_source", return_value="mic"), patch.object(
        audio.subprocess, "Popen", return_value=Process()
    ) as popen:
        audio.Capture().start()
    assert "--raw" in popen.call_args.args[0]
    print("  meeting capture target/raw PCM: ok")


def main() -> int:
    print("waylandbettervoice selftest")
    tests = [
        test_config_merge,
        test_state_atomic,
        test_rms_level,
        test_ipc_roundtrip,
        test_endpointer_two_utterances,
        test_endpointer_short_blip_discarded,
        test_endpointer_flush_in_progress,
        test_known_models_table,
        test_model_download_atomic_part,
        test_xdg_path_resolution,
        test_model_missing_error_message,
        test_hf_token_resolution_order,
        test_hf_token_not_leaked_on_redirect,
        test_inject_mode_command_persists_and_reloads,
        test_meeting_capture_targets_playback_stream,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  FAIL {t.__name__}: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
    if failed:
        print(f"FAILED ({failed}/{len(tests)})")
        return 1
    print(f"ALL PASS ({len(tests)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
