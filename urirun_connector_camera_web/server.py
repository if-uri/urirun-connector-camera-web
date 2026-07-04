# Author: Tom Sapletta · https://tom.sapletta.com
# Part of the ifURI solution.
#
# LAN HTTP server for the webcam:// connector. It serves a mobile-friendly page that uses
# the browser's getUserMedia (rear camera) to capture frames, and a same-origin /ingest
# endpoint that forwards each frame to the camera connector's pipeline (OCR / barcodes /
# inspection / scene description). Same origin → no CORS. Run standalone with:
#   python -m urirun_connector_camera_web.server
# configured entirely through WEBCAM_* environment variables set by the connector.

from __future__ import annotations

import base64
import json
import os
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

def _resolve_secret(value: str) -> str:
    """Resolve WEBCAM_TOKEN through the urirun secrets layer when present (so it may hold a
    ``secret://``/``getv://`` reference, allow = itself), degrading to the literal when urirun
    is not importable in this standalone server process."""
    try:
        import urirun
        return urirun.resolve_secret(value, value)
    except Exception:  # noqa: BLE001
        return value


DEFAULT_ACTION = os.getenv("WEBCAM_DEFAULT_ACTION", "analyze")
TITLE = os.getenv("WEBCAM_TITLE", "ifURI mobile scanner")
TOKEN = _resolve_secret(os.getenv("WEBCAM_TOKEN", ""))
CAPTURES_DIR = os.path.expanduser(os.getenv("WEBCAM_CAPTURES_DIR", "~/.urirun/webcam/captures"))
LANG = os.getenv("WEBCAM_LANG", "eng+pol")
MAX_UPLOAD = int(os.getenv("WEBCAM_MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))

ACTIONS = ["analyze", "inspect", "barcodes", "receipt", "ocr", "describe"]

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>__TITLE__</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:-apple-system,system-ui,Segoe UI,Roboto,sans-serif;
         background:#0b0e14; color:#e6edf3; }
  header { padding:12px 16px; font-weight:600; background:#11161f; border-bottom:1px solid #1f2733; }
  #wrap { padding:12px; max-width:720px; margin:0 auto; }
  video, canvas { width:100%; border-radius:12px; background:#000; display:block; }
  canvas { display:none; }
  .row { display:flex; gap:8px; margin:10px 0; flex-wrap:wrap; }
  select, button { font-size:16px; padding:12px 14px; border-radius:10px; border:1px solid #2b3645;
                   background:#1b2430; color:#e6edf3; }
  button.primary { background:#2563eb; border-color:#2563eb; flex:1; font-weight:600; }
  button:active { transform:scale(0.98); }
  label.toggle { display:flex; align-items:center; gap:8px; padding:10px 0; }
  pre { white-space:pre-wrap; word-break:break-word; background:#0f141c; border:1px solid #1f2733;
        border-radius:10px; padding:12px; font-size:13px; max-height:40vh; overflow:auto; }
  .ok { color:#3fb950; } .bad { color:#f85149; } .muted { color:#8b949e; font-size:13px; }
  #shot { margin-top:8px; }
</style>
</head>
<body>
<header>__TITLE__</header>
<div id="wrap">
  <video id="v" autoplay playsinline muted></video>
  <canvas id="c"></canvas>
  <div class="row">
    <select id="action" aria-label="action">__OPTIONS__</select>
    <select id="target" aria-label="crop target">
      <option value="auto">crop: auto</option>
      <option value="document">crop: receipt/doc</option>
      <option value="object">crop: object</option>
      <option value="none">crop: none</option>
    </select>
    <button id="cam">Enable camera</button>
  </div>
  <div class="row">
    <button id="snap" class="primary">Scan / capture</button>
  </div>
  <label class="toggle"><input type="checkbox" id="deskew"> Deskew (flatten an angled receipt/document)</label>
  <label class="toggle"><input type="checkbox" id="cont"> Continuous scan (every 1.5s)</label>
  <div id="status" class="muted">Tap “Enable camera”, allow access, point the rear camera at a document, label or QR code.</div>
  <img id="shot" style="display:none;width:100%;border-radius:12px"/>
  <pre id="out" class="muted">No result yet.</pre>
</div>
<script>
const TOKEN = "__TOKEN__";
const v = document.getElementById('v'), c = document.getElementById('c');
const out = document.getElementById('out'), statusEl = document.getElementById('status');
const shot = document.getElementById('shot');
let stream = null, timer = null, busy = false;

async function enableCam() {
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 } }, audio: false });
    v.srcObject = stream;
    statusEl.textContent = 'Camera on. Press “Scan / capture”.';
  } catch (e) {
    statusEl.innerHTML = '<span class="bad">Camera error: ' + e.message +
      '. On iOS/Android this page must be opened over HTTPS or http://localhost.</span>';
  }
}

function grabBase64() {
  const w = v.videoWidth, h = v.videoHeight;
  if (!w || !h) return null;
  c.width = w; c.height = h;
  c.getContext('2d').drawImage(v, 0, 0, w, h);
  shot.src = c.toDataURL('image/jpeg', 0.85); shot.style.display = 'block';
  return c.toDataURL('image/jpeg', 0.85);
}

async function scan() {
  if (busy) return;
  const dataUrl = grabBase64();
  if (!dataUrl) { statusEl.textContent = 'Camera not ready yet…'; return; }
  busy = true; statusEl.textContent = 'Uploading & processing…';
  try {
    const res = await fetch('/ingest' + (TOKEN ? ('?token=' + encodeURIComponent(TOKEN)) : ''), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bytes_b64: dataUrl, action: document.getElementById('action').value,
                             target: document.getElementById('target').value,
                             deskew: document.getElementById('deskew').checked }) });
    const data = await res.json();
    render(data);
  } catch (e) {
    out.textContent = 'Request failed: ' + e.message; out.className = 'bad';
  } finally { busy = false; }
}

