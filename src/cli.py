"""Command-line entry point for the local CSV analysis PoC."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

from src.logger import LogLevel, Logger
from src.parameter_distributions import generate_parameter_distributions
from src.patient_pipeline import analyze_inputs
from src.perceptron import train_model
from src.pipeline_runner import run_pipeline
from src.pipeline_config import load_pipeline_config
from src.training_io import (
    join_patient_vectors as _join_patient_vectors,
    read_csv_rows as _read_csv_rows,
    write_csv_rows as _write_csv_rows,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PARAMETER_MAP = ROOT / "config" / "pipeline.xml"
DEFAULT_PIPELINE_CONFIG = ROOT / "config" / "pipeline.xml"


def _values(value: str) -> tuple[str, ...]:
    values = tuple(item.strip() for item in value.split(",") if item.strip())
    if not values:
        raise argparse.ArgumentTypeError("provide at least one comma-separated value")
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pattern-recognition",
        description="Build one pre-operative feature vector per patient CSV and assess outcomes from post-operative data.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run configured preprocessing and model stages from XML")
    run.add_argument("--config", type=Path, default=DEFAULT_PIPELINE_CONFIG, help="XML run configuration")

    extract = commands.add_parser("analyze", help="analyze one or more CSV files or directories")
    extract.add_argument("inputs", nargs="+", type=Path, help="CSV file(s) or directories to search recursively")
    extract.add_argument("--output", type=Path, default=Path("results"), help="artifact directory (default: results)")
    extract.add_argument("--parameter-map", type=Path, default=DEFAULT_PARAMETER_MAP, help="pipeline XML configuration containing relevant parameters")
    extract.add_argument("--outcome-column", help="optional direct postoperative outcome column; default: configured columns")
    extract.add_argument("--success-values", type=_values, help="comma-separated exact success categories (defaults from parameter map)")
    extract.add_argument("--failure-values", type=_values, help="comma-separated exact failure categories (defaults from parameter map)")
    extract.add_argument("--log-dir", type=Path, default=Path("logs"), help="logger output directory (default: logs)")
    extract.add_argument("--verbose", action="store_true", help="include debug-level events")

    distributions = commands.add_parser(
        "distributions",
        help="plot the value distribution of every mapped pre- or post-operative parameter",
    )
    distributions.add_argument("inputs", nargs="+", type=Path, help="CSV file(s) or directories to search recursively")
    distributions.add_argument("--output", type=Path, default=Path("results"), help="artifact directory (default: results)")
    distributions.add_argument("--parameter-map", type=Path, default=DEFAULT_PARAMETER_MAP, help="pipeline XML configuration containing relevant parameters")
    distributions.add_argument("--phase", choices=("all", "preoperative", "postoperative"), default="all", help="which parameter phase to plot")
    distributions.add_argument("--bins", type=_positive_int, default=20, help="numeric histogram bins (default: 20)")
    distributions.add_argument("--log-dir", type=Path, default=Path("logs"), help="logger output directory (default: logs)")
    distributions.add_argument("--verbose", action="store_true", help="include debug-level events")

    train = commands.add_parser("train-perceptron", help="train and validate a binary perceptron")
    train.add_argument("--input", type=Path, default=Path("results/patient_vectors.csv"), help="pre-operative feature-only vectors CSV")
    train.add_argument("--outcomes", type=Path, default=Path("results/patient_outcomes.csv"), help="post-operative labels keyed by patient_id")
    train.add_argument("--training-set", type=Path, default=Path("results/training_set.csv"), help="output labeled training rows")
    train.add_argument("--validation-set", type=Path, default=Path("results/validation_set.csv"), help="output validation features and predictions; no true target")
    train.add_argument("--model", type=Path, default=Path("results/perceptron_model.json"), help="output model and aggregate validation metrics")
    train.add_argument("--parameter-map", type=Path, default=DEFAULT_PARAMETER_MAP, help="pipeline XML configuration containing relevant parameters")
    train.add_argument("--train-fraction", type=float, default=0.8, help="random fraction used for training; remaining rows validate")
    train.add_argument("--epochs", type=_positive_int, default=100, help="maximum training passes (default: 100)")
    train.add_argument("--seed", type=int, default=0, help="random split seed")
    train.add_argument("--omit", type=_values, default=(), help="additional comma-separated parameter IDs to omit; defaults come from the XML configuration")
    train.add_argument("--log-dir", type=Path, default=Path("logs"), help="logger output directory (default: logs)")
    train.add_argument("--verbose", action="store_true", help="include debug-level events")
    return parser


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            message = run_pipeline(args.config)
            print(f"XML pipeline complete: {message}")
            return 0
        level = LogLevel.DEBUG if args.verbose else LogLevel.INFO
        with Logger(minimum_level=level, log_root=args.log_dir) as logger:
            logger.write(LogLevel.INFO, "cli", f"starting {args.command}")
            if args.command == "analyze":
                reports = analyze_inputs(
                    args.inputs,
                    args.output,
                    args.parameter_map,
                    logger,
                    outcome_column=args.outcome_column,
                    success_values=args.success_values,
                    failure_values=args.failure_values,
                )
                message = f"Analysis complete: {len(reports)} patient(s) analyzed (numbered sibling files grouped). Artifacts: {args.output}"
            elif args.command == "distributions":
                analyze_inputs(args.inputs, args.output, args.parameter_map, logger)
                parameter_count = generate_parameter_distributions(
                    args.output,
                    args.parameter_map,
                    bins=args.bins,
                    phase=args.phase,
                )
                message = f"Distribution plots complete: {parameter_count} parameter(s). Artifacts: {args.output / 'parameter_distributions'}"
            else:
                _, vector_rows = _read_csv_rows(args.input)
                _, outcome_rows = _read_csv_rows(args.outcomes)
                rows = _join_patient_vectors(vector_rows, outcome_rows)
                run_config = load_pipeline_config(args.parameter_map)
                parameter_map = run_config["preprocessing"]["parameter_map"]
                configured_omit = run_config["model"]["models"]["perceptron"]["training_validation"]["omit"]
                parameters = [
                    parameter for parameter in parameter_map["parameters"]
                    if parameter["phase"] == "Pre-operative"
                ]
                model = train_model(
                    rows,
                    parameters,
                    omitted_parameters=list(dict.fromkeys((*args.omit, *configured_omit))),
                    train_fraction=args.train_fraction,
                    epochs=args.epochs,
                    seed=args.seed,
                    include_sets=True,
                )
                training_rows = model.pop("_training_set")
                validation_rows = model.pop("_validation_set")
                feature_ids = model["feature_ids"]
                _write_csv_rows(args.training_set, training_rows, ["patient_id", *feature_ids, "target"])
                _write_csv_rows(args.validation_set, validation_rows, ["patient_id", *feature_ids, "prediction"])
                args.model.parent.mkdir(parents=True, exist_ok=True)
                args.model.write_text(json.dumps(model, indent=2) + "\n", encoding="utf-8")
                logger.write(
                    LogLevel.INFO,
                    "perceptron",
                    f"validation complete; training_rows={model['training_size']}; validation_rows={model['validation_size']}; "
                    f"excluded_rows={model['excluded_rows']}; misclassified={model['misclassified']}; "
                    f"mislabeling_rate={model['mislabeling_rate']:.6f}; confusion={model['confusion']}",
                )
                message = (
                    f"Perceptron complete: {model['misclassified']}/{model['validation_size']} validation patients misclassified "
                    f"({model['mislabeling_rate']:.1%}); correct={model['correctly_classified']}; "
                    f"failure_predicted_success={model['confusion']['failure_predicted_success']}; "
                    f"success_predicted_failure={model['confusion']['success_predicted_failure']}; "
                    f"excluded={model['excluded_rows']}; training set={args.training_set}; "
                    f"validation predictions (no true target)={args.validation_set}; metrics/model={args.model}"
                )
        print(message)
        return 0
    except (csv.Error, OSError, ValueError, RuntimeError) as error:
        error_log_dir = getattr(args, "log_dir", ROOT / "logs")
        try:
            with Logger(minimum_level=LogLevel.ERROR, log_root=error_log_dir) as logger:
                logger.write(LogLevel.ERROR, "cli", f"command failed ({type(error).__name__})")
        except OSError:
            pass
        detail = str(error) if isinstance(error, ValueError) else "check inputs, mapping, and dependencies"
        print(f"Command failed ({type(error).__name__}): {detail}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
