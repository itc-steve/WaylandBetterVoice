"""Whisper ggml model catalog + stdlib-only downloader."""
from __future__ import annotations

import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from waylandbettervoice.config import MODEL_DIR, mkdirs

# Upstream: https://huggingface.co/ggerganov/whisper.cpp
_HF_BASE = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"

# Host the Authorization header may be sent to. HF answers a model request with a
# redirect to a CDN (cdn-lfs*.hf.co / cloudfront), and urllib would happily replay
# the header to whatever host it lands on. The token is a credential, so it goes to
# huggingface.co and nowhere else.
_TOKEN_HOST = "huggingface.co"


def read_token(explicit: str | None = None) -> str | None:
    """Resolve an optional Hugging Face token.

    Order: explicit argument, then HF_TOKEN / HUGGING_FACE_HUB_TOKEN, then the file
    the huggingface-cli writes (~/.cache/huggingface/token). Returns None when there
    is none — the whisper.cpp models are public and download fine anonymously.
    """
    if explicit and explicit.strip():
        return explicit.strip()
    for var in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        val = os.environ.get(var)
        if val and val.strip():
            return val.strip()
    # Same location huggingface-cli login uses, so an existing login just works.
    cache = os.environ.get("HF_HOME") or str(Path.home() / ".cache/huggingface")
    token_file = Path(cache) / "token"
    try:
        if token_file.is_file():
            val = token_file.read_text(encoding="utf-8").strip()
            if val:
                return val
    except OSError:
        pass
    return None


class _TokenSafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Strip Authorization when a redirect leaves the token host.

    Without this, an HF download that redirects to a CDN would forward the user's
    token to that third party.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        if urllib.parse.urlsplit(newurl).hostname != _TOKEN_HOST:
            for key in list(new.headers):
                if key.lower() == "authorization":
                    del new.headers[key]
            new.unredirected_hdrs.pop("Authorization", None)
        return new


def _build_opener(token: str | None):
    """urlopen-compatible callable that attaches the token only to the HF host."""
    opener = urllib.request.build_opener(_TokenSafeRedirectHandler)

    def _open(url):
        req = urllib.request.Request(url)
        if token and urllib.parse.urlsplit(url).hostname == _TOKEN_HOST:
            req.add_header("Authorization", f"Bearer {token}")
        return opener.open(req)

    return _open

# Sizes from Hugging Face Content-Length (verified 2026-08).
# ponytail: size-only verify; HF has no cheap stable SHA endpoint for these bins
@dataclass(frozen=True)
class KnownModel:
    name: str  # short CLI name, e.g. "large-v3"
    filename: str  # on-disk / URL name, e.g. "ggml-large-v3.bin"
    size: int  # expected bytes
    label: str  # human size


KNOWN_MODELS: tuple[KnownModel, ...] = (
    KnownModel("tiny.en", "ggml-tiny.en.bin", 77_704_715, "74 MB"),
    KnownModel("base.en", "ggml-base.en.bin", 147_964_211, "141 MB"),
    KnownModel("small", "ggml-small.bin", 487_601_967, "465 MB"),
    KnownModel("small.en", "ggml-small.en.bin", 487_614_201, "465 MB"),
    KnownModel("medium.en", "ggml-medium.en.bin", 1_533_774_781, "1.5 GB"),
    KnownModel("large-v3", "ggml-large-v3.bin", 3_095_033_483, "2.9 GB"),
)

_BY_NAME: dict[str, KnownModel] = {m.name: m for m in KNOWN_MODELS}
_BY_FILENAME: dict[str, KnownModel] = {m.filename: m for m in KNOWN_MODELS}


class ModelError(Exception):
    """User-facing model acquisition / resolution error."""


def resolve_known(name: str) -> KnownModel:
    """Accept short name ('large-v3') or full filename ('ggml-large-v3.bin')."""
    key = name.strip()
    if key in _BY_NAME:
        return _BY_NAME[key]
    if key in _BY_FILENAME:
        return _BY_FILENAME[key]
    # bare ggml-*.bin not in table still allowed for path resolution of custom drops
    raise ModelError(
        f"unknown model {name!r}. Known: {', '.join(m.name for m in KNOWN_MODELS)}"
    )


def short_name_for(filename: str) -> str:
    """Map config filename → CLI short name for error messages."""
    if filename in _BY_FILENAME:
        return _BY_FILENAME[filename].name
    # strip ggml- prefix / .bin suffix when possible
    s = filename
    if s.startswith("ggml-"):
        s = s[5:]
    if s.endswith(".bin"):
        s = s[:-4]
    return s or filename


def model_path(name: str) -> Path:
    """Path where a known (or raw filename) model would live under MODEL_DIR."""
    try:
        return MODEL_DIR / resolve_known(name).filename
    except ModelError:
        return MODEL_DIR / name


def is_present(m: KnownModel, directory: Path | None = None) -> bool:
    p = (directory or MODEL_DIR) / m.filename
    return p.is_file() and p.stat().st_size > 0


def sweep_partials(directory: Path | None = None) -> list[Path]:
    """Delete leftover .part files and return what was removed.

    download() clears its own .part on error, but a SIGKILL (or a full disk taking the
    process down) can strand one. They are never mistaken for a real model, they just
    waste space, so reclaim them on the next `wbv model list`.
    """
    d = directory or MODEL_DIR
    removed: list[Path] = []
    if not d.is_dir():
        return removed
    for p in d.glob("*.part"):
        try:
            p.unlink()
            removed.append(p)
        except OSError:  # ponytail: best effort; a locked file just stays
            pass
    return removed


