# godark (vendored)

This is a vendored copy of the GoDark Python trading SDK for market-maker
examples. Pre-generated protobuf modules live under `godark/_generated/`; you
do **not** need `protoc`.

Install into a virtualenv:

```bash
pip install .
```

The canonical `shared/symbols.json` is packaged as `godark/symbols.json`.

WebSocket base URL: set `GODARK_EDGE_URL` (or pass `base_url=` to
`GodarkClient`); the client appends `/ws/v1`.
