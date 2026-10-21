"""
Observability for the platform (Module 5): is the data fresh, valid, complete, and behaving as usual?

  freshness()      how old is the newest FX rate? The policy says rates are entered at least weekly.
  batch_check()    a new batch of placements against its contract, plus business rules
  psi()            Population Stability Index: has a distribution shifted? (the idea behind Evidently's drift checks)
  scorecard()      every signal with its target, status and owner, in one table
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def freshness_days(dates: pd.Series, as_of) -> int:
    """Days between the newest date in `dates` and as_of."""
    return int((pd.Timestamp(as_of) - pd.to_datetime(dates).max()).days)


def batch_check(batch: pd.DataFrame, schema: dict, key: str, approved: dict[str, list] | None = None) -> pd.DataFrame:
    """Contract check (schema rules + duplicate keys) plus business rules such as approved counterparties.
    Returns one row per problem."""
    from .registry import validate
    problems = validate(batch, schema, key=key)
    extra = []
    for column, allowed in (approved or {}).items():
        for i, v in batch[column].items():
            if pd.notna(v) and v not in allowed:
                extra.append({"row": i, "key": batch.at[i, key], "field": column, "problem": f"'{v}' is not approved"})
    return pd.concat([problems, pd.DataFrame(extra, columns=problems.columns)], ignore_index=True) \
        .sort_values(["row", "field"]).reset_index(drop=True)


def psi(expected: pd.Series, actual: pd.Series, bins: list[float]) -> float:
    """Population Stability Index between two samples, over fixed bins.
    Rule of thumb: < 0.1 stable, 0.1-0.25 moderate shift, > 0.25 significant shift."""
    e = np.histogram(expected, bins=bins)[0] / len(expected)
    a = np.histogram(actual, bins=bins)[0] / len(actual)
    e, a = np.clip(e, 1e-4, None), np.clip(a, 1e-4, None)
    return round(float(np.sum((a - e) * np.log(a / e))), 3)


def psi_band(value: float) -> str:
    return "stable" if value < 0.1 else "moderate" if value < 0.25 else "significant"


def scorecard(signals: list[dict]) -> pd.DataFrame:
    """signals: dicts with name, value, target (text), ok (bool), owner, action.
    Returns the scorecard table with a status column."""
    s = pd.DataFrame(signals)
    s.insert(2, "status", s.pop("ok").map({True: "OK", False: "BREACH"}))
    return s
