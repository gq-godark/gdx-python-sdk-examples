"""Trading WebSocket binary frames (``TradingWsBinaryFrame``)."""

from __future__ import annotations

import base64
from enum import Enum
from typing import Any

from godark._generated.gdx.edge.v1 import edge_pb2

from ._hpke import WIRE_VERSION
from .enums import request_type_to_proto


class DecodedBinary(Enum):
    ENCRYPTED_PUSH = "encrypted_push"
    ENCRYPTED_ORDER = "encrypted_order"
    HPKE_SETUP = "hpke_setup"
    HPKE_SETUP_REPLY = "hpke_setup_reply"
    IGNORED = "ignored"


def encode_hpke_setup(user_uuid: bytes, conn_id: int, encapped_key: bytes) -> bytes:
    frame = edge_pb2.TradingWsBinaryFrame()
    frame.hpke_setup.user_uuid = user_uuid
    frame.hpke_setup.conn_id = conn_id
    frame.hpke_setup.encapped_key = encapped_key
    return frame.SerializeToString()


def encode_hpke_setup_reply(conn_id: int, established: bool) -> bytes:
    frame = edge_pb2.TradingWsBinaryFrame()
    frame.hpke_setup_reply.conn_id = conn_id
    frame.hpke_setup_reply.established = established
    return frame.SerializeToString()


def encrypted_order_request(
    header: edge_pb2.OrderHeader, encrypted_body: bytes
) -> edge_pb2.EncryptedEdgeRequest:
    return edge_pb2.EncryptedEdgeRequest(
        version=WIRE_VERSION,
        header=header,
        encrypted_body=encrypted_body,
    )


def encode_encrypted_order(req: edge_pb2.EncryptedEdgeRequest) -> bytes:
    frame = edge_pb2.TradingWsBinaryFrame()
    frame.encrypted_order.CopyFrom(req)
    return frame.SerializeToString()


def encode_encrypted_push(resp: edge_pb2.EncryptedEdgeResponse) -> bytes:
    frame = edge_pb2.TradingWsBinaryFrame()
    frame.encrypted_push.CopyFrom(resp)
    return frame.SerializeToString()


_MESSAGE_TYPE_NAMES = {
    1: "order_update",
    2: "system_health",
    3: "ack",
    4: "open_orders_snapshot",
    5: "positions_snapshot",
    6: "balance_and_position",
    7: "account_margin_update",
    8: "mass_quote_ack",
    9: "batch_cancel_ack",
    10: "batch_modify_ack",
    11: "tpsl_update",
    12: "leverage_settings",
}


def encrypted_push_to_json(push: edge_pb2.EncryptedEdgeResponse) -> dict[str, Any] | None:
    if not push.HasField("header"):
        return None
    h = push.header
    message_type = _MESSAGE_TYPE_NAMES.get(h.message_type, "unknown")
    if not h.correlation_id:
        corr: str | None = None
    else:
        buf = bytearray(16)
        n = min(len(h.correlation_id), 16)
        buf[16 - n :] = h.correlation_id[:n]
        corr = f"{int.from_bytes(buf, 'big'):032x}"
    return {
        "type": "encrypted_push",
        "message_type": message_type,
        "encrypted_body": base64.b64encode(push.encrypted_body).decode("ascii"),
        "nonce": h.nonce,
        "fencing_epoch": h.fencing_epoch,
        "correlation_id": corr,
        "session_seq": h.session_seq,
        "conn_id": h.conn_id,
        "body_length": h.body_length,
    }


def decode_binary_frame(data: bytes) -> tuple[DecodedBinary, Any]:
    from google.protobuf.message import DecodeError

    frame = edge_pb2.TradingWsBinaryFrame()
    try:
        frame.ParseFromString(data)
    except DecodeError:
        return DecodedBinary.IGNORED, None
    body = frame.WhichOneof("body")
    if body == "encrypted_push":
        return DecodedBinary.ENCRYPTED_PUSH, frame.encrypted_push
    if body == "hpke_setup_reply":
        return DecodedBinary.HPKE_SETUP_REPLY, frame.hpke_setup_reply
    if body == "encrypted_order":
        return DecodedBinary.ENCRYPTED_ORDER, frame.encrypted_order
    if body == "hpke_setup":
        return DecodedBinary.HPKE_SETUP, frame.hpke_setup
    return DecodedBinary.IGNORED, None


def build_order_header_proto(
    *,
    user_uuid: bytes,
    symbol_id: int,
    request_type_str: str,
    nonce: int,
    body_length: int,
    correlation_id: bytes,
    conn_id: int,
) -> edge_pb2.OrderHeader:
    return edge_pb2.OrderHeader(
        user_uuid=user_uuid,
        symbol_id=symbol_id,
        request_type=request_type_to_proto(request_type_str),
        nonce=nonce,
        body_length=body_length,
        correlation_id=correlation_id,
        conn_id=conn_id,
    )
