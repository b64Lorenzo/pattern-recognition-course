"""CSV IO helpers shared by direct and XML-configured CLI workflows."""

from __future__ import annotations

import csv
from pathlib import Path


def read_csv_rows(path: str | Path) -> tuple[list[str], list[dict[str, str]]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise ValueError("CSV has no header")
        return list(reader.fieldnames), list(reader)


def write_csv_rows(path: str | Path, rows: list[dict], fields: list[str]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def join_patient_vectors(
    vectors: list[dict[str, str]], outcomes: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Attach postoperative labels by patient ID, never by row order."""
    def index_unique(rows: list[dict[str, str]], name: str) -> dict[str, dict[str, str]]:
        indexed = {}
        for row in rows:
            patient_id = row.get("patient_id", "").strip()
            if not patient_id or patient_id in indexed:
                raise ValueError(f"{name} must contain unique, non-empty patient_id values")
            indexed[patient_id] = row
        return indexed

    vector_map = index_unique(vectors, "patient vectors")
    outcome_map = index_unique(outcomes, "patient outcomes")
    if any("target" not in row for row in outcomes):
        raise ValueError("patient_outcomes.csv must contain a target column")
    if set(vector_map) != set(outcome_map):
        raise ValueError("Patient vector and postoperative outcome patient_id sets do not match")
    if any("target" in row for row in vectors):
        raise ValueError("patient_vectors.csv must contain pre-operative features only; rerun analyze")
    return [{**row, "target": outcome_map[patient_id].get("target", "NA")}
            for patient_id, row in vector_map.items()]
