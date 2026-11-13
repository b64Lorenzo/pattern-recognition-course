"""Typed XML nodes and registry for model-specific configuration."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET


ModelRegistry = dict[str, type["ModelConfig"]]
MODEL_CONFIGS: ModelRegistry = {}


def _required(parent: ET.Element, tag: str) -> str:
    element = parent.find(tag)
    if element is None or element.text is None or not element.text.strip():
        raise ValueError(f"Missing required XML value: {tag}")
    return element.text.strip()


def _boolean(parent: ET.Element, tag: str) -> bool:
    value = _required(parent, tag).casefold()
    if value not in {"true", "false", "1", "0"}:
        raise ValueError(f"{tag} must be true or false")
    return value in {"true", "1"}


def _path(project_root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (project_root / path).resolve()


def _ids(parent: ET.Element, container: str) -> list[str]:
    element = parent.find(container)
    if element is None:
        raise ValueError(f"Missing required XML section: {container}")
    values = [item.text.strip() for item in element.findall("id") if item.text and item.text.strip()]
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate value in {container}")
    return values


def register_model_config(name: str):
    """Register the XML parser class for one model name."""
    def register(model_type: type[ModelConfig]) -> type[ModelConfig]:
        if name in MODEL_CONFIGS:
            raise ValueError(f"Model configuration already registered: {name}")
        MODEL_CONFIGS[name] = model_type
        model_type.xml_name = name
        return model_type
    return register


@dataclass(frozen=True)
class TrainingValidationConfig:
    """Reusable settings for supervised training and holdout validation."""

    vectors: Path
    outcomes: Path
    training_set: Path
    validation_set: Path
    model_output: Path
    train_fraction: float
    epochs: int
    seed: int
    omit: list[str]

    @classmethod
    def from_xml(cls, element: ET.Element, project_root: Path) -> TrainingValidationConfig:
        fraction = float(_required(element, "trainFraction"))
        epochs = int(_required(element, "epochs"))
        if not 0 < fraction < 1 or epochs < 1:
            raise ValueError("Training requires trainFraction between 0 and 1 and positive epochs")
        return cls(
            vectors=_path(project_root, _required(element, "vectors")),
            outcomes=_path(project_root, _required(element, "outcomes")),
            training_set=_path(project_root, _required(element, "trainingSet")),
            validation_set=_path(project_root, _required(element, "validationSet")),
            model_output=_path(project_root, _required(element, "modelOutput")),
            train_fraction=fraction,
            epochs=epochs,
            seed=int(_required(element, "seed")),
            omit=_ids(element, "omit"),
        )

    def as_dict(self) -> dict:
        return {
            "vectors": self.vectors,
            "outcomes": self.outcomes,
            "training_set": self.training_set,
            "validation_set": self.validation_set,
            "model_output": self.model_output,
            "train_fraction": self.train_fraction,
            "epochs": self.epochs,
            "seed": self.seed,
            "omit": list(self.omit),
        }


@dataclass(frozen=True)
class NovelDataConfig:
    """Reusable saved-model prediction inputs and outputs."""

    vectors: Path
    model_input: Path
    predictions: Path

    @classmethod
    def from_xml(cls, element: ET.Element, project_root: Path) -> NovelDataConfig:
        return cls(
            vectors=_path(project_root, _required(element, "vectors")),
            model_input=_path(project_root, _required(element, "modelInput")),
            predictions=_path(project_root, _required(element, "predictions")),
        )

    def as_dict(self) -> dict:
        return {
            "vectors": self.vectors,
            "model_input": self.model_input,
            "predictions": self.predictions,
        }


@dataclass(frozen=True)
class PlotConfig:
    """Reusable enabled/disabled output-plot node."""

    run: bool
    output: Path

    @classmethod
    def from_xml(cls, element: ET.Element, project_root: Path) -> PlotConfig:
        return cls(
            run=_boolean(element, "run"),
            output=_path(project_root, _required(element, "output")),
        )

    def as_dict(self) -> dict:
        return {"run": self.run, "output": self.output}


class ModelConfig(ABC):
    """Base class for a registered model's XML configuration node."""

    xml_name: str

    @classmethod
    @abstractmethod
    def from_xml(cls, element: ET.Element, project_root: Path) -> ModelConfig:
        raise NotImplementedError

    @abstractmethod
    def as_dict(self) -> dict:
        raise NotImplementedError


@register_model_config("perceptron")
@dataclass(frozen=True)
class PerceptronModelConfig(ModelConfig):
    """Perceptron-specific controls composed from reusable XML child nodes."""

    run: bool
    mode: str
    training_validation: TrainingValidationConfig
    novel_data: NovelDataConfig
    plotting: PlotConfig

    @classmethod
    def from_xml(cls, element: ET.Element, project_root: Path) -> PerceptronModelConfig:
        training = element.find("trainingValidation")
        novel = element.find("novelData")
        plotting = element.find("plotting")
        if training is None or novel is None or plotting is None:
            raise ValueError("Perceptron requires trainingValidation, novelData, and plotting sections")
        mode = _required(element, "mode")
        if mode not in {"train-validation", "novel-data"}:
            raise ValueError("Perceptron mode must be train-validation or novel-data")
        model_plot = PlotConfig.from_xml(plotting, project_root)
        if model_plot.run and mode != "train-validation":
            raise ValueError("Perceptron validation plotting requires train-validation mode")
        return cls(
            run=_boolean(element, "run"),
            mode=mode,
            training_validation=TrainingValidationConfig.from_xml(training, project_root),
            novel_data=NovelDataConfig.from_xml(novel, project_root),
            plotting=model_plot,
        )

    def as_dict(self) -> dict:
        return {
            "run": self.run,
            "mode": self.mode,
            "training_validation": self.training_validation.as_dict(),
            "novel_data": self.novel_data.as_dict(),
            "plotting": self.plotting.as_dict(),
        }


def parse_active_model(element: ET.Element, project_root: Path) -> tuple[str, ModelConfig]:
    """Parse the selected model through its registered model-specific class."""
    name = _required(element, "activeModel")
    model_type = MODEL_CONFIGS.get(name)
    if model_type is None:
        raise ValueError(f"Unsupported model type: {name}")
    model_element = element.find(name)
    if model_element is None:
        raise ValueError(f"Missing configuration for active model: {name}")
    return name, model_type.from_xml(model_element, project_root)
