"""
Lineage (Module 5): record which job read which datasets and wrote which, so anyone can answer
"where did this board figure come from?" and "if this file is wrong, what else is wrong?".

Each pipeline step emits an OpenLineage-style RunEvent: a small JSON document with the job name,
a run id, its input datasets and its output datasets. In production you'd send these to a lineage
server (e.g. Marquez, or a data catalogue); here they are saved as JSON files and read back.
Spec: https://openlineage.io/docs/spec/object-model
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path

PRODUCER = "https://example.org/fundplatform"   # who emitted the event (a URL by convention)


def run_event(job: str, inputs: list[str], outputs: list[str], event_type: str = "COMPLETE",
              namespace: str = "treasury-desk", run_id: str | None = None) -> dict:
    """One OpenLineage RunEvent (START, COMPLETE or FAIL) for a job that read `inputs` and wrote `outputs`."""
    ds = lambda name: {"namespace": "file", "name": name}   # noqa: E731
    return {
        "eventType": event_type,
        "eventTime": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "producer": PRODUCER,
        "schemaURL": "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent",
        "run": {"runId": run_id or str(uuid.uuid4())},
        "job": {"namespace": namespace, "name": job},
        "inputs": [ds(n) for n in inputs],
        "outputs": [ds(n) for n in outputs],
    }


def save(events: list[dict], folder) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for i, e in enumerate(events):
        (folder / f"{i:02d}_{e['job']['name']}.json").write_text(json.dumps(e, indent=2), encoding="utf-8")
    return folder


def load(folder) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(folder).glob("*.json"))]


def edges(events: list[dict]) -> list[tuple[str, str, str]]:
    """(input dataset, job, output dataset) for every completed run."""
    return [(i["name"], e["job"]["name"], o["name"]) for e in events if e["eventType"] == "COMPLETE"
            for i in e["inputs"] for o in e["outputs"]]


def upstream(events: list[dict], dataset: str) -> set[str]:
    """Every dataset that feeds `dataset`, directly or indirectly (where did this number come from?)."""
    found, todo = set(), [dataset]
    while todo:
        d = todo.pop()
        for src, _, dst in edges(events):
            if dst == d and src not in found:
                found.add(src)
                todo.append(src)
    return found


def downstream(events: list[dict], dataset: str) -> set[str]:
    """Every dataset built from `dataset` (if this source is wrong, what else is wrong? impact analysis)."""
    found, todo = set(), [dataset]
    while todo:
        d = todo.pop()
        for src, _, dst in edges(events):
            if src == d and dst not in found:
                found.add(dst)
                todo.append(dst)
    return found
