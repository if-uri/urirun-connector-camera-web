# Examples — urirun-connector-camera-web

Make any phone on your Wi‑Fi a camera for this node.

```bash
# 1. start the LAN service, choosing what each frame does
urirun-webcam start --port 8780 --action barcodes        # or analyze | inspect | ocr | describe

# 2. open the printed URL on the phone (same Wi‑Fi), allow the camera, tap Scan
#    http://<lan-ip>:8780/

# 3. watch the results land
urirun-webcam captures --limit 10
urirun-webcam status

# 4. stop
urirun-webcam stop
```

Require a shared token (LAN is untrusted):

```bash
urirun-webcam start --port 8780 --action inspect --token s3cret
# openUrl already contains ?token=s3cret
```

## As URIs over a urirun node

Host the service on the node physically near the scan desk; phones connect to it:

```
webcam://host/server/command/start   payload: {"port":8780,"action":"barcodes","token":"s3cret"}
webcam://host/server/query/status
webcam://host/captures/query/list    payload: {"limit":20}
webcam://host/server/command/stop
```

The page posts each frame to the camera connector, so the same OCR / barcode / inspection /
description pipeline used by `camera://` and the office flow runs on mobile captures too.
