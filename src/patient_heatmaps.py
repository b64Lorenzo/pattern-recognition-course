"""Rendering helpers kept separate from extraction and outcome rules."""

from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from src.csv_utils import number_component

HEATMAP_SAMPLE_LIMIT = 500


def _number(value: object) -> float | None:
    return number_component(value)


def _pyplot():
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as error:
        raise RuntimeError("Heatmaps require Matplotlib; install dependencies from requirements.txt") from error
    return plt


def write_placeholder_heatmap(path: Path, title: str, message: str) -> None:
    plt = _pyplot()
    figure, axis = plt.subplots(figsize=(8, 3.5))
    axis.set_title(title)
    axis.text(0.5, 0.5, message, ha="center", va="center", transform=axis.transAxes)
    axis.set_axis_off()
    figure.tight_layout()
    figure.savefig(path, dpi=150)
    plt.close(figure)


def write_heatmap(path: Path, matrix: list[list[float]], row_labels: list[str], column_labels: list[str], title: str, colorbar_label: str, row_axis_label: str = "Patient pseudonym") -> None:
    if not matrix or not column_labels:
        write_placeholder_heatmap(path, title, "No mapped measurements available to plot.")
        return
    plt = _pyplot()
    width = max(8.0, min(22.0, 0.7 * len(column_labels) + 3.0))
    height = max(3.5, min(18.0, 0.28 * len(row_labels) + 2.5))
    figure, axis = plt.subplots(figsize=(width, height))
    cmap = plt.get_cmap("viridis").copy()
    cmap.set_bad(color="#b8b8b8")
    image = axis.imshow(matrix, aspect="auto", interpolation="nearest", cmap=cmap)
    axis.set_xticks(range(len(column_labels)), labels=column_labels, rotation=60, ha="right")
    if len(row_labels) <= 80:
        axis.set_yticks(range(len(row_labels)), labels=row_labels)
    else:
        axis.set_yticks([])
    axis.set_title(title)
    axis.set_xlabel("Parameter")
    axis.set_ylabel(row_axis_label)
    figure.colorbar(image, ax=axis, label=colorbar_label)
    figure.tight_layout()
    figure.savefig(path, dpi=150)
    plt.close(figure)


def sample_patients(patients: list[dict], limit: int = HEATMAP_SAMPLE_LIMIT) -> list[dict]:
    return patients if len(patients) <= limit else random.Random(0).sample(patients, limit)


def build_parameter_matrix(patients: list[dict], parameters: Sequence[Mapping], *, value_key: str, categorical_ids: Iterable[str] = ()) -> tuple[list[list[float]], list[str]]:
    """Build a visualization matrix while retaining configured empty columns."""
    categorical = set(categorical_ids)
    matrix: list[list[float]] = [[] for _ in patients]
    labels: list[str] = []

    for parameter in parameters:
        parameter_id = str(parameter["id"])
        values = [patient.get(value_key, {}).get(parameter_id, "") for patient in patients]
        if parameter_id in categorical:
            categories = sorted({str(value) for value in values if value not in (None, "")})
            if any(value in (None, "") for value in values):
                categories.append("__MISSING__")
            for category in categories:
                label = "NA" if category == "__MISSING__" else category
                labels.append(f"{parameter_id}={label}")
                for row, value in zip(matrix, values):
                    row.append(1.0 if (str(value) == category or (category == "__MISSING__" and value in (None, ""))) else 0.0)
            continue

        numeric = [_number(value) for value in values]
        usable = [value for value in numeric if value is not None]
        if not usable:
            labels.append(f"{parameter_id} (no observed values)")
            for row in matrix:
                row.append(math.nan)
            continue
        low, high = min(usable), max(usable)
        labels.append(f"{parameter_id} (min-max)")
        for row, value in zip(matrix, numeric):
            row.append(math.nan if value is None else (0.5 if high == low else (value - low) / (high - low)))
    return matrix, labels


def write_parameter_heatmap(path: Path, patients: list[dict], parameters: Sequence[Mapping], *, value_key: str, title: str, categorical_ids: Iterable[str] = ()) -> None:
    selected = sample_patients(patients)
    if not selected:
        write_placeholder_heatmap(path, title, "No patient records available to plot.")
        return
    matrix, labels = build_parameter_matrix(
        selected,
        parameters,
        value_key=value_key,
        categorical_ids=categorical_ids,
    )
    write_heatmap(path, matrix, [patient["patient_id"] for patient in selected], labels, title, "Min-max scaled value / one-hot indicator")
