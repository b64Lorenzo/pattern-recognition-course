"""Legacy row-oriented extraction prototype; the CLI uses patient_pipeline."""

from __future__ import annotations

import csv
from collections import Counter
from contextlib import ExitStack
import json
import math
import re
from pathlib import Path
import random
from typing import Iterable

from src.logger import LogLevel, Logger
from src.parameter_map import load_parameter_map as _load_xml_parameter_map
from src.csv_utils import (
    csv_encoding_and_dialect as _csv_encoding_and_dialect,
    is_missing as _is_missing,
    normalise as _normalise,
)


PHASES = ("Pre-operative", "Post-operative")
HEATMAP_SAMPLE_LIMIT = 500


def classify_outcome(
    value: object,
    success_values: Iterable[str],
    failure_values: Iterable[str],
) -> str:
    """Classify only explicitly configured categories; never infer from a score."""
    token = _normalise(value)
    if not token:
        return "unclassified"
    success = {_normalise(item) for item in success_values}
    failure = {_normalise(item) for item in failure_values}
    if success & failure:
        raise ValueError("Success and failure outcome values must not overlap")
    if token in success:
        return "success"
    if token in failure:
        return "failure"
    return "unclassified"


def load_parameter_map(path: str | Path) -> dict:
    return _load_xml_parameter_map(path)


def discover_csv_files(inputs: Iterable[str | Path], output_dir: str | Path) -> list[Path]:
    output = Path(output_dir).resolve()
    excluded = {".git", ".vs", ".venv", "venv", "__pycache__"}
    found: set[Path] = set()
    for item in inputs:
        path = Path(item)
        if not path.exists():
            raise FileNotFoundError(f"Input path does not exist: {path}")
        candidates = [path] if path.is_file() else path.rglob("*.csv")
        for candidate in candidates:
            if not candidate.is_file() or candidate.suffix.casefold() != ".csv":
                continue
            resolved = candidate.resolve()
            if resolved == output or output in resolved.parents:
                continue
            if any(part.casefold() in excluded for part in resolved.parts):
                continue
            found.add(resolved)
    return sorted(found, key=lambda path: str(path).casefold())


_NUMBERED_PART = re.compile(r"^(?P<family>.+)_(?P<part>[0-9]+)$")


def patient_family(path: Path) -> tuple[str, str]:
    """Split a numbered file part, preserving zero-padded patient identifiers."""
    match = _NUMBERED_PART.fullmatch(path.stem)
    if not match:
        return path.stem, ""
    part = match.group("part")
    if len(part) >= 3 and part.startswith("0"):
        return path.stem, ""
    return match.group("family"), part


def group_patient_files(files: Iterable[Path]) -> list[tuple[Path, ...]]:
    """Group same-directory numbered parts before assigning patient pseudonyms."""
    grouped: dict[tuple[Path, str], list[Path]] = {}
    for path in sorted(files, key=lambda item: (str(item).casefold(), str(item))):
        family, _ = patient_family(path)
        grouped.setdefault((path.parent, family), []).append(path)
    return [tuple(members) for members in grouped.values()]


def _match_columns(headers: list[str], parameters: list[dict]) -> tuple[list[dict], list[dict]]:
    indexes: dict[str, int] = {}
    for index, header in enumerate(headers):
        indexes.setdefault(_normalise(header), index)

    selected: list[dict] = []
    manifest: list[dict] = []
    seen_indexes: set[tuple[str, int]] = set()
    for parameter in parameters:
        fields = parameter.get("source_fields", [])
        if not fields:
            status = (
                "derived_not_computed"
                if (
                    "derived" in parameter.get("role", "").casefold()
                    or parameter.get("parameter_type") in {"derived_numeric", "derived_duration"}
                    or parameter.get("workbook_exact_field_entry", "").casefold().startswith("derived from")
                )
                else "source_unmapped"
            )
            manifest.append({
                "phase": parameter["phase"],
                "parameter": parameter["name"],
                "source_column": "",
                "status": status,
                "notes": parameter.get("notes", ""),
            })
            continue
        for field in fields:
            index = indexes.get(_normalise(field))
            if index is None:
                manifest.append({
                    "phase": parameter["phase"],
                    "parameter": parameter["name"],
                    "source_column": field,
                    "status": "column_missing",
                    "notes": parameter.get("notes", ""),
                })
                continue
            manifest.append({
                "phase": parameter["phase"],
                "parameter": parameter["name"],
                "source_column": headers[index],
                "status": "matched",
                "notes": parameter.get("notes", ""),
            })
            key = (parameter["phase"], index)
            if key not in seen_indexes:
                selected.append({
                    "phase": parameter["phase"],
                    "parameter": parameter["name"],
                    "priority": parameter.get("priority", ""),
                    "source_column": headers[index],
                    "index": index,
                })
                seen_indexes.add(key)
    return selected, manifest


