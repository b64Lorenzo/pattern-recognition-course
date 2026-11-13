"""Execute preprocessing and model stages declared by the XML run file."""

from __future__ import annotations

import json
from pathlib import Path

from src.csv_header_descriptions import write_csv_header_descriptions
from src.logger import LogLevel, Logger
from src.model_config import PerceptronModelConfig
from src.parameter_distributions import generate_parameter_distributions
from src.pipeline_config import load_pipeline_config, select_parameter_map, write_parameter_reference
from src.patient_pipeline import analyze_inputs
from src.perceptron import plot_confusion_matrix, predict_model, train_model
from src.training_io import join_patient_vectors, read_csv_rows, write_csv_rows


def _configured_selection(preprocessing: dict) -> dict | None:
    selection = preprocessing["parameter_selection"]
    if not selection["run"]:
        return None
    return {
        "phases": selection["phases"],
        "include_ids": selection["include_ids"],
        "exclude_ids": selection["exclude_ids"],
    }


def run_pipeline(config_path: str | Path) -> str:
    config = load_pipeline_config(config_path)
    preprocessing = config["preprocessing"]
    model_config = config["model"]["active_config"]
    if not isinstance(model_config, PerceptronModelConfig):
        raise ValueError(f"No runner is registered for model: {model_config.xml_name}")
    perceptron_config = model_config.as_dict()
    selection = _configured_selection(preprocessing)
    parameter_map = preprocessing["parameter_map"]
    selected_map = (
        select_parameter_map(parameter_map, **selection)
        if selection is not None else parameter_map
    )
    has_during = any(item.get("phase") == "During-operative" for item in selected_map["parameters"])
    if has_during and preprocessing["run"]:
        raise ValueError("During-operative extraction needs an operation-end rule; header reporting is available without extraction")
    if has_during and preprocessing["plotting"]["run"] and preprocessing["plotting"]["distributions"]:
        raise ValueError("During-operative extraction and plotting need an operation-end rule; use header-only reporting")
    level = LogLevel[config["logging"]["level"]]
    log_directory = config["logging"]["directory"]
    completed = []

    with Logger(minimum_level=level, log_root=log_directory) as logger:
        logger.write(LogLevel.INFO, "cli", "starting XML-configured pipeline")
        descriptions = preprocessing["parameter_descriptions"]
        if descriptions["run"]:
            write_parameter_reference(selected_map, descriptions["output"])
            completed.append("parameter reference")

        header_descriptions = preprocessing["csv_header_descriptions"]
        if header_descriptions["run"]:
            counts = write_csv_header_descriptions(
                preprocessing["inputs"],
                preprocessing["output_directory"],
                header_descriptions["inventory_output"],
                header_descriptions["summary_output"],
                parameter_map=parameter_map,
            )
            logger.write(LogLevel.INFO, "csv headers", f"header summary complete; files={counts['files']}; unique_headers={counts['unique_headers']}")
            completed.append("CSV header descriptions")

        plots = preprocessing["plotting"]
        if preprocessing["run"]:
            analyze_inputs(
                preprocessing["inputs"],
                preprocessing["output_directory"],
                selected_map,
                logger,
                generate_plots=plots["run"],
            )
            completed.append("preprocessing")
        if plots["run"] and plots["distributions"]:
            count = generate_parameter_distributions(
                preprocessing["output_directory"],
                selected_map,
                bins=plots["bins"],
                phase=plots["phase"],
            )
            logger.write(LogLevel.INFO, "plotting", f"distribution plots generated; parameters={count}")
            completed.append("preprocessing plots")

        if perceptron_config["run"]:
            if perceptron_config["mode"] == "train-validation":
                settings = perceptron_config["training_validation"]
                if preprocessing["run"]:
                    vectors_path = preprocessing["output_directory"] / "patient_vectors.csv"
                    outcomes_path = preprocessing["output_directory"] / "patient_outcomes.csv"
                else:
                    vectors_path = settings["vectors"]
                    outcomes_path = settings["outcomes"]
                _, vector_rows = read_csv_rows(vectors_path)
                _, outcome_rows = read_csv_rows(outcomes_path)
                rows = join_patient_vectors(vector_rows, outcome_rows)
                features = [
                    item for item in selected_map["parameters"]
                    if item.get("phase") == "Pre-operative"
                ]
                if not features:
                    raise ValueError("Perceptron training needs at least one selected pre-operative parameter")
                model_result = train_model(
                    rows,
                    features,
                    omitted_parameters=settings["omit"],
                    train_fraction=settings["train_fraction"],
                    epochs=settings["epochs"],
                    seed=settings["seed"],
                    include_sets=True,
                )
                training_rows = model_result.pop("_training_set")
                validation_rows = model_result.pop("_validation_set")
                feature_ids = model_result["feature_ids"]
                write_csv_rows(
                    settings["training_set"], training_rows,
                    ["patient_id", *feature_ids, "target"],
                )
                write_csv_rows(
                    settings["validation_set"], validation_rows,
                    ["patient_id", *feature_ids, "prediction"],
                )
                settings["model_output"].parent.mkdir(parents=True, exist_ok=True)
                settings["model_output"].write_text(
                    json.dumps(model_result, indent=2) + "\n", encoding="utf-8"
                )
                if perceptron_config["plotting"]["run"]:
                    plot_confusion_matrix(model_result, perceptron_config["plotting"]["output"])
                    completed.append("perceptron plot")
                logger.write(
                    LogLevel.INFO,
                    "perceptron",
                    f"training complete; training_rows={model_result['training_size']}; "
                    f"validation_rows={model_result['validation_size']}; "
                    f"misclassified={model_result['misclassified']}; "
                    f"mislabeling_rate={model_result['mislabeling_rate']:.6f}",
                )
                completed.append("perceptron training/validation")
            else:
                settings = perceptron_config["novel_data"]
                vectors_path = (
                    preprocessing["output_directory"] / "patient_vectors.csv"
                    if preprocessing["run"] else settings["vectors"]
                )
                fields, vector_rows = read_csv_rows(vectors_path)
                if "target" in fields:
                    raise ValueError("Novel-data input must contain features only, without target labels")
                model = json.loads(settings["model_input"].read_text(encoding="utf-8"))
                predictions = predict_model(model, vector_rows)
                write_csv_rows(
                    settings["predictions"], predictions,
                    ["patient_id", *model["feature_ids"], "prediction"],
                )
                logger.write(
                    LogLevel.INFO, "perceptron",
                    f"novel-data predictions complete; rows={len(predictions)}",
                )
                completed.append("novel-data predictions")

    return ", ".join(completed) if completed else "no stages enabled"
