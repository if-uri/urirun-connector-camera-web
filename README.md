# urirun-connector-camera-web

**Browser/mobile camera service** — connector ekosystemu [ifURI / urirun](https://github.com/if-uri/urirun).
Schemat URI: `webcam://`

Host a small camera-capture service on the LAN so a **phone, tablet or any browser becomes a
camera** for this node. It serves a mobile `getUserMedia` page (rear camera) and a same-origin
`/ingest` endpoint that forwards every captured frame to
[urirun-connector-camera](../urirun-connector-camera): OCR, barcodes/QR, inspection with alerts,
or scene description. No app to install, no CORS.

## Routes (URI)

| URI | What it does |
| --- | --- |
| `webcam://host/server/command/start` | Start the LAN server, return the URL to open on a phone |
| `webcam://host/server/command/stop` | Stop the server |
| `webcam://host/server/query/status` | Running? URL, health, capture count |
| `webcam://host/captures/query/list` | Recent frames + their OCR/barcode/inspection results |

## How it works

```text
phone browser (getUserMedia, rear cam)
   │  POST /ingest { bytes_b64, action }     (same origin → no CORS)
   ▼
webcam:// LAN server  ──►  urirun-connector-camera ingest
   │                          analyze | inspect | barcodes | receipt | ocr | describe
   ▼
result back to the phone  +  saved to the captures dir
```

`start` returns `openUrl` — open it on the phone (same Wi‑Fi). Pick the action in the page,
point the rear camera at a document/label/QR and tap **Scan**; the result (OCR text, decoded
codes, inspection verdict, description) comes straight back.

## Szybki start

```bash
pip install -e .            # also installs urirun-connector-camera
urirun-webcam start --port 8780 --action analyze
# → open the printed http://<lan-ip>:8780/ on your phone, allow the camera

urirun-webcam status
urirun-webcam captures --limit 10
urirun-webcam stop
```

Over a urirun node the same is a URI: `webcam://host/server/command/start` on the node hosting
the service makes any phone on that LAN a camera for the office flow (`usb://` + `camera://` +
`ocr://`).

## Camera access & HTTPS

Browsers only grant `getUserMedia` on a **secure context**: `https://…` or `http://localhost`.
On a plain `http://<lan-ip>` most mobile browsers block the camera. The simplest fix is to
serve over TLS:

```bash
urirun-webcam start --port 8780 --action receipt --https
# → openUrl: https://<lan-ip>:8780/   (self-signed: the phone shows a one-time warning to accept)
```

`--https` auto-generates a self-signed cert+key (via `openssl`) for the LAN IP and localhost
into `~/.urirun/webcam/`. Pass your own with `certfile=…`/`keyfile=…`. Other options:

- open `http://localhost:PORT` when testing on the same machine;
- put the service behind an HTTPS reverse proxy / tunnel for phones;
- on Android Chrome you can allowlist the origin under `chrome://flags/#unsafely-treat-insecure-origin-as-secure`.

## Security

The service binds to `0.0.0.0` by default (LAN-visible). Pass `token=<secret>` to require a
shared token on `/ingest` (the `openUrl` then embeds `?token=…`). Run it only on trusted
networks.

The server reads its `/ingest` token from `WEBCAM_TOKEN`, **addressed by reference**: the
value may be a literal token or a urirun secrets-layer reference (`secret://keyring/webcam#token`,
`getv://WEBCAM_TOKEN`), resolved at startup through `urirun.resolve_secret` (the standalone
server degrades to the literal when urirun is not importable). `WEBCAM_CERT` / `WEBCAM_KEY` are
TLS **file paths** (cert/key), not secret values.

## Wymagania

- **python:** `urirun`, `urirun-connector-camera`
- **optional:** `urirun-connector-ocr` (img2nl + richer OCR), `pyzbar` + `libzbar0` (barcodes)

## Powiązane

- Rdzeń: [if-uri/urirun](https://github.com/if-uri/urirun)
- Kamera: [urirun-connector-camera](../urirun-connector-camera) — capture/crop/OCR/inspect/barcodes
- USB: [urirun-connector-usb](../urirun-connector-usb) — device discovery
- Przykład: [examples/43-camera-usb-ocr-inspection](../examples/43-camera-usb-ocr-inspection)

---
Kategoria: Hardware · Słowa kluczowe: webcam, mobile, browser, getUserMedia, LAN, server, OCR, barcode, inspection · Wydawca: if-uri
