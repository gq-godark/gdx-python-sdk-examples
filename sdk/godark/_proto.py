"""Bridge between clean Python types and wire protobuf messages."""

from __future__ import annotations

import os
import sys
from typing import Any, TypeAlias

_GENERATED_DIR = os.path.join(os.path.dirname(__file__), "_generated")
if _GENERATED_DIR not in sys.path:
    sys.path.insert(0, _GENERATED_DIR)

from gdx.edge.v1 import edge_pb2  # noqa: E402
from gdx.sequencer.v1 import sequencer_pb2  # noqa: E402

from . import _identity  # noqa: E402
from .enums import (  # noqa: E402
    _CANCEL_REASON_FROM_PROTO,
    _ORDER_STATUS_FROM_PROTO,
    _ORDER_TYPE_TO_PROTO,
    _ORDER_UPDATE_TYPE_FROM_PROTO,
    _POSITION_UPDATE_TYPE_FROM_PROTO,
    _REQUEST_TYPE_TO_PROTO,
    _RESPONSE_MESSAGE_TYPE_TO_PROTO,
    _SIDE_FROM_PROTO,
    _SIDE_TO_PROTO,
    _TIME_IN_FORCE_TO_PROTO,
)
from .types import (  # noqa: E402
    BalanceUpdate,
    FundingRateUpdate,
    MarginAlert,
    OrderUpdate,
    PositionRow,
    PositionsSnapshot,
    PositionsSnapshotSource,
    PositionUpdate,
    SettlementBatchStatus,
    SettlementUpdate,
    SystemHealthUpdate,
    UnknownSequencerPush,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _correlation_id_to_int(raw: bytes) -> int:
    """Convert a 16-byte correlation ID (UUID bytes) to an integer."""
    if not raw:
        return 0
    return int.from_bytes(raw, "big")


def _uuid_bytes_to_str(raw: bytes) -> str:
    """Convert 16 raw UUID bytes to a standard hyphenated UUID string."""
    if len(raw) == _identity.USER_UUID_LEN:
        return _identity.bytes_to_uuid(raw)
    return "00000000-0000-0000-0000-000000000000"


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
    place = sequencer_pb2.PlaceOrderInput(
        symbol_id=symbol_id,
        side=_SIDE_TO_PROTO[side if isinstance(side, str) else side.value],
        order_type=_ORDER_TYPE_TO_PROTO[
            order_type if isinstance(order_type, str) else order_type.value
        ],
        quantity=quantity,
        user_commitment=b"",
        time_in_force=_TIME_IN_FORCE_TO_PROTO[
            time_in_force if isinstance(time_in_force, str) else time_in_force.value
        ],
        aon=aon,
        timestamp=timestamp,
        user_uuid=user_uuid,
    )
    if price is not None:
        place.price = price
    if min_fill_size is not None:
        place.min_fill_size = min_fill_size
    if expiry_time is not None:
        place.expiry_time = expiry_time
    if correlation_id_bytes is not None:
        place.correlation_id = correlation_id_bytes

    req = sequencer_pb2.EdgeSequencerRequest(place=place)
    return req.SerializeToString()


def build_cancel_order_proto(
    order_id: int,
    user_uuid: bytes,
    symbol_id: int,
    correlation_id_bytes: bytes,
) -> bytes:
    """Build a CancelMessage wrapped in EdgeSequencerRequest, return serialized bytes."""
    cancel = sequencer_pb2.CancelMessage(
        order_id=order_id,
        user_commitment=b"\x00" * 32,
        symbol_id=symbol_id,
        correlation_id=correlation_id_bytes,
    )
    req = sequencer_pb2.EdgeSequencerRequest(cancel=cancel)
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
        user_commitment=b"",
        symbol_id=symbol_id,
        correlation_id=correlation_id_bytes,
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
        correlation_id=correlation_id_bytes,
    )
    req = sequencer_pb2.EdgeSequencerRequest(update_leverage=update)
    return req.SerializeToString()


