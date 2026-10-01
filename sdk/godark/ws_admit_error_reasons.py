"""Hand-authored SDK reason strings for WsAdmitErrorCode variants."""

WS_ADMIT_ERROR_REASONS: dict[str, str] = {
    "NOT_AUTHENTICATED": "Sign in before sending requests on this connection.",
    "UNKNOWN_API_KEY": "Unknown API key. Check your credentials and try again.",
    "AUTH_TIMEOUT": "Authentication timed out. Connect and sign in again.",
    "TOO_MANY_SESSIONS": "Too many sessions for this account. Close another connection and retry.",
    "MARKET_TIF_INVALID": "Market orders require IOC or FOK time in force.",
    "QUANTITY_MUST_BE_NONZERO": "Quantity must be greater than zero.",
    "BELOW_MIN_NOTIONAL": "Order must meet the minimum notional for your tier.",
    "ALREADY_SUBSCRIBED": "Already subscribed to this channel.",
    "INVALID_POSITIONS_INTERVAL": "Invalid positions refresh interval. Allowed values: 2, 5, 10.",
    "INVALID_JSON": "Invalid message JSON.",
    "MESSAGE_TOO_LARGE": "Message exceeds the size limit.",
}
