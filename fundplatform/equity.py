"""
Equity: the fund's stakes in portfolio companies and the dividends they pay (FR-EQT-001, FR-EQT-003).

  dividends_by_year()   what the fund was declared and what it received, per fiscal year
  income()              this year's dividends, projected (declared) vs actual (received), and what's overdue
  baseline_forecast()   next years' dividends if each company pays what it paid last time: the
                        "same as last year" baseline that the Day 4 model (FR-EQT-005) must beat

    from fundplatform import equity
    equity.income(reg["dividends"], reg["equity_holdings"], year=2026, as_of="2026-10-21")
"""
from __future__ import annotations

import pandas as pd


def dividends_by_year(dividends: pd.DataFrame) -> pd.DataFrame:
    """Per fiscal year: companies paying, the fund's share declared and received (RWF m)."""
    d = dividends.groupby("fiscal_year").agg(companies_paying=("company_id", "nunique"),
                                              declared_rwf_m=("fund_share_rwf_m", "sum"),
                                              received_rwf_m=("received_rwf_m", "sum"))
    return d.round(1)


def income(dividends: pd.DataFrame, holdings: pd.DataFrame, year: int, as_of) -> pd.DataFrame:
    """Dividends due to the fund in `year` (by payment date): projected (declared) vs actual (received).
    status: received · due (payment date still ahead) · OVERDUE (payment date passed, nothing received)."""
    as_of = pd.Timestamp(as_of)
    d = dividends[dividends.pay_date.dt.year == year].merge(
        holdings[["company_id", "company_name", "sector", "country"]], on="company_id", how="left")
    received = d.received_rwf_m.fillna(0)
    d["status"] = "received"
    d.loc[received < d.fund_share_rwf_m, "status"] = "due"
    d.loc[(received < d.fund_share_rwf_m) & (d.pay_date < as_of), "status"] = "OVERDUE"
    d["days_overdue"] = ((as_of - d.pay_date).dt.days).where(d.status == "OVERDUE", 0)
    d["outstanding_rwf_m"] = (d.fund_share_rwf_m - received).round(1)
    cols = ["company_id", "company_name", "sector", "country", "fiscal_year", "declared_on", "pay_date",
            "fund_share_rwf_m", "received_rwf_m", "outstanding_rwf_m", "status", "days_overdue"]
    return d[cols].sort_values(["status", "outstanding_rwf_m"], ascending=[True, False]).reset_index(drop=True)


def baseline_forecast(dividends: pd.DataFrame, holdings: pd.DataFrame, years: int = 3) -> pd.DataFrame:
    """The naive multi-year forecast: each company pays the fund what it paid for its latest fiscal year,
    every year (0 if it paid nothing that year). One row per company, one column per future fiscal year."""
    last_fy = int(dividends.fiscal_year.max())
    last = dividends[dividends.fiscal_year == last_fy].set_index("company_id").fund_share_rwf_m
    f = holdings[["company_id", "company_name", "sector"]].copy()
    for k in range(1, years + 1):
        f[f"FY{last_fy + k}"] = f.company_id.map(last).fillna(0.0).round(1)
    return f.sort_values(f"FY{last_fy + 1}", ascending=False).reset_index(drop=True)
