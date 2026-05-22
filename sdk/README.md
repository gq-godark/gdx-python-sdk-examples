# GoDark Python SDK

Python SDK for encrypted trading on the GoDark DEX over WebSocket.

Pre-generated protobuf modules live under `godark/_generated/`; you do **not**
need `protoc`.

Install:

```bash
pip install .
```

The canonical `shared/symbols.json` is packaged as `godark/symbols.json`.

WebSocket base URL: set `GODARK_EDGE_URL` (or pass `base_url=` to
`GodarkClient`); the client appends `/ws/v1`.
