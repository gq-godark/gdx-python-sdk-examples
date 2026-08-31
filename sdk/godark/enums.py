from __future__ import annotations

from enum import Enum


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    PEG_TO_MID = "PEG_TO_MID"
    PEG_TO_BID = "PEG_TO_BID"
    PEG_TO_ASK = "PEG_TO_ASK"


class TimeInForce(str, Enum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    GTD = "GTD"


class OrderStatus(str, Enum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class OrderUpdateType(str, Enum):
    OPEN = "OPEN"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    MODIFIED = "MODIFIED"
    CANCEL_REJECTED = "CANCEL_REJECTED"
    MODIFY_REJECTED = "MODIFY_REJECTED"


class PositionUpdateType(str, Enum):
    SNAPSHOT = "SNAPSHOT"
    OPEN = "OPEN"
    INCREASE = "INCREASE"
    DECREASE = "DECREASE"
    CLOSE = "CLOSE"
    FUNDING_APPLIED = "FUNDING_APPLIED"


class CancelReason(str, Enum):
    USER_REQUESTED = "USER_REQUESTED"
    IOC_REMAINDER = "IOC_REMAINDER"
    FOK_NOT_FILLED = "FOK_NOT_FILLED"
    EXPIRED = "EXPIRED"
    SYSTEM = "SYSTEM"
    ADL = "ADL"
    LIQUIDATED_CANCELED = "LIQUIDATED_CANCELED"
    MARGIN_CANCELED = "MARGIN_CANCELED"
    REDUCE_ONLY = "REDUCE_ONLY"


# ---------------------------------------------------------------------------
# Proto int -> Python enum mappings
# ---------------------------------------------------------------------------

_SIDE_FROM_PROTO: dict[int, Side] = {
    1: Side.BUY,
    2: Side.SELL,
}

_ORDER_TYPE_FROM_PROTO: dict[int, OrderType] = {
    1: OrderType.MARKET,
    2: OrderType.LIMIT,
    3: OrderType.PEG_TO_MID,
    4: OrderType.PEG_TO_BID,
    5: OrderType.PEG_TO_ASK,
}

_TIME_IN_FORCE_FROM_PROTO: dict[int, TimeInForce] = {
    1: TimeInForce.GTC,
    2: TimeInForce.IOC,
    3: TimeInForce.FOK,
    4: TimeInForce.GTD,
}

_ORDER_STATUS_FROM_PROTO: dict[int, OrderStatus] = {
    1: OrderStatus.NEW,
    2: OrderStatus.PARTIALLY_FILLED,
    3: OrderStatus.FILLED,
    4: OrderStatus.CANCELLED,
    5: OrderStatus.REJECTED,
}

_ORDER_UPDATE_TYPE_FROM_PROTO: dict[int, OrderUpdateType] = {
    1: OrderUpdateType.OPEN,
    2: OrderUpdateType.FILLED,
    3: OrderUpdateType.PARTIALLY_FILLED,
    4: OrderUpdateType.CANCELLED,
    5: OrderUpdateType.REJECTED,
    6: OrderUpdateType.MODIFIED,
    7: OrderUpdateType.CANCEL_REJECTED,
    8: OrderUpdateType.MODIFY_REJECTED,
}

_POSITION_UPDATE_TYPE_FROM_PROTO: dict[int, PositionUpdateType] = {
    1: PositionUpdateType.SNAPSHOT,
    2: PositionUpdateType.OPEN,
    3: PositionUpdateType.INCREASE,
    4: PositionUpdateType.DECREASE,
    5: PositionUpdateType.CLOSE,
    6: PositionUpdateType.FUNDING_APPLIED,
}

_CANCEL_REASON_FROM_PROTO: dict[int, CancelReason] = {
    1: CancelReason.USER_REQUESTED,
    2: CancelReason.IOC_REMAINDER,
    3: CancelReason.FOK_NOT_FILLED,
    4: CancelReason.EXPIRED,
    5: CancelReason.SYSTEM,
    6: CancelReason.ADL,
    7: CancelReason.LIQUIDATED_CANCELED,
    8: CancelReason.MARGIN_CANCELED,
    9: CancelReason.REDUCE_ONLY,
}

# ---------------------------------------------------------------------------
# Python enum / string -> proto int mappings (for building protos)
# ---------------------------------------------------------------------------

_SIDE_TO_PROTO: dict[str, int] = {
    "BUY": 1,
    "SELL": 2,
}

_ORDER_TYPE_TO_PROTO: dict[str, int] = {
    "MARKET": 1,
    "LIMIT": 2,
    "PEG_TO_MID": 3,
    "PEG_TO_BID": 4,
    "PEG_TO_ASK": 5,
}

_TIME_IN_FORCE_TO_PROTO: dict[str, int] = {
    "GTC": 1,
    "IOC": 2,
    "FOK": 3,
    "GTD": 4,
}

_REQUEST_TYPE_TO_PROTO: dict[str, int] = {
    "place": 1,
    "cancel": 2,
    "modify": 3,
    "subscribe": 4,
    # Legacy alias; wire value is GET_OPEN_ORDERS.
    "signing": 5,
    "get_open_orders": 5,
    # Legacy alias; wire value is ADJUST_MARGIN.
    "get_order_history": 7,
    "adjust_margin": 7,
    "update_leverage": 6,
    "mass_quote": 8,
    "batch_cancel": 9,
    "batch_modify": 10,
    "cancel_tpsl": 11,
    "amend_tpsl": 12,
    "update_margin_mode": 13,
    "get_positions": 14,
    "get_account": 15,
    "cancel_all": 16,
    "close_all": 17,
    "reverse": 18,
}

_RESPONSE_MESSAGE_TYPE_TO_PROTO: dict[str, int] = {
    "order_update": 1,
    "system_health": 2,
    "ack": 3,
    "open_orders_snapshot": 4,
    "positions_snapshot": 5,
    "balance_and_position": 6,
    "account_margin_update": 7,
    # Devnet edge alias; same wire value as account_margin_update.
    "account_update": 7,
    "mass_quote_ack": 8,
    "batch_cancel_ack": 9,
    "batch_modify_ack": 10,
    "tpsl_update": 11,
    "leverage_settings": 12,
    "cancel_all_ack": 13,
    "close_all_ack": 14,
    "reverse_ack": 15,
}


def side_from_proto(value: int) -> Side:
    return _SIDE_FROM_PROTO[value]


def order_type_from_proto(value: int) -> OrderType:
    return _ORDER_TYPE_FROM_PROTO[value]


def time_in_force_from_proto(value: int) -> TimeInForce:
    return _TIME_IN_FORCE_FROM_PROTO[value]


def order_status_from_proto(value: int) -> OrderStatus:
    return _ORDER_STATUS_FROM_PROTO[value]


def order_update_type_from_proto(value: int) -> OrderUpdateType:
    return _ORDER_UPDATE_TYPE_FROM_PROTO[value]


def position_update_type_from_proto(value: int) -> PositionUpdateType:
    return _POSITION_UPDATE_TYPE_FROM_PROTO[value]


def cancel_reason_from_proto(value: int) -> CancelReason | None:
    return _CANCEL_REASON_FROM_PROTO.get(value)


def side_to_proto(value: str | Side) -> int:
    return _SIDE_TO_PROTO[str(value) if isinstance(value, Side) else value]


def order_type_to_proto(value: str | OrderType) -> int:
    return _ORDER_TYPE_TO_PROTO[str(value) if isinstance(value, OrderType) else value]


def time_in_force_to_proto(value: str | TimeInForce) -> int:
    return _TIME_IN_FORCE_TO_PROTO[str(value) if isinstance(value, TimeInForce) else value]


def request_type_to_proto(value: str) -> int:
    return _REQUEST_TYPE_TO_PROTO[value]


def response_message_type_to_proto(value: str) -> int:
    return _RESPONSE_MESSAGE_TYPE_TO_PROTO[value]
