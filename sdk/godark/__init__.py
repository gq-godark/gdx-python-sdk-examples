"""GoDark Python Trading SDK -- programmatic equivalent of the gdx-web frontend."""

from ._proto import SequencerPush
from ._transport import TransportConfig
from .client import GodarkClient
from .enums import (
    CancelReason,
    OrderStatus,
    OrderType,
    OrderUpdateType,
    PositionUpdateType,
    Side,
    TimeInForce,
)
from .errors import (
    AuthenticationError,
    ConnectionError,
    EncryptionError,
    GodarkError,
    OrderError,
    SessionError,
    TimeoutError,
)
from .market_data import MarketDataClient, subscription_callback_key
from .order_error_code import (
    ORDER_ERROR_CODES,
    OrderErrorEntry,
    make_order_error_from_code,
    make_order_error_from_json,
)
from .order_error_code import (
    find as find_order_error_code,
)
from .order_error_code import (
    find_symbolic as find_order_error_symbolic,
)
from .rest_client import GodarkRestClient
from .types import (
    Balance,
    BalanceUpdate,
    FundingRateUpdate,
    LeverageSetting,
    LeverageSettings,
    MarginAlert,
    MeProfile,
    OrderAck,
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

__all__ = [
    "GodarkClient",
    "GodarkRestClient",
    "MarketDataClient",
    "subscription_callback_key",
    "TransportConfig",
    "Side",
    "OrderType",
    "TimeInForce",
    "OrderStatus",
    "OrderUpdateType",
    "PositionUpdateType",
    "CancelReason",
    "PositionsSnapshotSource",
    "SettlementBatchStatus",
    "Balance",
    "LeverageSetting",
    "LeverageSettings",
    "MeProfile",
    "OrderAck",
    "OrderUpdate",
    "PositionRow",
    "PositionUpdate",
    "PositionsSnapshot",
    "SystemHealthUpdate",
    "BalanceUpdate",
    "MarginAlert",
    "FundingRateUpdate",
    "SettlementUpdate",
    "UnknownSequencerPush",
    "SequencerPush",
    "GodarkError",
    "AuthenticationError",
    "SessionError",
    "OrderError",
    "ConnectionError",
    "EncryptionError",
    "TimeoutError",
    "ORDER_ERROR_CODES",
    "OrderErrorEntry",
    "find_order_error_code",
    "find_order_error_symbolic",
    "make_order_error_from_code",
    "make_order_error_from_json",
]
