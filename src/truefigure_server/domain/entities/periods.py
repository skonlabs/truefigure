"""Period grammar helpers: YYYY-MM and YYYY-Qn -> date bounds (spec §Money and periods)."""

from __future__ import annotations

import re
from datetime import date

_MONTH = re.compile(r"^([0-9]{4})-(0[1-9]|1[0-2])$")
_QUARTER = re.compile(r"^([0-9]{4})-q([1-4])$")


def period_bounds(period: str) -> tuple[date, date]:
    """Return [start, end] inclusive dates for a period string."""
    m = _MONTH.match(period)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        start = date(y, mo, 1)
        end = date(y + 1, 1, 1) if mo == 12 else date(y, mo + 1, 1)
        return start, _prev_day(end)
    q = _QUARTER.match(period)
    if q:
        y, qn = int(q.group(1)), int(q.group(2))
        start_month = (qn - 1) * 3 + 1
        start = date(y, start_month, 1)
        end_month = start_month + 3
        end = date(y + 1, 1, 1) if end_month > 12 else date(y, end_month, 1)
        return start, _prev_day(end)
    raise ValueError(f"bad period {period!r}")


def _prev_day(d: date) -> date:
    from datetime import timedelta

    return d - timedelta(days=1)
