"""Shared calendar selection for overview totals and transaction rows."""

import re
from typing import Annotated, Literal

from fastapi import HTTPException, Query


Period = Annotated[
    Literal["month", "year", "all"],
    Query(description="Legacy period filter, used only when no years are selected."),
]
Years = Annotated[
    list[int] | None,
    Query(description="Repeat selected years. A year without specific months includes its full year."),
]
Months = Annotated[
    list[str] | None,
    Query(description="Selected months as YYYY-MM; each must belong to a selected year."),
]


def transaction_date_filter(
    period: str = "all",
    years: list[int] | None = None,
    months: list[str] | None = None,
) -> tuple[str, list[str]]:
    """A selected year includes all months unless specific months narrow it.

    Selections across years are combined. Explicit years take precedence over
    the legacy period parameter; without selections, period defaults to all.
    Match the stored calendar date, just as the dashboard displays it, rather
    than converting timestamp offsets into UTC and changing its day or month.
    """
    if period not in ("month", "year", "all"):
        raise HTTPException(status_code=422, detail="period must be month, year, or all")
    if any(not isinstance(year, int) or not 1 <= year <= 9999 for year in years or []):
        raise HTTPException(status_code=422, detail="years must be between 1 and 9999")

    selected_years = set(years or [])
    selected_months = set(months or [])
    for month in selected_months:
        if not re.fullmatch(r"[0-9]{4}-(?:0[1-9]|1[0-2])", month):
            raise HTTPException(status_code=422, detail="months must use YYYY-MM")
        if int(month[:4]) not in selected_years:
            raise HTTPException(status_code=422, detail="Each month must belong to a selected year")

    if selected_years:
        narrowed_years = {int(month[:4]) for month in selected_months}
        full_years = sorted(f"{year:04d}" for year in selected_years - narrowed_years)
        clauses = []
        parameters = []
        for length, values in ((4, full_years), (7, sorted(selected_months))):
            if values:
                placeholders = ", ".join("?" for _ in values)
                clauses.append(f"substr(transaction_date, 1, {length}) IN ({placeholders})")
                parameters.extend(values)
        return "(" + " OR ".join(clauses) + ")", parameters

    if period == "month":
        return (
            "transaction_date >= date('now', 'start of month') "
            "AND transaction_date < date('now', 'start of month', '+1 month')",
            [],
        )
    if period == "year":
        return (
            "transaction_date >= date('now', 'start of year') "
            "AND transaction_date < date('now', 'start of year', '+1 year')",
            [],
        )
    return "1", []
