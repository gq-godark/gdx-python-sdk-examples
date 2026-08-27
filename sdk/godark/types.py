from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .enums import CancelReason, OrderStatus, OrderUpdateType, PositionUpdateType, Side


@dataclass(frozen=True)
class OrderAck:
    order_id: str
    success: bool
    sequence: str
    error_code: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class MassQuoteLegResult:
    """Outcome of one cancel-replace leg in a mass-quote batch."""

    leg_index: int
    status: str  # "open" | "filled" | "failed" | "unspecified"
    cancelled_order_id: str | None = None
    new_order_id: str | None = None
    error_code: int | None = None
    # Number of taker fills produced by this leg in relaxed (post_only=False)
    # mode; 0 for a pure rest or a post-only leg.
    fill_count: int = 0


@dataclass(frozen=True)
class MassQuoteAck:
    """Batch-level result of a mass quote: one entry per submitted leg."""

    success: bool
    sequence: str
    results: list[MassQuoteLegResult]


@dataclass(frozen=True)
class BatchCancelLegResult:
    """Outcome of cancelling one order id in a batch-cancel request."""

    order_id: str
    cancelled: bool
    error_code: int | None = None


@dataclass(frozen=True)
class BatchCancelAck:
    """Batch-level result of a batch cancel: one entry per submitted order id."""

    success: bool
    sequence: str
    results: list[BatchCancelLegResult]


@dataclass(frozen=True)
class BatchModifyLegResult:
    """Outcome of amending one resting order in a batch-modify request."""

    order_id: str
    modified: bool
    error_code: int | None = None


@dataclass(frozen=True)
class BatchModifyAck:
    """Batch-level result of a batch modify: one entry per submitted leg."""

    success: bool
    sequence: str
    results: list[BatchModifyLegResult]


@dataclass(frozen=True)
class OrderUpdate:
    order_id: str
    user_uuid: str
    symbol_id: int
    side: Side
    status: OrderStatus
    update_type: OrderUpdateType
    price: str
    quantity: str
    filled_qty: str
    remaining_qty: str
    cum_fill: str
    cancel_reason: CancelReason | None = None
    reject_reason: str | None = None
    msg: str | None = None
    reduce_only: bool = False
    post_only: bool = False
    correlation_id: int = 0
    timestamp: int = 0
    #: Client-selected leverage at order-placement time (1 = 1x).
    leverage: int = 0
    #: Realized PnL on closing / terminal fills; omitted when absent on wire.
    realized_pnl: str | None = None


@dataclass(frozen=True)
class PositionUpdate:
    user_uuid: str
    symbol_id: int
    side: Side
    update_type: PositionUpdateType
    size: str
    entry_price: str
    previous_size: str
    fill_price: str
    fill_qty: str
    correlation_id: int = 0
    timestamp: int = 0


class PositionsSnapshotSource(str, Enum):
    UNSPECIFIED = "UNSPECIFIED"
    INITIAL = "INITIAL"
    PERIODIC = "PERIODIC"
    EVENT = "EVENT"


@dataclass(frozen=True)
class PositionRow:
    symbol_id: int
    side: Side
    size: str
    entry_price: str
    leverage: int
    mark_price: str | None = None
    unrealized_pnl: str | None = None
    notional: str | None = None
    mark_publish_time_sec: int | None = None


@dataclass(frozen=True)
class PositionsSnapshot:
    user_uuid: str
    rows: tuple[PositionRow, ...]
    server_timestamp: int
    source: PositionsSnapshotSource
    #: Echoed SubscribePositions correlation where present (protobuf bytes → uint128 big-endian).
    correlation_id: int | None = None


@dataclass(frozen=True)
class SystemHealthUpdate:
    total_nodes: int
    accepting_orders: bool
    ready: int
    degraded: int
    exhausted: int
    warming: int
    draining: int
    waiting: int


@dataclass(frozen=True)
class BalanceUpdate:
    user_uuid: str
    balance_raw: int
    timestamp: int
    balance: str = ""
    signed_balance_8dp: int = 0
    free_collateral_8dp: int = 0


@dataclass(frozen=True)
class MarginAlert:
    owner: str
    symbol_id: int
    tier: int
    margin_ratio_bps: int
    mark_price: str
    liquidation_price: str
    ts: int
    state_version: int
    recovered: bool


@dataclass(frozen=True)
class FundingRateUpdate:
    symbol_id: int
    current_rate: str
    predicted_rate: str
    next_funding_time: int
    timestamp: int


class SettlementBatchStatus(str, Enum):
    UNSPECIFIED = "UNSPECIFIED"
    SUBMITTED = "SUBMITTED"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class SettlementUpdate:
    batch_id: int
    status: SettlementBatchStatus
    tx_signature: str
    timestamp: int
    affected_user_uuids: tuple[str, ...]


@dataclass(frozen=True)
class MeProfile:
    """REST profile snapshot from ``GET /api/v1/auth/me``."""

    id: str
    dynamic_user_id: str
    email: str
    wallet_address: str
    referral_code: str
    tier: str


@dataclass(frozen=True)
class Balance:
    """Shielded-pool balance snapshot from ``GET /api/v1/shielded-pool/balances/{owner}``."""

    wallet_usdt_raw: int
    pending_deposits_raw: int
    shielded_balance_raw: int
    wallet_usdt_ui: float


@dataclass(frozen=True)
class LeverageSetting:
    symbol_id: int
    leverage: int


@dataclass(frozen=True)
class LeverageSettings:
    """Per-user leverage prefs from REST ``GET /leverage`` or encrypted WS push.

    WS pushes (positions subscribe / after ``update_leverage``) also carry
    ``user_uuid`` and ``server_timestamp``; REST snapshots leave those at defaults.
    """

    settings: tuple[LeverageSetting, ...]
    user_uuid: str = ""
    server_timestamp: int = 0


@dataclass(frozen=True)
class OpenOrderRow:
    """One resting order row inside an :class:`OpenOrdersSnapshot`."""

    order_id: str
    symbol_id: int
    leverage: int
    price: str = ""
    quantity: str = ""
    remaining_qty: str = ""


@dataclass(frozen=True)
class OpenOrdersSnapshot:
    """Encrypted ``NodeResponse::OpenOrdersSnapshot`` push (subscribe / UpdateLeverage refresh)."""

    rows: tuple[OpenOrderRow, ...]
    server_timestamp: int = 0
    correlation_id: int = 0


@dataclass(frozen=True)
class UnknownSequencerPush:
    """Decoded outer message with an inner variant this SDK revision does not map."""

    oneof_field: str | None = None
