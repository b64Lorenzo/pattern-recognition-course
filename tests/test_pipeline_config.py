import csv
import json
import tempfile
import unittest
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET

from src.cli import main
from src.pipeline_config import load_pipeline_config, select_parameter_map
from src.model_config import NovelDataConfig, PerceptronModelConfig, PlotConfig, TrainingValidationConfig
from src.perceptron import predict_model, train_model

ROOT = Path(__file__).resolve().parents[1]


class PipelineConfigurationTests(unittest.TestCase):
    def test_logger_is_a_direct_pipeline_configuration_child(self):
        for path in (ROOT / "config" / "pipeline.xml", ROOT / "config" / "pipeline.template.xml"):
            xml_root = ET.parse(path).getroot()
            self.assertIsNotNone(xml_root.find("logging"))
            self.assertIsNone(xml_root.find("general"))
        config = load_pipeline_config(ROOT / "config" / "pipeline.xml")
        self.assertEqual(config["logging"]["level"], "INFO")

    def test_default_xml_config_controls_each_pipeline_stage(self):
        config = load_pipeline_config(ROOT / "config" / "pipeline.xml")
        self.assertIsInstance(config["preprocessing"]["run"], bool)
        self.assertIsInstance(config["preprocessing"]["parameter_descriptions"]["run"], bool)
        self.assertIsInstance(config["preprocessing"]["csv_header_descriptions"]["run"], bool)
        self.assertEqual(config["preprocessing"]["csv_header_descriptions"]["inventory_output"], ROOT / "results" / "csv_headers.csv")
        self.assertEqual(config["preprocessing"]["parameter_selection"]["phases"], [
            "Pre-operative", "During-operative", "Post-operative"
        ])
        self.assertEqual(config["model"]["active_model"], "perceptron")
        active_model = config["model"]["active_config"]
        self.assertIsInstance(active_model, PerceptronModelConfig)
        self.assertIsInstance(active_model.training_validation, TrainingValidationConfig)
        self.assertIsInstance(active_model.novel_data, NovelDataConfig)
        self.assertIsInstance(active_model.plotting, PlotConfig)
        self.assertEqual(active_model.training_validation.omit, ["operation_diagnosis_proxy"])
        self.assertIsInstance(config["model"]["models"]["perceptron"]["run"], bool)
        self.assertIn(config["model"]["models"]["perceptron"]["mode"], {"train-validation", "novel-data"})
        self.assertEqual(
            config["model"]["models"]["perceptron"]["training_validation"]["omit"],
            ["operation_diagnosis_proxy"],
        )
        self.assertEqual(config["logging"]["level"], "INFO")
        parameters = config["preprocessing"]["parameter_map"]["parameters"]
        self.assertEqual(len(parameters), 20)
        self.assertEqual(sum(item["phase"] == "Pre-operative" for item in parameters), 9)
        self.assertEqual(sum(item["phase"] == "Post-operative" for item in parameters), 11)
        self.assertEqual(config["preprocessing"]["parameter_map"]["included_phases"], [
            "Pre-operative", "During-operative", "Post-operative"
        ])

    def test_template_disables_all_execution_switches(self):
        config = load_pipeline_config(ROOT / "config" / "pipeline.template.xml")
        self.assertFalse(config["preprocessing"]["run"])
        self.assertFalse(config["preprocessing"]["parameter_descriptions"]["run"])
        self.assertFalse(config["preprocessing"]["csv_header_descriptions"]["run"])
        self.assertFalse(config["preprocessing"]["plotting"]["run"])
        self.assertFalse(config["model"]["models"]["perceptron"]["run"])

    def test_unknown_active_model_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "pipeline.xml"
            text = (ROOT / "config" / "pipeline.xml").read_text(encoding="utf-8")
            config_path.write_text(
                text.replace("<activeModel>perceptron</activeModel>", "<activeModel>randomForest</activeModel>"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Unsupported model type: randomForest"):
                load_pipeline_config(config_path)

    def test_parameter_selection_filters_both_parameters_and_postoperative_measurements(self):
        parameter_map = {
            "parameters": [
                {"id": "age", "phase": "Pre-operative"},
                {"id": "sex", "phase": "Pre-operative"},
                {"id": "aki", "phase": "Post-operative"},
            ],
            "postoperative_measurements": [
                {"id": "aki"},
                {"id": "los"},
            ],
        }
        selected = select_parameter_map(
            parameter_map,
            phases=["Pre-operative", "Post-operative"],
            include_ids=["age", "aki"],
            exclude_ids=[],
        )
        self.assertEqual([item["id"] for item in selected["parameters"]], ["age", "aki"])
        self.assertEqual([item["id"] for item in selected["postoperative_measurements"]], ["aki"])

    def test_selection_rejects_unknown_parameter_ids(self):
        with self.assertRaisesRegex(ValueError, "Unknown parameter ID"):
            select_parameter_map(
                {"parameters": [{"id": "age", "phase": "Pre-operative"}]},
                phases=["Pre-operative"],
                include_ids=["missing"],
                exclude_ids=[],
            )

    def test_xml_runner_trains_then_predicts_novel_vectors(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "config").mkdir()
            (project / "data" / "patients").mkdir(parents=True)
            shutil.copy(ROOT / "config" / "pipeline.xml", project / "config" / "pipeline.xml")
            from tests.test_patient_vectors import PatientVectorTests
            patient_writer = PatientVectorTests()
            for index, creatinine in enumerate(("100", "100", "100", "45", "45")):
                patient_writer.make_patient(
                    project / "data" / "patients",
                    f"synthetic-{index}.csv",
                    postop_creatinine=creatinine,
                )

            config_path = project / "config" / "pipeline.xml"
            tree = ET.parse(config_path)
            root = tree.getroot()
            root.find("logging/directory").text = "logs"
            root.find("preprocessing/run").text = "true"
            root.find("preprocessing/inputs/path").text = "data/patients"
            root.find("preprocessing/outputDirectory").text = "results"
            root.find("preprocessing/parameterDescriptions/run").text = "true"
            root.find("preprocessing/parameterDescriptions/output").text = "results/parameter_reference.md"
            root.find("preprocessing/plotting/run").text = "false"
            root.find("preprocessing/plotting/distributions").text = "false"
            root.find("model/perceptron/run").text = "true"
            root.find("model/perceptron/mode").text = "train-validation"
            root.find("model/perceptron/trainingValidation/trainingSet").text = "results/training_set.csv"
            root.find("model/perceptron/trainingValidation/validationSet").text = "results/validation_set.csv"
            root.find("model/perceptron/trainingValidation/modelOutput").text = "results/perceptron_model.json"
            root.find("model/perceptron/novelData/modelInput").text = "results/perceptron_model.json"
            root.find("model/perceptron/novelData/predictions").text = "results/novel_predictions.csv"
            root.find("model/perceptron/plotting/run").text = "true"
            root.find("model/perceptron/plotting/output").text = "results/perceptron_confusion_matrix.png"
            ET.indent(root, space="  ")
            tree.write(config_path, encoding="utf-8", xml_declaration=True)
            self.assertEqual(
                load_pipeline_config(config_path)["preprocessing"]["inputs"],
                [(project / "data" / "patients").resolve()],
            )

            self.assertEqual(main(["run", "--config", str(config_path)]), 0)
            results = project / "results"
            self.assertTrue((results / "patient_vectors.csv").is_file())
            self.assertTrue((results / "patient_outcomes.csv").is_file())
            self.assertTrue((results / "parameter_reference.md").is_file())
            model_path = results / "perceptron_model.json"
            self.assertTrue(model_path.is_file())
            self.assertEqual(
                json.loads(model_path.read_text(encoding="utf-8"))["omitted_parameters"],
                ["operation_diagnosis_proxy"],
            )
            self.assertTrue((results / "perceptron_confusion_matrix.png").is_file())

            root.find("model/perceptron/mode").text = "novel-data"
            root.find("model/perceptron/plotting/run").text = "false"
            ET.indent(root, space="  ")
            tree.write(config_path, encoding="utf-8", xml_declaration=True)
            self.assertEqual(main(["run", "--config", str(config_path)]), 0)
            with (results / "novel_predictions.csv").open(newline="", encoding="utf-8") as stream:
                predictions = list(csv.DictReader(stream))
            self.assertEqual(len(predictions), 5)
            self.assertIn("prediction", predictions[0])
            self.assertNotIn("target", predictions[0])

    def test_saved_perceptron_predicts_novel_rows_without_targets(self):
        parameters = [{"id": "measurement", "parameter_type": "numeric"}]
        rows = [
            {"patient_id": f"p{i}", "measurement": str(i), "target": -1 if i < 5 else 1}
            for i in range(10)
        ]
        model = train_model(rows, parameters, train_fraction=0.8, epochs=20, seed=3)
        predictions = predict_model(
            model,
            [{"patient_id": "new-patient", "measurement": "0"}],
        )
        self.assertEqual(len(predictions), 1)
        self.assertEqual(predictions[0]["patient_id"], "new-patient")
        self.assertEqual(predictions[0]["prediction"], -1)
        self.assertNotIn("target", predictions[0])


if __name__ == "__main__":
    unittest.main()
