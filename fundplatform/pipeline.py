"""
The platform's batch pipeline: every step, what it reads and writes, run in dependency order.

This is what an orchestrator such as Apache Airflow does (Module 4): a DAG of tasks, each run once
its upstream tasks succeeded, with retries, timing and a record of every run. Here a 60-line runner
built on Python's graphlib does the same on one machine; dags/fund_daily_airflow.py shows the
equivalent Airflow DAG.

Each step also:
  - emits an OpenLineage RunEvent (lineage.py): which datasets it read and wrote
  - records a span (name, duration, status), the basic unit of tracing in OpenTelemetry

    from fundplatform import pipeline
    ctx = pipeline.run({"data_dir": "...", "output_dir": "output", "as_of": "2026-10-21", "year": 2026})
"""
from __future__ import annotations

import time
import uuid
from graphlib import TopologicalSorter
from pathlib import Path

import pandas as pd

from . import cashflow, extensions, fx, ingest, lineage, quality, registry, schedules, serving

APPROVED_BANKS = [f"Bank {c}" for c in "ABCDEFGHI"]


# ------------------------------------------------------------------ steps: (function, depends on, reads, writes)
def load_registers(ctx):
    ctx["cleaning_log"] = []                      # every fix ingest.clean made: register, row, before, after, rule
    ctx["reg"] = registry.load_all(ctx["data_dir"], log=ctx["cleaning_log"])


def validate_registers(ctx):
    """Contract checks. A row that breaks its contract is set aside (quarantined) with the reason and an owner,
    instead of stopping the whole run: every other figure stays available, the bad row is left out of all of
    them, and the scorecard and the Overview page say so until it is corrected at the source."""
    checked = {name: df for name, df in ctx["reg"].items() if name in ingest.CHECKED}
    accepted, ctx["exceptions"] = ingest.quarantine(checked, approved={"term_deposits": {"bank": APPROVED_BANKS}})
    ctx["reg"].update(accepted)
    ctx["register_problems"] = ctx["exceptions"]


def build_schedules(ctx):
    ctx["events"] = schedules.build_events(ctx["reg"])


def fx_table(ctx):
    ctx["fx"] = fx.rates_as_known(ctx["reg"]["fx_rates"])


def convert_rwf(ctx):
    e = ctx["events"].copy()
    e["amount_rwf"] = fx.to_rwf(e, ctx["fx"])
    ctx["events_rwf"] = e


def income_split(ctx):
    ctx["income"] = cashflow.income_split(ctx["events_rwf"], ctx["year"])


def master_plan(ctx):
    opening = float(ctx["reg"]["opening_position"].cash_rwf.iloc[0])
    ctx["master"] = cashflow.master_cashflow(ctx["events_rwf"], ctx["year"], opening)


def variance(ctx):
    month = pd.Timestamp(ctx["as_of"]).month - 1          # last complete month
    ctx["variance"] = cashflow.monthly_variance(ctx["reg"]["receipts"], ctx["year"], month)


def serve(ctx):
    ctx["status"] = serving.instrument_status(ctx["events_rwf"], ctx["as_of"])
    serving.write_online(ctx["status"], Path(ctx["output_dir"]) / "online_store.db")
    ctx["upcoming"] = {d: serving.upcoming_collections(ctx["events_rwf"], ctx["as_of"], d) for d in (30, 60, 90)}