function render(data) {
  const v = (data && data.result && data.result.value) ? data.result.value : data;
  let lines = [];
  const ok = v.ok !== false;
  lines.push((ok ? '✓ ' : '✗ ') + (v.action || '') + (v.error ? (' — ' + v.error) : ''));
  if (v.ocr && v.ocr.text) lines.push('\\nOCR:\\n' + v.ocr.text.trim());
  if (v.text) lines.push('\\nText:\\n' + v.text.trim());
  if (v.codes && v.codes.length) lines.push('\\nCodes:\\n' +
      v.codes.map(c => '• ' + c.type + ': ' + c.data).join('\\n'));
  if (v.items && v.items.length) lines.push('\\nItems:\\n' +
      v.items.map(it => '• ' + it.name + '  ' + it.price).join('\\n') +
      (v.total != null ? ('\\nTotal: ' + v.total + ' ' + (v.currency || '')) : ''));
  if (v.description && v.description.text) lines.push('\\n' + v.description.text);
  if (v.inspection) lines.push('\\nInspection: ' + (v.inspection.passed ? 'PASS' : 'ALERT') +
      (v.inspection.alerts && v.inspection.alerts.length ?
        ('\\n' + v.inspection.alerts.map(a => '⚠ ' + a.code + ': ' + a.message).join('\\n')) : ''));
  out.textContent = lines.join('\\n');
  out.className = ok ? 'ok' : 'bad';
  statusEl.textContent = 'Done at ' + new Date().toLocaleTimeString();
}

