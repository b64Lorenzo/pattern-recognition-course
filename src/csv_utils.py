"""Shared CSV decoding, normalization, and per-patient row access."""

from __future__ import annotations

import csv
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator


def normalise(value: object) -> str:
    return " ".join(str(value).strip().casefold().split())


def csv_encoding_and_dialect(path: str | Path) -> tuple[str, csv.Dialect | type[csv.Dialect]]:
    with Path(path).open("rb") as stream:
        sample_bytes = stream.read(65536)
    try:
        sample = sample_bytes.decode("utf-8-sig")
        encoding = "utf-8-sig"
    except UnicodeDecodeError:
        encoding = "cp1252"
        sample = sample_bytes.decode(encoding)
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return encoding, dialect


def is_missing(value: object, tokens: set[str]) -> bool:
    return normalise(value) in tokens


def number(value: object) -> float | None:
    if value is None:
        return None
    try:
        result = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def number_component(value: object) -> float | None:
    """Parse a numeric lab value before an optional pipe-delimited annotation."""
    if value is None:
        return None
    return number(str(value).split("|", 1)[0])


class PatientCsvReader:
    """Stream one patient's files through a shared, sparse header layout."""

    timestamp_fields = ("unixtime", "datetime", "timestamp", "time")

    def __init__(self, paths: str | Path | Iterable[str | Path]) -> None:
        self.paths = (Path(paths),) if isinstance(paths, (str, Path)) else tuple(Path(path) for path in paths)
        if not self.paths:
            raise ValueError("A patient needs at least one CSV file")
        self.headers: list[str] = []
        self.columns: dict[str, list[int]] = {}
        self._sources = []
        for path in self.paths:
            encoding, dialect = csv_encoding_and_dialect(path)
            with path.open("r", encoding=encoding, newline="") as stream:
                headers = [header.strip() for header in next(csv.reader(stream, dialect), [])]
            occurrences: dict[str, int] = {}
            positions = []
            for header in headers:
                key = normalise(header)
                occurrence = occurrences.get(key, 0)
                indexes = self.columns.setdefault(key, [])
                if occurrence == len(indexes):
                    indexes.append(len(self.headers))
                    self.headers.append(header)
                positions.append(indexes[occurrence])
                occurrences[key] = occurrence + 1
            self._sources.append((path, encoding, dialect, positions))

    def rows(self) -> Iterator[list[str]]:
        for path, encoding, dialect, positions in self._sources:
            with path.open("r", encoding=encoding, newline="") as stream:
                reader = csv.reader(stream, dialect)
                next(reader, None)
                for row in reader:
                    merged = [""] * len(self.headers)
                    for index, value in enumerate(row[:len(positions)]):
                        merged[positions[index]] = value
                    yield merged

    def find_index(self, names: list[str] | tuple[str, ...]) -> int | None:
        for name in names:
            matches = self.columns.get(normalise(name), [])
            if len(matches) == 1:
                return matches[0]
        return None

    def timestamp(self, row: list[str]) -> float | None:
        unix_indexes = self.columns.get("unixtime", [])
        if len(unix_indexes) == 1 and unix_indexes[0] < len(row):
            raw = row[unix_indexes[0]].strip()
            if raw:
                value = number(raw)
                if value is not None:
                    return value / 1000.0 if abs(value) >= 100_000_000_000 else value
        for name in self.timestamp_fields[1:]:
            indexes = self.columns.get(normalise(name), [])
            if len(indexes) != 1 or indexes[0] >= len(row):
                continue
            raw = row[indexes[0]].strip()
            if not raw:
                continue
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        return None
