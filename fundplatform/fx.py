"""
Exchange rates, point-in-time (FR-FXM-002, FR-UTD-002/003/004).

Two questions that sound alike but are not:
  rate IN EFFECT on a date   the latest rate dated on or before that date (merge_asof, as on Day 1)
  rate AS KNOWN on a date    what the desk's table said at that moment, before later corrections

A report produced on 30 June must be reproducible later with the rates as known on 30 June,
even after a typo was corrected on 2 July. That's why every rate row carries `entered_on`.
"""
from __future__ import annotations

import pandas as pd


def rates_as_known(fx: pd.DataFrame, known_on=None) -> pd.DataFrame:
    """The rate table as it stood on `known_on` (None = today, all corrections applied):
    one row per rate_date, the most recently entered value, only rows entered by then."""
    t = fx if known_on is None else fx[fx.entered_on <= pd.Timestamp(known_on)]
    return (t.sort_values(["rate_date", "entered_on"]).drop_duplicates("rate_date", keep="last")
            [["rate_date", "usd_rwf"]].reset_index(drop=True))


def rate_on(table: pd.DataFrame, dates) -> pd.Series:
    """The rate in effect on each date: latest rate_date on or before it. NaN before the first rate."""
    d = pd.DataFrame({"date": pd.to_datetime(pd.Series(dates))}).reset_index()
    m = pd.merge_asof(d.sort_values("date"), table.sort_values("rate_date"),
                      left_on="date", right_on="rate_date", direction="backward")
    return m.set_index("index").sort_index()["usd_rwf"]


def to_rwf(events: pd.DataFrame, table: pd.DataFrame, date_col: str = "pay_date") -> pd.Series:
    """Every event's amount in RWF: RWF as is, USD at the rate in effect on the event's date (FR-FXM-003)."""
    rate = rate_on(table, events[date_col]).set_axis(events.index)
    return events["amount"].where(events["currency"] == "RWF", events["amount"] * rate)


def usd_deposit_fx(deposits: pd.DataFrame, table: pd.DataFrame, as_of) -> pd.DataFrame:
    """For each USD deposit: RWF value at the value date, at maturity (or as_of if not yet matured),
    and the FX gain or loss on the principal (FR-UTD-004, FR-FXM-004)."""
    usd = deposits[deposits.currency == "USD"].copy()
    as_of = pd.Timestamp(as_of)
    usd["end_date"] = usd.maturity_date.where(usd.maturity_date <= as_of, as_of)
    usd["rate_value_date"] = rate_on(table, usd.value_date).values
    usd["rate_end_date"] = rate_on(table, usd.end_date).values
    usd["rwf_at_value_date"] = usd.amount * usd.rate_value_date
    usd["rwf_at_end_date"] = usd.amount * usd.rate_end_date
    usd["fx_gain_rwf"] = usd.rwf_at_end_date - usd.rwf_at_value_date
    return usd[["deposit_id", "amount", "value_date", "end_date", "rate_value_date", "rate_end_date",
                "rwf_at_value_date", "rwf_at_end_date", "fx_gain_rwf"]].reset_index(drop=True)
