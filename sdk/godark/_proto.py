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
    _STP_MODE_TO_PROTO,
    _TIME_IN_FORCE_TO_PROTO,
    Side,
)
from .types import (  # noqa: E402
    AccountMarginSummary,
    AccountMarginUpdate,
    BalanceUpdate,
    FundingRateUpdate,
    LeverageSetting,
    LeverageSettings,
    OpenOrderRow,
    OpenOrdersSnapshot,
    OrderUpdate,
    PlaceOrderOptions,
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


# Legacy NodeResponse oneof field numbers (pre hotpath-edge-frames REST replies).
_LEGACY_NODE_RESPONSE_FIELD_NUM: dict[str, int] = {
    "ack": 1,
    "fill": 2,
    "open_orders_snapshot": 3,
    "node_ready": 4,
    "mass_quote_ack": 5,
    "batch_cancel_ack": 6,
    "batch_modify_ack": 7,
    "positions_snapshot": 8,
    "account_margin_update": 9,
    "cancel_all_ack": 10,
    "close_all_ack": 11,
    "reverse_ack": 12,
}
_LEGACY_NODE_RESPONSE_FIELD_NAME: dict[int, str] = {
    v: k for k, v in _LEGACY_NODE_RESPONSE_FIELD_NUM.items()
}


def _read_varint(data: bytes, i: int) -> tuple[int, int]:
    shift = 0
    result = 0
    while i < len(data):
        b = data[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, i
        shift += 7
        if shift >= 64:
            raise ValueError("varint overflow")
    raise ValueError("truncated varint")


def _write_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def wrap_legacy_node_response(variant: str, inner: bytes) -> bytes:
    """Wrap ``inner`` as a legacy ``NodeResponse`` oneof (test / mock helper)."""
    field_num = _LEGACY_NODE_RESPONSE_FIELD_NUM[variant]
    tag = (field_num << 3) | 2
    return bytes([tag]) + _write_varint(len(inner)) + inner


def _unwrap_legacy_node_response(data: bytes) -> tuple[str, bytes] | None:
    """If ``data`` is a legacy ``NodeResponse`` wrapper, return ``(variant, inner)``."""
    if not data:
        return None
    tag = data[0]
    wire_type = tag & 0x07
    field_num = tag >> 3
    variant = _LEGACY_NODE_RESPONSE_FIELD_NAME.get(field_num)
    if variant is None or wire_type != 2:
        return None
    try:
        length, i = _read_varint(data, 1)
    except ValueError:
        return None
    end = i + length
    if end != len(data):
        return None
    return variant, data[i:end]


def _resolve_rest_payload(data: bytes, expected: str | None = None) -> tuple[str, bytes]:
    """Return ``(variant, payload_bytes)`` for REST HPKE plaintext."""
    unwrapped = _unwrap_legacy_node_response(data)
    if unwrapped is not None:
        return unwrapped
    if expected:
        return expected, data
    return "ack", data


def _parse_snapshot_variant(
    variant: str, payload: bytes, *, full_data: bytes | None = None
) -> tuple[str, Any]:
    if variant == "open_orders_snapshot":
        msg = sequencer_pb2.OpenOrdersSnapshot()
        msg.ParseFromString(payload)
        return "open_orders_snapshot", parse_open_orders_snapshot_proto(msg)
    if variant == "positions_snapshot":
        msg = sequencer_pb2.PositionsSnapshot()
        msg.ParseFromString(payload)
        return "positions_snapshot", parse_positions_snapshot_proto(msg)
    if variant in ("account_margin_update", "account_update"):
        msg = sequencer_pb2.AccountMarginUpdate()
        msg.ParseFromString(payload)
        return "account_margin_update", parse_account_margin_update_proto(msg)
    if variant == "ack":
        return "ack", parse_node_response(full_data if full_data is not None else payload)
    return variant or "unknown", {"type": variant or "unknown"}


def _decode_rest_variant(variant: str, payload: bytes, *, full_data: bytes) -> tuple[str, Any]:
    count_ack_configs: dict[str, tuple[type, str, str]] = {
        "cancel_all_ack": (sequencer_pb2.CancelAllAck, "cancelled", "cancelled_order_ids"),
        "close_all_ack": (sequencer_pb2.CloseAllAck, "closed", "close_order_ids"),
        "reverse_ack": (sequencer_pb2.ReverseAck, "reversed", "reverse_order_ids"),
    }
    if variant in count_ack_configs:
        cls, count_field, ids_field = count_ack_configs[variant]
        msg = cls()
        msg.ParseFromString(payload)
        return variant, _parse_count_ack_message(
            variant, msg, count_field=count_field, ids_field=ids_field
        )
    return _parse_snapshot_variant(variant, payload, full_data=full_data)


def _parse_node_response_with_expected(data: bytes, expected: str | None) -> tuple[str, Any]:
    variant, payload = _resolve_rest_payload(data, expected)
    try:
        return _decode_rest_variant(variant, payload, full_data=data)
    except Exception:
        if expected and expected != variant:
            return _decode_rest_variant(expected, data, full_data=data)
        raise


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
    options: PlaceOrderOptions | None = None,
    timestamp: int = 0,
) -> bytes:
    """Build a PlaceOrderInput wrapped in EdgeSequencerRequest, return serialized bytes."""
    del timestamp  # legacy param; PlaceOrderInput no longer carries timestamp
    opts = options or PlaceOrderOptions()
    if aon and min_fill_size is None:
        min_fill_size = quantity
    stp = opts.stp_mode.value if hasattr(opts.stp_mode, "value") else str(opts.stp_mode)
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
        stp_mode=_STP_MODE_TO_PROTO.get(stp, 0),
        reduce_only=opts.reduce_only,
        post_only=opts.post_only,
    )
    if price is not None:
        place.price = price
    if min_fill_size is not None:
        place.min_fill_size = min_fill_size
    if expiry_time is not None:
        place.expiry_time = expiry_time
    if correlation_id_bytes is not None:
        place.correlation_id = correlation_id_body_bytes(correlation_id_bytes)
    if opts.peg_offset_bps is not None:
        place.peg_offset_bps = opts.peg_offset_bps
    if opts.trigger_price is not None:
        place.trigger_price = opts.trigger_price
    if opts.take_profit_price is not None:
        place.take_profit_price = opts.take_profit_price
    if opts.stop_loss_price is not None:
        place.stop_loss_price = opts.stop_loss_price

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


