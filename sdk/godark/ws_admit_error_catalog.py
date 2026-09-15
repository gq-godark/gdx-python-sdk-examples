"""Build WS admit error catalog rows from proto identity + hand-authored reasons."""

from __future__ import annotations

from godark._generated.gdx.common.v1 import error_catalog_pb2 as ec

from .ws_admit_error_reasons import WS_ADMIT_ERROR_REASONS


def _symbolic(proto_name: str) -> str:
    return proto_name.removeprefix("WS_ADMIT_ERROR_CODE_")


def _ws_admit_rows() -> dict[int, tuple[str, str]]:
    rows: dict[int, tuple[str, str]] = {}
    for name, value in ec.WsAdmitErrorCode.items():
        if name == "WS_ADMIT_ERROR_CODE_UNSPECIFIED":
            continue
        symbolic = _symbolic(name)
        rows[int(value)] = (
            symbolic,
            WS_ADMIT_ERROR_REASONS.get(symbolic, "request rejected"),
        )
    return rows


WS_ADMIT_ERROR_CODE_ROWS: dict[int, tuple[str, str]] = _ws_admit_rows()
