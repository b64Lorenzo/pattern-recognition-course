import csv
import json
import tempfile
import unittest
from pathlib import Path

from src.perceptron import train_model

ROOT = Path(__file__).resolve().parents[1]
PARAMETER_MAP = ROOT / "config" / "pipeline.xml"


class PerceptronTests(unittest.TestCase):
    def setUp(self):
        self.parameters = [
            {"id": "measurement", "parameter_type": "numeric"},
            {"id": "group", "parameter_type": "categorical"},
        ]
        self.rows = [
            {"measurement": str(value), "group": "A" if value < 5 else "B", "target": "-1" if value < 5 else "1"}
            for value in range(10)
        ]

    def test_training_uses_repeatable_random_holdout_and_reports_generalization_error(self):
        first = train_model(self.rows, self.parameters, train_fraction=0.7, epochs=20, seed=41)
        second = train_model(self.rows, self.parameters, train_fraction=0.7, epochs=20, seed=41)

        self.assertEqual(first, second)
        self.assertEqual(first["training_size"], 7)
        self.assertEqual(first["validation_size"], 3)
        self.assertGreaterEqual(first["generalization_error"], 0)
        self.assertLessEqual(first["generalization_error"], 1)
        self.assertIn("group=A", first["feature_names"])
        self.assertIn("group=B", first["feature_names"])

    def test_only_exact_binary_targets_are_accepted(self):
        rows = [{"measurement": str(value), "target": -1 if value < 5 else 1} for value in range(10)]
        model = train_model(rows, self.parameters[:1], train_fraction=0.7, epochs=20, seed=3)
        self.assertEqual(model["target_values"], [-1, 1])
        rows[0]["target"] = -100
        with self.assertRaisesRegex(ValueError, "exactly -1.*or 1"):
            train_model(rows, self.parameters[:1], train_fraction=0.7, epochs=20, seed=3)

    def test_omitted_parameters_are_not_encoded(self):
        model = train_model(
            self.rows,
            self.parameters,
            omitted_parameters=["group"],
            train_fraction=0.7,
            epochs=10,
            seed=5,
        )

        self.assertEqual(model["omitted_parameters"], ["group"])
        self.assertTrue(all(not name.startswith("group=") for name in model["feature_names"]))

    def test_parameter_omissions_are_supplied_by_the_caller_not_built_into_the_trainer(self):
        parameters = [
            {"id": "measurement", "parameter_type": "numeric"},
            {"id": "operation_diagnosis_proxy", "parameter_type": "categorical"},
        ]
        rows = [
            {
                "measurement": str(value),
                "operation_diagnosis_proxy": "procedure-a" if value < 5 else "procedure-b",
                "target": -1 if value < 5 else 1,
            }
            for value in range(10)
        ]
        model = train_model(rows, parameters, train_fraction=0.7, epochs=10, seed=5)
        self.assertIn("operation_diagnosis_proxy", model["feature_ids"])
        self.assertEqual(model["omitted_parameters"], [])

    def test_unknown_omitted_parameter_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown omitted parameter"):
            train_model(self.rows, self.parameters, omitted_parameters=["not_a_feature"])

    def test_join_uses_patient_id_and_rejects_unmatched_outcomes(self):
        from src.cli import _join_patient_vectors

        vectors = [{"patient_id": "a", "age": "2"}, {"patient_id": "b", "age": "8"}]
        outcomes = [{"patient_id": "b", "target": "1"}, {"patient_id": "a", "target": "-1"}]
        joined = _join_patient_vectors(vectors, outcomes)
        self.assertEqual([row["target"] for row in joined], ["-1", "1"])
        with self.assertRaisesRegex(ValueError, "patient_id sets do not match"):
            _join_patient_vectors(vectors, outcomes[:1])

    def test_cli_creates_labeled_training_and_unlabeled_validation_sets_and_reports_mislabeling(self):
        from src.cli import main

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            vectors_path = root / "patient_vectors.csv"
            outcomes_path = root / "patient_outcomes.csv"
            with vectors_path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=["patient_id", "age", "operation_diagnosis_proxy"],
                )
                writer.writeheader()
                for value in range(20):
                    writer.writerow({
                        "patient_id": f"p{value}",
                        "age": value,
                        "operation_diagnosis_proxy": "",
                    })
            with outcomes_path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=["patient_id", "target"])
                writer.writeheader()
                for value in reversed(range(20)):
                    target = "NA" if value == 0 else "-1" if value < 10 else "1"
                    writer.writerow({"patient_id": f"p{value}", "target": target})

            model_path = root / "model.json"
            training_path = root / "training_set.csv"
            validation_path = root / "validation_set.csv"
            result = main([
                "train-perceptron",
                "--input", str(vectors_path),
                "--outcomes", str(outcomes_path),
                "--parameter-map", str(PARAMETER_MAP),
                "--model", str(model_path),
                "--training-set", str(training_path),
                "--validation-set", str(validation_path),
                "--train-fraction", "0.7",
                "--epochs", "20",
                "--seed", "19",
                "--omit", "sex,asa,weight,height,bmi,baseline_creatinine,baseline_hemoglobin",
                "--log-dir", str(root / "logs"),
            ])

            self.assertEqual(result, 0)
            model = json.loads(model_path.read_text(encoding="utf-8"))
            with training_path.open(newline="", encoding="utf-8") as stream:
                training = list(csv.DictReader(stream))
            with validation_path.open(newline="", encoding="utf-8") as stream:
                validation = list(csv.DictReader(stream))
            self.assertEqual(model["omitted_parameters"], ["sex", "asa", "weight", "height", "bmi", "baseline_creatinine", "baseline_hemoglobin", "operation_diagnosis_proxy"])
            self.assertEqual(len(training), model["training_size"])
            self.assertEqual(len(validation), model["validation_size"])
            self.assertTrue(all(row["target"] in {"-1", "1"} for row in training))
            self.assertNotIn("target", validation[0])
            self.assertTrue(all(row["prediction"] in {"-1", "1"} for row in validation))
            self.assertEqual(model["misclassified"], 0)
            self.assertEqual(model["misclassified"], model["confusion"]["failure_predicted_success"] + model["confusion"]["success_predicted_failure"])
            self.assertEqual(model["generalization_error"], model["mislabeling_rate"])
            self.assertTrue(all("patient_id" not in name for name in model["feature_names"]))


if __name__ == "__main__":
    unittest.main()
