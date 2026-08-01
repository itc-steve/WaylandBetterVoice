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
            assert loaded["dictation_max_seconds"] == 300
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


def main() -> int:
    print("waylandbettervoice selftest")
    tests = [test_config_merge, test_state_atomic, test_rms_level, test_ipc_roundtrip]
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
