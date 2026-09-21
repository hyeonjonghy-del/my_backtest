"""Detection of external cash changes between read-only account snapshots."""

from __future__ import annotations

import math
from collections.abc import Mapping


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def detect_external_cash_flow(
    previous_snapshot: object,
    current_snapshot: object,
    symbols: tuple[str, ...],
    *,
    cash_tolerance: float = 0.01,
    share_tolerance: float = 1e-9,
) -> float:
    """Return a cash-only account change, or zero when it is not identifiable.

    A cash change with unchanged strategy holdings is treated as an external
    deposit or withdrawal. If share quantities changed at the same time, the
    difference may instead be an order fill, so automatic resynchronization is
    deliberately disabled and the manual recovery control remains available.
    """
    if not isinstance(previous_snapshot, Mapping) or not isinstance(current_snapshot, Mapping):
        return 0.0

    previous_cash = _number(previous_snapshot.get("cash"))
    current_cash = _number(current_snapshot.get("cash"))
    if previous_cash is None or current_cash is None:
        return 0.0

    previous_shares = previous_snapshot.get("shares")
    current_shares = current_snapshot.get("shares")
    if not isinstance(previous_shares, Mapping) or not isinstance(current_shares, Mapping):
        return 0.0

    for symbol in symbols:
        old_quantity = _number(previous_shares.get(symbol, 0.0))
        new_quantity = _number(current_shares.get(symbol, 0.0))
        if old_quantity is None or new_quantity is None:
            return 0.0
        if not math.isclose(old_quantity, new_quantity, rel_tol=0.0, abs_tol=share_tolerance):
            return 0.0

    cash_flow = current_cash - previous_cash
    return cash_flow if abs(cash_flow) > cash_tolerance else 0.0
