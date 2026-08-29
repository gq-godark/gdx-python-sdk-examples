"""Bridge between clean Python types and wire protobuf messages."""

from __future__ import annotations

import os
import sys
import uuid
from typing import Any, TypeAlias

_GENERATED_DIR = os.path.join(os.path.dirname(__file__), "_generated")
if _GENERATED_DIR not in sys.path:
    sys.path.insert(0, _GENERATED_DIR)

from gdx.edge.v1 import edge_pb2  # noqa: E402
from gdx.health.v1 import health_pb2  # noqa: E402
from gdx.sequencer.v1 import sequencer_pb2  # noqa: E402

from . import _identity  # noqa: E402
from .enums import (  # noqa: E402
    _CANCEL_REASON_FROM_PROTO,
    _ORDER_STATUS_FROM_PROTO,
    _ORDER_TYPE_TO_PROTO,
    _ORDER_UPDATE_TYPE_FROM_PROTO,
    _REQUEST_TYPE_TO_PROTO,
    _RESPONSE_MESSAGE_TYPE_TO_PROTO,
    _SIDE_FROM_PROTO,
    _SIDE_TO_PROTO,
    _TIME_IN_FORCE_TO_PROTO,
)
from .types import (  # noqa: E402
    BalanceUpdate,
    FundingRateUpdate,
    OpenOrderRow,
    OpenOrdersSnapshot,
    OrderUpdate,
    PositionRow,
    PositionsSnapshot,
    PositionsSnapshotSource,
    PositionUpdate,
    SystemHealthUpdate,
    UnknownSequencerPush,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _correlation_id_to_int(raw: bytes) -> int:
    """Convert little-endian sequencer correlation bytes to an integer."""
    if not raw:
        return 0
    return int.from_bytes(raw, "little")


def correlation_id_body_bytes(canonical: bytes | None) -> bytes:
    """Convert canonical big-endian u128 bytes to sequencer-body little-endian."""
    return bytes(reversed(canonical)) if canonical else b""


def response_correlation_id_bytes(value: Any) -> bytes:
    """Parse an edge JSON correlation id into canonical 16-byte big-endian bytes."""
    if value is None or value == 0 or value == "":
        return b""
    try:
        if isinstance(value, int):
            parsed = value
        else:
            raw = str(value).strip()
            if "-" in raw:
                return uuid.UUID(raw).bytes
            parsed = int(raw, 10 if raw.isdigit() else 16)
        if parsed <= 0 or parsed.bit_length() > 128:
            return b""
        return parsed.to_bytes(16, "big")
    except (ValueError, TypeError, OverflowError):
        return b""


def _uuid_bytes_to_str(raw: bytes) -> str:
    """Convert 16 raw UUID bytes to a standard hyphenated UUID string."""
    if len(raw) == _identity.USER_UUID_LEN:
        return _identity.bytes_to_uuid(raw)
    return "00000000-0000-0000-0000-000000000000"


# Maximum legs / ids accepted in a single mass-quote, batch-cancel, or
# batch-modify request. The node fans batches out at ~constant MPC cost only up
# to this bound; larger batches are rejected client-side before hitting the wire.
_MAX_BATCH_LEGS = 20


# ---------------------------------------------------------------------------
# Builders – Python values → serialized protobuf bytes
# ---------------------------------------------------------------------------


def build_place_order_proto(
    symbol_id: int,
    side: str,
    order_type: str,
    quantity: float,
    user_uuid: bytes,
    price: float | None = None,
    time_in_force: str = "GTC",
    aon: bool = False,
    min_fill_size: float | None = None,
    expiry_time: int | None = None,
    correlation_id_bytes: bytes | None = None,
    timestamp: int = 0,
) -> bytes:
    """Build a PlaceOrderInput wrapped in EdgeSequencerRequest, return serialized bytes."""
    del timestamp  # legacy param; PlaceOrderInput no longer carries timestamp
    if aon and min_fill_size is None:
        min_fill_size = quantity
    place = sequencer_pb2.PlaceOrderInput(
        symbol_id=symbol_id,
        side=_SIDE_TO_PROTO[side if isinstance(side, str) else side.value],
        order_type=_ORDER_TYPE_TO_PROTO[
            order_type if isinstance(order_type, str) else order_type.value
        ],
        quantity=quantity,
        time_in_force=_TIME_IN_FORCE_TO_PROTO[
            time_in_force if isinstance(time_in_force, str) else time_in_force.value
        ],
        user_uuid=user_uuid,
    )
    if price is not None:
        place.price = price
    if min_fill_size is not None:
        place.min_fill_size = min_fill_size
    if expiry_time is not None:
        place.expiry_time = expiry_time
    if correlation_id_bytes is not None:
        place.correlation_id = correlation_id_body_bytes(correlation_id_bytes)

    req = sequencer_pb2.EdgeSequencerRequest(place=place)
    return req.SerializeToString()


def build_cancel_order_proto(
    order_id: int,
    user_uuid: bytes,
    symbol_id: int,
    correlation_id_bytes: bytes,
) -> bytes:
    """Build a CancelOrderInput wrapped in EdgeSequencerRequest, return serialized bytes."""
    cancel = sequencer_pb2.CancelOrderInput(
        order_id=order_id,
        symbol_id=symbol_id,
        correlation_id=correlation_id_body_bytes(correlation_id_bytes),
        user_uuid=user_uuid,
    )
    req = sequencer_pb2.EdgeSequencerRequest(cancel=cancel)
    return req.SerializeToString()


def build_amend_tpsl_proto(
    user_uuid: bytes,
    order_id: int,
    correlation_id_bytes: bytes,
    *,
    take_profit_price: float | None = None,
    stop_loss_price: float | None = None,
    symbol_id: int | None = None,
    position_side: str | None = None,
) -> bytes:
    """Build an AmendTpslRequest wrapped in EdgeSequencerRequest."""
    amend = sequencer_pb2.AmendTpslRequest(
        user_uuid=user_uuid,
        order_id=order_id,
        correlation_id=correlation_id_body_bytes(correlation_id_bytes),
    )
    if take_profit_price is not None:
        amend.take_profit_price = take_profit_price
    if stop_loss_price is not None:
        amend.stop_loss_price = stop_loss_price
    if symbol_id is not None:
        amend.symbol_id = symbol_id
    if position_side is not None:
        amend.position_side = _SIDE_TO_PROTO[
            position_side if isinstance(position_side, str) else position_side.value
        ]
    req = sequencer_pb2.EdgeSequencerRequest(amend_tpsl=amend)
    return req.SerializeToString()


def build_modify_order_proto(
    order_id: int,
    user_uuid: bytes,
    symbol_id: int,
    new_price: float | None = None,
    new_quantity: float | None = None,
    correlation_id_bytes: bytes = b"",
) -> bytes:
    """Build a ModifyOrderInput wrapped in EdgeSequencerRequest, return serialized bytes."""
    modify = sequencer_pb2.ModifyOrderInput(
        order_id=order_id,
        symbol_id=symbol_id,
        correlation_id=correlation_id_body_bytes(correlation_id_bytes),
        user_uuid=user_uuid,
    )
    if new_price is not None:
        modify.new_price = new_price
    if new_quantity is not None:
        modify.new_quantity = new_quantity

    req = sequencer_pb2.EdgeSequencerRequest(modify=modify)
    return req.SerializeToString()


def build_update_leverage_proto(
    user_uuid: bytes,
    symbol_id: int,
    leverage: int,
    correlation_id_bytes: bytes = b"",
) -> bytes:
    """Build an UpdateLeverageRequest wrapped in EdgeSequencerRequest, return serialized bytes."""
    lev = max(1, int(leverage))
    update = sequencer_pb2.UpdateLeverageRequest(
        user_uuid=user_uuid,
        symbol_id=symbol_id,
        leverage=lev,
        correlation_id=correlation_id_body_bytes(correlation_id_bytes),
    )
    req = sequencer_pb2.EdgeSequencerRequest(update_leverage=update)
    return req.SerializeToString()


def build_mass_quote_proto(
    symbol_id: int,
    user_uuid: bytes,
    legs: list[dict[str, Any]],
    correlation_id_bytes: bytes | None = None,
    leverage: int = 1,
    post_only: bool | None = None,
) -> bytes:
    """Build a MassQuoteInput wrapped in EdgeSequencerRequest, return serialized bytes.

    Each leg dict supports: ``side`` (str/Side), ``price`` (float), ``quantity``
    (float), ``cancel_order_id`` (int|None, 0/None = pure place), ``time_in_force``
    (str, default GTC), ``expiry_time`` (int|None), ``correlation_id`` (bytes|None).

    ``post_only`` is the batch-level flag: ``None`` encodes post-only (``True``);
    ``False`` enables the relaxed path where a crossing leg takes liquidity up
    to its limit and rests the remainder.

    Raises ``ValueError`` if ``legs`` is empty or has more than 20 entries.
    """
    del leverage  # legacy param; MassQuoteInput no longer carries leverage
    if not legs:
        raise ValueError("mass quote requires at least one leg")
    if len(legs) > _MAX_BATCH_LEGS:
        raise ValueError(f"mass quote accepts at most {_MAX_BATCH_LEGS} legs, got {len(legs)}")
    mq = sequencer_pb2.MassQuoteInput(
        symbol_id=symbol_id,
        user_uuid=user_uuid,
    )
    if correlation_id_bytes is not None:
        mq.correlation_id = correlation_id_body_bytes(correlation_id_bytes)
    mq.post_only = True if post_only is None else post_only

    for leg in legs:
        side = leg["side"]
        tif = leg.get("time_in_force", "GTC")
        pb_leg = mq.legs.add()
        pb_leg.side = _SIDE_TO_PROTO[side if isinstance(side, str) else side.value]
        pb_leg.price = float(leg["price"])
        pb_leg.quantity = float(leg["quantity"])
        pb_leg.time_in_force = _TIME_IN_FORCE_TO_PROTO[tif if isinstance(tif, str) else tif.value]
        cancel_id = leg.get("cancel_order_id")
        # cancel_order_id is plain uint64; 0 means "pure place" (no cancel target).
        pb_leg.cancel_order_id = int(cancel_id) if cancel_id else 0
        if leg.get("expiry_time") is not None:
            pb_leg.expiry_time = int(leg["expiry_time"])
        # Each leg becomes its own order, so it carries a unique 16-byte
        # correlation_id (the wire requires exactly 16 bytes per leg). Callers
        # may supply one explicitly; otherwise generate a fresh one.
        leg_cid = leg.get("correlation_id")
        pb_leg.correlation_id = leg_cid if leg_cid else uuid.uuid4().bytes

    req = sequencer_pb2.EdgeSequencerRequest(mass_quote=mq)
    return req.SerializeToString()


def build_batch_cancel_proto(
    symbol_id: int,
    user_uuid: bytes,
    order_ids: list[int],
    correlation_id_bytes: bytes | None = None,
) -> bytes:
    """Build a BatchCancelInput wrapped in EdgeSequencerRequest, return serialized bytes.

    ``order_ids`` is a list of resting order ids (plain uint64) to cancel in one
    fanned-out batch. Up to 20 ids per request, single symbol. Cancels are pure
    index removals (no MPC comparison), so the whole batch costs zero online rounds.

    Raises ``ValueError`` if ``order_ids`` is empty or has more than 20 entries.
    """
    if not order_ids:
        raise ValueError("batch cancel requires at least one order id")
    if len(order_ids) > _MAX_BATCH_LEGS:
        raise ValueError(
            f"batch cancel accepts at most {_MAX_BATCH_LEGS} ids, got {len(order_ids)}"
        )
    bc = sequencer_pb2.BatchCancelInput(
        symbol_id=symbol_id,
        user_uuid=user_uuid,
        order_ids=[int(oid) for oid in order_ids],
    )
    if correlation_id_bytes is not None:
        bc.correlation_id = correlation_id_body_bytes(correlation_id_bytes)

    req = sequencer_pb2.EdgeSequencerRequest(batch_cancel=bc)
    return req.SerializeToString()


def build_batch_modify_proto(
    symbol_id: int,
    user_uuid: bytes,
    legs: list[dict[str, Any]],
    correlation_id_bytes: bytes | None = None,
) -> bytes:
    """Build a BatchModifyInput wrapped in EdgeSequencerRequest, return serialized bytes.

    Each leg dict supports: ``order_id`` (int, the resting order to amend),
    ``new_price`` (float|None) and ``new_quantity`` (float|None) — at least one
    must be set — and an optional ``correlation_id`` (bytes). Up to 20 legs per
    request, single symbol. Amends are post-only: a leg whose amended order would
    cross is rejected rather than taking liquidity, keeping the batch ~constant
    online MPC rounds.

    Raises ``ValueError`` if ``legs`` is empty, has more than 20 entries, or
    contains a leg with neither ``new_price`` nor ``new_quantity`` set (a no-op
    amend that the node would reject).
    """
    if not legs:
        raise ValueError("batch modify requires at least one leg")
    if len(legs) > _MAX_BATCH_LEGS:
        raise ValueError(f"batch modify accepts at most {_MAX_BATCH_LEGS} legs, got {len(legs)}")
    for i, leg in enumerate(legs):
        if leg.get("new_price") is None and leg.get("new_quantity") is None:
            raise ValueError(f"batch modify leg {i} must set new_price and/or new_quantity")
    bm = sequencer_pb2.BatchModifyInput(
        symbol_id=symbol_id,
        user_uuid=user_uuid,
    )
    if correlation_id_bytes is not None:
        bm.correlation_id = correlation_id_body_bytes(correlation_id_bytes)

    for leg in legs:
        pb_leg = bm.legs.add()
        pb_leg.order_id = int(leg["order_id"])
        if leg.get("new_price") is not None:
            pb_leg.new_price = float(leg["new_price"])
        if leg.get("new_quantity") is not None:
            pb_leg.new_quantity = float(leg["new_quantity"])
        # Each leg carries a unique 16-byte correlation_id (wire requires exactly
        # 16 bytes per leg). Callers may supply one; otherwise generate a fresh one.
        leg_cid = leg.get("correlation_id")
        pb_leg.correlation_id = leg_cid if leg_cid else uuid.uuid4().bytes

    req = sequencer_pb2.EdgeSequencerRequest(batch_modify=bm)
    return req.SerializeToString()


def build_order_header_aad(
    user_uuid: bytes,
    symbol_id: int,
    request_type_str: str,
    nonce: int,
    body_length: int,
    correlation_id: bytes = b"",
    conn_id: int = 0,
) -> bytes:
    """Create an OrderHeader proto and serialize it (used as AES-GCM AAD)."""
    header = edge_pb2.OrderHeader(
        user_uuid=user_uuid,
        symbol_id=symbol_id,
        request_type=_REQUEST_TYPE_TO_PROTO[request_type_str],
        nonce=nonce,
        body_length=body_length,
        correlation_id=correlation_id,
        conn_id=conn_id,
    )
    return header.SerializeToString()


def build_response_header_aad(
    user_uuid: bytes,
    message_type_str: str,
    body_length: int,
    nonce: int,
    fencing_epoch: int = 0,
    correlation_id: bytes = b"",
    session_seq: int = 0,
    conn_id: int = 0,
) -> bytes:
    """Create a ResponseHeader proto and serialize it (used as AES-GCM AAD)."""
    header = edge_pb2.ResponseHeader(
        user_uuid=user_uuid,
        message_type=_RESPONSE_MESSAGE_TYPE_TO_PROTO[message_type_str],
        body_length=body_length,
        nonce=nonce,
        fencing_epoch=fencing_epoch,
        correlation_id=correlation_id,
        session_seq=session_seq,
        conn_id=conn_id,
    )
    return header.SerializeToString()


# ---------------------------------------------------------------------------
# Parsers – serialized protobuf bytes → Python types
# ---------------------------------------------------------------------------


def parse_node_response(data: bytes) -> dict[str, Any]:
    """Decode a NodeResponse protobuf and return a dict with ack/fill/signing fields."""
    resp = sequencer_pb2.NodeResponse()
    resp.ParseFromString(data)

    which = resp.WhichOneof("inner")
    if which == "ack":
        ack = resp.ack
        outcome = ack.ack_outcome
        if outcome and outcome.kind:
            success = outcome.kind == sequencer_pb2.ACK_OUTCOME_KIND_APPLIED
            error_code = None
            if outcome.HasField("business_error_code"):
                error_code = outcome.business_error_code
            elif outcome.HasField("system_error_code"):
                error_code = outcome.system_error_code
            order_status = None
            if outcome.HasField("order_status"):
                order_status = _ORDER_STATUS_FROM_PROTO.get(outcome.order_status)
        else:
            success = False
            error_code = None
            order_status = None
        result: dict[str, Any] = {
            "type": "ack",
            "sequence": ack.sequence,
            "order_id": ack.order_id,
            "success": success,
            "correlation_id": ack.correlation_id,
        }
        if error_code is not None:
            result["error_code"] = error_code
        if ack.HasField("reject_text"):
            result["reject_text"] = ack.reject_text
        if order_status is not None:
            result["order_status"] = order_status
        return result
    elif which == "fill":
        fill = resp.fill
        return {
            "type": "fill",
            "trade_id": fill.trade_id,
            "taker_order_id": fill.taker_order_id,
            "maker_order_id": fill.maker_order_id,
            "symbol_id": fill.symbol_id,
            "timestamp": fill.timestamp,
            "correlation_id": fill.correlation_id,
        }
    elif which == "tpsl_ack":
        t = resp.tpsl_ack
        result: dict[str, Any] = {
            "type": "tpsl_ack",
            "correlation_id": t.correlation_id,
            "parent_order_id": t.parent_order_id,
        }
        if t.HasField("take_profit"):
            result["take_profit"] = t.take_profit
        if t.HasField("stop_loss"):
            result["stop_loss"] = t.stop_loss
        if t.HasField("error_code"):
            result["error_code"] = t.error_code
        if t.HasField("reject_text"):
            result["reject_text"] = t.reject_text
        return result
    elif which == "signing":
        return {"type": "signing"}
    else:
        return {"type": "unknown"}


_MASS_QUOTE_LEG_STATUS_FROM_PROTO: dict[int, str] = {
    0: "unspecified",
    1: "open",
    2: "filled",
    3: "failed",
}


def parse_mass_quote_ack(data: bytes) -> dict[str, Any]:
    """Decode a NodeResponse carrying a MassQuoteAck into a plain dict.

    Returns ``{"type": "mass_quote_ack", "sequence", "correlation_id",
    "results": [{"leg_index", "cancelled_order_id", "new_order_id", "status",
    "error_code", "fill_count"}]}``. ``cancelled_order_id`` / ``new_order_id``
    are ``None`` when zero (no cancel target / replacement failed).
    ``fill_count`` is the number of taker fills the leg produced in relaxed
    (post_only=False) mode; 0 for a pure rest or a post-only leg.
    """
    resp = sequencer_pb2.NodeResponse()
    resp.ParseFromString(data)
    which = resp.WhichOneof("inner")
    if which != "mass_quote_ack":
        return {"type": which or "unknown"}

    a = resp.mass_quote_ack
    results: list[dict[str, Any]] = []
    for r in a.results:
        results.append(
            {
                "leg_index": r.leg_index,
                "cancelled_order_id": r.cancelled_order_id or None,
                "new_order_id": r.new_order_id or None,
                "status": _MASS_QUOTE_LEG_STATUS_FROM_PROTO.get(r.status, "unknown"),
                "error_code": r.error_code if r.HasField("error_code") else None,
                "fill_count": r.fill_count,
            }
        )
    return {
        "type": "mass_quote_ack",
        "node_id": a.node_id,
        "sequence": a.sequence,
        "correlation_id": a.correlation_id,
        "results": results,
    }


def parse_batch_cancel_ack(data: bytes) -> dict[str, Any]:
    """Decode a NodeResponse carrying a BatchCancelAck into a plain dict.

    Returns ``{"type": "batch_cancel_ack", "node_id", "sequence", "correlation_id",
    "results": [{"order_id", "cancelled", "error_code"}]}``. ``error_code`` is
    ``None`` for a successful cancel and set (e.g. 2003 ORDER_NOT_FOUND) otherwise.
    """
    resp = sequencer_pb2.NodeResponse()
    resp.ParseFromString(data)
    which = resp.WhichOneof("inner")
    if which != "batch_cancel_ack":
        return {"type": which or "unknown"}

    a = resp.batch_cancel_ack
    results: list[dict[str, Any]] = []
    for r in a.results:
        results.append(
            {
                "order_id": r.order_id,
                "cancelled": r.cancelled,
                "error_code": r.error_code if r.HasField("error_code") else None,
            }
        )
    return {
        "type": "batch_cancel_ack",
        "node_id": a.node_id,
        "sequence": a.sequence,
        "correlation_id": a.correlation_id,
        "results": results,
    }


def parse_batch_modify_ack(data: bytes) -> dict[str, Any]:
    """Decode a NodeResponse carrying a BatchModifyAck into a plain dict.

    Returns ``{"type": "batch_modify_ack", "node_id", "sequence", "correlation_id",
    "results": [{"order_id", "modified", "error_code"}]}``. ``error_code`` is
    ``None`` for a successful amend and set otherwise (2003 not-found, 2018 crossed).
    """
    resp = sequencer_pb2.NodeResponse()
    resp.ParseFromString(data)
    which = resp.WhichOneof("inner")
    if which != "batch_modify_ack":
        return {"type": which or "unknown"}

    a = resp.batch_modify_ack
    results: list[dict[str, Any]] = []
    for r in a.results:
        results.append(
            {
                "order_id": r.order_id,
                "modified": r.modified,
                "error_code": r.error_code if r.HasField("error_code") else None,
            }
        )
    return {
        "type": "batch_modify_ack",
        "node_id": a.node_id,
        "sequence": a.sequence,
        "correlation_id": a.correlation_id,
        "results": results,
    }


def parse_order_update_proto(data: bytes) -> OrderUpdate:
    """Decode an OrderUpdateMessage protobuf into an OrderUpdate dataclass."""
    msg = sequencer_pb2.OrderUpdateMessage()
    msg.ParseFromString(data)

    cancel_reason = None
    if msg.HasField("cancel_reason"):
        cancel_reason = _CANCEL_REASON_FROM_PROTO.get(msg.cancel_reason)

    reject_reason = None
    if msg.HasField("reject_reason_code"):
        reject_reason = str(msg.reject_reason_code)

    realized_pnl: str | None = None
    if msg.HasField("realized_pnl"):
        realized_pnl = str(msg.realized_pnl)

    from .enums import OrderStatus, OrderUpdateType, Side

    return OrderUpdate(
        order_id=str(msg.order_id),
        user_uuid=_uuid_bytes_to_str(msg.user_uuid),
        symbol_id=int(msg.symbol_id),
        side=_SIDE_FROM_PROTO.get(msg.side, Side.BUY),
        status=_ORDER_STATUS_FROM_PROTO.get(msg.order_status, OrderStatus.NEW),
        update_type=_ORDER_UPDATE_TYPE_FROM_PROTO.get(msg.message_type, OrderUpdateType.OPEN),
        price=msg.price,
        quantity=msg.quantity,
        filled_qty=msg.filled_qty,
        remaining_qty=msg.remaining_qty,
        cum_fill=msg.cum_fill,
        cancel_reason=cancel_reason,
        reject_reason=reject_reason,
        msg=msg.msg if msg.HasField("msg") else None,
        reduce_only=msg.reduce_only,
        post_only=msg.post_only,
        correlation_id=_correlation_id_to_int(msg.correlation_id),
        timestamp=int(msg.timestamp),
        leverage=int(msg.leverage),
        realized_pnl=realized_pnl,
    )


def _parse_positions_snapshot_source(value: int) -> PositionsSnapshotSource:
    if value == 1:
        return PositionsSnapshotSource.INITIAL
    if value == 2:
        return PositionsSnapshotSource.PERIODIC
    if value == 3:
        return PositionsSnapshotSource.EVENT
    return PositionsSnapshotSource.UNSPECIFIED


def parse_position_row_proto(row: sequencer_pb2.PositionRow) -> PositionRow:
    from .enums import Side

    mark_price = str(row.mark_price) if row.HasField("mark_price") else None
    unrealized = str(row.unrealized_pnl) if row.HasField("unrealized_pnl") else None
    notional = str(row.notional) if row.HasField("notional") else None
    mpts = int(row.mark_publish_time_sec) if row.HasField("mark_publish_time_sec") else None
    return PositionRow(
        symbol_id=int(row.symbol_id),
        side=_SIDE_FROM_PROTO.get(row.side, Side.BUY),
        size=row.size,
        entry_price=row.entry_price,
        leverage=int(row.leverage),
        mark_price=mark_price,
        unrealized_pnl=unrealized,
        notional=notional,
        mark_publish_time_sec=mpts,
    )


def parse_positions_snapshot_proto(msg: sequencer_pb2.PositionsSnapshot) -> PositionsSnapshot:
    corr: int | None = None
    if msg.HasField("correlation_id"):
        corr = _correlation_id_to_int(msg.correlation_id)

    rows = tuple(parse_position_row_proto(r) for r in msg.rows)
    return PositionsSnapshot(
        user_uuid=_uuid_bytes_to_str(msg.user_uuid),
        rows=rows,
        server_timestamp=int(msg.server_timestamp),
        source=_parse_positions_snapshot_source(int(msg.source)),
        correlation_id=corr,
    )


def parse_system_health_proto(msg: health_pb2.HealthReport) -> SystemHealthUpdate:
    """Map the current health package report to the legacy aggregate SDK update."""
    state = int(msg.state)
    return SystemHealthUpdate(
        total_nodes=1,
        accepting_orders=bool(msg.serving),
        ready=int(state == health_pb2.HEALTH_STATE_READY),
        degraded=int(state == health_pb2.HEALTH_STATE_DEGRADED),
        exhausted=0,
        warming=int(state == health_pb2.HEALTH_STATE_WARMING),
        draining=int(state == health_pb2.HEALTH_STATE_DRAINING),
        waiting=int(state == health_pb2.HEALTH_STATE_WAITING),
    )


def parse_balance_update_proto(msg: sequencer_pb2.BalanceUpdateMessage) -> BalanceUpdate:
    return BalanceUpdate(
        user_uuid=_uuid_bytes_to_str(msg.user_uuid),
        balance_raw=int(msg.balance_raw),
        timestamp=int(msg.timestamp),
        balance=msg.balance,
        signed_balance_8dp=int(msg.signed_balance_8dp),
        free_collateral_8dp=int(msg.free_collateral_8dp),
    )


def parse_open_orders_snapshot(data: bytes) -> OpenOrdersSnapshot:
    """Decode a ``NodeResponse`` carrying ``OpenOrdersSnapshot`` (not ``SequencerToEdgeMessage``)."""
    resp = sequencer_pb2.NodeResponse()
    resp.ParseFromString(data)
    which = resp.WhichOneof("inner")
    if which != "open_orders_snapshot":
        raise ValueError(f"NodeResponse is not open_orders_snapshot (got {which!r})")
    snap = resp.open_orders_snapshot
    rows: list[OpenOrderRow] = []
    for row in snap.rows:
        rows.append(
            OpenOrderRow(
                order_id=str(row.order_id),
                symbol_id=int(row.symbol_id),
                leverage=int(row.leverage),
                price=row.price,
                quantity=row.quantity,
                remaining_qty=row.remaining_qty,
            )
        )
    corr = 0
    if snap.correlation_id:
        corr = _correlation_id_to_int(snap.correlation_id)
    return OpenOrdersSnapshot(
        rows=tuple(rows),
        server_timestamp=int(snap.server_timestamp),
        correlation_id=corr,
    )


def parse_funding_rate_update_proto(
    msg: sequencer_pb2.FundingRateUpdateMessage,
) -> FundingRateUpdate:
    return FundingRateUpdate(
        symbol_id=int(msg.symbol_id),
        funding_rate=msg.funding_rate,
        timestamp=int(msg.timestamp),
        last_funding_rate=msg.last_funding_rate,
    )


def parse_funding_rate_snapshot_json(msg: dict[str, Any]) -> list[FundingRateUpdate]:
    """Decode edge JSON ``funding_rate_snapshot`` (public WS channel) into updates."""
    if msg.get("type") != "funding_rate_snapshot":
        return []
    rows = msg.get("rows")
    if not isinstance(rows, list):
        return []
    out: list[FundingRateUpdate] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            symbol_id = int(row.get("symbol_id") or 0)
        except (TypeError, ValueError):
            continue
        funding_rate = str(row.get("funding_rate") or "")
        last_funding_rate = str(row.get("last_funding_rate") or "")
        try:
            timestamp = int(row.get("timestamp") or 0)
        except (TypeError, ValueError):
            timestamp = 0
        out.append(
            FundingRateUpdate(
                symbol_id=symbol_id,
                funding_rate=funding_rate,
                timestamp=timestamp,
                last_funding_rate=last_funding_rate,
            )
        )
    return out


SequencerPush: TypeAlias = (
    OrderUpdate
    | PositionUpdate
    | PositionsSnapshot
    | SystemHealthUpdate
    | BalanceUpdate
    | FundingRateUpdate
    | UnknownSequencerPush
)


def parse_sequencer_to_edge_message(data: bytes) -> SequencerPush:
    """Decode a SequencerToEdgeMessage and dispatch to the appropriate parsed type."""
    msg = sequencer_pb2.SequencerToEdgeMessage()
    msg.ParseFromString(data)

    which = msg.WhichOneof("inner")
    if which == "order_update":
        return parse_order_update_proto(msg.order_update.SerializeToString())
    if which == "positions_snapshot":
        return parse_positions_snapshot_proto(msg.positions_snapshot)
    if which == "health_report":
        return parse_system_health_proto(msg.health_report)
    if which == "funding_rate_update":
        return parse_funding_rate_update_proto(msg.funding_rate_update)
    if which == "balance_update":
        return parse_balance_update_proto(msg.balance_update)
    return UnknownSequencerPush(oneof_field=which)
