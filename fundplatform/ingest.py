"""
Getting data in: Excel workbooks and CSV files -> cleaned registers -> accepted rows and exceptions.

Registers arrive the way people keep them in Excel: "3,000,000,000", "10.5%", 15/10/2026, " Bank B ",
a TOTAL row at the bottom. This module turns that into clean tables in three steps:

  read_workbook(file)        every sheet whose name matches a register ("Term Deposits" -> term_deposits)
  clean(df, register, log)   safe, rule-based fixes; every change is written to the log (what, where, before, after)
  quarantine(registers)      contract + business rules: rows that pass are accepted, rows that don't are set
                             aside as EXCEPTIONS, each with the reason, an owner and the action to take

Cleaning only fixes what has one possible meaning (a thousands separator, a % sign, a stray space).
Anything that needs judgement (a rate typed as 10.8, an unknown bank, a missing date) is NOT guessed:
it becomes an exception for a person to correct in the source, so the figures never rest on a guess.

    from fundplatform import ingest
    raw = ingest.read_workbook("registers.xlsx")          # {register: DataFrame}
    log = []
    clean = {name: ingest.clean(df, name, log) for name, df in raw.items()}
    accepted, exceptions = ingest.quarantine(clean, approved={"term_deposits": {"bank": ["Bank A", "Bank B"]}})
"""
from __future__ import annotations

import re
from numbers import Integral
from pathlib import Path

import pandas as pd

from . import quality, registry

# Registers with a data contract: register -> (contract file in contracts/, key column)
CHECKED = {
    "term_deposits": ("term_deposit", "deposit_id"),
    "treasury_bonds": ("treasury_bond", "bond_id"),
    "subsidiary_loans": ("subsidiary_loan", "loan_id"),
    "dividends": ("dividend", "dividend_id"),
}
# Who corrects a register's rows when they are set aside (the contract's producer)
OWNERS = {
    "term_deposits": "Finance office (placement desk)", "treasury_bonds": "Investment analyst",
    "subsidiary_loans": "Finance office (loans)", "dividends": "Investment analyst (equity)",
}
DAY_FIRST = True          # 03/04/2026 is 3 April (East African and European convention). Change if yours differs.
TOTAL_ROW = re.compile(r"^\s*(grand\s+|sub-?\s*)?total\b", re.IGNORECASE)
NUMBER = re.compile(r"^\s*-?[\d,\s]*\.?\d+\s*%?\s*$")


def snake(name: str) -> str:
    """'Tenor (days)' -> 'tenor_days', 'USD/RWF' -> 'usd_rwf': how headers and sheet names are matched."""
    return re.sub(r"[^0-9a-z]+", "_", str(name).strip().lower()).strip("_")


def excel_row(label) -> str:
    """The row number a person sees in Excel for a row label (headers are row 1)."""
    return str(label + 2) if isinstance(label, Integral) else str(label)


def register_for(name: str) -> str | None:
    """The register a sheet or file name refers to: by register name or file name ('Receipts 2026' -> receipts)."""
    key = snake(Path(name).stem if name.lower().endswith((".csv", ".xlsx")) else name)
    for file, (register, _) in registry.REGISTERS.items():
        if key in (register, snake(Path(file).stem)):
            return register
    return None


