"""Canonical numeric order error codes (shared registry across GoDark SDKs)."""

from __future__ import annotations

from dataclasses import dataclass

from .errors import OrderError


@dataclass(frozen=True)
class OrderErrorEntry:
    #: Wire code from the sequencer protobuf.
    code: int
    #: SCREAMING_SNAKE_CASE name (matches JSON `OrderErrorCode::as_json_str`).
    symbolic: str
    #: Human reason from the canonical protocol definition.
    reason: str


# All canonical codes the sequencer can emit. Keep in sync with `gdx-protocol`.
ORDER_ERROR_CODES: tuple[OrderErrorEntry, ...] = (
    # 1xxx -- Node / MPC
    OrderErrorEntry(1001, "TRIPLE_EXHAUSTED", "Beaver triple store exhausted"),
    OrderErrorEntry(1002, "RANDOM_BIT_EXHAUSTED", "random bit store exhausted"),
    OrderErrorEntry(1003, "MPC_PROTOCOL_ERROR", "MPC protocol error"),
    OrderErrorEntry(1004, "MPC_TIMEOUT", "MPC session timeout"),
    OrderErrorEntry(1005, "MPC_CONFIG_ERROR", "MPC configuration error"),
    OrderErrorEntry(1006, "MPC_OPS_LIMIT_EXCEEDED", "MPC ops limit exceeded"),
    # 2xxx -- Risk / validation
    OrderErrorEntry(2001, "RISK_CHECK_FAILED", "pre-trade risk check failed"),
    OrderErrorEntry(2002, "INSUFFICIENT_COLLATERAL", "insufficient collateral"),
    OrderErrorEntry(2003, "ORDER_NOT_FOUND", "order not found in book"),
    OrderErrorEntry(2004, "DUPLICATE_ORDER_ID", "duplicate order ID"),
    OrderErrorEntry(2005, "INSUFFICIENT_LIQUIDITY", "insufficient liquidity"),
    OrderErrorEntry(2006, "POSITION_UNDER_LIQUIDATION", "position is under active liquidation"),
    OrderErrorEntry(2007, "PRICE_DEVIATION_TOO_LARGE", "order price too far from oracle price"),
    OrderErrorEntry(2008, "LEVERAGE_EXCEEDS_MAX", "leverage exceeds instrument max"),
    OrderErrorEntry(2009, "INSTRUMENT_HALTED", "instrument halted -- not currently accepting orders"),
    OrderErrorEntry(2010, "LIQUIDITY_POOL_WITHDRAW_COOLDOWN", "withdrawal cooldown active"),
    OrderErrorEntry(2011, "LIQUIDITY_POOL_PAUSED", "liquidity pool paused"),
    OrderErrorEntry(2012, "LIQUIDITY_POOL_ILLIQUID", "insufficient pool liquidity for withdrawal"),
    OrderErrorEntry(2013, "BELOW_MIN_NOTIONAL", "order notional below tier minimum"),
    OrderErrorEntry(2014, "ORDER_EXCEEDS_COLLATERAL", "order size exceeds collateral value limits"),
    OrderErrorEntry(2015, "MARGIN_INSUFFICIENT", "insufficient margin for this trade"),
    # 3xxx -- Sequencer
    OrderErrorEntry(3001, "ACK_TIMEOUT", "ACK collection timed out"),
    OrderErrorEntry(3002, "ACK_THRESHOLD_NOT_MET", "ACK threshold not met"),
    OrderErrorEntry(3003, "SEQUENCER_NOT_PRIMARY", "sequencer is standby, not primary"),
    OrderErrorEntry(3004, "INSUFFICIENT_MASKS", "insufficient input masks for authenticated split"),
    OrderErrorEntry(3005, "FANOUT_FAILED", "fanout delivery failed"),
    OrderErrorEntry(3006, "DESERIALIZATION_FAILED", "message deserialization failed"),
    OrderErrorEntry(3007, "ALL_NODES_EXHAUSTED", "all MPC nodes have exhausted precompute pools"),
    OrderErrorEntry(3008, "SESSION_EXPIRED", "E2E session expired or not established"),
    OrderErrorEntry(3009, "E2E_DECRYPTION_FAILED", "E2E decryption failed (session key mismatch)"),
    OrderErrorEntry(3010, "SHIELD_SUBMIT_RPC_FAILED", "shield transaction rejected by Solana RPC"),
    OrderErrorEntry(3011, "SEQUENCER_BUSY", "sequencer busy -- try again"),
    # 4xxx -- Fencing / hot standby
    OrderErrorEntry(4001, "EPOCH_STALE", "fencing epoch is stale"),
    # 9xxx -- catch-all
    OrderErrorEntry(9999, "INTERNAL_ERROR", "internal processing error"),
)


_ORDER_BY_CODE = {e.code: e for e in ORDER_ERROR_CODES}


def find(code: int) -> OrderErrorEntry | None:
    """Look up an entry by its numeric wire code."""
    return _ORDER_BY_CODE.get(code)


def find_symbolic(symbolic: str) -> OrderErrorEntry | None:
    """Look up by SCREAMING_SNAKE_CASE symbolic name."""
    for entry in ORDER_ERROR_CODES:
        if entry.symbolic == symbolic:
            return entry
    return None


def make_order_error_from_code(numeric: int | None) -> OrderError:
    """Map protobuf `AckMessage.error_code` → rich :class:`~godark.errors.OrderError`."""
    if numeric is None:
        return OrderError("order rejected", error_code=None)
    raw = numeric
    if 0 <= raw <= 65535:
        entry = find(raw)
        if entry:
            msg = f"{entry.reason} ({entry.symbolic}, code={entry.code})"
            return OrderError(msg, error_code=entry.symbolic)
    return OrderError("order rejected", error_code=str(raw))


def make_order_error_from_json(reason: str | None, code: str | None) -> OrderError:
    """JSON ack path — wire may carry numeric or symbolic ``error_code`` strings."""
    final_reason = reason if reason else "order rejected"
    final_code = code

    if code:
        stripped = code.strip()
        try:
            parsed = int(stripped)
        except ValueError:
            parsed = None
        if parsed is not None:
            narrow = parsed
            if 0 <= narrow <= 65535:
                entry = find(narrow)
                if entry:
                    final_code = entry.symbolic
                    if reason in (None, "", "order rejected"):
                        final_reason = f"{entry.reason} ({entry.symbolic}, code={entry.code})"
            else:
                final_code = str(narrow)
        else:
            entry = find_symbolic(stripped)
            if entry and reason in (None, "", "order rejected"):
                final_reason = f"{entry.reason} ({entry.symbolic}, code={entry.code})"

    return OrderError(final_reason, error_code=final_code)
