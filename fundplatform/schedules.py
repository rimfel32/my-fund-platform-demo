"""
Payment schedules: every coupon, interest payment, instalment and principal repayment of every
instrument, past and future, as one table of "events". Everything else (cash vs accrual, the master
cash-flow plan, alerts, collections calendars) is computed from this table, so each figure is
defined ONCE (Day 1's lesson, applied to finance).

One row per payment:
  instrument_id, asset_class, counterparty, currency
  flow        interest | principal
  direction   in (we receive) | out (we pay)
  period_start, period_end   the days the interest was earned over (interest rows)
  pay_date    when the cash moves
  amount      in the instrument's currency
  outstanding principal outstanding at the start of the period

Conventions (state them; they change the numbers):
  - Bonds, deposits and borrowings: interest = principal x rate x days / 365   (Actual/365)
  - Subsidiary loans: French amortisation (equal instalments), periodic rate = annual rate / 12 or / 2
  - Amortising corporate bonds: equal principal repayments, interest on the outstanding balance
"""
from __future__ import annotations

import pandas as pd

COLUMNS = ["instrument_id", "asset_class", "counterparty", "currency", "flow", "direction",
           "period_start", "period_end", "pay_date", "amount", "outstanding"]


def _event(iid, cls, cpty, ccy, flow, direction, start, end, pay, amount, outstanding):
    return dict(instrument_id=iid, asset_class=cls, counterparty=cpty, currency=ccy, flow=flow,
                direction=direction, period_start=start, period_end=end, pay_date=pay,
                amount=round(float(amount), 2), outstanding=round(float(outstanding), 2))


def actual_365(principal: float, rate: float, start, end) -> float:
    """Interest for a period under Actual/365: principal x rate x actual days / 365."""
    return principal * rate * (pd.Timestamp(end) - pd.Timestamp(start)).days / 365


def treasury_bond(row) -> list[dict]:
    """Semi-annual coupons (FR-TBD-002) from the value date to maturity, then the face value back."""
    events, start = [], row.value_date
    k = 1
    while True:
        end = row.value_date + pd.DateOffset(months=6 * k)
        if end > row.maturity_date:
            break
        events.append(_event(row.bond_id, "treasury_bond", "Government (central bank auction)", "RWF",
                             "interest", "in", start, end, end,
                             actual_365(row.face_value_rwf, row.coupon_rate, start, end), row.face_value_rwf))
        start, k = end, k + 1
    events.append(_event(row.bond_id, "treasury_bond", "Government (central bank auction)", "RWF", "principal",
                         "in", None, None, row.maturity_date, row.face_value_rwf, row.face_value_rwf))
    return events


def term_deposit(row) -> list[dict]:
    """Interest and principal both paid at maturity (FR-FTD-001, FR-UTD-001)."""
    interest = actual_365(row.amount, row.interest_rate, row.value_date, row.maturity_date)
    return [_event(row.deposit_id, "term_deposit", row.bank, row.currency, "interest", "in",
                   row.value_date, row.maturity_date, row.maturity_date, interest, row.amount),
            _event(row.deposit_id, "term_deposit", row.bank, row.currency, "principal", "in",
                   None, None, row.maturity_date, row.amount, row.amount)]


def corporate_bond(row) -> list[dict]:
    """Semi-annual payments. Bullet: interest only, principal at the end (FR-CBN-002).
    Amortising: equal principal each period plus interest on what is still outstanding (FR-CBN-003/005)."""
    events, outstanding, start = [], float(row.face_value_rwf), row.value_date
    repay = row.face_value_rwf / row.n_payments if row.bond_type == "amortising" else 0.0
    for k in range(1, int(row.n_payments) + 1):
        end = row.value_date + pd.DateOffset(months=6 * k)
        events.append(_event(row.bond_id, "corporate_bond", row.issuer, "RWF", "interest", "in", start, end, end,
                             actual_365(outstanding, row.coupon_rate, start, end), outstanding))
        principal = outstanding if (k == row.n_payments) else repay
        if principal:
            events.append(_event(row.bond_id, "corporate_bond", row.issuer, "RWF", "principal", "in", None, None,
                                 end, principal, outstanding))
        outstanding -= principal
        start = end
    return events


def french_instalment(principal: float, periodic_rate: float, n: int) -> float:
    """The constant instalment of a French (annuity) loan: P x r / (1 - (1 + r)^-n)."""
    return principal * periodic_rate / (1 - (1 + periodic_rate) ** -n)


def subsidiary_loan(row) -> list[dict]:
    """French amortisation (FR-SBL-002/003/004): a constant instalment, split each period into interest on
    the outstanding balance and principal. Monthly or semi-annual."""
    months = 1 if row.frequency == "monthly" else 6
    r = row.interest_rate * months / 12
    pay = french_instalment(row.principal_rwf, r, int(row.n_instalments))
    events, outstanding = [], float(row.principal_rwf)
    start = row.first_payment_date - pd.DateOffset(months=months)
    for k in range(int(row.n_instalments)):
        end = row.first_payment_date + pd.DateOffset(months=months * k)
        interest = outstanding * r
        principal = outstanding if k == row.n_instalments - 1 else pay - interest
        events.append(_event(row.loan_id, "subsidiary_loan", row.borrower, "RWF", "interest", "in",
                             start, end, end, interest, outstanding))
        events.append(_event(row.loan_id, "subsidiary_loan", row.borrower, "RWF", "principal", "in",
                             None, None, end, principal, outstanding))
        outstanding -= principal
        start = end
    return events


def borrowing(row) -> list[dict]:
    """The fund's own debt (FR-BRW-002): principal and interest both repaid at maturity, as cash OUT."""
    interest = actual_365(row.principal_rwf, row.interest_rate, row.issue_date, row.maturity_date)
    return [_event(row.borrowing_id, "borrowing", row.lender, "RWF", "interest", "out",
                   row.issue_date, row.maturity_date, row.maturity_date, interest, row.principal_rwf),
            _event(row.borrowing_id, "borrowing", row.lender, "RWF", "principal", "out",
                   None, None, row.maturity_date, row.principal_rwf, row.principal_rwf)]


BUILDERS = {"treasury_bonds": treasury_bond, "term_deposits": term_deposit, "corporate_bonds": corporate_bond,
            "subsidiary_loans": subsidiary_loan, "borrowings": borrowing}


def build_events(reg: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """All payments of all instruments in the registers, sorted by pay date."""
    rows = []
    for name, builder in BUILDERS.items():
        for row in reg.get(name, pd.DataFrame()).itertuples(index=False):
            rows += builder(row)
    events = pd.DataFrame(rows, columns=COLUMNS)
    for c in ("period_start", "period_end", "pay_date"):
        events[c] = pd.to_datetime(events[c])
    return events.sort_values(["pay_date", "instrument_id", "flow"]).reset_index(drop=True)
