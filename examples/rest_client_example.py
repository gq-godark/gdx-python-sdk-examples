#!/usr/bin/env python3
"""Minimal GodarkRestClient demo — auth + account reads + public market data.

Encrypted place/cancel/modify/update_leverage require GodarkClient (WebSocket /
Noise XK); see quickstart.py / full_trader_example.py.

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
        # Public market-data GETs — no connect() / bearer required.
        rates = await client.get_funding_rates()
        oi = await client.get_open_interest()
        vol = await client.get_volume()
        print(f"funding_rates: {len(rates)} symbols (first={rates[0] if rates else None})")
        print(f"open_interest: {len(oi)} symbols (first={oi[0] if oi else None})")
        print(
            f"volume: total_24h={vol.get('total_volume_24h')} "
            f"symbols={len(vol.get('symbols') or [])}"
        )

        print("connecting (REST auth/token)...")
        await client.connect()

        me = await client.get_me()
        print(f"me: id={me.id} wallet={me.wallet_address} tier={me.tier}")

        lev = await client.get_leverage()
        print(f"leverage settings: {len(lev.settings)} entries")
        for row in lev.settings[:5]:
            print(f"  symbol_id={row.symbol_id} leverage={row.leverage}")

        try:
            bal = await client.get_my_balance()
            print(
                f"balance: shielded_raw={bal.shielded_balance_raw} "
                f"wallet_ui={bal.wallet_usdt_ui}"
            )
        except Exception as exc:
            print(f"get_my_balance skipped: {exc}")

        print("REST reads succeeded.")
        print("Encrypted trading requires GodarkClient over WebSocket (Noise XK).")
    except Exception as exc:
        print(f"{exc}", file=sys.stderr)
        return 1
    finally:
        await client.disconnect()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