document.getElementById('cam').onclick = enableCam;
document.getElementById('snap').onclick = scan;
document.getElementById('cont').onchange = (e) => {
  if (e.target.checked) { timer = setInterval(scan, 1500); }
  else { clearInterval(timer); timer = null; }
};
</script>
</body>
</html>"""


def _render_page() -> bytes:
    options = "".join(
        f'<option value="{a}"{" selected" if a == DEFAULT_ACTION else ""}>{a}</option>'
        for a in ACTIONS
    )
    html = (PAGE.replace("__TITLE__", TITLE)
                .replace("__OPTIONS__", options)
                .replace("__TOKEN__", TOKEN))
    return html.encode("utf-8")


def _save_capture(action: str, value: dict) -> str:
    os.makedirs(CAPTURES_DIR, exist_ok=True)
    cap_id = f"{int(time.time())}-{secrets.token_hex(3)}"
    summary = {
        "id": cap_id,
        "ts": time.time(),
        "action": action,
        "ok": value.get("ok", True),
        "textPreview": str((value.get("ocr") or {}).get("text") or value.get("text") or "")[:200],
        "codes": [c.get("data") for c in (value.get("codes") or [])],
        "inspectionPassed": (value.get("inspection") or {}).get("passed"),
    }
    with open(os.path.join(CAPTURES_DIR, cap_id + ".json"), "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "value": value}, fh, ensure_ascii=False, indent=2)
    return cap_id


def _count_captures() -> int:
    try:
        return len([n for n in os.listdir(CAPTURES_DIR) if n.endswith(".json")])
    except OSError:
        return 0


class Handler(BaseHTTPRequestHandler):
    server_version = "ifuri-webcam/0.1"

    def log_message(self, *args):  # quiet by default; the connector captures stdout
        pass

    def _send(self, code: int, body: bytes, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json")

    def _authorized(self) -> bool:
        if not TOKEN:
            return True
        q = parse_qs(urlparse(self.path).query)
        supplied = (q.get("token", [""])[0] or self.headers.get("X-Webcam-Token", ""))
        return secrets.compare_digest(supplied, TOKEN)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            self._send(200, _render_page(), "text/html; charset=utf-8")
        elif path == "/health":
            self._json(200, {"ok": True, "service": "webcam", "captures": _count_captures(),
                             "defaultAction": DEFAULT_ACTION})
        elif path == "/captures":
            if not self._authorized():
                return self._json(401, {"ok": False, "error": "unauthorized"})
            items = []
            try:
                names = sorted((n for n in os.listdir(CAPTURES_DIR) if n.endswith(".json")), reverse=True)
            except OSError:
                names = []
            for name in names[:50]:
                try:
                    with open(os.path.join(CAPTURES_DIR, name), encoding="utf-8") as fh:
                        items.append(json.load(fh).get("summary", {}))
                except (OSError, json.JSONDecodeError):
                    continue
            self._json(200, {"ok": True, "count": len(items), "captures": items})
        else:
            self._json(404, {"ok": False, "error": "not found"})

    def _read_ingest_body(self) -> "tuple[dict, None] | tuple[None, int, str]":
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_UPLOAD + (1 << 20):
            return None, 413, "missing or oversize body"
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return None, 400, f"bad json: {exc}"
        return body, None, ""

    def _call_camera_ingest(self, body: dict) -> "dict | None":
        try:
            from urirun_connector_camera.core import ingest  # type: ignore
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"ok": False, "error": f"camera connector unavailable: {exc}"})
            return None
        action = (body.get("action") or DEFAULT_ACTION).strip().lower()
        return ingest(
            bytes_b64=str(body.get("bytes_b64") or ""),
            filename=str(body.get("filename") or "frame.jpg"),
            action=action,
            target=str(body.get("target") or "auto"),
            deskew=bool(body.get("deskew") or False),
            lang=str(body.get("lang") or LANG),
            required_text=str(body.get("required_text") or ""),
            required=str(body.get("required") or ""),
            min_chars=int(body.get("min_chars") or 1),
            require_object=bool(body.get("require_object") or False),
            fail_if_missing=bool(body.get("fail_if_missing") or False),
            store=bool(body.get("store", action in ("receipt", "analyze", "inspect"))),
            store_name=str(body.get("store_name") or "paragon"),
            max_input_bytes=MAX_UPLOAD,
        )

    def do_POST(self):
        if urlparse(self.path).path != "/ingest":
            return self._json(404, {"ok": False, "error": "not found"})
        if not self._authorized():
            return self._json(401, {"ok": False, "error": "unauthorized"})
        body, err_code, err_msg = self._read_ingest_body()
        if body is None:
            return self._json(err_code, {"ok": False, "error": err_msg})
        if not (body.get("bytes_b64") or ""):
            return self._json(400, {"ok": False, "error": "bytes_b64 required"})
        value = self._call_camera_ingest(body)
        if value is None:
            return
        action = (body.get("action") or DEFAULT_ACTION).strip().lower()
        try:
            cap_id = _save_capture(action, value if isinstance(value, dict) else {"value": value})
            if isinstance(value, dict):
                value["captureId"] = cap_id
        except OSError:
            pass
        self._json(200, value if isinstance(value, dict) else {"ok": True, "value": value})


def main() -> int:
    host = os.getenv("WEBCAM_BIND", "0.0.0.0")
    port = int(os.getenv("WEBCAM_PORT", "8780"))
    os.makedirs(CAPTURES_DIR, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), Handler)
    scheme = "http"
    certfile = os.getenv("WEBCAM_CERT", "")
    keyfile = os.getenv("WEBCAM_KEY", "")
    if os.getenv("WEBCAM_TLS") == "1" and certfile and keyfile:
        # TLS lets phones use the camera on a plain LAN IP (getUserMedia needs a secure
        # context). The cert is self-signed, so the phone shows a one-time warning to accept.
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=certfile, keyfile=keyfile)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        scheme = "https"
    print(f"webcam server on {scheme}://{host}:{port}/ action={DEFAULT_ACTION} captures={CAPTURES_DIR}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
