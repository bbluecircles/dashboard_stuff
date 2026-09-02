"""YYYYMM window arithmetic. No database access."""

from __future__ import annotations


def add_months(yyyymm: int, delta: int) -> int:
    """Shift a YYYYMM period by `delta` calendar months."""
    year, month = divmod(int(yyyymm), 100)
    if not 1 <= month <= 12:
        raise ValueError(f"Invalid YYYYMM: {yyyymm}")
    idx = year * 12 + (month - 1) + int(delta)
    if idx < 0:
        raise ValueError(f"Month underflow: {yyyymm} + {delta}")
    new_year, new_month = divmod(idx, 12)
    return new_year * 100 + (new_month + 1)


def iter_period_codes(start: int, end: int) -> list[int]:
    """Inclusive YYYYMM months from start through end."""
    periods: list[int] = []
    current = int(start)
    stop = int(end)
    while current <= stop:
        periods.append(current)
        current = add_months(current, 1)
    return periods


def shift_window(start: int, end: int, months: int = 1) -> tuple[int, int]:
    return add_months(start, months), add_months(end, months)


def prior_window(start: int, end: int, years: int = 1) -> tuple[int, int]:
    delta = -12 * int(years)
    return add_months(start, delta), add_months(end, delta)


def usable_window(
    warehouse_max: int,
    *,
    lag_months: int = 2,
    length: int = 12,
) -> tuple[int, int]:
    """Latest complete 12-month window: drop the last `lag_months` warehouse periods."""
    end = add_months(int(warehouse_max), -int(lag_months))
    start = add_months(end, -(int(length) - 1))
    return start, end


def slide_diff(
    current_start: int,
    current_end: int,
    target_start: int,
    target_end: int,
) -> tuple[list[int], list[int]]:
    """Periods to drop from the current window and add to reach the target window."""
    current = set(iter_period_codes(current_start, current_end))
    target = set(iter_period_codes(target_start, target_end))
    drop = sorted(current - target)
    add = sorted(target - current)
    return drop, add
