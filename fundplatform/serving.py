"""
Serving: the same numbers, delivered two ways (Module 4).

  OFFLINE   full history and schedules as files (Parquet/CSV): board packs, the master plan, analysis.
            Rebuilt in batch (nightly, or when a register changes).
  ONLINE    one row per instrument with what a screen needs NOW: next payment, days to maturity,
            alert band. A key-value lookup in SQLite here (a database table or Redis in production).

Alerts (FR-TBD-003, FR-SPE-003): maturity within 90, 60 or 30 days.
Upcoming collections (FR-RPT-001, FR-RPT-012): cash expected in the next 30/60/90 days.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

M = 1_000_000


def alert_band(days: int | float) -> str:
    """'30', '60', '90' (days to maturity within that window) or '' (no alert)."""
    for limit in (30, 60, 90):
        if 0 <= days <= limit:
            return str(limit)
    return ""


def instrument_status(events: pd.DataFrame, as_of, amount_col: str = "amount_rwf") -> pd.DataFrame:
    """One row per live instrument: next payment date and amount (RWF m), maturity, days left, alert band."""
    as_of = pd.Timestamp(as_of)
    future = events[(events.pay_date > as_of) & (events.direction == "in")]
    nxt = future.groupby("instrument_id").pay_date.min().rename("next_pay_date")
    nxt_amount = (future.merge(nxt, left_on=["instrument_id", "pay_date"], right_on=["instrument_id", "next_pay_date"])
                  .groupby("instrument_id")[amount_col].sum().div(M).round(1).rename("next_amount_rwf_m"))
    maturity = events[events.flow == "principal"].groupby("instrument_id").pay_date.max().rename("maturity_date")
    meta = events.drop_duplicates("instrument_id").set_index("instrument_id")[["asset_class", "counterparty", "currency"]]
    s = meta.join([nxt, nxt_amount, maturity], how="inner")
    s = s[s.maturity_date > as_of]
    s["days_to_maturity"] = (s.maturity_date - as_of).dt.days
    s["alert"] = s.days_to_maturity.map(alert_band)
    return s.reset_index().sort_values("days_to_maturity").reset_index(drop=True)


def upcoming_collections(events: pd.DataFrame, as_of, days: int, amount_col: str = "amount_rwf") -> float:
    """Cash expected IN over the next `days` days after as_of, RWF millions."""
    as_of = pd.Timestamp(as_of)
    window = events[(events.direction == "in") & (events.pay_date > as_of)
                    & (events.pay_date <= as_of + pd.Timedelta(days=days))]
    return round(window[amount_col].sum() / M, 1)


def write_offline(tables: dict[str, pd.DataFrame], out_dir) -> list[Path]:
    """Write the offline outputs: CSV for people (opens in Excel), Parquet for programs."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, df in tables.items():
        df.to_csv(out_dir / f"{name}.csv", index=not isinstance(df.index, pd.RangeIndex))   # keep row labels
        df.to_parquet(out_dir / f"{name}.parquet")
        paths += [out_dir / f"{name}.csv", out_dir / f"{name}.parquet"]
    return paths


def write_online(status: pd.DataFrame, db_path) -> Path:
    """Materialize the latest status per instrument into the online store (SQLite), keyed by instrument_id."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    s = status.copy()
    for c in ("next_pay_date", "maturity_date"):
        s[c] = s[c].dt.strftime("%Y-%m-%d")
    with sqlite3.connect(db_path) as con:
        s.to_sql("instrument_status", con, if_exists="replace", index=False)
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_status ON instrument_status(instrument_id)")
    return db_path


def lookup(db_path, instrument_id: str) -> dict | None:
    """Online read: one instrument's current status, by key (what a screen or an API would call)."""
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM instrument_status WHERE instrument_id = ?", (instrument_id,)).fetchone()
    return dict(row) if row else None
