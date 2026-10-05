"""Live-mark pricing and post-trade checks for the trading samples.

Samples never invent a BTC mark. A price environment variable may override the
feed; otherwise the mark comes from an authenticated position row that actually
carries one, or from public open-interest notionals on the SDK.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR

from dotenv import get_first
from godark import (
    GodarkClient,
    GodarkRestClient,
    OrderType,
    PlaceOrderOptions,
    PositionsSnapshot,
    Side,
    TimeInForce,
)

SYMBOL = "BTC-USDC-PERP"
BTC_SYMBOL_ID = 1
OFFSET = Decimal("500")
TICK = Decimal("0.5")
MAX_QTY = Decimal("0.001")
QTY = "0.001"


def qty_capped() -> str:
    """Sample size: at most 0.001 and at most 4 decimal places."""
    q = MAX_QTY.quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
    if q <= 0 or q > MAX_QTY:
        raise RuntimeError("sample quantity is outside the 0.001 cap")
    text = format(q, "f")
    if "." in text and len(text.split(".", 1)[1]) > 4:
        raise RuntimeError("sample quantity has more than 4 decimal places")
    return text


def _parse_mark(raw: str) -> Decimal:
    mark = Decimal(raw.strip())
    if mark <= 0:
        raise RuntimeError(f"mark must be positive, got {raw!r}")
    return mark


def post_only_price(mark: str, side: Side, *, steps: int = 1) -> str:
    """Post-only limit at least ``steps * 500`` away from the mark, on the BTC tick."""
    if steps < 1:
        raise RuntimeError("price offset steps must be >= 1")
    m = _parse_mark(mark)
    gap = OFFSET * steps
    if side is Side.SELL:
        raw = m + gap
        ticks = (raw / TICK).to_integral_value(rounding=ROUND_CEILING)
        px = ticks * TICK
        if px < m + gap:
            px += TICK
    elif side is Side.BUY:
        raw = m - gap
        ticks = (raw / TICK).to_integral_value(rounding=ROUND_FLOOR)
        px = ticks * TICK
        if px > m - gap:
            px -= TICK
    else:
        raise RuntimeError(f"unsupported side {side}")
    if px <= 0:
        raise RuntimeError("computed price is not positive")
    return format(px, "f")


def mark_from_open_interest(rows: object, symbol_id: int = BTC_SYMBOL_ID) -> str | None:
    """Implied mark = oi_ccy / open_interest from the public open-interest snapshot."""
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            sid = int(row.get("symbol_id") or 0)
        except (TypeError, ValueError):
            continue
        if sid != symbol_id:
            continue
        oi_raw = row.get("open_interest")
        ccy_raw = row.get("oi_ccy")
        if oi_raw is None or ccy_raw is None or str(ccy_raw).strip() == "":
            return None
        oi = Decimal(str(oi_raw))
        if oi <= 0:
            return None
        mark = Decimal(str(ccy_raw)) / oi
        if mark <= 0:
            return None
        return format(mark, "f")
    return None


def mark_from_position_rows(rows: object, symbol_id: int = BTC_SYMBOL_ID) -> str | None:
    if not rows:
        return None
    for row in rows:
        sid = getattr(row, "symbol_id", None)
        mark = getattr(row, "mark_price", None)
        if sid == symbol_id and mark:
            text = str(mark).strip()
            if text and text != "—":
                _parse_mark(text)
                return text
    return None


async def resolve_live_mark(
    rest: GodarkRestClient,
    *,
    position_rows: object = (),
    symbol_id: int = BTC_SYMBOL_ID,
) -> str:
    """Env override, else a snapshot mark, else public open interest. Empty means no trade."""
    raw = get_first("GODARK_E2E_PRICE", "GDX_E2E_PRICE", "GDX_LIVE_PRICE")
    if raw:
        _parse_mark(raw)
        return raw.strip()
    snapped = mark_from_position_rows(position_rows, symbol_id)
    if snapped:
        return snapped
    rows = await rest.get_open_interest()
    implied = mark_from_open_interest(rows, symbol_id)
    if not implied:
        raise RuntimeError(
            "no live mark: set a price env or wait for a snapshot/open-interest mark"
        )
    return implied


def position_fingerprint(snap: PositionsSnapshot) -> dict[tuple[int, str], str]:
    out: dict[tuple[int, str], str] = {}
    for row in snap.rows:
        size = Decimal(row.size or "0")
        if size == 0:
            continue
        side = row.side.value if isinstance(row.side, Side) else str(row.side)
        out[(int(row.symbol_id), side)] = format(size, "f")
    return out


def open_order_ids(snap) -> set[str]:
    return {str(row.order_id) for row in snap.rows if str(row.order_id)}


async def read_book(rest: GodarkRestClient):
    if not rest.bearer_token:
        await rest.connect()
    orders = await rest.get_open_orders()
    positions = await rest.get_positions()
    return orders, positions


async def assert_own_orders_flat(
    rest: GodarkRestClient,
    own_ids: set[str],
    baseline: dict[tuple[int, str], str],
) -> None:
    """Fail if an order this process placed is still open or positions changed."""
    orders, positions = await read_book(rest)
    still = own_ids & open_order_ids(orders)
    if still:
        raise RuntimeError(f"own order(s) still open: {sorted(still)}")
    now = position_fingerprint(positions)
    if now != baseline:
        raise RuntimeError(f"positions changed: before={baseline} after={now}")


async def cancel_own(
    client: GodarkClient,
    order_id: str,
    *,
    symbol: str = SYMBOL,
) -> None:
    """Wait at least one second, then cancel only this order id."""
    await asyncio.sleep(1)
    ack = await client.cancel_order(str(order_id), symbol)
    if not ack.success:
        raise RuntimeError(f"cancel failed for {order_id}: {ack.error or ack.error_code}")


def _four_dp(size: Decimal) -> str:
    q = size.copy_abs().quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
    if q <= 0:
        raise RuntimeError(f"position size {size} is below 0.0001")
    return format(q, "f")


async def flatten_positions(
    client: GodarkClient,
    rest: GodarkRestClient,
    baseline: dict[tuple[int, str], str],
    mark: str,
) -> None:
    """Reduce-only limit to remove size this process added. Quantity is at most 4 dp."""
    _, positions = await read_book(rest)
    now = position_fingerprint(positions)
    for key, size_s in now.items():
        before = Decimal(baseline.get(key, "0"))
        size = Decimal(size_s)
        extra = size - before
        if extra <= 0:
            continue
        symbol_id, side_name = key
        if symbol_id != BTC_SYMBOL_ID:
            raise RuntimeError(f"will not flatten unexpected symbol_id={symbol_id}")
        qty = _four_dp(extra)
        if side_name == Side.BUY.value:
            side = Side.SELL
            price = post_only_price(mark, Side.BUY, steps=1)
        elif side_name == Side.SELL.value:
            side = Side.BUY
            price = post_only_price(mark, Side.SELL, steps=1)
        else:
            raise RuntimeError(f"unknown position side {side_name}")
        ack = await client.place_order(
            SYMBOL,
            side,
            OrderType.LIMIT,
            qty,
            price=price,
            time_in_force=TimeInForce.GTC,
            options=PlaceOrderOptions(reduce_only=True, post_only=False),
        )
        if not ack.success:
            raise RuntimeError(f"flatten place failed: {ack.error or ack.error_code}")
        await asyncio.sleep(1)
        orders, after = await read_book(rest)
        if str(ack.order_id) in open_order_ids(orders):
            await cancel_own(client, str(ack.order_id))
        _, after = await read_book(rest)
        left = position_fingerprint(after).get(key)
        if left is not None and Decimal(left) >= size:
            raise RuntimeError("reduce-only flatten did not reduce the position")
