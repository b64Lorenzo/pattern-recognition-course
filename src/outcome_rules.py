"""Binary classification rules for postoperative outcome evidence."""
from __future__ import annotations
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

class CreatinineStager:
    """Apply configured creatinine staging criteria to a baseline and series."""
    def __init__(self, rules: Mapping) -> None:
        self.rules = rules
    def stage(self, baseline: float | None, baseline_time: float | None,
              postoperative: Sequence[tuple[float, float]]) -> int | None:
        if baseline is None or baseline <= 0 or not postoperative:
            return None
        lookback = float(self.rules["baseline_lookback_hours"]) * 3600
        eligible = [(t, v) for t, v in postoperative
                    if baseline_time is not None and 0 <= t - baseline_time <= lookback]
        stages = []
        for _, value in eligible:
            for rule in self.rules.get("absolute_criteria", []):
                if value >= float(rule["minimum_value"]) and (not rule.get("requires_increase_from_baseline", False) or value > baseline):
                    stages.append(int(rule["stage"]))
            ratio = value / baseline
            stages.extend(int(r["stage"]) for r in self.rules.get("ratio_stages", []) if ratio >= float(r["minimum_ratio"]))
        acute = self.rules.get("acute_increase", {})
        if baseline_time is not None and any(0 <= t - baseline_time <= float(acute.get("within_hours", 0))*3600 and v-baseline >= float(acute.get("minimum_increase", float("inf"))) for t, v in postoperative):
            stages.append(int(acute["stage"]))
        pairwise = self.rules.get("pairwise_increase", {})
        ordered = sorted(postoperative)
        if any(tb-ta <= float(pairwise.get("within_hours", 0))*3600 and vb-va >= float(pairwise.get("minimum_increase", float("inf"))) for i, (ta, va) in enumerate(ordered) for tb, vb in ordered[i+1:]):
            stages.append(int(pairwise["stage"]))
        return max(stages) if stages else (int(self.rules["default_stage"]) if eligible else None)

@dataclass(frozen=True)
class OutcomeClassification:
    target: int | None
    status: str
    label_basis: str

class OutcomeClassifier:
    """Apply explicit binary criteria; any matching failure takes precedence."""
    OPERATORS = {"equals", "in", "lt", "lte", "gt", "gte"}
    def __init__(self, parameter_map: Mapping, *, success_values: Iterable[str] | None = None,
                 failure_values: Iterable[str] | None = None) -> None:
        self.criteria = {}
        for parameter in parameter_map.get("parameters", []):
            if parameter.get("phase") != "Post-operative":
                continue
            rules = parameter.get("outcome_criteria")
            if not isinstance(rules, Mapping) or not {"success", "failure"} <= rules.keys():
                raise ValueError(f"Missing success/failure outcome criteria for {parameter.get('id', 'parameter')}")
            self._validate(parameter["id"], rules)
            self.criteria[parameter["id"]] = rules
        configured = parameter_map.get("patient_outcome_rules", {}).get("categorical_rules", [])
        good = [v for r in configured if r.get("classification") == "success" for v in r.get("values", [])]
        bad = [v for r in configured if r.get("classification") == "failure" for v in r.get("values", [])]
        self.success_values = self._tokens(success_values if success_values is not None else good)
        self.failure_values = self._tokens(failure_values if failure_values is not None else bad)
        if self.success_values & self.failure_values:
            raise ValueError("Success and failure outcome values must not overlap")
    @staticmethod
    def _tokens(values: Iterable[object]) -> set[str]:
        return {str(v).strip().casefold() for v in values if str(v).strip()}
    @staticmethod
    def _number(value: object) -> float | None:
        try: number = float(str(value))
        except (TypeError, ValueError): return None
        return number if math.isfinite(number) else None
    @classmethod
    def _same(cls, a: object, b: object) -> bool:
        x, y = cls._number(a), cls._number(b)
        return x == y if x is not None and y is not None else str(a).strip().casefold() == str(b).strip().casefold()
    @classmethod
    def _validate(cls, parameter_id: str, rules: Mapping) -> None:
        for outcome in ("success", "failure"):
            rule = rules[outcome]
            if rule is None: continue
            if not isinstance(rule, Mapping) or rule.get("operator") not in cls.OPERATORS:
                raise ValueError(f"Invalid {outcome} criterion for {parameter_id}")
            op = rule["operator"]
            if op == "in" and not isinstance(rule.get("values"), list):
                raise ValueError(f"The in criterion for {parameter_id} requires a values list")
            if op == "equals" and "value" not in rule:
                raise ValueError(f"The equals criterion for {parameter_id} requires a value")
            if op in {"lt", "lte", "gt", "gte"} and cls._number(rule.get("value")) is None:
                raise ValueError(f"Numeric criterion for {parameter_id} requires a finite value")
        a, b = rules["success"], rules["failure"]
        if a and b and a.get("operator") in {"equals", "in"} and b.get("operator") in {"equals", "in"}:
            av, bv = a.get("values", [a.get("value")]), b.get("values", [b.get("value")])
            if any(cls._same(x, y) for x in av for y in bv):
                raise ValueError(f"Success and failure criteria overlap for {parameter_id}")
    @classmethod
    def _matches(cls, value: object, rule: Mapping | None) -> bool:
        if rule is None or value is None or str(value).strip() == "": return False
        op = rule["operator"]
        if op == "equals": return cls._same(value, rule["value"])
        if op == "in": return any(cls._same(value, v) for v in rule["values"])
        n, bound = cls._number(value), cls._number(rule["value"])
        if n is None or bound is None: return False
        return {"lt": n < bound, "lte": n <= bound, "gt": n > bound, "gte": n >= bound}[op]
    def classify(self, measurements: Mapping[str, object], *, categories: Iterable[object] = ()) -> OutcomeClassification:
        failures, successes = [], []
        for pid, rules in self.criteria.items():
            value = measurements.get(pid)
            if self._matches(value, rules["failure"]): failures.append(pid)
            elif self._matches(value, rules["success"]): successes.append(pid)
        tokens = self._tokens(categories)
        if tokens & self.failure_values: failures.append("direct_outcome")
        elif tokens & self.success_values: successes.append("direct_outcome")
        if failures: return OutcomeClassification(-1, "failure", ", ".join(failures))
        if successes: return OutcomeClassification(1, "success", ", ".join(successes))
        return OutcomeClassification(None, "unclassified", "")