def _write_heatmap(path: Path, source_id: str, columns: list[dict], sample: list[tuple[int, list[bool]]], total_rows: int) -> None:
    if not columns or not sample:
        return
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.colors import ListedColormap
    except ImportError as error:
        raise RuntimeError("Heatmap plotting requires Matplotlib; install it with: py -3 -m pip install -r requirements.txt") from error

    matrix = [[1 if is_missing else 0 for is_missing in mask] for _, mask in sample]
    figure_width = max(8, min(18, 0.75 * len(columns) + 4))
    figure_height = max(4, min(12, 0.018 * len(sample) + 3))
    figure, axis = plt.subplots(figsize=(figure_width, figure_height))
    image = axis.imshow(
        matrix,
        aspect="auto",
        interpolation="nearest",
        cmap=ListedColormap(["#2f855a", "#c53030"]),
        vmin=0,
        vmax=1,
    )
    axis.set_xticks(range(len(columns)))
    axis.set_xticklabels([item["parameter"] for item in columns], rotation=45, ha="right")
    y_step = max(1, len(sample) // 20)
    ticks = list(range(0, len(sample), y_step))
    axis.set_yticks(ticks)
    axis.set_yticklabels([str(sample[index][0]) for index in ticks])
    axis.set_xlabel("Matched pre-operative parameter")
    axis.set_ylabel("Source record number (sampled rows shown if >500)")
    note = f"{total_rows:,} records; heatmap shows {len(sample):,} deterministic sample rows"
    axis.set_title(f"Pre-operative missingness — {source_id}\n{note}")
    colorbar = figure.colorbar(image, ax=axis, ticks=[0, 1], fraction=0.04, pad=0.03)
    colorbar.ax.set_yticklabels(["Observed", "Missing"])
    figure.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=150)
    plt.close(figure)


def _phi(n11: int, n10: int, n01: int, n00: int) -> str:
    denominator = math.sqrt((n11 + n10) * (n01 + n00) * (n11 + n01) * (n10 + n00))
    if denominator == 0:
        return ""
    return f"{(n11 * n00 - n10 * n01) / denominator:.8f}"


