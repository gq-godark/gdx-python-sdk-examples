"""REST trading client — one-shot HPKE per encrypted request."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
import uuid
from typing import Any, Literal

from . import _proto
from ._access_token import user_uuid_from_access_token_jwt
from ._hpke import SealedSession, nonce_from_u64, pinned_sequencer_static_pub
from ._rest_transport import RestEnvelopeError, RestTransport
from ._session import CryptoSession
from ._symbols import load_offline_symbol_map, load_symbol_map_from_edge
from .client import Environment, _resolve_noise_static_public_key_hex, _resolve_passphrase
from .enums import OrderType, Side, TimeInForce
from .errors import EncryptionError, OrderError, SessionError, TimeoutError
from .order_error_code import make_order_error_from_json
from .types import (
    AccountMarginUpdate,
    BatchCancelAck,
    BatchCancelLegResult,
    BatchModifyAck,
    BatchModifyLegResult,
    MassQuoteAck,
    MassQuoteLegResult,
    OpenOrdersSnapshot,
    OrderAck,
    PositionsSnapshot,
)

_LOG = logging.getLogger(__name__)

_GCM_TAG_LEN = 16


def _resolve_rest_base_url(explicit: str | None) -> str:
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip().rstrip("/")
    for key in ("GODARK_REST_URL", "GDX_REST_URL"):
        v = os.environ.get(key, "").strip()
        if v:
            return v.rstrip("/")
    ws = os.environ.get("GODARK_EDGE_URL", os.environ.get("GDX_EDGE_URL", "")).strip()
    if ws:
        return _ws_origin_to_http_rest(ws)
    return "https://api.godark-dex.com"


def _ws_origin_to_http_rest(ws_url: str) -> str:
    u = ws_url.strip().rstrip("/")
    if u.endswith("/ws/v1"):
        u = u[: -len("/ws/v1")]
    elif u.endswith("/ws"):
        u = u[: -len("/ws")]
    if u.startswith("ws://"):
        return "http://" + u.removeprefix("ws://")
    if u.startswith("wss://"):
        return "https://" + u.removeprefix("wss://")
    return u


def _infer_environment_from_rest_url(rest_base: str) -> Environment:
    """Infer Environment from REST origin host (testnet/devnet pins; localnet none)."""
    host = rest_base.strip().lower()
    # Strip scheme
    for prefix in ("https://", "http://", "wss://", "ws://"):
        if host.startswith(prefix):
            host = host[len(prefix) :]
            break
    host = host.split("/")[0].split(":")[0]
    if host in ("127.0.0.1", "localhost") or host.endswith(".localhost"):
        return Environment.LOCALNET
    if "devnet" in host or host == "18.143.165.149":
        return Environment.DEVNET
    if "godark-dex.com" in host:
        return Environment.TESTNET
    return Environment.TESTNET


def _new_correlation_id() -> bytes:
    return uuid.uuid4().bytes


def _timestamp_ns() -> int:
    return int(time.time() * 1_000_000_000)


def _env_user_uuid() -> str | None:
    for key in ("GODARK_USER_UUID", "GDX_USER_UUID"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return None


def _correlation_id_header_hex(correlation_id: bytes) -> str:
    if len(correlation_id) != 16:
        return correlation_id.hex() if correlation_id else ""
    value = int.from_bytes(correlation_id, "big")
    return f"{value:032x}" if value else ""


class GodarkRestClient:
    """
    REST client for API-key auth, encrypted trading, and trading read endpoints.

    Identity comes from the JWT ``sub`` claim returned by ``POST /auth/token``.
    Session-only platform routes such as ``GET /auth/me`` are intentionally not
    exposed here — they require a browser session JWT, not an API key token.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        api_key_id: str | None = None,
        api_secret: str | None = None,
        passphrase: str | None = None,
        rest_base_url: str | None = None,
        user_uuid: str | None = None,
        hpke_static_public_key_hex: str | None = None,
        environment: Environment | None = None,
        symbol_map: dict[str, int] | None = None,
    ):
        if api_key_id is not None or api_secret is not None:
            if api_key_id is None or api_secret is None:
                raise ValueError("api_key_id and api_secret must be provided together")
            if api_key is not None:
                raise ValueError("use either api_key or (api_key_id, api_secret), not both")
            resolved_passphrase = _resolve_passphrase(passphrase)
            if resolved_passphrase is None:
                raise ValueError("passphrase is required when using api_key_id and api_secret")
            self._api_key_id = api_key_id
            self._api_secret = api_secret
            self._passphrase = resolved_passphrase
            self._auth_token: str | None = None
        elif api_key is not None:
            if passphrase is not None and str(passphrase).strip() != "":
                raise ValueError("passphrase must not be set when using legacy api_key")
            self._api_key_id = None
            self._api_secret = None
            self._passphrase = None
            self._auth_token = api_key
        else:
            raise ValueError("provide api_key or both api_key_id and api_secret")

        self._rest_base = _resolve_rest_base_url(rest_base_url)
        self._user_symbol_map = symbol_map is not None
        self._symbol_map = dict(symbol_map) if symbol_map is not None else load_offline_symbol_map()
        self._http = RestTransport(self._rest_base)
        self._bearer: str | None = None
        self._user_uuid = user_uuid or _env_user_uuid()
        self._token_scope: str | None = None
        env = (
            environment
            if environment is not None
            else _infer_environment_from_rest_url(self._rest_base)
        )
        if not isinstance(env, Environment):
            raise TypeError("environment must be an Environment")
        self._environment = env
        # explicit → env vars → Environment preset (testnet/devnet baked; localnet none)
        self._hpke_pin_hex = _resolve_noise_static_public_key_hex(hpke_static_public_key_hex, env)
        self._next_request_id = 1
        self._local_coid_index: dict[str, str] = {}

    @property
    def bearer_token(self) -> str | None:
        return self._bearer

    @property
    def token_scope(self) -> str | None:
        return self._token_scope

    @property
    def user_uuid_str(self) -> str | None:
        return self._user_uuid

    @property
    def user_uuid(self) -> str | None:
        """Alias for ``user_uuid_str`` (WS client parity)."""
        return self._user_uuid

    def _resolve_symbol(self, symbol: str) -> int:
        sid = self._symbol_map.get(symbol)
        if sid is None:
            raise ValueError(f"unknown symbol: {symbol}")
        return sid

    def _user_uuid_bytes(self) -> bytes:
        if self._user_uuid is None:
            raise SessionError("Not authenticated")
        return uuid.UUID(self._user_uuid).bytes

    async def connect(self) -> None:
        if not self._user_symbol_map:
            self._symbol_map = await load_symbol_map_from_edge(self._rest_base)
        if self._api_key_id is not None:
            auth_data = await self._http.auth_token(
                grant_type="client_credentials",
                client_id=self._api_key_id,
                client_secret=self._api_secret,
                passphrase=self._passphrase,
            )
        else:
            auth_data = await self._http.auth_token(token=self._auth_token)
        self._bearer = auth_data.get("access_token") or auth_data.get("token")
        if not self._bearer:
            raise SessionError("auth/token missing access_token/token")
        self._token_scope = auth_data.get("scope")
        if self._user_uuid is None:
            legacy_uuid = auth_data.get("user_uuid")
            if isinstance(legacy_uuid, str) and legacy_uuid.strip():
                self._user_uuid = legacy_uuid.strip()
            else:
                parsed = user_uuid_from_access_token_jwt(self._bearer)
                if parsed is not None:
                    self._user_uuid = str(parsed)

    async def disconnect(self) -> None:
        try:
            if self._bearer:
                await self._http.revoke_token(bearer=self._bearer)
        finally:
            self._bearer = None
            self._user_uuid = None
            self._token_scope = None
            await self._http.aclose()

    async def __aenter__(self) -> GodarkRestClient:
        await self.connect()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.disconnect()

    async def _send_encrypted_envelope(
        self,
        request_type: str,
        symbol_id: int,
        plaintext: bytes,
        correlation_id: bytes,
        *,
        route: Literal["post_orders", "post_leverage", "delete", "patch", "post_path"],
        order_id: str | None = None,
        client_order_id: str | None = None,
        header_leverage: int | None = None,
        post_path: str | None = None,
    ) -> tuple[SealedSession, dict[str, Any]]:
        if not self._bearer:
            raise SessionError("not authenticated – call connect() first")
        recipient = pinned_sequencer_static_pub(self._hpke_pin_hex)
        user = uuid.UUID(self._user_uuid)
        request_id = self._next_request_id
        self._next_request_id += 1
        encapped, sealed = CryptoSession.setup_rest(recipient, user, request_id)

        nonce = 0
        body_length = len(plaintext) + _GCM_TAG_LEN
        aad = _proto.build_order_header_aad(
            user_uuid=self._user_uuid_bytes(),
            symbol_id=symbol_id,
            request_type_str=request_type,
            nonce=nonce,
            body_length=body_length,
            correlation_id=correlation_id,
            conn_id=0,
        )
        ciphertext = sealed.seal_c2s(nonce_from_u64(nonce), aad, plaintext)

        body: dict[str, Any] = {
            "header": {
                "symbol_id": symbol_id,
                "request_type": request_type,
                "nonce": nonce,
                "body_length": body_length,
                "correlation_id": _correlation_id_header_hex(correlation_id),
            },
            "encrypted_body": base64.b64encode(ciphertext).decode("ascii"),
            "encapped_key": base64.b64encode(encapped).decode("ascii"),
            "request_id": request_id,
        }
        if header_leverage is not None:
            body["header"]["leverage"] = header_leverage
        if client_order_id:
            body["client_order_id"] = client_order_id

        if route == "post_orders":
            raw = await self._http.post_encrypted_order(bearer=self._bearer, body=body)
        elif route == "post_leverage":
            raw = await self._http.post_encrypted_leverage(bearer=self._bearer, body=body)
        elif route == "post_path":
            if not post_path:
                raise ValueError("post_path route requires post_path")
            raw = await self._http.post_encrypted(path=post_path, bearer=self._bearer, body=body)
        elif route == "delete":
            if not order_id:
                raise ValueError("delete route requires order_id")
            raw = await self._http.delete_encrypted_order(
                bearer=self._bearer, order_id=order_id, body=body
            )
        else:
            if not order_id:
                raise ValueError("patch route requires order_id")
            raw = await self._http.patch_encrypted_order(
                bearer=self._bearer, order_id=order_id, body=body
            )
        return sealed, raw

    async def _send_encrypted(
        self,
        request_type: str,
        symbol_id: int,
        plaintext: bytes,
        correlation_id: bytes,
        *,
        route: Literal["post_orders", "post_leverage", "delete", "patch", "post_path"],
        order_id: str | None = None,
        client_order_id: str | None = None,
        header_leverage: int | None = None,
        post_path: str | None = None,
    ) -> OrderAck:
        sealed, raw = await self._send_encrypted_envelope(
            request_type,
            symbol_id,
            plaintext,
            correlation_id,
            route=route,
            order_id=order_id,
            client_order_id=client_order_id,
            header_leverage=header_leverage,
            post_path=post_path,
        )
        return self._parse_ack(raw, sealed)

    async def _send_encrypted_order(
        self,
        request_type: str,
        symbol_id: int,
        plaintext: bytes,
        correlation_id: bytes = b"",
        *,
        client_order_id: str | None = None,
    ) -> OrderAck:
        return await self._send_encrypted(
            request_type,
            symbol_id,
            plaintext,
            correlation_id,
            route="post_orders",
            client_order_id=client_order_id,
        )

    def _parse_ack(self, raw: dict[str, Any], sealed: SealedSession | None = None) -> OrderAck:
        if raw.get("encrypted") or raw.get("encrypted_body"):
            if sealed is None:
                raise EncryptionError("encrypted REST ack requires one-shot HPKE session")
            return self._decrypt_rest_ack(raw, sealed)
        if not raw.get("success", True):
            raise OrderError(
                raw.get("error", "order rejected"),
                error_code=raw.get("error_code"),
            )
        return OrderAck(
            order_id=str(raw.get("order_id", "")),
            success=True,
            sequence=str(raw.get("sequence", "")),
        )

    def _decrypt_rest_plaintext(self, msg: dict[str, Any], sealed: SealedSession) -> bytes:
        ct_b64 = msg.get("encrypted_body", "") or msg.get("ciphertext", "")
        ct = base64.b64decode(ct_b64)
        nonce = int(msg.get("nonce", 0))
        message_type = str(msg.get("message_type", "ack"))
        fencing_epoch = int(msg.get("fencing_epoch", 0))
        aad = _proto.build_response_header_aad(
            user_uuid=self._user_uuid_bytes(),
            message_type_str=message_type,
            body_length=len(ct),
            nonce=nonce,
            fencing_epoch=fencing_epoch,
            correlation_id=_proto.response_correlation_id_bytes(msg.get("correlation_id")),
            session_seq=int(msg.get("session_seq") or 0),
        )
        try:
            return sealed.open_s2c(nonce_from_u64(nonce), aad, ct)
        except Exception as e:
            raise EncryptionError(f"Failed to decrypt REST reply: {e}") from e

    def _decrypt_rest_ack(self, msg: dict[str, Any], sealed: SealedSession) -> OrderAck:
        plaintext = self._decrypt_rest_plaintext(msg, sealed)
        ack_dict = _proto.parse_node_response(plaintext)
        if ack_dict.get("type") != "ack":
            raise OrderError(f"Expected ack, got {ack_dict.get('type')}")
        if not ack_dict.get("success"):
            raise OrderError(
                ack_dict.get("reject_text") or "order rejected",
                error_code=str(ack_dict.get("error_code", "")) or None,
            )
        return OrderAck(
            order_id=str(ack_dict.get("order_id", "")),
            success=True,
            sequence=str(ack_dict.get("sequence", "")),
        )

    def _decrypt_rest_node_response(
        self, msg: dict[str, Any], sealed: SealedSession
    ) -> tuple[str, Any]:
        ct_b64 = msg.get("encrypted_body", "")
        ct = base64.b64decode(ct_b64)
        nonce = int(msg.get("nonce", 0))
        message_type = str(msg.get("message_type", "ack"))
        fencing_epoch = int(msg.get("fencing_epoch", 0))
        aad = _proto.build_response_header_aad(
            user_uuid=self._user_uuid_bytes(),
            message_type_str=message_type,
            body_length=len(ct),
            nonce=nonce,
            fencing_epoch=fencing_epoch,
            correlation_id=_proto.response_correlation_id_bytes(msg.get("correlation_id")),
            session_seq=int(msg.get("session_seq") or 0),
        )
        try:
            plaintext = sealed.open_s2c(nonce_from_u64(nonce), aad, ct)
        except Exception as e:
            raise EncryptionError(f"Failed to decrypt REST reply: {e}") from e
        return _proto.parse_node_response_snapshot(plaintext, message_type=message_type)

    async def _snapshot_rpc(
        self,
        request_type: str,
        build_proto,
        path: str,
    ) -> tuple[str, Any]:
        corr_id = _new_correlation_id()
        plaintext = build_proto(self._user_uuid_bytes(), corr_id)
        header_symbol_id = self._symbol_map.get("BTC-USDC-PERP")
        if header_symbol_id is None:
            header_symbol_id = next(iter(self._symbol_map.values()), 1)
        if not self._bearer:
            raise SessionError("not authenticated – call connect() first")
        recipient = pinned_sequencer_static_pub(self._hpke_pin_hex)
        user = uuid.UUID(self._user_uuid)
        request_id = self._next_request_id
        self._next_request_id += 1
        encapped, sealed = CryptoSession.setup_rest(recipient, user, request_id)
        nonce = 0
        body_length = len(plaintext) + _GCM_TAG_LEN
        aad = _proto.build_order_header_aad(
            user_uuid=self._user_uuid_bytes(),
            symbol_id=header_symbol_id,
            request_type_str=request_type,
            nonce=nonce,
            body_length=body_length,
            correlation_id=corr_id,
            conn_id=0,
        )
        ciphertext = sealed.seal_c2s(nonce_from_u64(nonce), aad, plaintext)
        body: dict[str, Any] = {
            "header": {
                "symbol_id": header_symbol_id,
                "request_type": request_type,
                "nonce": nonce,
                "body_length": body_length,
                "correlation_id": _correlation_id_header_hex(corr_id),
            },
            "encrypted_body": base64.b64encode(ciphertext).decode("ascii"),
            "encapped_key": base64.b64encode(encapped).decode("ascii"),
            "request_id": request_id,
        }
        raw = await self._http.post_encrypted(path=path, bearer=self._bearer, body=body)
        if raw.get("encrypted") or raw.get("encrypted_body"):
            return self._decrypt_rest_node_response(raw, sealed)
        raise OrderError(f"expected encrypted snapshot reply for {request_type}")

    async def get_open_orders(self) -> OpenOrdersSnapshot:
        """Live open orders via encrypted ``POST /api/v1/openOrders``."""
        variant, parsed = await self._snapshot_rpc(
            "get_open_orders",
            _proto.build_get_open_orders_proto,
            "/api/v1/openOrders",
        )
        if variant != "open_orders_snapshot":
            raise OrderError(f"expected open_orders_snapshot, got {variant}")
        return parsed

    async def get_positions(self) -> PositionsSnapshot:
        """Live positions via encrypted ``POST /api/v1/positions``."""
        variant, parsed = await self._snapshot_rpc(
            "get_positions",
            _proto.build_get_positions_proto,
            "/api/v1/positions",
        )
        if variant != "positions_snapshot":
            raise OrderError(f"expected positions_snapshot, got {variant}")
        return parsed

    async def get_account(self) -> AccountMarginUpdate:
        """Live account margin via encrypted ``POST /api/v1/account``."""
        variant, parsed = await self._snapshot_rpc(
            "get_account",
            _proto.build_get_account_proto,
            "/api/v1/account",
        )
        if variant != "account_margin_update":
            raise OrderError(f"expected account_margin_update, got {variant}")
        return parsed

    async def place_order(
        self,
        symbol: str,
        side: str | Side,
        *,
        quantity: float,
        type: str | OrderType | None = None,
        order_type: str | OrderType | None = None,
        price: float | None = None,
        time_in_force: str | TimeInForce = "GTC",
        aon: bool = False,
        min_fill_size: float | None = None,
        expiry_time: int | None = None,
        client_order_id: str | None = None,
    ) -> OrderAck:
        """Place order. Accept docs alias ``type=`` or ``order_type=``."""
        ot = type if type is not None else order_type
        if ot is None:
            raise ValueError("provide type= or order_type=")

        symbol_id = self._resolve_symbol(symbol)
        side_str = side.value if isinstance(side, Side) else side
        otype_str = ot.value if isinstance(ot, OrderType) else ot
        tif_str = time_in_force.value if isinstance(time_in_force, TimeInForce) else time_in_force
        corr_id = _new_correlation_id()

        plaintext = _proto.build_place_order_proto(
            symbol_id=symbol_id,
            side=side_str,
            order_type=otype_str,
            quantity=quantity,
            user_uuid=self._user_uuid_bytes(),
            price=price,
            time_in_force=tif_str,
            aon=aon,
            min_fill_size=min_fill_size,
            expiry_time=expiry_time,
            correlation_id_bytes=corr_id,
            timestamp=_timestamp_ns(),
        )

        ack = await self._send_encrypted_order(
            "place",
            symbol_id,
            plaintext,
            corr_id,
            client_order_id=client_order_id,
        )

        # After local decrypt of the ack we know the assigned order_id. Two things:
        # 1. Cache it in our local coid index so cancel_order_by_client_id can encrypt
        #    with the real order_id (sequencer requires the real id inside the ciphertext).
        # 2. Push the cleartext routing mapping back to the edge so other clients
        #    (and edge-side ?client_order_id= resolution) work too. Best-effort.
        if client_order_id and ack.success and ack.order_id:
            self._local_coid_index[client_order_id] = str(ack.order_id)
            try:
                if not self._bearer:
                    raise SessionError("not authenticated – call connect() first")
                await self._http.register_client_order_mapping(
                    bearer=self._bearer,
                    client_order_id=client_order_id,
                    order_id=str(ack.order_id),
                )
            except Exception as e:  # noqa: BLE001 — registration is best-effort
                _LOG.warning(
                    "register_client_order_mapping failed for coid=%s order_id=%s: %s",
                    client_order_id,
                    ack.order_id,
                    e,
                )

        return ack

    async def cancel_order(self, order_id: str, symbol: str = "BTC-USDC-PERP") -> OrderAck:
        symbol_id = self._resolve_symbol(symbol)
        corr_id = _new_correlation_id()
        plaintext = _proto.build_cancel_order_proto(
            order_id=int(order_id),
            user_uuid=self._user_uuid_bytes(),
            symbol_id=symbol_id,
            correlation_id_bytes=corr_id,
        )
        return await self._send_encrypted(
            "cancel",
            symbol_id,
            plaintext,
            corr_id,
            route="delete",
            order_id=str(order_id),
        )

    async def cancel_order_by_client_id(
        self, client_order_id: str, symbol: str = "BTC-USDC-PERP"
    ) -> OrderAck:
        """Cancel by client-supplied idempotency key.

        Resolution order (Zone A: SDK is the only party that decrypted the place ACK
        and therefore knows the real ``order_id`` for this ``client_order_id``):

        1. Local in-memory index populated by :meth:`place_order`.
        2. Edge-side ``GET /api/v1/orders?client_order_id=`` (returns the routing key).
        3. Otherwise raise — SDK cannot encrypt a cancel with a sentinel id since the
           sequencer matches against the real id inside the ciphertext.
        """
        order_id = self._local_coid_index.get(client_order_id)
        if order_id is None:
            if not self._bearer:
                raise SessionError("not authenticated – call connect() first")
            try:
                row = await self._http.get_order_by_client_order_id(
                    bearer=self._bearer,
                    client_order_id=client_order_id,
                )
                order_id = str(row.get("order_id") or "")
            except RestEnvelopeError:
                order_id = ""
            if not order_id:
                raise OrderError(f"unknown client_order_id: {client_order_id}")
            self._local_coid_index[client_order_id] = order_id
        return await self.cancel_order(order_id, symbol=symbol)

    async def modify_order(
        self,
        order_id: str,
        symbol: str = "BTC-USDC-PERP",
        *,
        new_price: float | None = None,
        new_quantity: float | None = None,
    ) -> OrderAck:
        symbol_id = self._resolve_symbol(symbol)
        corr_id = _new_correlation_id()
        plaintext = _proto.build_modify_order_proto(
            order_id=int(order_id),
            user_uuid=self._user_uuid_bytes(),
            symbol_id=symbol_id,
            new_price=new_price,
            new_quantity=new_quantity,
            correlation_id_bytes=corr_id,
        )
        return await self._send_encrypted(
            "modify",
            symbol_id,
            plaintext,
            corr_id,
            route="patch",
            order_id=str(order_id),
        )

    async def get_order(self, order_id: str) -> dict[str, Any]:
        if not self._bearer:
            raise SessionError("not authenticated – call connect() first")
        return await self._http.get_order(bearer=self._bearer, order_id=order_id)

    async def get_order_by_client_id(self, client_order_id: str) -> dict[str, Any]:
        if not self._bearer:
            raise SessionError("not authenticated – call connect() first")
        return await self._http.get_order_by_client_order_id(
            bearer=self._bearer,
            client_order_id=client_order_id,
        )

    async def await_terminal_status(
        self,
        order_id: str,
        *,
        timeout_sec: float = 120.0,
        poll_interval_sec: float = 0.25,
    ) -> dict[str, Any]:
        deadline = asyncio.get_event_loop().time() + timeout_sec
        terminal = {"FILLED", "CANCELLED", "REJECTED"}
        while asyncio.get_event_loop().time() < deadline:
            row = await self.get_order(order_id)
            st = str(row.get("status", "")).upper()
            if st in terminal:
                return row
            await asyncio.sleep(poll_interval_sec)
        raise TimeoutError(f"order {order_id} did not reach terminal status within {timeout_sec}s")

    async def get_funding_rates(self) -> list[Any]:
        """``GET /api/v1/market-data/funding-rates`` — public snapshot (no connect required)."""
        return await self._http.get_funding_rates()

    async def get_open_interest(self) -> list[Any]:
        """``GET /api/v1/market-data/open-interest`` — public snapshot (no connect required)."""
        return await self._http.get_open_interest()

    async def get_volume(self) -> dict[str, Any]:
        """``GET /api/v1/market-data/volume`` — public 24h volume snapshot (no connect required)."""
        return await self._http.get_volume()

    async def update_leverage(self, symbol: str, leverage: int) -> OrderAck:
        """Send encrypted leverage update via ``POST /api/v1/leverage``."""
        symbol_id = self._resolve_symbol(symbol)
        lev = max(1, int(leverage))
        corr_id = _new_correlation_id()
        plaintext = _proto.build_update_leverage_proto(
            user_uuid=self._user_uuid_bytes(),
            symbol_id=symbol_id,
            leverage=lev,
            correlation_id_bytes=corr_id,
        )
        return await self._send_encrypted(
            "update_leverage",
            symbol_id,
            plaintext,
            corr_id,
            route="post_leverage",
            header_leverage=lev,
        )

    async def mass_quote(
        self,
        symbol: str,
        legs: list[dict[str, Any]],
        *,
        leverage: int = 1,
        post_only: bool | None = None,
    ) -> MassQuoteAck:
        """Bulk cancel-replace via encrypted ``POST /api/v1/orders/massQuote``."""
        symbol_id = self._resolve_symbol(symbol)
        corr_id = _new_correlation_id()
        plaintext = _proto.build_mass_quote_proto(
            symbol_id=symbol_id,
            user_uuid=self._user_uuid_bytes(),
            legs=legs,
            correlation_id_bytes=corr_id,
            leverage=leverage,
            post_only=post_only,
        )
        sealed, raw = await self._send_encrypted_envelope(
            "mass_quote",
            symbol_id,
            plaintext,
            corr_id,
            route="post_path",
            post_path="/api/v1/orders/massQuote",
        )
        return self._parse_mass_quote_rest(raw, sealed)

    async def batch_cancel(self, symbol: str, order_ids: list[int]) -> BatchCancelAck:
        """Cancel up to 20 resting orders via encrypted ``POST /api/v1/orders``."""
        symbol_id = self._resolve_symbol(symbol)
        corr_id = _new_correlation_id()
        plaintext = _proto.build_batch_cancel_proto(
            symbol_id=symbol_id,
            user_uuid=self._user_uuid_bytes(),
            order_ids=order_ids,
            correlation_id_bytes=corr_id,
        )
        sealed, raw = await self._send_encrypted_envelope(
            "batch_cancel",
            symbol_id,
            plaintext,
            corr_id,
            route="post_orders",
        )
        return self._parse_batch_cancel_rest(raw, sealed)

    async def batch_modify(self, symbol: str, legs: list[dict[str, Any]]) -> BatchModifyAck:
        """Amend up to 20 resting orders via encrypted ``POST /api/v1/orders``."""
        symbol_id = self._resolve_symbol(symbol)
        corr_id = _new_correlation_id()
        plaintext = _proto.build_batch_modify_proto(
            symbol_id=symbol_id,
            user_uuid=self._user_uuid_bytes(),
            legs=legs,
            correlation_id_bytes=corr_id,
        )
        sealed, raw = await self._send_encrypted_envelope(
            "batch_modify",
            symbol_id,
            plaintext,
            corr_id,
            route="post_orders",
        )
        return self._parse_batch_modify_rest(raw, sealed)

    def _parse_mass_quote_rest(self, raw: dict[str, Any], sealed: SealedSession) -> MassQuoteAck:
        plaintext = self._decrypt_rest_plaintext(raw, sealed)
        parsed = _proto.parse_mass_quote_ack(plaintext)
        if parsed.get("type") != "mass_quote_ack":
            reject = _proto.parse_node_response(plaintext)
            if reject.get("type") == "ack" and not reject.get("success", True):
                raise make_order_error_from_json(
                    reject.get("reject_text") or reject.get("message"),
                    str(reject["error_code"]) if reject.get("error_code") is not None else None,
                )
            raise OrderError(f"expected mass_quote_ack, got {parsed.get('type')}")
        results = [
            MassQuoteLegResult(
                leg_index=r["leg_index"],
                status=r["status"],
                cancelled_order_id=(
                    str(r["cancelled_order_id"]) if r.get("cancelled_order_id") else None
                ),
                new_order_id=str(r["new_order_id"]) if r.get("new_order_id") else None,
                error_code=r.get("error_code"),
                fill_count=r.get("fill_count", 0),
            )
            for r in parsed.get("results", [])
        ]
        success = bool(results) and all(r.status != "failed" for r in results)
        return MassQuoteAck(
            success=success,
            sequence=str(parsed.get("sequence", "")),
            results=results,
        )

    def _parse_batch_cancel_rest(
        self, raw: dict[str, Any], sealed: SealedSession
    ) -> BatchCancelAck:
        plaintext = self._decrypt_rest_plaintext(raw, sealed)
        parsed = _proto.parse_batch_cancel_ack(plaintext)
        if parsed.get("type") != "batch_cancel_ack":
            reject = _proto.parse_node_response(plaintext)
            if reject.get("type") == "ack" and not reject.get("success", True):
                raise make_order_error_from_json(
                    reject.get("reject_text") or reject.get("message"),
                    str(reject["error_code"]) if reject.get("error_code") is not None else None,
                )
            raise OrderError(f"expected batch_cancel_ack, got {parsed.get('type')}")
        results = [
            BatchCancelLegResult(
                order_id=str(r["order_id"]),
                cancelled=r["cancelled"],
                error_code=r.get("error_code"),
            )
            for r in parsed.get("results", [])
        ]
        success = bool(results) and all(r.cancelled for r in results)
        return BatchCancelAck(
            success=success,
            sequence=str(parsed.get("sequence", "")),
            results=results,
        )

    def _parse_batch_modify_rest(
        self, raw: dict[str, Any], sealed: SealedSession
    ) -> BatchModifyAck:
        plaintext = self._decrypt_rest_plaintext(raw, sealed)
        parsed = _proto.parse_batch_modify_ack(plaintext)
        if parsed.get("type") != "batch_modify_ack":
            reject = _proto.parse_node_response(plaintext)
            if reject.get("type") == "ack" and not reject.get("success", True):
                raise make_order_error_from_json(
                    reject.get("reject_text") or reject.get("message"),
                    str(reject["error_code"]) if reject.get("error_code") is not None else None,
                )
            raise OrderError(f"expected batch_modify_ack, got {parsed.get('type')}")
        results = [
            BatchModifyLegResult(
                order_id=str(r["order_id"]),
                modified=r["modified"],
                error_code=r.get("error_code"),
            )
            for r in parsed.get("results", [])
        ]
        success = bool(results) and all(r.modified for r in results)
        return BatchModifyAck(
            success=success,
            sequence=str(parsed.get("sequence", "")),
            results=results,
        )