def build_order_header_aad(
    user_uuid: bytes,
    symbol_id: int,
    request_type_str: str,
    nonce: int,
    body_length: int,
    correlation_id: bytes = b"",
) -> bytes:
    """Create an OrderHeader proto and serialize it (used as AES-GCM AAD)."""
    header = edge_pb2.OrderHeader(
        user_uuid=user_uuid,
        symbol_id=symbol_id,
        request_type=_REQUEST_TYPE_TO_PROTO[request_type_str],
        nonce=nonce,
        body_length=body_length,
        correlation_id=correlation_id,
    )
    return header.SerializeToString()


def build_response_header_aad(
    user_uuid: bytes,
    message_type_str: str,
    body_length: int,
    nonce: int,
    fencing_epoch: int = 0,
) -> bytes:
    """Create a ResponseHeader proto and serialize it (used as AES-GCM AAD)."""
    header = edge_pb2.ResponseHeader(
        user_uuid=user_uuid,
        message_type=_RESPONSE_MESSAGE_TYPE_TO_PROTO[message_type_str],
        body_length=body_length,
        nonce=nonce,
        fencing_epoch=fencing_epoch,
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
        result: dict[str, Any] = {
            "type": "ack",
            "node_id": ack.node_id,
            "sequence": ack.sequence,
            "order_id": ack.order_id,
            "success": ack.success,
            "correlation_id": ack.correlation_id,
        }
        if ack.HasField("error_code"):
            result["error_code"] = ack.error_code
        if ack.HasField("order_status"):
            result["order_status"] = _ORDER_STATUS_FROM_PROTO.get(ack.order_status)
        if ack.HasField("node_health"):
            result["node_health"] = ack.node_health
        return result
    elif which == "fill":
        fill = resp.fill
        return {
            "type": "fill",
            "trade_id": fill.trade_id,
            "taker_order_id": fill.taker_order_id,
            "maker_order_id": fill.maker_order_id,
            "maker_user_commitment": fill.maker_user_commitment,
            "symbol_id": fill.symbol_id,
            "timestamp": fill.timestamp,
            "correlation_id": fill.correlation_id,
        }
    elif which == "signing":
        return {"type": "signing"}
    else:
        return {"type": "unknown"}


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
        correlation_id=_correlation_id_to_int(msg.correlation_id),
        timestamp=int(msg.timestamp),
        leverage=int(msg.leverage),
        realized_pnl=realized_pnl,
    )


def parse_position_update_proto(data: bytes) -> PositionUpdate:
    """Decode a PositionUpdateMessage protobuf into a PositionUpdate dataclass."""
    msg = sequencer_pb2.PositionUpdateMessage()
    msg.ParseFromString(data)

    from .enums import PositionUpdateType, Side

    return PositionUpdate(
        user_uuid=_uuid_bytes_to_str(msg.user_uuid),
        symbol_id=int(msg.symbol_id),
        side=_SIDE_FROM_PROTO.get(msg.side, Side.BUY),
        update_type=_POSITION_UPDATE_TYPE_FROM_PROTO.get(
            msg.update_type, PositionUpdateType.SNAPSHOT
        ),
        size=msg.size,
        entry_price=msg.entry_price,
        previous_size=msg.previous_size,
        fill_price=msg.fill_price,
        fill_qty=msg.fill_qty,
        correlation_id=_correlation_id_to_int(msg.correlation_id),
        timestamp=int(msg.timestamp),
    )


def _parse_positions_snapshot_source(value: int) -> PositionsSnapshotSource:
    if value == 1:
        return PositionsSnapshotSource.INITIAL
    if value == 2:
        return PositionsSnapshotSource.PERIODIC
    if value == 3:
        return PositionsSnapshotSource.EVENT
    return PositionsSnapshotSource.UNSPECIFIED