def read_workbook(source) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Read every sheet of an Excel workbook (a path or an uploaded file) that matches a register.
    Returns ({register: raw table}, [names of sheets that matched no register])."""
    sheets = pd.read_excel(source, sheet_name=None, dtype=object)
    found, ignored = {}, []
    for sheet, df in sheets.items():
        register = register_for(sheet)
        if register:
            found[register] = df
        else:
            ignored.append(sheet)
    return found, ignored


def _date_columns(register: str, schema: dict | None) -> list[str]:
    by_file = next((dates for _, (name, dates) in registry.REGISTERS.items() if name == register), [])
    by_contract = [k for k, v in (schema or {}).get("properties", {}).items() if v.get("format") == "date"]
    return list(dict.fromkeys(by_file + by_contract))


def _blank(v) -> bool:
    return v is None or (isinstance(v, float) and pd.isna(v)) or (isinstance(v, str) and not v.strip())


def clean(df: pd.DataFrame, register: str, log: list | None = None) -> pd.DataFrame:
    """Apply the cleaning rules to one register. Row labels are kept, so label + 2 is the row in Excel
    (row 1 holds the headers). Every change is appended to log as a dict: register, excel_row, column,
    before, after, rule."""
    log = log if log is not None else []
    contract = CHECKED.get(register)
    schema = registry.contract(contract[0]) if contract else None
    props = (schema or {}).get("properties", {})

    def note(row, column, before, after, rule):
        log.append({"register": register, "excel_row": excel_row(row), "column": column, "before": str(before)[:60],
                    "after": str(after)[:60], "rule": rule})

    out = df.copy()
    renamed = {c: snake(c) for c in out.columns if snake(c) != c}
    for before, after in renamed.items():
        note("header", before, before, after, "column name standardised")
    out = out.rename(columns=renamed)

    # 1 · rows that aren't records: completely empty, or a TOTAL line typed under the table
    for i, row in out.iterrows():
        if all(_blank(v) for v in row.values):
            note(i, "(row)", "", "", "empty row removed")
            out = out.drop(index=i)
        elif any(isinstance(v, str) and TOTAL_ROW.match(v) for v in row.values[:2]):
            note(i, "(row)", next(v for v in row.values[:2] if isinstance(v, str)), "", "total row removed")
            out = out.drop(index=i)

    # 2 · text: stray and double spaces; codes in capitals where the contract lists them so ("rwf" -> "RWF")
    for col in out.columns:
        if out[col].dtype != object:
            continue
        upper = all(isinstance(e, str) and e.isupper() for e in props.get(col, {}).get("enum", [None]))
        for i, v in out[col].items():
            if isinstance(v, str):
                fixed = re.sub(r"\s+", " ", v).strip()
                if upper and fixed.upper() in props[col]["enum"]:
                    fixed = fixed.upper()
                if fixed != v:
                    note(i, col, v, fixed, "spaces / capitals standardised")
                    out.at[i, col] = fixed

    # 3 · dates: ISO (2026-10-15) as is; anything else read day-first (15/10/2026, 15 Oct 2026), logged
    for col in _date_columns(register, schema):
        if col not in out.columns:
            continue
        values = out[col]
        parsed = pd.to_datetime(values.where(values.map(lambda v: isinstance(v, str))), format="%Y-%m-%d",
                                errors="coerce")
        for i, v in values.items():
            if _blank(v) or pd.notna(parsed.get(i)):
                continue
            if isinstance(v, pd.Timestamp) or hasattr(v, "year"):         # a real date cell in Excel
                parsed.at[i] = pd.Timestamp(v)
                continue
            if isinstance(v, (int, float)) and 20000 < v < 80000:           # an Excel serial number
                parsed.at[i] = pd.Timestamp("1899-12-30") + pd.Timedelta(days=int(v))
                note(i, col, v, parsed.at[i].date(), "Excel date number converted")
                continue
            try:
                parsed.at[i] = pd.to_datetime(str(v), dayfirst=DAY_FIRST)
                note(i, col, v, parsed.at[i].date(), "date read day-first")
            except (ValueError, TypeError):
                note(i, col, v, "", "unreadable date left empty")
        out[col] = pd.to_datetime(parsed)

    # 4 · numbers typed as text: "3,000,000,000" -> 3000000000; "10.5%" -> 0.105 (the % sign is unambiguous)
    numeric = {k for k, v in props.items() if v.get("type") in ("number", "integer")}
    for col in out.columns:
        if out[col].dtype != object or col in _date_columns(register, schema) or col.endswith("id"):
            continue
        values = [v for v in out[col] if not _blank(v)]
        readable = [isinstance(v, (int, float)) and not isinstance(v, bool) or isinstance(v, str) and NUMBER.match(v)
                    for v in values]
        if not values or (col not in numeric and not all(readable)):
            continue                                                   # a text column: leave it alone
        for i, v in out[col].items():
            if isinstance(v, str) and NUMBER.match(v):
                n = float(v.replace(",", "").replace(" ", "").rstrip().rstrip("%"))
                if v.strip().endswith("%"):
                    n = round(n / 100, 10)
                note(i, col, v, n, "percentage -> decimal" if v.strip().endswith("%") else "text -> number")
                out.at[i, col] = n
            elif _blank(v):
                out.at[i, col] = float("nan")
        if all(readable):                                              # else keep the text: the contract reports it
            out[col] = pd.to_numeric(out[col])
            whole = out[col].notna().all() and (out[col] % 1 == 0).all()      # e.g. amounts: 3000000000, not 3e9
            if whole and (props.get(col, {}).get("type") == "integer" or out[col].abs().max() >= 1000):
                out[col] = out[col].astype("int64")
    return out


def check(df: pd.DataFrame, register: str, approved: dict[str, list] | None = None) -> pd.DataFrame:
    """Every problem in one register: contract rules, duplicate keys and approved values (e.g. banks)."""
    if register not in CHECKED:
        return pd.DataFrame(columns=["row", "key", "field", "problem"])
    contract, key = CHECKED[register]
    approved = {c: v for c, v in (approved or {}).items() if c in df.columns}
    return quality.batch_check(df, registry.contract(contract), key=key, approved=approved)


def quarantine(registers: dict[str, pd.DataFrame], approved: dict[str, dict] | None = None):
    """Split each checked register into accepted rows and exceptions.
    Returns ({register: accepted rows}, exceptions table with register, excel_row, key, field, problem,
    owner, action). Registers without a contract are passed through unchanged."""
    accepted, found = {}, []
    for name, df in registers.items():
        problems = check(df, name, (approved or {}).get(name))
        accepted[name] = df.drop(index=problems["row"].unique()) if len(problems) else df
        if len(problems):
            found.append(problems.assign(
                register=name, excel_row=problems["row"].map(excel_row),
                owner=OWNERS.get(name, "Register owner"),
                action="Correct it in the source and import again; the row is left out of every figure until then"))
    cols = ["register", "excel_row", "key", "field", "problem", "owner", "action"]
    exceptions = pd.concat(found, ignore_index=True)[cols] if found else pd.DataFrame(columns=cols)
    return accepted, exceptions