def score(ctx):
    as_of = ctx["as_of"]
    fx_age = quality.freshness_days(ctx["reg"]["fx_rates"].rate_date, as_of)
    v = ctx["variance"]
    rcv = ctx["reg"]["receivables"].dropna(subset=["paid_date"])
    days_late = (rcv.paid_date - rcv.due_date).dt.days
    drift = quality.psi(days_late[rcv.due_date.dt.year == 2025], days_late[rcv.due_date.dt.year == 2026],
                        bins=[-1, 0, 7, 14, 30, 60, 10_000])
    ctx["scorecard"] = quality.scorecard([
        dict(name="FX rate freshness (days)", value=fx_age, ok=fx_age <= 7, target="<= 7 days (rates entered weekly)",
             owner="Finance office", action="Enter the latest reference rates; USD figures are stale until then"),
        dict(name="Register contract problems", value=len(ctx["register_problems"]), ok=len(ctx["register_problems"]) == 0,
             target="0", owner="Investment analyst",
             action="Correct the rows under Exceptions (Data quality page); they are left out of every figure until then"),
        dict(name="Months collected >10% below budget", value=int(v.flag.sum()), ok=int(v.flag.sum()) == 0,
             target="0", owner="Finance manager", action="Review the variance report; chase arrears"),
        dict(name="Receivables payment-delay drift (PSI)", value=drift, ok=drift < 0.1, target="< 0.1 (stable)",
             owner="Investment analyst", action="Review payers' behaviour; update collection forecasts"),
        dict(name="Maturities within 30 days", value=int((ctx["status"].alert == "30").sum()), ok=True,
             target="information", owner="Finance office", action="Prepare rollover decisions"),
    ])


def board_summary(ctx):
    m = ctx["master"]
    ctx["board"] = pd.DataFrame({
        "measure": ["Interest income, cash, year (RWF m)", "Interest income, accrual, year (RWF m)",
                    "Accrued interest receivable at year end (RWF m)", "Closing cash, December (RWF m)",
                    "Collections next 30 days (RWF m)", "Instruments maturing within 90 days", "Scorecard breaches"],
        "value": [round(ctx["income"].cash.sum() / 1e6, 1), round(ctx["income"].accrual.sum() / 1e6, 1),
                  round(ctx["income"].accrued_at_ye.sum() / 1e6, 1), m.loc["Closing balance", "Dec"],
                  ctx["upcoming"][30], int((ctx["status"].alert != "").sum()),
                  int((ctx["scorecard"].status == "BREACH").sum())]})


def run_extensions(ctx):
    """Your Capstone Part 3 requirements: run each one you've implemented and keep its output."""
    ctx["extensions"] = {}
    calls = {"bank_concentration": lambda: extensions.bank_concentration(ctx["reg"]["term_deposits"], ctx["fx"], ctx["as_of"]),
             "loan_arrears": lambda: extensions.loan_arrears(ctx["events"], ctx["reg"]["receipts"], ctx["as_of"]),
             "recovery_rates": lambda: extensions.recovery_rates(ctx["reg"]["receivables"], ctx["as_of"])}
    for name, call in calls.items():
        try:
            ctx["extensions"][name] = call()
        except NotImplementedError:
            pass


def write_outputs(ctx):
    tables = {"schedule_events": ctx["events_rwf"], f"cash_vs_accrual_{ctx['year']}": ctx["income"],
              f"master_cashflow_{ctx['year']}": ctx["master"], f"variance_{ctx['year']}": ctx["variance"],
              "instrument_status": ctx["status"], "scorecard": ctx["scorecard"], "board_summary": ctx["board"],
              "exceptions": ctx["exceptions"].astype(str),
              "cleaning_log": pd.DataFrame(ctx["cleaning_log"], columns=["register", "excel_row", "column", "before",
                                                                         "after", "rule"]).astype(str),
              **ctx["extensions"]}
    serving.write_offline(tables, ctx["output_dir"])


