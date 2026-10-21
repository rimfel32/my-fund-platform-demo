"""
fundplatform: a small, working investment data platform for a development fund's treasury desk.

It turns the registers the desk keeps in Excel (bonds, deposits, loans, FX rates) into the outputs the
desk produces by hand today: payment schedules, cash vs accrual income, the 12-month master cash-flow
plan, maturity alerts, upcoming collections, a data-quality scorecard and a lineage record.

Modules, in the order data flows through them (see pipeline.py):
  ingest      Excel/CSV in: cleaning rules, then accepted rows vs exceptions (rows set aside, with an owner)
  registry    read the registers and check them against their data contracts (contracts/*.json)
  equity      equity holdings and dividends: projected vs received, overdue payers, a baseline forecast
  schedules   every future and past payment of every instrument (the "features" of this platform)
  fx          the exchange rate in effect on any date, as known at any date (point-in-time)
  cashflow    cash vs accrual income, the master cash-flow plan, budget vs actual variance
  serving     offline outputs (Parquet/CSV) and an online lookup table (SQLite) with alerts
  quality     freshness, validity, variance, drift: the observability scorecard
  lineage     OpenLineage-style run events and "what feeds what" questions
  pipeline    runs everything in dependency order, timing each step
  extensions  requirements YOU implement (Capstone Part 3)
"""
__version__ = "1.1"
