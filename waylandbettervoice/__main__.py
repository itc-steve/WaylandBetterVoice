"""CLI entry: `python -m waylandbettervoice` / `wbv`."""
from __future__ import annotations

import argparse
import json
import sys

from waylandbettervoice import __version__
from waylandbettervoice import ipc


# Stopping a long meeting finalizes transcripts and mixes the wav; draining queued
# dictation utterances waits on whisper. Both blow past the 5 s default.
_SLOW_COMMANDS = {"meeting.stop", "meeting.toggle", "dictate.stop", "dictate.toggle", "quit"}


def _client(cmd: str, args: dict | None = None) -> int:
    timeout = 180.0 if cmd in _SLOW_COMMANDS else 5.0
    try:
        resp = ipc.send(cmd, args or {}, timeout=timeout)
    except ConnectionError as e:
        print(str(e), file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"ipc error: {e}", file=sys.stderr)
        return 1
    if not resp.get("ok", False):
        err = resp.get("error") or "command failed"
        print(err, file=sys.stderr)
        return 1
    data = resp.get("data") or {}
    if data:
        # human-readable one-liner; status --json handled by caller
        if isinstance(data, dict) and "text" in data:
            print(data["text"])
        elif not (len(data) == 1 and "mode" in data):
            print(json.dumps(data, indent=2) if isinstance(data, (dict, list)) else data)
    return 0


def _cmd_inject(args: argparse.Namespace) -> int:
    """Persist injection method and reload daemon when it is running."""
    from waylandbettervoice.config import CONFIG_PATH, STATE_PATH, save_config

    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"cannot update invalid config {CONFIG_PATH}: {e}", file=sys.stderr)
            return 1
        if not isinstance(cfg, dict):
            print(f"cannot update invalid config {CONFIG_PATH}: expected JSON object", file=sys.stderr)
            return 1
    daemon_up = STATE_PATH.exists()
    if daemon_up:
        try:
            status = ipc.send("status")
        except ConnectionError as e:
            print(f"daemon is starting or unreachable; retry: {e}", file=sys.stderr)
            return 1
        if not status.get("ok", False):
            print(status.get("error") or "daemon status failed", file=sys.stderr)
            return 1
    cfg["inject_method"] = args.method
    try:
        save_config(cfg)
    except OSError as e:
        print(f"cannot update config {CONFIG_PATH}: {e}", file=sys.stderr)
        return 1
    try:
        resp = ipc.send("reload") if daemon_up else None
    except ConnectionError as e:
        print(f"config saved but daemon reload failed: {e}", file=sys.stderr)
        return 1
    if resp is not None and not resp.get("ok", False):
        print(resp.get("error") or "daemon reload failed", file=sys.stderr)
        return 1
    print(f"injection mode: {args.method}")
    return 0


def _cmd_model(args: argparse.Namespace) -> int:
    """Local model ops — no daemon / IPC required."""
    from waylandbettervoice import models as M
    from waylandbettervoice.config import MODEL_DIR, mkdirs

    if args.model_action == "path":
        print(MODEL_DIR)
        return 0

    if args.model_action == "list":
        mkdirs()
        print(M.format_list())
        return 0

    if args.model_action == "download":
        if not args.name:
            print("usage: wbv model download <name>", file=sys.stderr)
            print(f"known: {', '.join(m.name for m in M.KNOWN_MODELS)}", file=sys.stderr)
            return 2
        mkdirs()
        try:
            path = M.download(args.name, token=getattr(args, "token", None))
        except M.ModelError as e:
            print(str(e), file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("download cancelled", file=sys.stderr)
            return 130
        print(path)
        return 0

    print(f"unknown model action: {args.model_action}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wbv", description="WaylandBetterVoice dictation daemon")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_daemon = sub.add_parser("daemon", help="start daemon")
    p_daemon.add_argument("--foreground", action="store_true", help="run in foreground (systemd)")

    p_dictate = sub.add_parser("dictate", help="control dictation")
    p_dictate.add_argument(
        "action",
        nargs="?",
        default="toggle",
        choices=["toggle", "start", "stop", "cancel"],
    )

    p_meeting = sub.add_parser("meeting", help="control meeting recorder")
    p_meeting.add_argument(
        "action",
        nargs="?",
        default="toggle",
        choices=["toggle", "start", "stop"],
    )

    p_status = sub.add_parser("status", help="show daemon status")
    p_status.add_argument("--json", action="store_true", help="raw JSON")

    p_inject = sub.add_parser("inject", help="select text injection mode")
    p_inject.add_argument("method", choices=["wtype", "ydotool", "clipboard"])

    sub.add_parser("quit", help="shut down daemon")

    p_model = sub.add_parser("model", help="list / download whisper models (no daemon needed)")
    p_model.add_argument(
        "model_action",
        choices=["list", "download", "path"],
        help="list known models, download one, or print MODEL_DIR",
    )
    p_model.add_argument("name", nargs="?", help="model name for download (e.g. large-v3)")
    p_model.add_argument(
        "--token",
        metavar="HF_TOKEN",
        help="optional Hugging Face token. Not needed for the default models; "
        "useful for rate limits or gated repos. Falls back to HF_TOKEN, "
        "HUGGING_FACE_HUB_TOKEN, then ~/.cache/huggingface/token",
    )

    args = parser.parse_args(argv)

    if args.command == "daemon":
        from waylandbettervoice.daemon import run
        return run(foreground=args.foreground)

    if args.command == "dictate":
        return _client(f"dictate.{args.action}")

    if args.command == "meeting":
        return _client(f"meeting.{args.action}")

    if args.command == "status":
        try:
            resp = ipc.send("status")
        except ConnectionError as e:
            print(str(e), file=sys.stderr)
            return 1
        if not resp.get("ok", False):
            print(resp.get("error") or "status failed", file=sys.stderr)
            return 1
        data = resp.get("data") or {}
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            mode = data.get("mode", "?")
            model = data.get("model", "?")
            loaded = data.get("model_loaded")
            last = data.get("last_text") or ""
            err = data.get("error")
            print(f"mode={mode} model={model} loaded={loaded}")
            if last:
                print(f"last_text={last}")
            if err:
                print(f"error={err}")
            meeting = data.get("meeting") or {}
            if meeting.get("active"):
                print(f"meeting active elapsed={meeting.get('elapsed')} file={meeting.get('file')}")
        return 0

    if args.command == "quit":
        return _client("quit")

    if args.command == "inject":
        return _cmd_inject(args)

    if args.command == "model":
        return _cmd_model(args)

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