def _parse_settlement_batch_status(value: int) -> SettlementBatchStatus:
    if value == 1:
        return SettlementBatchStatus.SUBMITTED
    if value == 2:
        return SettlementBatchStatus.CONFIRMED
    if value == 3:
        return SettlementBatchStatus.FAILED
    return SettlementBatchStatus.UNSPECIFIED


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


def parse_system_health_proto(msg: sequencer_pb2.SystemHealthMessage) -> SystemHealthUpdate:
    return SystemHealthUpdate(
        total_nodes=int(msg.total_nodes),
        accepting_orders=bool(msg.accepting_orders),
        ready=int(msg.ready),
        degraded=int(msg.degraded),
        exhausted=int(msg.exhausted),
        warming=int(msg.warming),
        draining=int(msg.draining),
        waiting=int(msg.waiting),
    )


def parse_balance_update_proto(msg: sequencer_pb2.BalanceUpdateMessage) -> BalanceUpdate:
    return BalanceUpdate(
        user_uuid=_uuid_bytes_to_str(msg.user_uuid),
        shielded_balance_raw=int(msg.shielded_balance_raw),
        timestamp=int(msg.timestamp),
    )


def parse_margin_alert_proto(msg: sequencer_pb2.MarginAlertMessage) -> MarginAlert:
    return MarginAlert(
        owner=_uuid_bytes_to_str(msg.owner),
        symbol_id=int(msg.symbol_id),
        tier=int(msg.tier),
        margin_ratio_bps=int(msg.margin_ratio_bps),
        mark_price_bps=int(msg.mark_price_bps),
        liquidation_price_bps=int(msg.liquidation_price_bps),
        ts=int(msg.ts),
        state_version=int(msg.state_version),
        recovered=bool(msg.recovered),
    )


def parse_funding_rate_update_proto(
    msg: sequencer_pb2.FundingRateUpdateMessage,
) -> FundingRateUpdate:
    return FundingRateUpdate(
        symbol_id=int(msg.symbol_id),
        current_rate=msg.current_rate,
        predicted_rate=msg.predicted_rate,
        next_funding_time=int(msg.next_funding_time),
        timestamp=int(msg.timestamp),
    )


def parse_settlement_update_proto(msg: sequencer_pb2.SettlementUpdateMessage) -> SettlementUpdate:
    affected = tuple(_uuid_bytes_to_str(b) for b in msg.affected_user_uuids)
    return SettlementUpdate(
        batch_id=int(msg.batch_id),
        status=_parse_settlement_batch_status(int(msg.status)),
        tx_signature=msg.tx_signature,
        timestamp=int(msg.timestamp),
        affected_user_uuids=affected,
    )


SequencerPush: TypeAlias = (
    OrderUpdate
    | PositionUpdate
    | PositionsSnapshot
    | SystemHealthUpdate
    | BalanceUpdate
    | MarginAlert
    | FundingRateUpdate
    | SettlementUpdate
    | UnknownSequencerPush
)


def parse_sequencer_to_edge_message(data: bytes) -> SequencerPush:
    """Decode a SequencerToEdgeMessage and dispatch to the appropriate parsed type."""
    msg = sequencer_pb2.SequencerToEdgeMessage()
    msg.ParseFromString(data)

    which = msg.WhichOneof("inner")
    if which == "order_update":
        return parse_order_update_proto(msg.order_update.SerializeToString())
    if which == "position_update":
        return parse_position_update_proto(msg.position_update.SerializeToString())
    if which == "positions_snapshot":
        return parse_positions_snapshot_proto(msg.positions_snapshot)
    if which == "system_health":
        return parse_system_health_proto(msg.system_health)
    if which == "settlement_update":
        return parse_settlement_update_proto(msg.settlement_update)
    if which == "funding_rate_update":
        return parse_funding_rate_update_proto(msg.funding_rate_update)
    if which == "balance_update":
        return parse_balance_update_proto(msg.balance_update)
    if which == "margin_alert":
        return parse_margin_alert_proto(msg.margin_alert)
    return UnknownSequencerPush(oneof_field=which)
