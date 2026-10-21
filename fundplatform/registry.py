"""
Registers: the instrument "tables" the desk maintains, read from CSV files or an Excel workbook
(from a database later), cleaned (ingest.py) and checked against their data contracts.

    reg = load_all("path/to/data")      # dict of DataFrames, dates parsed
    reg["term_deposits"].head()

Where each register comes from, in order: <register>.csv in the data folder, else a sheet with the
register's name in a workbook called registers*.xlsx in the same folder ("Term Deposits" -> term_deposits).
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"

# file name -> (register name, date columns)
REGISTERS = {
    "treasury_bonds.csv": ("treasury_bonds", ["value_date", "maturity_date"]),
    "term_deposits.csv": ("term_deposits", ["value_date", "maturity_date"]),
    "corporate_bonds.csv": ("corporate_bonds", ["value_date"]),
    "subsidiary_loans.csv": ("subsidiary_loans", ["first_payment_date"]),
    "borrowings.csv": ("borrowings", ["issue_date", "maturity_date"]),
    "fx_rates.csv": ("fx_rates", ["rate_date", "entered_on"]),
    "receipts_2026.csv": ("receipts", ["pay_date", "received_on"]),
    "receivables.csv": ("receivables", ["due_date", "paid_date"]),
    "opening_position.csv": ("opening_position", ["date"]),
    "equity_holdings.csv": ("equity_holdings", ["investment_date"]),
    "dividends.csv": ("dividends", ["declared_on", "pay_date", "received_on"]),
}


def load_all(data_dir, log: list | None = None) -> dict[str, pd.DataFrame]:
    """Read and clean every register found in data_dir (CSV first, else a registers*.xlsx sheet).
    Missing registers are simply left out. Each cleaning fix is appended to log (see ingest.clean)."""
    from . import ingest
    data_dir = Path(data_dir)
    workbook = {}
    for book in sorted(data_dir.glob("registers*.xlsx")):
        workbook.update(ingest.read_workbook(book)[0])
    out = {}
    for file, (name, _) in REGISTERS.items():
        path = data_dir / file
        if path.exists():
            out[name] = ingest.clean(pd.read_csv(path), name, log)
        elif name in workbook:
            out[name] = ingest.clean(workbook[name], name, log)
    return out


def contract(name: str) -> dict:
    """The JSON Schema contract for one register, e.g. contract("term_deposit")."""
    return json.loads((CONTRACTS / f"{name}.schema.json").read_text(encoding="utf-8"))


def validate(df: pd.DataFrame, schema: dict, key: str | None = None) -> pd.DataFrame:
    """Check every row of df against a JSON Schema contract (Day 1, Module 2).
    Returns one row per problem: row number, the record's key, the field and the message.
    With key=..., also reports rows whose key appears more than once (uniqueness)."""
    from jsonschema import Draft202012Validator, FormatChecker
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    def plain(v):
        """JSON-style value: dates as 'YYYY-MM-DD' text, numpy numbers as Python numbers."""
        if isinstance(v, pd.Timestamp):
            return v.strftime("%Y-%m-%d")
        return v.item() if hasattr(v, "item") else v

    problems = []
    for i, row in df.iterrows():
        record = {k: plain(v) for k, v in row.items() if not pd.isna(v) and v != ""}
        for err in validator.iter_errors(record):
            field = err.path[0] if err.path else (err.message.split("'")[1] if "'" in err.message else "(record)")
            problems.append({"row": i, "key": record.get(key, "?") if key else "?", "field": field,
                             "problem": err.message[:90]})
    if key:
        for i in df.index[df.duplicated(subset=[key], keep="first")]:
            problems.append({"row": i, "key": df.at[i, key], "field": key, "problem": "duplicate key"})
    return pd.DataFrame(problems, columns=["row", "key", "field", "problem"])
