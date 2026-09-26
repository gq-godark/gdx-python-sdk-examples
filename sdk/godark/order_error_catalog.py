"""Build order error catalog rows from proto identity + hand-authored reasons."""

from __future__ import annotations

from dataclasses import dataclass

from godark._generated.gdx.common.v1 import error_catalog_pb2 as ec

from .order_error_reasons import ORDER_ERROR_REASONS


@dataclass(frozen=True)
class OrderErrorEntry:
    code: int
    symbolic: str
    reason: str


def _symbolic(proto_name: str) -> str:
    return proto_name.removeprefix("ORDER_ERROR_CODE_")


def _order_catalog_entries() -> tuple[OrderErrorEntry, ...]:
    entries: list[OrderErrorEntry] = []
    for name, value in ec.OrderErrorCode.items():
        if name == "ORDER_ERROR_CODE_UNSPECIFIED":
            continue
        symbolic = _symbolic(name)
        entries.append(
            OrderErrorEntry(
                int(value),
                symbolic,
                ORDER_ERROR_REASONS.get(symbolic, "order rejected"),
            )
        )
    return tuple(entries)


ORDER_ERROR_CODES: tuple[OrderErrorEntry, ...] = _order_catalog_entries()