R = "registers/"
S = "staging/"
O = "output/"
REG = {name: file for file, (name, _) in registry.REGISTERS.items()}   # register name -> CSV file
STEPS = {
    # step: (function, runs after, datasets it reads, datasets it writes)
    "load_registers": (load_registers, [], [R + f for f in REG.values()], [S + n for n in REG]),
    "validate_registers": (validate_registers, ["load_registers"],
                           [S + "term_deposits", S + "treasury_bonds", S + "subsidiary_loans", "contracts/"],
                           [S + "validated_registers"]),
    "build_schedules": (build_schedules, ["validate_registers"],
                        [S + "validated_registers", S + "corporate_bonds", S + "borrowings"], [O + "schedule_events"]),
    "fx_table": (fx_table, ["load_registers"], [S + "fx_rates"], [S + "fx_table"]),
    "convert_rwf": (convert_rwf, ["build_schedules", "fx_table"], [O + "schedule_events", S + "fx_table"],
                    [S + "events_rwf"]),
    "income_split": (income_split, ["convert_rwf"], [S + "events_rwf"], [O + "cash_vs_accrual"]),
    "master_plan": (master_plan, ["convert_rwf"], [S + "events_rwf", S + "opening_position"], [O + "master_cashflow"]),
    "variance": (variance, ["load_registers"], [S + "receipts"], [O + "variance"]),
    "serve": (serve, ["convert_rwf"], [S + "events_rwf"], [O + "instrument_status", "online/instrument_status"]),
    "score": (score, ["serve", "variance", "validate_registers"],
              [S + "fx_rates", S + "receivables", S + "validated_registers", O + "variance", O + "instrument_status"],
              [O + "scorecard"]),
    "board_summary": (board_summary, ["income_split", "master_plan", "serve", "score"],
                      [O + "cash_vs_accrual", O + "master_cashflow", O + "instrument_status", O + "scorecard"],
                      [O + "board_summary"]),
    "run_extensions": (run_extensions, ["build_schedules", "fx_table"],
                       [S + "term_deposits", S + "fx_table", O + "schedule_events", S + "receipts", S + "receivables"],
                       [O + "extensions"]),
    "write_outputs": (write_outputs, ["board_summary", "run_extensions"],
                      [O + "board_summary", O + "extensions"], ["files/output_folder"]),
}



def run(config: dict, retries: int = 1, quiet: bool = False) -> dict:
    """Run every step in dependency order. Returns the context: all tables, plus 'spans' and 'lineage'."""
    ctx = {**config, "as_of": pd.Timestamp(config["as_of"]), "year": int(config["year"]),
           "spans": [], "lineage": []}
    Path(ctx["output_dir"]).mkdir(parents=True, exist_ok=True)
    order = list(TopologicalSorter({k: set(v[1]) for k, v in STEPS.items()}).static_order())
    run_id = str(uuid.uuid4())
    for name in order:
        fn, _, reads, writes = STEPS[name]
        for attempt in range(retries + 1):
            t0 = time.perf_counter()
            try:
                fn(ctx)
                status = "OK"
            except Exception as e:  # noqa: BLE001
                status = f"ERROR: {e}"
            ms = round(1000 * (time.perf_counter() - t0), 1)
            ctx["spans"].append({"span": name, "attempt": attempt + 1, "duration_ms": ms, "status": status[:120]})
            if status == "OK":
                break
        kind = "COMPLETE" if status == "OK" else "FAIL"
        if name == "load_registers":
            # One event per register: a single event would link EVERY register to EVERY staging table
            # (job-level lineage is that coarse), and impact analysis would say "everything affects everything".
            for src, dst in zip(reads, writes):
                ctx["lineage"].append(lineage.run_event(f"{name}.{dst.split('/')[-1]}", [src], [dst], kind, run_id=run_id))
        else:
            ctx["lineage"].append(lineage.run_event(name, reads, writes, kind, run_id=run_id))
        if not quiet:
            print(f"  {'✅' if status == 'OK' else '❌'} {name:<20} {ms:>8.1f} ms  {'' if status == 'OK' else status}")
        if status != "OK":
            lineage.save(ctx["lineage"], Path(ctx["output_dir"]) / "lineage")
            raise RuntimeError(f"step '{name}' failed: {status}")
    lineage.save(ctx["lineage"], Path(ctx["output_dir"]) / "lineage")
    return ctx