def analyze_inputs(
    inputs: Iterable[str | Path],
    output_dir: str | Path,
    parameter_map_path: str | Path,
    logger: Logger,
    *,
    outcome_column: str | None = None,
    success_values: Iterable[str] | None = None,
    failure_values: Iterable[str] | None = None,
) -> list[dict]:
    """Extract the two in-scope phases and write a descriptive missingness study."""
    mapping = load_parameter_map(parameter_map_path)
    output = Path(output_dir)
    files = discover_csv_files(inputs, output)
    if not files:
        raise ValueError("No input CSV files were found")

    category_rules = mapping.get("patient_outcome_rules", {}).get("categorical_rules", [])
    configured_success = tuple(
        value for rule in category_rules if rule.get("classification") == "success"
        for value in rule.get("values", [])
    )
    configured_failure = tuple(
        value for rule in category_rules if rule.get("classification") == "failure"
        for value in rule.get("values", [])
    )
    success_values = tuple(success_values) if success_values is not None else configured_success
    failure_values = tuple(failure_values) if failure_values is not None else configured_failure
    if {_normalise(item) for item in success_values} & {_normalise(item) for item in failure_values}:
        raise ValueError("Success and failure outcome values must not overlap")

    output.mkdir(parents=True, exist_ok=True)
    (output / "extracted" / "preoperative").mkdir(parents=True, exist_ok=True)
    (output / "extracted" / "postoperative").mkdir(parents=True, exist_ok=True)
    (output / "missingness_heatmaps").mkdir(parents=True, exist_ok=True)

    summary_path = output / "missingness_summary.csv"
    records_path = output / "missingness_by_record.csv"
    patterns_path = output / "missingness_patterns.csv"
    pairwise_path = output / "pairwise_missingness.csv"
    manifest_path = output / "extraction_manifest.csv"
    labels_path = output / "postoperative_labels.csv"
    label_summary_path = output / "postoperative_label_summary.csv"

    reports: list[dict] = []
    missing_tokens = {_normalise(token) for token in mapping.get("missing_value_tokens", [])}
    with ExitStack() as stack:
        summary_writer = csv.DictWriter(stack.enter_context(summary_path.open("w", newline="", encoding="utf-8")), fieldnames=["source_id", "parameter", "priority", "source_column", "status", "records", "missing_count", "missing_percent"])
        summary_writer.writeheader()
        records_writer = csv.DictWriter(stack.enter_context(records_path.open("w", newline="", encoding="utf-8")), fieldnames=["source_id", "source_record", "preoperative_features", "missing_count", "missing_percent"])
        records_writer.writeheader()
        patterns_writer = csv.DictWriter(stack.enter_context(patterns_path.open("w", newline="", encoding="utf-8")), fieldnames=["source_id", "missing_parameters", "record_count", "percent"])
        patterns_writer.writeheader()
        pairwise_writer = csv.DictWriter(stack.enter_context(pairwise_path.open("w", newline="", encoding="utf-8")), fieldnames=["source_id", "feature_a", "feature_b", "both_missing", "both_present", "phi_missingness"])
        pairwise_writer.writeheader()
        manifest_writer = csv.DictWriter(stack.enter_context(manifest_path.open("w", newline="", encoding="utf-8")), fieldnames=["source_id", "phase", "parameter", "source_column", "status", "notes"])
        manifest_writer.writeheader()
        labels_writer = csv.DictWriter(stack.enter_context(labels_path.open("w", newline="", encoding="utf-8")), fieldnames=["source_id", "source_record", "outcome_column", "label"])
        labels_writer.writeheader()
        label_summary_writer = csv.DictWriter(stack.enter_context(label_summary_path.open("w", newline="", encoding="utf-8")), fieldnames=["source_id", "outcome_column", "label", "record_count", "percent"])
        label_summary_writer.writeheader()

        for file_number, source in enumerate(files, start=1):
            source_id = f"input_{file_number:03d}"
            key = source_id
            encoding, dialect = _csv_encoding_and_dialect(source)
            with source.open("r", newline="", encoding=encoding) as stream:
                reader = csv.reader(stream, dialect)
                headers = next(reader, [])
                headers = [header.strip().lstrip("\ufeff") for header in headers]
                parameters = [item for item in mapping["parameters"] if item["phase"] in PHASES]
                selected, manifest_rows = _match_columns(headers, parameters)
                for item in manifest_rows:
                    manifest_writer.writerow({"source_id": source_id, **item})

                by_phase = {phase: [item for item in selected if item["phase"] == phase] for phase in PHASES}
                outcome_index = None
                if outcome_column:
                    normalized_headers = {_normalise(value): index for index, value in enumerate(headers)}
                    outcome_index = normalized_headers.get(_normalise(outcome_column))
                    if outcome_index is None:
                        manifest_writer.writerow({
                            "source_id": source_id,
                            "phase": "Post-operative",
                            "parameter": "Configured outcome label",
                            "source_column": outcome_column,
                            "status": "column_missing",
                            "notes": "Outcome values were not found; labels remain unclassified.",
                        })
                    else:
                        if all(item["index"] != outcome_index for item in by_phase["Post-operative"]):
                            by_phase["Post-operative"].append({
                                "phase": "Post-operative",
                                "parameter": "Configured outcome category",
                                "priority": "User-configured",
                                "source_column": headers[outcome_index],
                                "index": outcome_index,
                            })
                        manifest_writer.writerow({
                            "source_id": source_id,
                            "phase": "Post-operative",
                            "parameter": "Configured outcome label",
                            "source_column": headers[outcome_index],
                            "status": "matched",
                            "notes": "Only configured exact success/failure values are classified.",
                        })

                pre_columns = by_phase["Pre-operative"]
                post_columns = by_phase["Post-operative"]
                pre_path = output / "extracted" / "preoperative" / f"{key}.csv"
                post_path = output / "extracted" / "postoperative" / f"{key}.csv"
                pre_stream = stack.enter_context(pre_path.open("w", newline="", encoding="utf-8")) if pre_columns else None
                post_stream = stack.enter_context(post_path.open("w", newline="", encoding="utf-8")) if post_columns else None
                pre_writer = csv.writer(pre_stream) if pre_stream else None
                post_writer = csv.writer(post_stream) if post_stream else None
                if pre_writer:
                    pre_writer.writerow(["source_record", *[item["source_column"] for item in pre_columns]])
                if post_writer:
                    post_writer.writerow(["source_record", *[item["source_column"] for item in post_columns], "outcome_label"])

                feature_missing = [0] * len(pre_columns)
                pairwise_counts: dict[tuple[int, int], list[int]] = {
                    (left, right): [0, 0, 0, 0]
                    for left in range(len(pre_columns))
                    for right in range(left + 1, len(pre_columns))
                }
                pattern_counts: Counter[tuple[int, ...]] = Counter()
                heatmap_sample: list[tuple[int, list[bool]]] = []
                sample_rng = random.Random(0)
                outcome_counts: Counter[str] = Counter()
                record_count = 0
                missing_cells = 0
                complete_records = 0

                for row in reader:
                    if not row:
                        continue
                    record_count += 1
                    if pre_writer:
                        pre_writer.writerow([record_count, *[row[item["index"]] if item["index"] < len(row) else "" for item in pre_columns]])
                    label_value = row[outcome_index] if outcome_index is not None and outcome_index < len(row) else ""
                    label = classify_outcome(label_value, success_values, failure_values) if outcome_index is not None else "unclassified"
                    outcome_counts[label] += 1
                    labels_writer.writerow({
                        "source_id": source_id,
                        "source_record": record_count,
                        "outcome_column": outcome_column or "",
                        "label": label,
                    })
                    if post_writer:
                        post_writer.writerow([record_count, *[row[item["index"]] if item["index"] < len(row) else "" for item in post_columns], label])

                    mask = [
                        _is_missing(row[item["index"]] if item["index"] < len(row) else "", missing_tokens)
                        for item in pre_columns
                    ]
                    count_missing = sum(mask)
                    missing_cells += count_missing
                    if pre_columns and count_missing == 0:
                        complete_records += 1
                    if pre_columns:
                        percent_missing = 100.0 * count_missing / len(pre_columns)
                        records_writer.writerow({
                            "source_id": source_id,
                            "source_record": record_count,
                            "preoperative_features": len(pre_columns),
                            "missing_count": count_missing,
                            "missing_percent": f"{percent_missing:.4f}",
                        })
                        pattern_counts[tuple(index for index, absent in enumerate(mask) if absent)] += 1
                        for index, absent in enumerate(mask):
                            feature_missing[index] += int(absent)
                        for (left, right), counts in pairwise_counts.items():
                            left_absent, right_absent = mask[left], mask[right]
                            if left_absent and right_absent:
                                counts[0] += 1
                            elif left_absent:
                                counts[1] += 1
                            elif right_absent:
                                counts[2] += 1
                            else:
                                counts[3] += 1
                        if len(heatmap_sample) < HEATMAP_SAMPLE_LIMIT:
                            heatmap_sample.append((record_count, mask))
                        else:
                            replacement = sample_rng.randrange(record_count)
                            if replacement < HEATMAP_SAMPLE_LIMIT:
                                heatmap_sample[replacement] = (record_count, mask)

                if pre_stream:
                    pre_stream.close()
                if post_stream:
                    post_stream.close()

                for index, item in enumerate(pre_columns):
                    missing = feature_missing[index]
                    percent = 100.0 * missing / record_count if record_count else 0.0
                    summary_writer.writerow({
                        "source_id": source_id,
                        "parameter": item["parameter"],
                        "priority": item["priority"],
                        "source_column": item["source_column"],
                        "status": "present" if record_count else "empty_data",
                        "records": record_count,
                        "missing_count": missing,
                        "missing_percent": f"{percent:.4f}" if record_count else "",
                    })
                for item in manifest_rows:
                    if item["phase"] == "Pre-operative" and item["status"] in {"column_missing", "source_unmapped", "derived_not_computed"}:
                        summary_writer.writerow({
                            "source_id": source_id,
                            "parameter": item["parameter"],
                            "priority": next((p.get("priority", "") for p in parameters if p["name"] == item["parameter"]), ""),
                            "source_column": item["source_column"],
                            "status": item["status"],
                            "records": record_count,
                            "missing_count": "",
                            "missing_percent": "",
                        })

                for pattern, count in sorted(pattern_counts.items(), key=lambda pair: (-pair[1], pair[0])):
                    labels = [pre_columns[index]["parameter"] for index in pattern] or ["(complete)"]
                    patterns_writer.writerow({
                        "source_id": source_id,
                        "missing_parameters": " | ".join(labels),
                        "record_count": count,
                        "percent": f"{100.0 * count / record_count:.4f}" if record_count else "",
                    })
                for (left, right), counts in pairwise_counts.items():
                    n11, n10, n01, n00 = counts
                    pairwise_writer.writerow({
                        "source_id": source_id,
                        "feature_a": pre_columns[left]["parameter"],
                        "feature_b": pre_columns[right]["parameter"],
                        "both_missing": n11,
                        "both_present": n00,
                        "phi_missingness": _phi(n11, n10, n01, n00),
                    })
                for label, count in sorted(outcome_counts.items()):
                    label_summary_writer.writerow({
                        "source_id": source_id,
                        "outcome_column": outcome_column or "",
                        "label": label,
                        "record_count": count,
                        "percent": f"{100.0 * count / record_count:.4f}" if record_count else "",
                    })

                if pre_columns:
                    heatmap_sample.sort(key=lambda item: item[0])
                    _write_heatmap(output / "missingness_heatmaps" / f"{key}.png", source_id, pre_columns, heatmap_sample, record_count)
                overall = 100.0 * missing_cells / (record_count * len(pre_columns)) if record_count and pre_columns else 0.0
                reports.append({
                    "source_id": source_id,
                    "records": record_count,
                    "preoperative_fields": len(pre_columns),
                    "preoperative_missing_cells": missing_cells,
                    "preoperative_missing_percent": overall,
                    "preoperative_complete_records": complete_records,
                    "preoperative_complete_percent": 100.0 * complete_records / record_count if record_count else 0.0,
                    "postoperative_fields": len(post_columns),
                    "outcome_column": outcome_column or "",
                    "outcome_labels": dict(outcome_counts),
                    "heatmap": f"missingness_heatmaps/{key}.png" if pre_columns else "",
                })
                logger.write(
                    LogLevel.INFO,
                    "csv-analysis",
                    f"processed {source_id}; records={record_count}; preoperative_fields={len(pre_columns)}; postoperative_fields={len(post_columns)}; missing_cells={missing_cells}",
                )

    _write_report(output / "missingness_report.md", reports, mapping)
    logger.write(LogLevel.INFO, "csv-analysis", f"analysis completed; files={len(reports)}; artifacts written to the selected output directory")
    return reports


