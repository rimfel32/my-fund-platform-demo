"""
Capstone Part 3: requirements YOU implement. Implement at least TWO of the three functions below.

Each one is a requirement from the investment-management requirements list (see CAPSTONE.md).
Replace `raise NotImplementedError` with your code, then run:

    python run.py                 # the pipeline runs your function and writes its output
    python check.py day3          # (from the top of your repository) grades it

Rules for all three:
  - return a pandas DataFrame with EXACTLY the columns listed, one row per group, sorted as stated
  - amounts in RWF millions, rounded to 1 decimal; percentages rounded to 1 decimal
  - use as_of as "today": ignore anything dated after it
Tips: the labs show every pandas move you need (filtering, groupby, merge_asof, rounding).
Look at the data first: print(df.head()), print(df.dtypes).
"""
from __future__ import annotations

import pandas as pd


def bank_concentration(deposits: pd.DataFrame, fx_table: pd.DataFrame, as_of) -> pd.DataFrame:
    """FR-FTD-007 Bank concentration view.

    For deposits LIVE on as_of (value_date <= as_of < maturity_date), one row per bank:
      bank                 the bank's name
      n_deposits           how many live deposits it holds
      deployed_rwf_m       total principal in RWF millions (USD converted at the rate in effect on as_of:
                           use fundplatform.fx.rate_on(fx_table, [as_of]))
      expected_interest_rwf_m   total interest those deposits will pay at maturity, RWF millions
                           (amount x interest_rate x tenor_days / 365, USD converted the same way)
      share_pct            deployed_rwf_m as a % of all live deposits
    Sorted by deployed_rwf_m, largest first.
    """
    raise NotImplementedError


def loan_arrears(events: pd.DataFrame, receipts: pd.DataFrame, as_of) -> pd.DataFrame:
    """FR-SBL-005 Instalment collection tracking: arrears per borrower.

    For subsidiary loans only (asset_class == "subsidiary_loan"):
      scheduled = every instalment amount (interest and principal) in `events` due from 1 Jan of
                  as_of's year up to and including as_of
      received  = the `received` amounts in `receipts` for those loans, received on or before as_of
    One row per borrower (the `counterparty` column in events):
      borrower, scheduled_rwf_m, received_rwf_m, arrears_rwf_m (= scheduled - received, never below 0)
    Sorted by arrears_rwf_m, largest first.
    Hint: receipts has instrument_id but not the borrower; take the borrower from events.
    """
    raise NotImplementedError


def recovery_rates(receivables: pd.DataFrame, as_of) -> pd.DataFrame:
    """FR-PRP-003 / FR-PRP-006 Collection efficiency and recovery rate per entity.

    For instalments due on or before as_of, one row per entity:
      entity, due_rwf_m (sum of amount_due_rwf), collected_rwf_m (sum of amount_paid_rwf paid on or
      before as_of), recovery_pct (collected / due x 100), overdue_rwf_m (due - collected)
    Sorted by recovery_pct, lowest first (the files to chase).
    """
    raise NotImplementedError
