"""Newline-delimited JSON over AF_UNIX SOCK_STREAM."""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
from pathlib import Path
from typing import Callable

from waylandbettervoice.config import SOCKET_PATH

log = logging.getLogger("wbv.ipc")

Handler = Callable[[dict], dict]


class Server:
    """Threaded AF_UNIX server. dispatch: cmd -> callable(args) -> data dict."""

    def __init__(self, path: Path | None = None, dispatch: dict[str, Handler] | None = None):
        self.path = Path(path) if path else SOCKET_PATH
        self.dispatch = dispatch or {}
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # unlink stale socket left by a crashed previous daemon
        if self.path.exists():
            try:
                self.path.unlink()
            except OSError as e:
                log.warning("could not unlink stale socket %s: %s", self.path, e)
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(self.path))
        os.chmod(self.path, 0o600)
        self._sock.listen(8)
        self._sock.settimeout(0.5)
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, name="wbv-ipc", daemon=True)
        self._thread.start()
        log.info("IPC listening on %s", self.path)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError:
            pass

    def _serve(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    break
                continue
            threading.Thread(
                target=self._handle, args=(conn,), name="wbv-ipc-client", daemon=True
            ).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            with conn:
                buf = b""
                conn.settimeout(5.0)
                while b"\n" not in buf:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                if not buf:
                    return
                line = buf.split(b"\n", 1)[0]
                try:
                    req = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as e:
                    resp = {"ok": False, "data": {}, "error": f"bad json: {e}"}
                    conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
                    return
                cmd = req.get("cmd")
                args = req.get("args") or {}
                if not isinstance(args, dict):
                    args = {}
                handler = self.dispatch.get(cmd) if isinstance(cmd, str) else None
                if handler is None:
                    resp = {"ok": False, "data": {}, "error": f"unknown cmd: {cmd!r}"}
                else:
                    try:
                        data = handler(args) or {}
                        if not isinstance(data, dict):
                            data = {"result": data}
                        # handlers may return {"ok": False, "error": ...} or plain data dict
                        if "ok" in data:
                            ok = bool(data["ok"])
                            err = data.get("error")
                            payload = data.get("data")
                            if payload is None:
                                payload = {k: v for k, v in data.items() if k not in ("ok", "error", "data")}
                            resp = {"ok": ok, "data": payload, "error": err}
                        else:
                            resp = {"ok": True, "data": data, "error": None}
                    except Exception as e:  # noqa: BLE001 — surface any handler error to client
                        log.exception("handler %s failed", cmd)
                        resp = {"ok": False, "data": {}, "error": str(e)}
                conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
        except OSError as e:
            log.debug("client conn error: %s", e)


def send(cmd: str, args: dict | None = None, path: Path | None = None, timeout: float = 5.0) -> dict:
    """Client: send one request, return response dict. Raises if socket missing/stale."""
    sock_path = Path(path) if path else SOCKET_PATH
    if not sock_path.exists():
        raise ConnectionError(
            f"daemon not running (socket missing: {sock_path}). Start with: wbv daemon"
        )
    req = {"cmd": cmd, "args": args or {}}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(sock_path))
            s.sendall((json.dumps(req) + "\n").encode("utf-8"))
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
    except (ConnectionRefusedError, FileNotFoundError, OSError) as e:
        raise ConnectionError(
            f"daemon not running or socket stale ({sock_path}): {e}. Start with: wbv daemon"
        ) from e
    if not buf:
        raise ConnectionError("daemon closed connection without reply")
    line = buf.split(b"\n", 1)[0]
    return json.loads(line.decode("utf-8"))
