"""Generate per-parameter distribution plots and aggregate summaries."""

from __future__ import annotations

import csv
from pathlib import Path
import re
import statistics
from collections import Counter
from collections.abc import Mapping

from src.csv_utils import number_component
from src.parameter_map import load_parameter_map
from src.pipeline_config import select_parameter_map


def _number(value: object) -> float | None:
    return number_component(value)


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _plot_distribution(
    path: Path,
    title: str,
    values: list[str],
    bins: int,
    *,
    numeric_expected: bool | None = None,
) -> tuple[str, list[float], Counter[str]]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as error:
        raise RuntimeError("Distribution plots require Matplotlib; install requirements.txt") from error

    observed = [value for value in values if value.strip()]
    numeric = [_number(value) for value in observed]
    is_numeric = (
        bool(observed) and all(value is not None for value in numeric)
        if numeric_expected is None
        else numeric_expected
    )
    figure, axis = plt.subplots(figsize=(7, 4.5))
    categories: Counter[str] = Counter()
    numeric_values = [value for value in numeric if value is not None] if is_numeric else []

    if not observed:
        kind = "no_data"
        axis.text(0.5, 0.5, "No mapped values available", ha="center", va="center", transform=axis.transAxes)
        axis.set_axis_off()
    elif is_numeric and not numeric_values:
        kind = "no_data"
        axis.text(0.5, 0.5, "No numeric values available", ha="center", va="center", transform=axis.transAxes)
        axis.set_axis_off()
    elif is_numeric:
        kind = "numeric"
        axis.hist(numeric_values, bins=bins, color="#4c78a8", edgecolor="white")
        axis.set_xlabel("Recorded value")
        axis.set_ylabel("Patient count")
    else:
        kind = "categorical"
        categories = Counter(observed)
        ordered = sorted(categories.items(), key=lambda item: (-item[1], item[0]))
        axis.bar([item[0] for item in ordered], [item[1] for item in ordered], color="#4c78a8")
        axis.set_xlabel("Recorded category")
        axis.set_ylabel("Patient count")
        axis.tick_params(axis="x", labelrotation=45)
        for tick in axis.get_xticklabels():
            tick.set_ha("right")

    axis.set_title(title)
    figure.tight_layout()
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return kind, numeric_values, categories


def _measurement_column(parameter: Mapping, postoperative_specs: list[dict], available: set[str]) -> str:
    parameter_id = str(parameter["id"])
    if parameter_id in available:
        return parameter_id
    wanted_sources = set(parameter.get("source_fields", []))
    matches = [
        str(spec["id"])
        for spec in postoperative_specs
        if wanted_sources.intersection(spec.get("source_fields", [])) and spec.get("id") in available
    ]
    return matches[0] if len(matches) == 1 else ""


def _format(value: float) -> str:
    return format(value, ".8g")


def generate_parameter_distributions(
    output_dir: str | Path,
    parameter_map_path: str | Path | Mapping,
    *,
    bins: int = 20,
    phase: str = "all",
    parameter_selection: Mapping[str, object] | None = None,
) -> int:
    """Plot every mapped parameter from the patient-level analysis tables."""
    if bins < 1:
        raise ValueError("bins must be at least 1")
    output = Path(output_dir)
    config = (
        parameter_map_path
        if isinstance(parameter_map_path, Mapping)
        else load_parameter_map(parameter_map_path)
    )
    if parameter_selection is not None:
        config = select_parameter_map(config, **parameter_selection)
    postoperative_specs = config.get("postoperative_measurements", [])
    preoperative_rows = _read_rows(output / "patient_vectors.csv")
    postoperative_rows = _read_rows(output / "postoperative_measurements.csv")
    preoperative_columns = set(preoperative_rows[0]) if preoperative_rows else set()
    postoperative_columns = set(postoperative_rows[0]) if postoperative_rows else set()
    plots = output / "parameter_distributions"
    plots.mkdir(parents=True, exist_ok=True)

    summary_rows: list[dict[str, str | int]] = []
    category_rows: list[dict[str, str | int]] = []
    phase_names = {"all": None, "preoperative": "Pre-operative", "postoperative": "Post-operative"}
    try:
        phase_name = phase_names[phase.casefold()]
    except KeyError as error:
        raise ValueError("phase must be all, preoperative, or postoperative") from error
    parameters = [
        parameter for parameter in config.get("parameters", [])
        if parameter.get("phase") in {"Pre-operative", "Post-operative"}
        and (phase_name is None or parameter.get("phase") == phase_name)
    ]
    for parameter in parameters:
        parameter_id = str(parameter["id"])
        parameter_phase = str(parameter["phase"])
        if parameter_phase == "Pre-operative":
            rows = preoperative_rows
            column = parameter_id if parameter_id in preoperative_columns else ""
            filename_phase = "preoperative"
        else:
            rows = postoperative_rows
            column = _measurement_column(parameter, postoperative_specs, postoperative_columns)
            filename_phase = "postoperative"
        values = [row.get(column, "") if column else "" for row in rows]
        numeric_expected = parameter.get("parameter_type") in {"numeric", "derived_numeric", "time_series_numeric"}
        plot_path = plots / f"{filename_phase}_{re.sub(r'[^A-Za-z0-9_-]+', '_', parameter_id)}.png"
        kind, numeric_values, categories = _plot_distribution(
            plot_path,
            f"{parameter.get('name', parameter_id)} — {parameter_phase}",
            values,
            bins,
            numeric_expected=numeric_expected,
        )
        observed_count = len(numeric_values) if numeric_expected else sum(bool(value.strip()) for value in values)
        summary: dict[str, str | int] = {
            "parameter_id": parameter_id,
            "parameter": str(parameter.get("name", parameter_id)),
            "phase": parameter_phase,
            "kind": kind,
            "patient_count": len(rows),
            "observed_count": observed_count,
            "missing_count": len(rows) - observed_count,
            "mean": "",
            "median": "",
            "std": "",
            "min": "",
            "max": "",
            "plot": str(plot_path.relative_to(output)),
        }
        if numeric_values:
            summary.update({
                "mean": _format(statistics.fmean(numeric_values)),
                "median": _format(statistics.median(numeric_values)),
                "std": _format(statistics.pstdev(numeric_values)),
                "min": _format(min(numeric_values)),
                "max": _format(max(numeric_values)),
            })
        summary_rows.append(summary)
        category_rows.extend(
            {
                "parameter_id": parameter_id,
                "phase": parameter_phase,
                "category": category,
                "patient_count": count,
                "percent_of_observed": _format(100.0 * count / observed_count),
            }
            for category, count in sorted(categories.items())
        )

    summary_fields = ["parameter_id", "parameter", "phase", "kind", "patient_count", "observed_count", "missing_count", "mean", "median", "std", "min", "max", "plot"]
    with (output / "parameter_distribution_summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=summary_fields)
        writer.writeheader()
        writer.writerows(summary_rows)
    with (output / "parameter_distribution_categories.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["parameter_id", "phase", "category", "patient_count", "percent_of_observed"])
        writer.writeheader()
        writer.writerows(category_rows)
    return len(summary_rows)
