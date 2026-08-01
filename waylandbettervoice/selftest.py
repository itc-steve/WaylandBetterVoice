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
        out = st.write_state(path=path, mode="idle", level=0.0, model_loaded=True, last_text="hi")
        assert path.is_file()
        assert not path.with_suffix(".tmp").exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["mode"] == "idle"
        assert data["model_loaded"] is True
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
