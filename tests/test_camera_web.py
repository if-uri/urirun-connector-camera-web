"""Tests for the webcam:// connector. The lifecycle test launches the real LAN server on
a free local port, posts a frame, and checks the pipeline ran — then stops it."""
import base64
import io
import json
import shutil
import socket
import time
import urllib.request

import pytest

import urirun_connector_camera_web.core as c


def test_bindings_valid():
    b = c.urirun_bindings()
    assert set(b["bindings"]) == {
        "webcam://host/server/command/start",
        "webcam://host/server/command/stop",
        "webcam://host/server/query/status",
        "webcam://host/captures/query/list",
    }


def test_lan_ip_is_a_string():
    ip = c._lan_ip()
    assert isinstance(ip, str) and ip.count(".") == 3


def test_status_when_never_started(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "META_PATH", str(tmp_path / "none.json"))
    r = c.status()
    assert r["ok"] and r["running"] is False


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _png_b64(text="HELLO"):
    Image = pytest.importorskip("PIL.Image")
    img = Image.new("RGB", (320, 240), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _qr_b64(data="https://ifuri.com/INV-2026-555"):
    qrcode = pytest.importorskip("qrcode")
    pytest.importorskip("pyzbar")
    img = qrcode.make(data).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _post_json(url, obj):
    req = urllib.request.Request(url, data=json.dumps(obj).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _has(mod):
    import importlib.util
    return importlib.util.find_spec(mod) is not None


@pytest.mark.skipif(not (_has("urirun_connector_camera") and shutil.which("openssl")),
                    reason="needs camera connector + openssl")
def test_https_server_serves_over_tls(tmp_path, monkeypatch):
    import ssl
    monkeypatch.setattr(c, "META_PATH", str(tmp_path / "server.json"))
    monkeypatch.setattr(c, "LOG_PATH", str(tmp_path / "server.log"))
    monkeypatch.setattr(c, "STATE_DIR", str(tmp_path / "state"))
    port = _free_port()
    started = c.start(port=port, bind="127.0.0.1", action="ocr", https=True,
                      captures_dir=str(tmp_path / "caps"), wait_seconds=8)
    try:
        assert started["ok"] and started["https"] is True
        assert started["url"].startswith("https://") and started["healthy"] is True
        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(f"https://127.0.0.1:{port}/health", context=ctx, timeout=5) as resp:
            health = json.loads(resp.read().decode())
        assert health["ok"] is True
    finally:
        c.stop()


@pytest.mark.skipif(__import__("importlib.util", fromlist=["util"]).find_spec("urirun_connector_camera") is None,
                    reason="urirun-connector-camera not installed")
def test_server_lifecycle_ingests_a_frame(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "META_PATH", str(tmp_path / "server.json"))
    monkeypatch.setattr(c, "LOG_PATH", str(tmp_path / "server.log"))
    port = _free_port()
    started = c.start(port=port, bind="127.0.0.1", action="barcodes",
                      captures_dir=str(tmp_path / "caps"), wait_seconds=8)
    try:
        assert started["ok"] and started["healthy"] is True
        base = f"http://127.0.0.1:{port}"

        # the served page is the mobile getUserMedia capture UI
        with urllib.request.urlopen(base + "/", timeout=5) as resp:
            page = resp.read().decode("utf-8")
        assert "getUserMedia" in page and "/ingest" in page

        # post a QR frame → the barcodes action should decode it
        result = _post_json(base + "/ingest", {"bytes_b64": _qr_b64("https://ifuri.com/INV-2026-555"),
                                               "action": "barcodes", "required": "INV-2026-555"})
        assert result.get("ok") and result.get("found") is True
        assert result.get("captureId")

        # the capture is listed with a summary
        caps = c.captures(captures_dir=str(tmp_path / "caps"))
        assert caps["ok"] and caps["count"] >= 1

        st = c.status()
        assert st["running"] is True
    finally:
        c.stop()
    # after stop, status reports not running
    assert c.status()["running"] is False


def test_status_idle_is_live_false(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "META_PATH", str(tmp_path / "none.json"))
    r = c.status()
    assert r["kind"] == "stream" and r["live"] is False      # nothing running → not a live widget


def test_prune_cache_removes_stale_entries(tmp_path):
    import os, time as _t
    base = tmp_path / "captures"
    base.mkdir()
    fresh = base / "fresh.json"; fresh.write_text("{}")
    stale = base / "stale.json"; stale.write_text("{}")
    old = _t.time() - 4000
    os.utime(stale, (old, old))                       # older than the 1800s TTL
    removed = c._prune_cache(str(base), ttl_seconds=1800)
    assert removed == 1
    assert fresh.exists() and not stale.exists()


def test_captures_marks_cache_with_ttl(tmp_path):
    r = c.captures(captures_dir=str(tmp_path / "none"))
    assert r["ok"] and r["cache"] is True and r["ttlSeconds"] >= 0 and r["kind"] == "capture-list"
