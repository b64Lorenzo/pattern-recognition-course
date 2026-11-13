"""Pre-operative parameter selection and feature extraction."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from src.csv_utils import PatientCsvReader, is_missing, normalise, number, number_component

PREOPERATIVE_PHASE = "Pre-operative"
EVENT_FIELDS = ("epic.EVENTS.EVENT", "event", "events")


@dataclass(frozen=True)
class PreoperativeResult:
    cutoff: float | None
    features: dict[str, str]
    feature_status: dict[str, str]
    feature_indexes: dict[str, list[int]]
    baseline_creatinine: float | None
    baseline_time_seconds: float | None

    @property
    def missing_features(self) -> list[str]:
        return [feature_id for feature_id, value in self.features.items() if not value]

    @property
    def matched_feature_count(self) -> int:
        return sum(bool(indexes) for indexes in self.feature_indexes.values())


class PreoperativeProcessor:
    """Extract only configured pre-operative predictors from one patient's CSV files."""

    def __init__(self, parameter_map: dict) -> None:
        self.parameter_map = parameter_map
        self.parameter_specs = tuple(
            parameter for parameter in parameter_map["parameters"]
            if parameter["phase"] == PREOPERATIVE_PHASE
        )
        self.parameters = tuple(parameter["id"] for parameter in self.parameter_specs)
        self.missing_tokens = {
            normalise(token) for token in parameter_map.get("missing_value_tokens", [])
        }

    def _derive_bmi(self, features: dict[str, str], statuses: dict[str, str]) -> None:
        rule = self.parameter_map.get("derived_features", {}).get("bmi", {})
        if not rule.get("enabled", False):
            return
        bmi_id = str(rule.get("parameter_id", "bmi"))
        if number_component(features.get(bmi_id, "")) is not None:
            return

        weight = number_component(features.get(str(rule.get("weight_parameter_id", "weight")), ""))
        height = number_component(features.get(str(rule.get("height_parameter_id", "height")), ""))
        if weight is None or height is None or weight <= 0 or height <= 0:
            return

        weight_units = {"kg": 1.0, "g": 0.001, "lb": 0.45359237}
        height_units = {"m": 1.0, "cm": 0.01, "in": 0.0254}
        weight_unit = str(rule.get("weight_unit", "kg")).casefold()
        height_unit = str(rule.get("height_unit", "cm")).casefold()
        if weight_unit not in weight_units or height_unit not in height_units:
            raise ValueError("BMI derivation requires weight unit kg/g/lb and height unit m/cm/in")

        weight_kg = weight * weight_units[weight_unit]
        height_m = height * height_units[height_unit]
        bmi = weight_kg / (height_m * height_m)
        features[bmi_id] = format(bmi, ".12g")
        statuses[bmi_id] = "derived_from_weight_height"

    def process(self, paths: str | Path | Iterable[str | Path]) -> PreoperativeResult:
        reader = PatientCsvReader(paths)
        cutoff = self._find_cutoff(reader)
        feature_indexes = self._feature_indexes(reader)
        best: dict[str, tuple[float, str] | None] = {feature_id: None for feature_id in self.parameters}
        static_values: dict[str, set[str]] = {feature_id: set() for feature_id in self.parameters}
        preoperative_creatinine: list[tuple[float, float]] = []
        rules = self.parameter_map.get("patient_outcome_rules", {})
        baseline_creatinine_id = rules["baseline_creatinine_parameter_id"]
        creatinine_index = reader.find_index([rules["creatinine_field"]])

        for row in reader.rows():
            timestamp = reader.timestamp(row)
            for parameter in self.parameter_specs:
                feature_id = parameter["id"]
                for index in feature_indexes[feature_id]:
                    if index >= len(row) or is_missing(row[index], self.missing_tokens):
                        continue
                    value = row[index].strip()
                    if parameter.get("parameter_type") in {"numeric", "derived_numeric", "time_series_numeric"}:
                        numeric_value = number_component(value)
                        if numeric_value is None:
                            continue
                        value = format(numeric_value, ".12g")
                    static_values[feature_id].add(value)
                    if cutoff is not None and timestamp is not None and timestamp < cutoff:
                        candidate = best[feature_id]
                        if candidate is None or timestamp >= candidate[0]:
                            best[feature_id] = (timestamp, value)
            if (
                cutoff is not None
                and timestamp is not None
                and timestamp < cutoff
                and creatinine_index is not None
                and creatinine_index < len(row)
            ):
                value = number(row[creatinine_index])
                if value is not None:
                    preoperative_creatinine.append((timestamp, value))

        features: dict[str, str] = {}
        statuses: dict[str, str] = {}
        for parameter in self.parameter_specs:
            feature_id = parameter["id"]
            candidate = best[feature_id]
            if candidate is not None:
                features[feature_id] = candidate[1]
                statuses[feature_id] = "present_preoperatively"
            elif not feature_indexes[feature_id]:
                features[feature_id] = ""
                statuses[feature_id] = "column_missing"
            elif cutoff is None and parameter.get("allow_static_cutoff_fallback", False) and len(static_values[feature_id]) == 1:
                features[feature_id] = next(iter(static_values[feature_id]))
                statuses[feature_id] = "present_static_cutoff_unavailable"
            elif cutoff is None:
                features[feature_id] = ""
                statuses[feature_id] = "cutoff_unavailable"
            else:
                features[feature_id] = ""
                statuses[feature_id] = "no_preoperative_value"

        self._derive_bmi(features, statuses)
        baseline = number(features.get(baseline_creatinine_id, ""))
        baseline_candidate = best.get(baseline_creatinine_id)
        baseline_time = baseline_candidate[0] if baseline_candidate else None
        if baseline is None and preoperative_creatinine:
            baseline_time, baseline = max(preoperative_creatinine, key=lambda item: item[0])
        return PreoperativeResult(
            cutoff=cutoff,
            features=features,
            feature_status=statuses,
            feature_indexes=feature_indexes,
            baseline_creatinine=baseline,
            baseline_time_seconds=baseline_time,
        )

    def _feature_indexes(self, reader: PatientCsvReader) -> dict[str, list[int]]:
        result: dict[str, list[int]] = {}
        for parameter in self.parameter_specs:
            matches: list[int] = []
            for field in parameter.get("source_fields", []):
                indexes = reader.columns.get(normalise(field), [])
                if len(indexes) == 1:
                    matches.extend(indexes)
            result[parameter["id"]] = list(dict.fromkeys(matches))
        return result

    def _find_cutoff(self, reader: PatientCsvReader) -> float | None:
        event_index = reader.find_index(EVENT_FIELDS)
        if event_index is None:
            return None
        patterns = [
            normalise(pattern)
            for pattern in self.parameter_map.get("preoperative_start_event_patterns", [])
            if normalise(pattern)
        ]
        matches = []
        for row in reader.rows():
            if event_index >= len(row):
                continue
            event = normalise(row[event_index])
            if event and any(pattern in event for pattern in patterns):
                timestamp = reader.timestamp(row)
                if timestamp is not None:
                    matches.append(timestamp)
        return min(matches) if matches else None
