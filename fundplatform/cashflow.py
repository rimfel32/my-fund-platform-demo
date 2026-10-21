"""
Cash vs accrual income, the 12-month master cash-flow plan, and budget vs actual variance.

  cash income      interest actually paid in the year (by pay date)                       FR-BUD-002
  accrual income   interest EARNED in the year, whenever it is paid (days in the year)     FR-CBN-007, FR-SBL-007
  accrued at YE    interest earned by 31 Dec but paid next year (a receivable)           FR-FTD-004

The master plan (FR-MCF-001/002) is on a ROLLOVER BASIS: principal that matures is assumed to be
reinvested at once, so it appears as an inflow and an equal outflow; income and debt service change
the cash balance. All amounts in the outputs are RWF millions.
"""
from __future__ import annotations

import pandas as pd

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
M = 1_000_000


def _overlap_days(start, end, lo, hi) -> pd.Series:
    """Days of each [start, end) period that fall inside [lo, hi)."""
    s = start.where(start > lo, lo)
    e = end.where(end < hi, hi)
    return (e - s).dt.days.clip(lower=0)


def income_split(events: pd.DataFrame, year: int, amount_col: str = "amount_rwf") -> pd.DataFrame:
    """Cash vs accrual interest income for one year, per instrument (incoming interest only)."""
    lo, hi = pd.Timestamp(f"{year}-01-01"), pd.Timestamp(f"{year + 1}-01-01")
    i = events[(events.flow == "interest") & (events.direction == "in")].copy()
    days = (i.period_end - i.period_start).dt.days
    i["cash"] = i[amount_col].where((i.pay_date >= lo) & (i.pay_date < hi), 0.0)
    i["accrual"] = i[amount_col] * _overlap_days(i.period_start, i.period_end, lo, hi) / days
    spans_ye = (i.period_start < hi) & (i.period_end >= hi)
    # earned from the period start up to 31 Dec, but paid next year
    i["accrued_at_ye"] = (i[amount_col] * (hi - i.period_start).dt.days / days).where(spans_ye, 0.0)
    out = i.groupby(["asset_class", "instrument_id"], as_index=False)[["cash", "accrual", "accrued_at_ye"]].sum()
    return out[(out[["cash", "accrual", "accrued_at_ye"]].abs().sum(axis=1)) > 0]


def master_cashflow(events: pd.DataFrame, year: int, opening_cash_rwf: float,
                    amount_col: str = "amount_rwf") -> pd.DataFrame:
    """The 12-column master cash-flow plan (rollover basis), RWF millions, one column per month."""
    e = events[events.pay_date.dt.year == year].copy()
    e["month"] = e.pay_date.dt.month
    rows = {}
    income = e[(e.flow == "interest") & (e.direction == "in")]
    for cls, g in income.groupby("asset_class"):
        rows[f"Income: {cls.replace('_', ' ')}"] = g.groupby("month")[amount_col].sum()
    matured = e[(e.flow == "principal") & (e.direction == "in")].groupby("month")[amount_col].sum()
    rows["Principal maturing"] = matured
    rows["Reinvested (rollover basis)"] = -matured
    rows["Debt service (borrowings)"] = -e[e.direction == "out"].groupby("month")[amount_col].sum()
    plan = pd.DataFrame(rows).T.reindex(columns=range(1, 13)).fillna(0.0)
    net = plan.sum()
    closing = opening_cash_rwf + net.cumsum()
    opening = closing - net
    plan.loc["Net cash flow"] = net
    plan.loc["Opening balance"] = opening
    plan.loc["Closing balance"] = closing
    order = ["Opening balance"] + [r for r in plan.index if r.startswith("Income")] + \
            ["Principal maturing", "Reinvested (rollover basis)", "Debt service (borrowings)", "Net cash flow",
             "Closing balance"]
    plan = (plan.loc[order] / M).round(1)
    plan.columns = MONTHS
    return plan


def monthly_variance(receipts: pd.DataFrame, year: int, through_month: int, threshold: float = 0.10,
                     amount_rwf: bool = True) -> pd.DataFrame:
    """Budget (scheduled, by due month) vs actual (received, by month received), RWF millions (FR-MCF-003/006).
    A month is flagged when actual collections fall more than `threshold` below budget."""
    r = receipts[receipts.currency == "RWF"] if amount_rwf else receipts
    budget = r.groupby(r.pay_date.dt.month)["scheduled"].sum()
    got = r[r.received_on.dt.year == year]
    actual = got.groupby(got.received_on.dt.month)["received"].sum()
    v = pd.DataFrame({"budget": budget, "actual": actual}).reindex(range(1, through_month + 1)).fillna(0.0) / M
    v["variance"] = v.actual - v.budget
    v["variance_pct"] = (100 * v.variance / v.budget).round(1)
    v["flag"] = v.actual < (1 - threshold) * v.budget
    v.index = [MONTHS[m - 1] for m in v.index]
    return v.round(1)
