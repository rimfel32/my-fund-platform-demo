"""
The same pipeline as an Apache Airflow DAG (for reading; you don't need Airflow to run the platform).

In production an orchestrator runs the pipeline on a schedule, retries failed steps, keeps a history of
every run and alerts someone when it fails. Airflow is the most common open-source choice; managed
versions exist in every cloud. Each task below calls the same step function as fundplatform.pipeline.

To try it later:  pip install apache-airflow  ->  put this file in Airflow's dags/ folder.
"""
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.python import PythonOperator

from fundplatform import pipeline

CONFIG = {"data_dir": "/data/treasury", "output_dir": "/data/platform/output", "year": 2026}


def step(name):
    """Run one pipeline step in a fresh context (in production each step would read/write its datasets)."""
    def _run(ds, **_):
        ctx = {**CONFIG, "as_of": ds, "spans": [], "lineage": []}
        for dep in pipeline.STEPS:                       # replay upstream steps in memory (fine at this size)
            pipeline.STEPS[dep][0](ctx)
            if dep == name:
                break
    return _run


with DAG(
    dag_id="treasury_platform_daily",
    schedule="0 5 * * 1-5",                  # 05:00 on working days, before the desk opens
    start_date=datetime(2026, 1, 1),
    catchup=False,                           # don't run every missed day since 1 Jan (set True to BACKFILL)
    default_args={"retries": 2, "retry_delay": timedelta(minutes=10)},
    tags=["treasury", "capstone"],
) as dag:
    tasks = {name: PythonOperator(task_id=name, python_callable=step(name)) for name in pipeline.STEPS}
    for name, (_, deps, _, _) in pipeline.STEPS.items():
        for d in deps:
            tasks[d] >> tasks[name]           # ">>" means "runs before"
