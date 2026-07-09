# Author: Tom Sapletta · https://tom.sapletta.com
# Part of the ifURI solution.
#
# webcam:// connector — host a small camera-capture service on the LAN so a phone, tablet
# or any browser can be the camera. It launches the HTTP server in server.py (which serves
# a getUserMedia page and a same-origin /ingest endpoint) and forwards every captured frame
# to urirun-connector-camera's pipeline (OCR / barcodes / inspection / scene description).
# Routes: server/command/{start,stop}, server/query/status, captures/query/list.

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

import urirun

from . import _urirun_compat

CONNECTOR_ID = "webcam"
WEBCAM = _urirun_compat.connector(CONNECTOR_ID, scheme="webcam", target="host",
                          meta={"label": "Browser/mobile camera service"})

STATE_DIR = os.path.expanduser("~/.urirun/webcam")
META_PATH = os.path.join(STATE_DIR, "server.json")
LOG_PATH = os.path.join(STATE_DIR, "server.log")
DEFAULT_CAPTURES_DIR = os.path.join(STATE_DIR, "captures")


def _lan_ip() -> str:
    """Best-effort primary LAN IPv4 (no packets are actually sent)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _read_meta() -> dict[str, Any]:
    try:
        with open(META_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def _write_meta(meta: dict[str, Any]) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(META_PATH, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)


def _alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _health(url: str, timeout: float = 1.5) -> dict[str, Any]:
    ctx = None
    if url.startswith("https://"):
        import ssl
        ctx = ssl._create_unverified_context()  # self-signed cert is expected
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return {}


def _ensure_self_signed(lan_ip: str) -> dict[str, str]:
    """Create (once) a self-signed cert+key in the state dir, valid for the LAN IP and
    localhost, so the TLS server can offer a secure context to phones. Needs openssl."""
    cert = os.path.join(STATE_DIR, "cert.pem")
    key = os.path.join(STATE_DIR, "key.pem")
    if os.path.isfile(cert) and os.path.isfile(key):
        return {"cert": cert, "key": key}
    if not shutil.which("openssl"):
        return {"error": "openssl not found; pass certfile/keyfile or use a tunnel"}
    os.makedirs(STATE_DIR, exist_ok=True)
    san = f"subjectAltName=IP:{lan_ip},IP:127.0.0.1,DNS:localhost"
    proc = subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
         "-keyout", key, "-out", cert, "-days", "365",
         "-subj", f"/CN={lan_ip}", "-addext", san],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0 or not (os.path.isfile(cert) and os.path.isfile(key)):
        return {"error": (proc.stderr or "openssl failed").strip()[:300]}
    return {"cert": cert, "key": key}


def _camera_connector_present() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("urirun_connector_camera") is not None
    except Exception:  # noqa: BLE001
        return False


def _server_already_running(meta: dict[str, Any]) -> bool:
    if not meta:
        return False
    health_url = meta.get("healthUrl") or meta.get("url", "")
    return _alive(meta.get("pid", 0)) and bool(_health(health_url).get("ok"))


def _resolve_tls(https: bool, certfile: str, keyfile: str, lan_ip: str) -> tuple[str, str, str, str]:
    """Return (scheme, cert, key, error). error is '' on success."""
    if not https:
        return "http", "", "", ""
    cert = os.path.expanduser(certfile) if certfile else ""
    key = os.path.expanduser(keyfile) if keyfile else ""
    if not (cert and key):
        generated = _ensure_self_signed(lan_ip)
        if "error" in generated:
            return "https", "", "", generated["error"]
        cert, key = generated["cert"], generated["key"]
    return "https", cert, key, ""


def _poll_healthy(proc: subprocess.Popen, health_url: str, deadline: float) -> tuple[bool, str]:
    """Poll until healthy or deadline. Returns (healthy, error_tail)."""
    while time.time() < deadline:
        if not _alive(proc.pid):
            tail = ""
            try:
                with open(LOG_PATH, encoding="utf-8", errors="replace") as fh:
                    tail = fh.read()[-500:]
            except OSError:
                pass
            return False, tail
        if _health(health_url).get("ok"):
            return True, ""
        time.sleep(0.2)
    return False, ""


@WEBCAM.handler("server/command/start", isolated=True,
                meta={"label": "Start the LAN browser-camera service", "cliAlias": "start"})
def start(port: int = 8780, bind: str = "0.0.0.0", action: str = "analyze",
          captures_dir: str = "", title: str = "ifURI mobile scanner", lang: str = "eng+pol",
          token: str = "", https: bool = False, certfile: str = "", keyfile: str = "",
          max_upload_mb: int = 20, wait_seconds: float = 5.0) -> dict[str, Any]:
    """Start the browser/mobile camera service on the LAN and return the URL to open on the
    phone. The page captures via getUserMedia (rear camera) and posts each frame to /ingest,
    which runs urirun-connector-camera's `action` (analyze | inspect | barcodes | receipt |
    ocr | describe). Set `token` to require a shared secret. https=true serves over TLS
    (self-signed cert auto-generated via openssl unless certfile/keyfile are given) so phones
    can use the camera on a plain LAN IP. Idempotent: re-running while up returns the live
    server."""
    if not _camera_connector_present():
        return urirun.fail("urirun-connector-camera is required (pip install -e urirun-connector-camera)",
                           connector=CONNECTOR_ID)

    meta = _read_meta()
    if _server_already_running(meta):
        meta["alreadyRunning"] = True
        return urirun.ok(connector=CONNECTOR_ID, kind="stream", live=True, **meta)

    captures = os.path.expanduser(captures_dir) if captures_dir else DEFAULT_CAPTURES_DIR
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(captures, exist_ok=True)

    lan_ip = _lan_ip()
    scheme, cert, key, tls_err = _resolve_tls(https, certfile, keyfile, lan_ip)
    if tls_err:
        return urirun.fail(f"could not enable HTTPS: {tls_err}", connector=CONNECTOR_ID)

    env = dict(os.environ)
    env.update({
        "WEBCAM_BIND": bind,
        "WEBCAM_PORT": str(int(port)),
        "WEBCAM_DEFAULT_ACTION": action,
        "WEBCAM_CAPTURES_DIR": captures,
        "WEBCAM_TITLE": title,
        "WEBCAM_LANG": lang,
        "WEBCAM_TOKEN": token,
        "WEBCAM_MAX_UPLOAD_BYTES": str(int(max_upload_mb) * 1024 * 1024),
        "WEBCAM_TLS": "1" if https else "0",
        "WEBCAM_CERT": cert,
        "WEBCAM_KEY": key,
    })

    log = open(LOG_PATH, "ab", buffering=0)  # noqa: SIM115 - handed to the child process
    proc = subprocess.Popen(
        [sys.executable, "-m", "urirun_connector_camera_web.server"],
        stdout=log, stderr=log, stdin=subprocess.DEVNULL,
        start_new_session=True, env=env,
    )

    # The phone-facing URL uses the LAN IP when bound to all interfaces, else the bind addr.
    # The health probe must hit a locally-reachable host (127.0.0.1 when bound to 0.0.0.0).
    wildcard = bind in ("0.0.0.0", "::", "")
    url_host = lan_ip if wildcard else bind
    health_host = "127.0.0.1" if wildcard else bind
    url = f"{scheme}://{url_host}:{int(port)}/"
    health_url = f"{scheme}://{health_host}:{int(port)}/"
    meta = {
        "pid": proc.pid, "port": int(port), "bind": bind, "action": action, "scheme": scheme,
        "url": url, "lanIp": lan_ip, "openUrl": f"{url}?token={token}" if token else url,
        "healthUrl": health_url, "capturesDir": captures, "tokenRequired": bool(token),
        "https": bool(https), "title": title, "startedAt": time.time(), "log": LOG_PATH,
    }

    deadline = time.time() + max(0.5, float(wait_seconds))
    healthy, err_tail = _poll_healthy(proc, health_url, deadline)
    if err_tail:
        return urirun.fail(f"server exited during startup: {err_tail}", connector=CONNECTOR_ID, **meta)

    meta["healthy"] = healthy
    _write_meta(meta)
    return urirun.ok(connector=CONNECTOR_ID, kind="stream", live=bool(healthy), **meta)


@WEBCAM.handler("server/command/stop", isolated=True,
                meta={"label": "Stop the LAN browser-camera service", "cliAlias": "stop"})
def stop() -> dict[str, Any]:
    """Stop the running browser-camera service."""
    meta = _read_meta()
    pid = meta.get("pid", 0)
    if not pid or not _alive(pid):
        return urirun.ok(connector=CONNECTOR_ID, kind="stream", live=False, stopped=False, reason="not running")
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as exc:
            return urirun.fail(f"could not stop pid {pid}: {exc}", connector=CONNECTOR_ID)
    for _ in range(15):
        if not _alive(pid):
            break
        time.sleep(0.1)
    meta["stoppedAt"] = time.time()
    try:
        os.remove(META_PATH)
    except OSError:
        pass
    return urirun.ok(connector=CONNECTOR_ID, kind="stream", live=False, stopped=True, pid=pid)


@WEBCAM.handler("server/query/status", isolated=True,
                meta={"label": "Status of the LAN browser-camera service", "cliAlias": "status"})
def status() -> dict[str, Any]:
    """Report whether the browser-camera service is running, its URL and capture count."""
    meta = _read_meta()
    if not meta:
        return urirun.ok(connector=CONNECTOR_ID, kind="stream", live=False, running=False)
    alive = _alive(meta.get("pid", 0))
    health = _health(meta.get("healthUrl") or meta.get("url", "")) if alive else {}
    return urirun.ok(connector=CONNECTOR_ID, kind="stream", live=bool(alive and health.get("ok")), running=bool(alive and health.get("ok")),
                     processAlive=alive, health=health, **meta)


def _cache_ttl() -> int:
    try:
        return max(0, int(os.getenv("WEBCAM_CACHE_TTL", "1800")))
    except ValueError:
        return 1800


def _prune_cache(base: str, *, ttl_seconds: int, max_files: int = 200) -> int:
    """Captures are an ephemeral CACHE, not a store: delete entries older than `ttl_seconds`
    (and cap the count), so raw mobile frames don't accumulate. Only artifacts persist
    elsewhere. Returns how many were pruned. Best-effort, never raises."""
    removed = 0
    try:
        names = [n for n in os.listdir(base) if n.endswith(".json")]
    except OSError:
        return 0
    now = time.time()
    paths = [os.path.join(base, n) for n in names]
    for path in paths:
        try:
            if ttl_seconds and (now - os.path.getmtime(path)) > ttl_seconds:
                os.remove(path)
                removed += 1
        except OSError:
            continue
    # cap total: keep the newest max_files
    try:
        remaining = sorted((p for p in paths if os.path.exists(p)), key=os.path.getmtime, reverse=True)
        for path in remaining[max(1, int(max_files)):]:
            try:
                os.remove(path)
                removed += 1
            except OSError:
                continue
    except OSError:
        pass
    return removed


@WEBCAM.handler("captures/query/list", isolated=True,
                meta={"label": "List frames captured via the browser service", "cliAlias": "captures"})
def captures(limit: int = 25, captures_dir: str = "") -> dict[str, Any]:
    """List the most recent frames received from browsers (an ephemeral CACHE — entries older
    than WEBCAM_CACHE_TTL are pruned). Each carries a summary (action, OCR preview, decoded
    codes, inspection verdict). Only artifacts are stored permanently, not these frames."""
    base = os.path.expanduser(captures_dir) if captures_dir else (
        _read_meta().get("capturesDir") or DEFAULT_CAPTURES_DIR)
    ttl = _cache_ttl()
    pruned = _prune_cache(base, ttl_seconds=ttl)
    try:
        names = sorted((n for n in os.listdir(base) if n.endswith(".json")), reverse=True)
    except OSError:
        return urirun.ok(connector=CONNECTOR_ID, kind="capture-list", live=False, cache=True,
                         ttlSeconds=ttl, dir=base, count=0, pruned=pruned, captures=[])
    items: list[dict[str, Any]] = []
    for name in names[: max(1, int(limit))]:
        try:
            with open(os.path.join(base, name), encoding="utf-8") as fh:
                items.append(json.load(fh).get("summary", {}))
        except (OSError, json.JSONDecodeError):
            continue
    return urirun.ok(connector=CONNECTOR_ID, kind="capture-list", live=False, cache=True,
                     ttlSeconds=ttl, dir=base, count=len(items), pruned=pruned, captures=items)


def urirun_bindings() -> dict[str, Any]:
    """Serializable v2 bindings for this connector."""
    return WEBCAM.bindings()

@WEBCAM.handler("webcam://host/doctor/query/report", isolated=True, meta={"label": "Connector readiness report"})
def doctor() -> dict[str, Any]:
    """Return a safe, read-only connector readiness report for CI smoke tests."""
    return {
        "ok": True,
        "connector": CONNECTOR_ID,
        "version": _connector_version(),
        "status": "ready",
    }


def _connector_version() -> str:
    try:
        from importlib.metadata import version

        return version("urirun-connector-camera-web")
    except Exception:
        return "0.1.0"


def connector_manifest() -> dict[str, Any]:
    """Full manifest: prose plus derived routes."""
    return WEBCAM.manifest(_urirun_compat.load_manifest(__package__))


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point."""
    return WEBCAM.cli(argv, manifest_prose=_urirun_compat.load_manifest(__package__))


if __name__ == "__main__":
    raise SystemExit(main())