def build_modify_order_proto(
    order_id: int,
    user_uuid: bytes,
    symbol_id: int,
    new_price: float | None = None,
    new_quantity: float | None = None,
    new_trigger_price: float | None = None,
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
    if new_trigger_price is not None:
        modify.new_trigger_price = new_trigger_price

    req = sequencer_pb2.EdgeSequencerRequest(modify=modify)
    return req.SerializeToString()


def build_get_open_orders_proto(user_uuid: bytes, correlation_id_bytes: bytes = b"") -> bytes:
    """Build GetOpenOrdersRequest wrapped in EdgeSequencerRequest."""
    inner = sequencer_pb2.GetOpenOrdersRequest(
        user_uuid=user_uuid,
        correlation_id=correlation_id_body_bytes(correlation_id_bytes),
    )
    return sequencer_pb2.EdgeSequencerRequest(get_open_orders=inner).SerializeToString()


def build_get_positions_proto(user_uuid: bytes, correlation_id_bytes: bytes = b"") -> bytes:
    """Build GetPositionsRequest wrapped in EdgeSequencerRequest."""
    inner = sequencer_pb2.GetPositionsRequest(
        user_uuid=user_uuid,
        correlation_id=correlation_id_body_bytes(correlation_id_bytes),
    )
    return sequencer_pb2.EdgeSequencerRequest(get_positions=inner).SerializeToString()


def build_get_account_proto(user_uuid: bytes, correlation_id_bytes: bytes = b"") -> bytes:
    """Build GetAccountRequest wrapped in EdgeSequencerRequest."""
    inner = sequencer_pb2.GetAccountRequest(
        user_uuid=user_uuid,
        correlation_id=correlation_id_body_bytes(correlation_id_bytes),
    )
    return sequencer_pb2.EdgeSequencerRequest(get_account=inner).SerializeToString()


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


def build_cancel_all_proto(
    symbol_id: int | None,
    user_uuid: bytes,
    correlation_id_bytes: bytes,
) -> bytes:
    """Build CancelAllInput wrapped in EdgeSequencerRequest."""
    cancel_all = sequencer_pb2.CancelAllInput(
        user_uuid=user_uuid,
        correlation_id=correlation_id_body_bytes(correlation_id_bytes),
    )
    if symbol_id is not None:
        cancel_all.symbol_id = symbol_id
    return sequencer_pb2.EdgeSequencerRequest(cancel_all=cancel_all).SerializeToString()


def build_close_all_proto(
    symbol_id: int | None,
    user_uuid: bytes,
    correlation_id_bytes: bytes,
) -> bytes:
    """Build CloseAllInput wrapped in EdgeSequencerRequest."""
    close_all = sequencer_pb2.CloseAllInput(
        user_uuid=user_uuid,
        correlation_id=correlation_id_body_bytes(correlation_id_bytes),
    )
    if symbol_id is not None:
        close_all.symbol_id = symbol_id
    return sequencer_pb2.EdgeSequencerRequest(close_all=close_all).SerializeToString()


def build_reverse_proto(
    symbol_id: int,
    user_uuid: bytes,
    correlation_id_bytes: bytes,
) -> bytes:
    """Build ReverseInput wrapped in EdgeSequencerRequest."""
    reverse = sequencer_pb2.ReverseInput(
        symbol_id=symbol_id,
        user_uuid=user_uuid,
        correlation_id=correlation_id_body_bytes(correlation_id_bytes),
    )
    return sequencer_pb2.EdgeSequencerRequest(reverse=reverse).SerializeToString()


def build_amend_tpsl_proto(
    user_uuid: bytes,
    order_id: int,
    correlation_id_bytes: bytes,
    *,
    take_profit_price: float | None = None,
    stop_loss_price: float | None = None,
    symbol_id: int | None = None,
    position_side: str | Side | None = None,
) -> bytes:
    """Build AmendTpslRequest wrapped in EdgeSequencerRequest."""
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
        side = position_side if isinstance(position_side, str) else position_side.value
        amend.position_side = _SIDE_TO_PROTO[side]
    return sequencer_pb2.EdgeSequencerRequest(amend_tpsl=amend).SerializeToString()


def build_cancel_tpsl_proto(
    user_uuid: bytes,
    order_id: int,
    correlation_id_bytes: bytes,
    *,
    symbol_id: int | None = None,
    position_side: str | Side | None = None,
) -> bytes:
    """Build CancelTpslRequest wrapped in EdgeSequencerRequest."""
    cancel = sequencer_pb2.CancelTpslRequest(
        user_uuid=user_uuid,
        order_id=order_id,
        correlation_id=correlation_id_body_bytes(correlation_id_bytes),
    )
    if symbol_id is not None:
        cancel.symbol_id = symbol_id
    if position_side is not None:
        side = position_side if isinstance(position_side, str) else position_side.value
        cancel.position_side = _SIDE_TO_PROTO[side]
    return sequencer_pb2.EdgeSequencerRequest(cancel_tpsl=cancel).SerializeToString()


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


def _parse_ack_message(msg: sequencer_pb2.AckMessage) -> dict[str, Any]:
    outcome = msg.ack_outcome
    if outcome and outcome.kind:
        success = outcome.kind == sequencer_pb2.ACK_OUTCOME_KIND_APPLIED
        error_code = None
        if outcome.HasField("business_error_code"):
            error_code = outcome.business_error_code
        elif outcome.HasField("system_error_code"):
            error_code = outcome.system_error_code
        if error_code is not None:
            success = False
        order_status = None
        if outcome.HasField("order_status"):
            order_status = _ORDER_STATUS_FROM_PROTO.get(outcome.order_status)
    else:
        success = False
        error_code = None
        order_status = None
    result: dict[str, Any] = {
        "type": "ack",
        "sequence": msg.sequence,
        "order_id": msg.order_id,
        "success": success,
        "correlation_id": msg.correlation_id,
    }
    if error_code is not None:
        result["error_code"] = error_code
    if msg.HasField("reject_text"):
        result["reject_text"] = msg.reject_text
    if order_status is not None:
        result["order_status"] = order_status
    return result


def parse_node_response(data: bytes) -> dict[str, Any]:
    """Decode REST/WS ack (or fill) plaintext into a plain dict."""
    variant, payload = _resolve_rest_payload(data, "ack")
    if variant == "ack":
        msg = sequencer_pb2.AckMessage()
        msg.ParseFromString(payload)
        return _parse_ack_message(msg)
    if variant == "fill":
        fill = sequencer_pb2.TradeMessage()
        fill.ParseFromString(payload)
        return {
            "type": "fill",
            "trade_id": fill.trade_id,
            "taker_order_id": fill.taker_order_id,
            "maker_order_id": fill.maker_order_id,
            "symbol_id": fill.symbol_id,
            "timestamp": fill.timestamp,
            "correlation_id": fill.correlation_id,
        }
    if variant == "tpsl_ack":
        return parse_tpsl_ack(data)
    return {"type": variant or "unknown"}


def parse_tpsl_ack(data: bytes) -> dict[str, Any]:
    """Decode a ``tpsl_ack`` plaintext body."""
    variant, payload = _resolve_rest_payload(data, "tpsl_ack")
    if variant != "tpsl_ack":
        return {"type": variant or "unknown"}
    msg = sequencer_pb2.TpslAck()
    msg.ParseFromString(payload)
    out: dict[str, Any] = {
        "type": "tpsl_ack",
        "parent_order_id": msg.parent_order_id,
        "correlation_id": msg.correlation_id,
    }
    if msg.HasField("take_profit"):
        out["take_profit"] = msg.take_profit
    if msg.HasField("stop_loss"):
        out["stop_loss"] = msg.stop_loss
    if msg.error_code:
        out["error_code"] = msg.error_code
    if msg.reject_text:
        out["reject_text"] = msg.reject_text
    return out


_MASS_QUOTE_LEG_STATUS_FROM_PROTO: dict[int, str] = {
    0: "unspecified",
    1: "open",
    2: "filled",
    3: "failed",
}


def parse_mass_quote_ack(data: bytes) -> dict[str, Any]:
    """Decode a MassQuoteAck (legacy NodeResponse wrapper or direct message)."""
    variant, payload = _resolve_rest_payload(data, "mass_quote_ack")
    if variant != "mass_quote_ack":
        return {"type": variant or "unknown"}

    a = sequencer_pb2.MassQuoteAck()
    a.ParseFromString(payload)
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
    """Decode a BatchCancelAck (legacy NodeResponse wrapper or direct message)."""
    variant, payload = _resolve_rest_payload(data, "batch_cancel_ack")
    if variant != "batch_cancel_ack":
        return {"type": variant or "unknown"}

    a = sequencer_pb2.BatchCancelAck()
    a.ParseFromString(payload)
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
    """Decode a BatchModifyAck (legacy NodeResponse wrapper or direct message)."""
    variant, payload = _resolve_rest_payload(data, "batch_modify_ack")
    if variant != "batch_modify_ack":
        return {"type": variant or "unknown"}

    a = sequencer_pb2.BatchModifyAck()
    a.ParseFromString(payload)
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


def _parse_count_ack_message(
    ack_type: str,
    msg: Any,
    *,
    count_field: str,
    ids_field: str,
) -> dict[str, Any]:
    order_ids = [str(x) for x in getattr(msg, ids_field)]
    return {
        "type": ack_type,
        "sequence": msg.sequence,
        "count": getattr(msg, count_field),
        "order_ids": order_ids,
        "error_code": msg.error_code if msg.HasField("error_code") else None,
        "reject_text": msg.reject_text if msg.HasField("reject_text") else None,
    }


def parse_cancel_all_ack(data: bytes) -> dict[str, Any]:
    """Decode a CancelAllAck (legacy NodeResponse wrapper or direct message)."""
    variant, payload = _resolve_rest_payload(data, "cancel_all_ack")
    if variant != "cancel_all_ack":
        return {"type": variant or "unknown"}
    msg = sequencer_pb2.CancelAllAck()
    msg.ParseFromString(payload)
    return _parse_count_ack_message(
        "cancel_all_ack", msg, count_field="cancelled", ids_field="cancelled_order_ids"
    )


def parse_close_all_ack(data: bytes) -> dict[str, Any]:
    """Decode a CloseAllAck (legacy NodeResponse wrapper or direct message)."""
    variant, payload = _resolve_rest_payload(data, "close_all_ack")
    if variant != "close_all_ack":
        return {"type": variant or "unknown"}
    msg = sequencer_pb2.CloseAllAck()
    msg.ParseFromString(payload)
    return _parse_count_ack_message(
        "close_all_ack", msg, count_field="closed", ids_field="close_order_ids"
    )


def parse_reverse_ack(data: bytes) -> dict[str, Any]:
    """Decode a ReverseAck (legacy NodeResponse wrapper or direct message)."""
    variant, payload = _resolve_rest_payload(data, "reverse_ack")
    if variant != "reverse_ack":
        return {"type": variant or "unknown"}
    msg = sequencer_pb2.ReverseAck()
    msg.ParseFromString(payload)
    return _parse_count_ack_message(
        "reverse_ack", msg, count_field="reversed", ids_field="reverse_order_ids"
    )


def parse_count_ack(data: bytes, expected: str) -> dict[str, Any]:
    """Decode a cancel_all_ack / close_all_ack / reverse_ack plaintext body."""
    variant, parsed = _parse_node_response_with_expected(data, expected)
    if variant == expected:
        return parsed
    if variant == "ack":
        return parsed
    reject = parse_node_response(data)
    if reject.get("type") == "ack" and not reject.get("success", True):
        return reject
    return {"type": variant or "unknown"}


def parse_leverage_settings_proto(msg: sequencer_pb2.LeverageSettings) -> LeverageSettings:
    """Decode LeverageSettings protobuf into a typed dataclass."""
    settings = tuple(
        LeverageSetting(symbol_id=row.symbol_id, leverage=row.leverage) for row in msg.settings
    )
    user_uuid = _identity.bytes_to_uuid(msg.user_uuid) if msg.user_uuid else ""
    server_timestamp = int(msg.server_timestamp or 0)
    return LeverageSettings(
        settings=settings,
        user_uuid=user_uuid,
        server_timestamp=server_timestamp,
    )


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


def parse_open_orders_snapshot_proto(msg: sequencer_pb2.OpenOrdersSnapshot) -> OpenOrdersSnapshot:
    corr = _correlation_id_to_int(msg.correlation_id) if msg.correlation_id else 0
    rows = tuple(
        OpenOrderRow(
            order_id=str(r.order_id),
            symbol_id=int(r.symbol_id),
            leverage=int(r.leverage),
            price=str(r.price) if r.price else "",
            quantity=str(r.quantity) if r.quantity else "",
            remaining_qty=str(r.remaining_qty) if r.remaining_qty else "",
        )
        for r in msg.rows
    )
    return OpenOrdersSnapshot(
        rows=rows,
        server_timestamp=int(msg.server_timestamp),
        correlation_id=corr,
    )


def parse_open_orders_snapshot(data: bytes) -> OpenOrdersSnapshot:
    """Decode ``open_orders_snapshot`` wire (legacy NodeResponse or direct message).

    Used by the WS client: field 3 on ``NodeResponse`` collides with
    ``SequencerToEdgeMessage.funding_rate_update``, so this path must not go
    through :func:`parse_sequencer_to_edge_message`.
    """
    variant, payload = _resolve_rest_payload(data, "open_orders_snapshot")
    expected = "open_orders_snapshot"
    err: Exception | None = None
    if variant == expected:
        try:
            msg = sequencer_pb2.OpenOrdersSnapshot()
            msg.ParseFromString(payload)
            return parse_open_orders_snapshot_proto(msg)
        except Exception as exc:
            err = exc
    if variant != expected:
        try:
            msg = sequencer_pb2.OpenOrdersSnapshot()
            msg.ParseFromString(data)
            return parse_open_orders_snapshot_proto(msg)
        except Exception:
            pass
    if err is not None:
        raise err
    raise ValueError(f"payload is not an open_orders_snapshot (got {variant!r})")


def parse_account_margin_update_proto(
    msg: sequencer_pb2.AccountMarginUpdate,
) -> AccountMarginUpdate:
    account = None
    if msg.HasField("account"):
        a = msg.account
        account = AccountMarginSummary(
            total_collateral=str(a.total_collateral),
            position_margin=str(a.position_margin),
            reserved_order_margin=str(a.reserved_order_margin),
            free_collateral=str(a.free_collateral),
        )
    return AccountMarginUpdate(
        user_uuid=_uuid_bytes_to_str(msg.user_uuid),
        server_timestamp=int(msg.server_timestamp),
        account=account,
    )


def parse_node_response_snapshot(data: bytes, message_type: str | None = None) -> tuple[str, Any]:
    """Decode REST snapshot plaintext into ``(variant, parsed)``."""
    expected = message_type.replace("-", "_") if message_type else None
    if expected in ("account_margin", "account_update"):
        expected = "account_margin_update"
    return _parse_node_response_with_expected(data, expected)


def parse_positions_snapshot_proto(msg: sequencer_pb2.PositionsSnapshot) -> PositionsSnapshot:
    corr: int | None = None
    if msg.correlation_id:
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


def parse_funding_rate_update_proto(
    msg: sequencer_pb2.FundingRateUpdateMessage,
) -> FundingRateUpdate:
    return FundingRateUpdate(
        symbol_id=int(msg.symbol_id),
        funding_rate=msg.funding_rate,
        timestamp=int(msg.timestamp),
        last_funding_rate=msg.last_funding_rate,
    )


SequencerPush: TypeAlias = (
    OrderUpdate
    | PositionUpdate
    | PositionsSnapshot
    | SystemHealthUpdate
    | BalanceUpdate
    | FundingRateUpdate
    | LeverageSettings
    | UnknownSequencerPush
)


def parse_funding_rate_snapshot_json(msg: dict) -> list[FundingRateUpdate]:
    """Parse a public ``funding_rate_snapshot`` JSON push into updates."""
    if not isinstance(msg, dict) or msg.get("type") != "funding_rate_snapshot":
        return []
    rows = msg.get("rows")
    if not isinstance(rows, list):
        return []
    out: list[FundingRateUpdate] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        funding_rate = str(row.get("funding_rate") or "")
        if not funding_rate:
            continue
        out.append(
            FundingRateUpdate(
                symbol_id=int(row.get("symbol_id") or 0),
                funding_rate=funding_rate,
                timestamp=int(row.get("timestamp") or 0),
                last_funding_rate=str(row.get("last_funding_rate") or ""),
            )
        )
    return out


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
    if which == "leverage_settings":
        return parse_leverage_settings_proto(msg.leverage_settings)
    return UnknownSequencerPush(oneof_field=which)