def list_status(directory: Path | None = None) -> list[tuple[KnownModel, bool, int]]:
    """Return (model, present, actual_size) for each known model."""
    d = directory or MODEL_DIR
    out: list[tuple[KnownModel, bool, int]] = []
    for m in KNOWN_MODELS:
        p = d / m.filename
        if p.is_file():
            out.append((m, True, p.stat().st_size))
        else:
            out.append((m, False, 0))
    return out


def format_list(directory: Path | None = None) -> str:
    d = directory or MODEL_DIR
    lines = [f"model dir: {d}", ""]
    for stale in sweep_partials(d):
        lines.insert(1, f"removed incomplete download: {stale.name}")
    lines.append(f"{'NAME':<12} {'SIZE':>8}  {'STATUS':<12} FILE")
    for m, present, actual in list_status(d):
        if present:
            if actual == m.size:
                status = "present"
            else:
                status = f"present ({_fmt_bytes(actual)})"
        else:
            status = "missing"
        lines.append(f"{m.name:<12} {m.label:>8}  {status:<12} {m.filename}")
    return "\n".join(lines)


def _fmt_bytes(n: int) -> str:
    if n >= 1024**3:
        return f"{n / 1024**3:.2f} GB"
    if n >= 1024**2:
        return f"{n / 1024**2:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n} B"


def download(
    name: str,
    *,
    directory: Path | None = None,
    base_url: str | None = None,
    opener=None,
    progress_stream=None,
    token: str | None = None,
) -> Path:
    """Download a known model into directory (default MODEL_DIR).

    Writes to `<filename>.part`, verifies size, then os.replace into place.
    Never overwrites an existing final file. On HTTP/disk/Ctrl-C error, removes .part.
    """
    m = resolve_known(name)
    dest_dir = directory or MODEL_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / m.filename
    part = dest_dir / (m.filename + ".part")

    if dest.is_file():
        sz = dest.stat().st_size
        if sz == m.size:
            _prog(progress_stream, f"{m.filename} already present ({m.label}) — skip\n")
            return dest
        raise ModelError(
            f"{dest} exists but size {sz} != expected {m.size}. "
            "Remove it manually if you want to re-download."
        )

    url = f"{(base_url or _HF_BASE).rstrip('/')}/{m.filename}"
    resolved_token = read_token(token)
    open_url = opener or _build_opener(resolved_token)
    err_stream = progress_stream if progress_stream is not None else sys.stderr

    try:
        if part.exists():
            try:
                part.unlink()
            except OSError:
                pass

        _prog(err_stream, f"downloading {m.filename} ({m.label}) from {url}\n")
        if resolved_token:
            _prog(err_stream, "using Hugging Face token\n")
        try:
            resp = open_url(url)  # noqa: S310 — fixed HF URL / test file://
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise ModelError(
                    f"HTTP {e.code} fetching {url}: {e.reason}. "
                    "This model needs a Hugging Face token with access to it — pass "
                    "--token, set HF_TOKEN, or run: huggingface-cli login"
                ) from e
            if e.code == 429:
                raise ModelError(
                    f"HTTP 429 (rate limited) fetching {url}. Retry later, or use a "
                    "Hugging Face token (--token / HF_TOKEN) for a higher limit."
                ) from e
            raise ModelError(f"HTTP {e.code} fetching {url}: {e.reason}") from e
        except urllib.error.URLError as e:
            raise ModelError(f"network error fetching {url}: {e.reason}") from e

        expected = m.size
        # prefer server Content-Length when present
        try:
            cl = resp.headers.get("Content-Length") if hasattr(resp, "headers") else None
            if cl and cl.isdigit():
                expected = int(cl)
        except Exception:  # noqa: BLE001
            pass

        written = 0
        last_pct = -1
        try:
            with open(part, "wb") as out, resp:
                while True:
                    chunk = resp.read(1024 * 1024)  # 1 MiB
                    if not chunk:
                        break
                    try:
                        out.write(chunk)
                    except OSError as e:
                        raise ModelError(f"write failed (disk full?): {e}") from e
                    written += len(chunk)
                    if expected > 0:
                        pct = int(written * 100 / expected)
                        # throttle: one line per whole percent
                        if pct != last_pct and (pct % 1 == 0):
                            last_pct = pct
                            _prog(
                                err_stream,
                                f"\r  {pct:3d}%  {_fmt_bytes(written)} / {_fmt_bytes(expected)}",
                            )
            _prog(err_stream, "\n")
        except KeyboardInterrupt:
            _prog(err_stream, "\ninterrupted — removing partial\n")
            _unlink_quiet(part)
            raise

        if written != m.size and expected == m.size:
            _unlink_quiet(part)
            raise ModelError(
                f"size mismatch after download: got {written}, expected {m.size}"
            )
        # if server gave different Content-Length we already used it for progress;
        # still require exact catalog size when known
        if written != m.size:
            _unlink_quiet(part)
            raise ModelError(
                f"size mismatch after download: got {written}, expected {m.size}"
            )

        os.replace(part, dest)
        _prog(err_stream, f"saved {dest} ({m.label})\n")
        return dest
    except ModelError:
        _unlink_quiet(part)
        raise
    except Exception:
        _unlink_quiet(part)
        raise


def _prog(stream, text: str) -> None:
    if stream is None:
        return
    try:
        stream.write(text)
        stream.flush()
    except Exception:  # noqa: BLE001
        pass


def _unlink_quiet(p: Path) -> None:
    try:
        if p.exists():
            p.unlink()
    except OSError:
        pass


def ensure_model_dir() -> Path:
    mkdirs()
    return MODEL_DIR