def _write_report(path: Path, reports: list[dict], mapping: dict) -> None:
    lines = [
        "# CSV extraction and pre-operative missingness report",
        "",
        f"Parameter map source: `{mapping.get('source_workbook', 'configured parameter map')}`.",
        "Included phases: Pre-operative and Post-operative. During-operation fields are excluded.",
        "",
        "Missingness counts and patterns are descriptive; they do not determine the missingness mechanism. No imputation is performed.",
        "",
        "A missing column is reported as `column_missing`, which is distinct from a present column whose values are all missing. Derived outcomes, unresolved fields, and time-windowed values are not inferred by this extractor.",
        "",
        "`source_record` is only the ordinal CSV data row, not a patient or surgery identifier. In long-format exports it may represent a measurement/timepoint rather than one patient.",
        "",
        "## Input files",
        "",
    ]
    if not reports:
        lines.append("No CSV files were analyzed.")
    for item in reports:
        lines.extend([
            f"### `{item['source_id']}`",
            "",
            f"- Records: {item['records']}",
            f"- Matched pre-operative fields: {item['preoperative_fields']}",
            f"- Missing pre-operative cells: {item['preoperative_missing_cells']} ({item['preoperative_missing_percent']:.2f}%)",
            f"- Complete records (complete cases) among matched pre-operative fields: {item['preoperative_complete_records']} ({item['preoperative_complete_percent']:.2f}%)",
            f"- Matched post-operative fields: {item['postoperative_fields']}",
            f"- Configured outcome column: `{item['outcome_column'] or 'not configured'}`",
            f"- Outcome labels: `{json.dumps(item['outcome_labels'], ensure_ascii=False, sort_keys=True)}`",
            f"- Heatmap: `{item['heatmap'] or 'not generated (no pre-operative fields matched)'}`",
            "",
        ])
    lines.extend([
        "## Detailed tables",
        "",
        "Per-field availability and row-level completeness are summarized in the missingness tables; co-missing patterns and pairwise phi statistics are in `missingness_patterns.csv` and `pairwise_missingness.csv`.",
        "",
        "## Interpretation limits",
        "",
        "CSV sources are analyzed independently. The application does not join patient rows across files because no approved patient/case key is defined in the parameter sheet. Add a reviewed join key before combining exports; do not infer one from row order.",
        "",
        "Success/failure labels are created only when an outcome column is explicitly selected and its value exactly matches the configured category list. By default, `optimal condition` and `mid condition` are success; severe-problem and death categories are failure; all other values are unclassified. Numeric AKI, ICU stay, ventilation, PRISM II, or PIM values are not thresholded into classes. Confirm endpoint timing and clinical definitions before modeling.",
        "",
        "The 30-day mortality source field in the workbook is flagged as needing verification; extraction alone does not establish that it represents 30-day mortality. Time windows such as the first 72 hours are not applied without timestamps and a surgery-time anchor.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
