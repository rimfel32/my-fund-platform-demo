"""
Run the investment data platform end to end.

    python run.py                    # uses config.yaml next to this file
    python run.py --as-of 2026-11-30 # same, pretending today is 30 Nov 2026

It reads the registers (data_dir in config.yaml), validates them, builds every payment schedule,
converts USD at the right rate, and writes to output/:
  schedule_events.csv/.parquet        every payment of every instrument
  cash_vs_accrual_<year>.csv           interest income, cash vs accrual, per instrument
  master_cashflow_<year>.csv           the 12-month master cash-flow plan (open in Excel)
  variance_<year>.csv                  budget vs actual collections per month, flagged months
  instrument_status.csv + online_store.db   next payment, days to maturity, 90/60/30-day alerts
  scorecard.csv                        freshness, validity, variance, drift: the observability scorecard
  board_summary.csv                    the headline figures for the board pack
  <your extensions>.csv                whatever you implemented in fundplatform/extensions.py
  lineage/*.json                       one OpenLineage event per step: what each step read and wrote
and results/extensions.json (NOT in output/, so it is submitted with your work): your extensions' results.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # lets ✅ print in any Windows terminal

from fundplatform import pipeline  # noqa: E402


def find_data(configured: str) -> Path:
    """The data folder: as configured, else the course's Day 3 data wherever this copy lives.
    A folder counts if it has term_deposits.csv or a registers*.xlsx workbook."""
    candidates = [HERE / configured, HERE.parent.parent / "data",                    # materials/day3/capstone/platform
                  HERE.parents[2] / "materials" / "day3" / "data"]                  # my_work/capstone/platform
    for c in candidates:
        if (c / "term_deposits.csv").exists() or any(c.glob("registers*.xlsx")):
            return c.resolve()
    for c in candidates:                                                             # data not generated yet
        gen = c.parent / "scripts" / "generate_data.py"
        if gen.exists():
            subprocess.run([sys.executable, str(gen)], check=True)
            return c.resolve()
    raise SystemExit("Can't find the data. Set data_dir in config.yaml to the folder with term_deposits.csv.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of")
    args = ap.parse_args()
    cfg = yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))
    cfg["data_dir"] = find_data(cfg["data_dir"])
    cfg["output_dir"] = (HERE / cfg["output_dir"]).resolve()
    if args.as_of:
        cfg["as_of"] = args.as_of
    print(f"Platform run · as of {cfg['as_of']} · data {cfg['data_dir']}")
    ctx = pipeline.run(cfg)
    print("\nBoard summary")
    print(ctx["board"].to_string(index=False))
    print("\nScorecard")
    print(ctx["scorecard"][["name", "value", "status"]].to_string(index=False))
    # Your extensions' results are saved where they are committed with your work: the grading reads this
    # file (it never runs your code on the instructor's side). Re-run python run.py after every change.
    results = HERE / "results" / "extensions.json"
    results.parent.mkdir(exist_ok=True)
    results.write_text(json.dumps({
        "as_of": str(cfg["as_of"])[:10], "year": cfg["year"],
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "results": {k: v.to_dict("records") for k, v in ctx["extensions"].items()}}, indent=1, default=str),
        encoding="utf-8")
    done = sorted(ctx["extensions"]) or ["none yet (see fundplatform/extensions.py)"]
    print("\nYour extensions:", ", ".join(done))
    print(f"\nOutputs in {cfg['output_dir']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
