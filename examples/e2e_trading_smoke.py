#!/usr/bin/env python3
"""E2E trading smoke for Python SDK (auth-only or place+cancel)."""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from godark import AuthenticationError, ConnectionError, GodarkClient, OrderError, SessionError

from common import require_credentials, ws_base


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="GoDark Python SDK E2E smoke")
    p.add_argument("--auth-only", action="store_true", help="Only authenticate + ECDH setup")
    return p.parse_args()


async def run(auth_only: bool) -> int:
    key_id, secret = require_credentials()
    client = GodarkClient(
        api_key_id=key_id,
        api_secret=secret,
        base_url=ws_base(),
        auto_reconnect=False,
    )

    t0 = time.perf_counter()
    await client.connect()
    ms = round((time.perf_counter() - t0) * 1000, 2)
    print(f"[e2e] Auth + ECDH OK user_uuid={client.user_uuid} ({ms} ms)")

    if auth_only:
        await client.disconnect()
        print("[e2e] --auth-only done")
        return 0

    symbol = "BTC-USDT-PERP"
    ack = await client.place_order(
        symbol=symbol,
        side="SELL",
        order_type="LIMIT",
        quantity=0.001,
        price=999999.0,
    )
    print(f"[e2e] Place OK order_id={ack.order_id}")

    cancel = await client.cancel_order(order_id=ack.order_id, symbol=symbol)
    print(f"[e2e] Cancel OK order_id={cancel.order_id}")
    await client.disconnect()
    return 0


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(run(auth_only=args.auth_only))
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 1
    except (AuthenticationError, ConnectionError, SessionError) as e:
        print(f"[e2e] connect/auth/session failure: {e}", file=sys.stderr)
        return 2
    except OrderError as e:
        print(f"[e2e] order flow failure: {e}", file=sys.stderr)
        return 3
    except Exception as e:  # noqa: BLE001
        print(f"[e2e] unexpected error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
