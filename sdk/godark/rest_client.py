"""REST-only trading client — ``auth`` → ``session.setup`` → encrypted ``/orders`` (docs §base-urls)."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
import uuid
from typing import Any

from . import _proto
from ._identity import bytes_to_uuid
from ._rest_transport import RestEnvelopeError, RestTransport
from ._session import CryptoSession
from ._symbols import load_default_symbol_map
from .enums import OrderType, Side, TimeInForce
from .errors import EncryptionError, OrderError, SessionError, TimeoutError
from .types import OrderAck

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
    return "https://api.godarkdex.com"


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


def _new_correlation_id() -> bytes:
    return uuid.uuid4().bytes


def _timestamp_ns() -> int:
    return int(time.time() * 1_000_000_000)


class GodarkRestClient:
    """
    Docs-aligned REST trading client (no WebSocket).

    Same crypto session + protobuf builders as :class:`~godark.client.GodarkClient`,
    but uses ``POST /api/v1/auth/token``, ``POST /api/v1/session/setup``, and encrypted HTTP verbs on ``/orders``.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        api_key_id: str | None = None,
        api_secret: str | None = None,
        rest_base_url: str | None = None,
        symbol_map: dict[str, int] | None = None,
    ):
        if api_key_id is not None or api_secret is not None:
            if api_key_id is None or api_secret is None:
                raise ValueError("api_key_id and api_secret must be provided together")
            if api_key is not None:
                raise ValueError("use either api_key or (api_key_id, api_secret), not both")
            self._auth_token = f"{api_key_id}:{api_secret}"
        elif api_key is not None:
            self._auth_token = api_key
        else:
            raise ValueError("provide api_key or both api_key_id and api_secret")

        self._rest_base = _resolve_rest_base_url(rest_base_url)
        self._symbol_map = dict(symbol_map) if symbol_map is not None else load_default_symbol_map()
        self._session = CryptoSession()
        self._http = RestTransport(self._rest_base)
        self._bearer: str | None = None
        self._user_uuid: str | None = None
        # Local routing index: client_order_id -> assigned order_id. Populated when this
        # SDK instance places an order with a client_order_id (after decrypting the ack).
        # Used by cancel_order_by_client_id to encrypt with the *real* order_id, since
        # the encrypted body must contain the real id (the edge only routes, doesn't decrypt).
        self._local_coid_index: dict[str, str] = {}

    @property
    def bearer_token(self) -> str | None:
        return self._bearer

    @property
    def session(self) -> CryptoSession:
        return self._session

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
        # JWT mint
        if ":" in self._auth_token:
            kid, sec = self._auth_token.split(":", 1)
            auth_data = await self._http.auth_token(
                grant_type="client_credentials",
                client_id=kid,
                client_secret=sec,
                passphrase=os.environ.get("GDX_PASSPHRASE", ""),
            )
        else:
            auth_data = await self._http.auth_token(token=self._auth_token)

        self._bearer = auth_data.get("access_token") or auth_data.get("token")
        if not self._bearer:
            raise SessionError("auth/token missing access_token/token")
        self._user_uuid = auth_data.get("user_uuid")

        # ECDH
        client_pk_b64 = self._session.generate_keypair()
        assert self._bearer is not None
        sess = await self._http.session_setup(bearer=self._bearer, client_ecdh_pubkey=client_pk_b64)
        seq_pk = sess.get("server_ecdh_pubkey") or sess.get("sequencer_ecdh_pubkey")
        sid = sess.get("session_id")
        if isinstance(sid, str):
            sid = int(sid)
        if not seq_pk or sid is None:
            raise SessionError("session/setup missing server_ecdh_pubkey or session_id")
        self._session.establish(str(seq_pk), int(sid))

    async def disconnect(self) -> None:
        try:
            if self._bearer:
                await self._http.revoke_token(bearer=self._bearer)
        finally:
            self._bearer = None
            self._user_uuid = None
            self._session.reset()
            await self._http.aclose()

    async def __aenter__(self) -> GodarkRestClient:
        await self.connect()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.disconnect()

    async def _send_encrypted_order(
        self,
        request_type: str,
        symbol_id: int,
        plaintext: bytes,
        correlation_id: bytes = b"",
        *,
        client_order_id: str | None = None,
    ) -> OrderAck:
        body_length = len(plaintext) + _GCM_TAG_LEN
        nonce_counter = self._session.next_nonce

        aad = _proto.build_order_header_aad(
            user_uuid=self._user_uuid_bytes(),
            symbol_id=symbol_id,
            request_type_str=request_type,
            nonce=nonce_counter,
            body_length=body_length,
            correlation_id=correlation_id,
        )

        try:
            actual_nonce, ciphertext = self._session.encrypt_order(aad, plaintext)
        except Exception as e:
            raise EncryptionError(f"Failed to encrypt order: {e}") from e

        body_b64 = base64.b64encode(ciphertext).decode("ascii")
        corr_id_str = bytes_to_uuid(correlation_id) if len(correlation_id) == 16 else ""
        header_obj: dict[str, Any] = {
            "symbol_id": symbol_id,
            "request_type": request_type,
            "nonce": actual_nonce,
            "body_length": body_length,
            "correlation_id": corr_id_str,
        }
        payload: dict[str, Any] = {"header": header_obj, "ciphertext": body_b64}
        if client_order_id:
            payload["client_order_id"] = client_order_id

        assert self._bearer is not None
        raw = await self._http.post_encrypted_order(bearer=self._bearer, body=payload)
        return self._parse_ack(raw)

    def _parse_ack(self, raw: dict[str, Any]) -> OrderAck:
        # Encrypted ACK (Mradul's Zone A: edge never decrypts). Same shape as WS encrypted_push;
        # decrypt with the session key and reuse the cleartext AckMessage parser.
        if raw.get("encrypted") or raw.get("encrypted_body"):
            return self._decrypt_rest_ack(raw)
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

    def _decrypt_rest_ack(self, msg: dict[str, Any]) -> OrderAck:
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
        )
        try:
            plaintext = self._session.decrypt_push(nonce, aad, ct)
        except Exception as e:
            raise EncryptionError(f"Failed to decrypt REST ack: {e}") from e
        ack_dict = _proto.parse_node_response(plaintext)
        if ack_dict.get("type") != "ack":
            raise OrderError(f"Expected ack, got {ack_dict.get('type')}")
        if not ack_dict.get("success"):
            raise OrderError(
                "order rejected",
                error_code=str(ack_dict.get("error_code", "")),
            )
        return OrderAck(
            order_id=str(ack_dict.get("order_id", "")),
            success=True,
            sequence=str(ack_dict.get("sequence", "")),
        )

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
                assert self._bearer is not None
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
        assert self._bearer is not None
        body_length = len(plaintext) + _GCM_TAG_LEN
        nonce_counter = self._session.next_nonce
        aad = _proto.build_order_header_aad(
            user_uuid=self._user_uuid_bytes(),
            symbol_id=symbol_id,
            request_type_str="cancel",
            nonce=nonce_counter,
            body_length=body_length,
            correlation_id=corr_id,
        )
        actual_nonce, ciphertext = self._session.encrypt_order(aad, plaintext)
        body_b64 = base64.b64encode(ciphertext).decode("ascii")
        corr_id_str = bytes_to_uuid(corr_id) if len(corr_id) == 16 else ""
        header_obj = {
            "symbol_id": symbol_id,
            "request_type": "cancel",
            "nonce": actual_nonce,
            "body_length": body_length,
            "correlation_id": corr_id_str,
        }
        raw = await self._http.delete_encrypted_order(
            bearer=self._bearer,
            order_id=str(order_id),
            body={"header": header_obj, "ciphertext": body_b64},
        )
        return self._parse_ack(raw)

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
            assert self._bearer is not None
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
        body_length = len(plaintext) + _GCM_TAG_LEN
        nonce_counter = self._session.next_nonce
        aad = _proto.build_order_header_aad(
            user_uuid=self._user_uuid_bytes(),
            symbol_id=symbol_id,
            request_type_str="modify",
            nonce=nonce_counter,
            body_length=body_length,
            correlation_id=corr_id,
        )
        actual_nonce, ciphertext = self._session.encrypt_order(aad, plaintext)
        body_b64 = base64.b64encode(ciphertext).decode("ascii")
        corr_id_str = bytes_to_uuid(corr_id) if len(corr_id) == 16 else ""
        header_obj = {
            "symbol_id": symbol_id,
            "request_type": "modify",
            "nonce": actual_nonce,
            "body_length": body_length,
            "correlation_id": corr_id_str,
        }
        assert self._bearer is not None
        raw = await self._http.patch_encrypted_order(
            bearer=self._bearer,
            order_id=str(order_id),
            body={"header": header_obj, "ciphertext": body_b64},
        )
        return self._parse_ack(raw)

    async def get_order(self, order_id: str) -> dict[str, Any]:
        assert self._bearer is not None
        return await self._http.get_order(bearer=self._bearer, order_id=order_id)

    async def get_order_by_client_id(self, client_order_id: str) -> dict[str, Any]:
        assert self._bearer is not None
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
