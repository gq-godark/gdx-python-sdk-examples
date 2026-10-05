#!/usr/bin/env python3
"""Minimal GodarkRestClient demo — auth + account reads.

For encrypted place/modify/cancel over REST (one-shot HPKE), see full_trader_rest.py.

  cd examples && python rest_client_example.py

Environment:
  GODARK_API_KEY_ID, GODARK_API_SECRET, GODARK_PASSPHRASE
  GODARK_REST_URL (optional; default https://api.godark-dex.com)
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv
from godark import GodarkRestClient


async def main() -> int:
    load_dotenv()

    api_key_id = os.environ.get("GODARK_API_KEY_ID", "").strip()
    api_secret = os.environ.get("GODARK_API_SECRET", "").strip()
    passphrase = os.environ.get("GODARK_PASSPHRASE", "").strip()
    if not api_key_id or not api_secret or not passphrase:
        print(
            "Missing credentials: set GODARK_API_KEY_ID, GODARK_API_SECRET and "
            "GODARK_PASSPHRASE (e.g. in a .env file at the repo root).",
            file=sys.stderr,
        )
        return 1

    rest_kwargs: dict = {
        "api_key_id": api_key_id,
        "api_secret": api_secret,
        "passphrase": passphrase,
    }
    if rest := os.environ.get("GODARK_REST_URL", "").strip():
        rest_kwargs["rest_base_url"] = rest

    client = GodarkRestClient(**rest_kwargs)
    try:
        print("connecting (REST auth/token)...")
        await client.connect()

        positions = await client.get_positions()
        orders = await client.get_open_orders()
        account = await client.get_account()
        funding = await client.get_funding_rates()
        interest = await client.get_open_interest()
        volume = await client.get_volume()
        print(f"positions: {type(positions).__name__}")
        print(f"open_orders: {type(orders).__name__}")
        print(f"account: {type(account).__name__}")
        print(f"funding_rates: {len(funding)} rows")
        print(f"open_interest: {len(interest)} rows")
        print(f"volume: {type(volume).__name__}")

        print("REST reads succeeded.")
        print("For REST trading (place/modify/cancel), see full_trader_rest.py.")
    except Exception as exc:
        print(f"{exc}", file=sys.stderr)
        return 1
    finally:
        await client.disconnect()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
