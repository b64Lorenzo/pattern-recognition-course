"""Inventory CSV column names without loading patient records."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
import csv
from html import escape
from io import StringIO
from pathlib import Path
from typing import Iterable

from src.pipeline import discover_csv_files, patient_family

_MAX_HEADER_BYTES = 1_000_000


def _header_separators(sample: str) -> tuple[Counter, bool]:
    """Count delimiters outside quoted names and detect a continued header."""
    counts = Counter()
    in_quotes = False
    index = 0
    while index < len(sample):
        char = sample[index]
        if char == '"':
            if in_quotes and index + 1 < len(sample) and sample[index + 1] == '"':
                index += 1
            else:
                in_quotes = not in_quotes
        elif not in_quotes and char in ",;\t|":
            counts[char] += 1
        index += 1
    return counts, in_quotes


def _delimiter_from_header(sample: str) -> str:
    """Prefer structural tabs/semicolons over commas in clinical field labels."""
    counts, _ = _header_separators(sample)
    for delimiter in ("\t", ";", ",", "|"):
        if counts[delimiter]:
            return delimiter
    return ","  # One-column CSV.


def _read_header(path: Path) -> list[str]:
    """Decode and parse the first CSV record only, including quoted newlines."""
    with path.open("rb") as stream:
        first_line = stream.readline(_MAX_HEADER_BYTES + 1)
        if len(first_line) > _MAX_HEADER_BYTES:
            raise ValueError("CSV header exceeds the supported size")
        try:
            sample = first_line.decode("utf-8-sig")
            encoding = "utf-8-sig"
        except UnicodeDecodeError:
            sample = first_line.decode("cp1252")
            encoding = "cp1252"
        if not first_line:
            raise ValueError("Encountered an empty CSV header")
        sample_bytes = first_line
        for _ in range(20):
            if not _header_separators(sample)[1]:
                break
            continuation = stream.readline(_MAX_HEADER_BYTES - len(sample_bytes) + 1)
            if len(sample_bytes) + len(continuation) > _MAX_HEADER_BYTES:
                raise ValueError("CSV header exceeds the supported size")
            if not continuation:
                raise ValueError("Unterminated quoted CSV header")
            sample_bytes += continuation
            sample = sample_bytes.decode(encoding)
        else:
            raise ValueError("CSV header has too many physical lines")
        delimiter = _delimiter_from_header(sample)
        header = next(csv.reader(StringIO(sample, newline=""), delimiter=delimiter, strict=True), [])
        if not header or all(not field for field in header):
            raise ValueError("Encountered an empty CSV header")
        return header


def _markdown(value: object) -> str:
    """Keep filenames and field names in one Markdown table cell."""
    text = escape(str(value)).replace("\\", "\\\\").replace("|", "\\|")
    return "".join(char if char.isprintable() else " " for char in text)


def _epic_class(field: str) -> str | None:
    parts = field.split(".", 2)
    if len(parts) == 3 and parts[0] == "epic" and parts[1] and parts[2]:
        return ".".join(parts[:2])
    return None


def _append_phase_tables(
    lines: list[str], frequency: Counter, files: list[dict], parameter_map: Mapping | None,
) -> None:
    """Report XML-assigned phases without inferring timing from column names."""
    assignments: dict[str, set[str]] = defaultdict(set)
    for parameter in (parameter_map or {}).get("parameters", []):
        for field in parameter.get("source_fields", []):
            if field in frequency:
                assignments[field].add(parameter["phase"])
    class_files: dict[str, set[int]] = defaultdict(set)
    for index, item in enumerate(files):
        for field in set(item["headers"]):
            name = _epic_class(field)
            if name:
                class_files[name].add(index)

    lines.extend(["", "Only exact catalogued source fields receive XML phase assignments. The same header may appear in multiple phases; header text alone cannot establish when a value was measured."])
    for phase in ("Pre-operative", "During-operative", "Post-operative"):
        fields = sorted((field for field in frequency if phase in assignments.get(field, ())), key=str.casefold)
        lines.extend(["", f"## {phase} headers", "", "| Header | Epic class | Files |", "|---|---|---:|"])
        for field in fields:
            lines.append("| " + " | ".join(_markdown(value) for value in (
                field, _epic_class(field) or "—", frequency[field]
            )) + " |")
        if not fields:
            lines.append("| — | — | 0 |")
        counts = Counter(name for field in fields if (name := _epic_class(field)))
        lines.extend(["", f"### Classes in {phase}", "", "| Epic class | Headers in section |", "|---|---:|"])
        for name, count in sorted(counts.items(), key=lambda pair: pair[0].casefold()):
            lines.append(f"| {_markdown(name)} | {count} |")
        if not counts:
            lines.append("| — | 0 |")

    lines.extend(["", "## Unassigned headers", "", "No XML-selected parameter maps these exact names. Do not infer a clinical phase from an Epic class alone.", "",
                  "| Header | Epic class | Files |", "|---|---|---:|"])
    for field in sorted((field for field in frequency if not assignments.get(field)), key=str.casefold):
        lines.append("| " + " | ".join(_markdown(value) for value in (
            field, _epic_class(field) or "—", frequency[field]
        )) + " |")
    lines.extend(["", "## Epic class index", "", "All discovered epic.CLASS.item headers, including classes with no XML assignment.", "",
                  "| Epic class | Distinct headers | CSV files | Assigned phase(s) |", "|---|---:|---:|---|"])
    for name in sorted(class_files, key=str.casefold):
        fields = [field for field in frequency if _epic_class(field) == name]
        phases = ", ".join(phase for phase in ("Pre-operative", "During-operative", "Post-operative")
                           if any(phase in assignments.get(field, ()) for field in fields)) or "Unassigned"
        lines.append("| " + " | ".join(_markdown(value) for value in (
            name, len(fields), len(class_files[name]), phases
        )) + " |")
    if not class_files:
        lines.append("| — | 0 | 0 | Unassigned |")


def write_csv_header_descriptions(
    inputs: Iterable[str | Path],
    output_directory: str | Path,
    inventory_output: str | Path,
    summary_output: str | Path,
    *,
    parameter_map: Mapping | None = None,
) -> dict[str, int]:
    """Write exact per-file headers and a family/schema/phase summary; never read data rows."""
    sources = [Path(item).resolve() for item in inputs]
    inventory = Path(inventory_output).resolve()
    summary = Path(summary_output).resolve()
    if inventory == summary:
        raise ValueError("CSV header inventory and summary outputs must differ")
    discovered = discover_csv_files(sources, output_directory)
    if inventory in discovered or summary in discovered:
        raise ValueError("Header report output overlaps a CSV input; choose a separate output directory")
    paths = discovered
    if not paths:
        raise ValueError("No CSV files found for header descriptions")

    files = []
    for path in paths:
        for index, source in enumerate(sources, start=1):
            if source == path or (source.is_dir() and path.is_relative_to(source)):
                relative = path.name if source == path else path.relative_to(source).as_posix()
                break
        else:
            raise ValueError("Discovered a CSV outside the configured inputs")
        base, part = patient_family(path)
        family = (Path(relative).parent / base).as_posix()
        files.append({
            "source": index,
            "file": relative,
            "family": family,
            "part": part,
            "_path": path,
            "_base": base,
            "headers": _read_header(path),
        })
    files.sort(key=lambda item: (item["source"], item["file"].casefold(), item["file"]))
    family_names = {}
    for item in files:
        key = (item["_path"].parent, item["_base"])
        item["family"] = family_names.setdefault(key, item["family"])

    schemas = {}
    schema_files = defaultdict(list)
    groups = defaultdict(list)
    frequency = Counter()
    for item in files:
        fields = item["headers"]
        key = tuple(fields)
        if key not in schemas:
            schemas[key] = f"S{len(schemas) + 1}"
        item["schema"] = schemas[key]
        schema_files[item["schema"]].append((item["source"], item["file"]))
        groups[(item["_path"].parent, item["_base"])].append(item)
        frequency.update(set(fields))

    shared = sum(count == len(files) for count in frequency.values())
    lines = [
        "# CSV header summary", "",
        "Header-only inspection. No patient rows, values, or outcomes are read. File names may identify patients: keep this report private.", "",
        f"- Files: {len(files)}",
        f"- Distinct headers: {len(frequency)}",
        f"- Headers in every file: {shared}",
        f"- Distinct ordered schemas: {len(schemas)}",
        f"- Patient families (by filename): {len(groups)}", "",
        "## Files", "",
        "| Input | CSV file (relative to input) | Family | Numbered part | Columns | Schema |",
        "|---|---|---|---|---:|---|",
    ]
    for item in files:
        lines.append("| " + " | ".join(_markdown(value) for value in (
            item["source"], item["file"], item["family"], item["part"] or "—",
            len(item["headers"]), item["schema"],
        )) + " |")
    lines.extend(["", "## Ordered header schemas", "", "Schemas match only if every exact column name and its position match.", "",
                  "| Schema | Files | CSV file names |", "|---|---:|---|"])
    for schema, members in schema_files.items():
        names = ", ".join(f"input {source}: {file}" for source, file in members)
        lines.append("| " + " | ".join(_markdown(value) for value in (schema, len(members), names)) + " |")
    lines.extend(["", "## Filename families", "", "The final `_` plus a number denotes another file for the same patient, except a zero-padded identifier of three or more digits (for example, `ref_0002`) remains part of the patient name. Families never span directories.", "",
                  "| Input(s) | Family | Files | CSV file names |", "|---|---|---:|---|"])
    for members in sorted(groups.values(), key=lambda items: (items[0]["source"], items[0]["family"].casefold())):
        sources_in_family = ", ".join(str(value) for value in sorted({item["source"] for item in members}))
        lines.append("| " + " | ".join(_markdown(value) for value in (
            sources_in_family, members[0]["family"], len(members), ", ".join(item["file"] for item in members),
        )) + " |")
    lines.extend(["", "## Header presence", "", "Exact field names and the number of CSV files containing each one (repeated columns count once per file). Positions and per-file names are in the inventory CSV.", "",
                  "| Header | Files |", "|---|---:|"])
    for field, count in sorted(frequency.items(), key=lambda entry: entry[0].casefold()):
        lines.append(f"| {_markdown(field)} | {count} |")
    _append_phase_tables(lines, frequency, files, parameter_map)

    inventory.parent.mkdir(parents=True, exist_ok=True)
    summary.parent.mkdir(parents=True, exist_ok=True)
    with inventory.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["source", "file", "family", "part", "position", "header"])
        for item in files:
            writer.writerows(
                (item["source"], item["file"], item["family"], item["part"], position, field)
                for position, field in enumerate(item["headers"], start=1)
            )
    summary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"files": len(files), "unique_headers": len(frequency), "shared_headers": shared, "families": len(groups)}
