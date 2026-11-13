import csv
import io
import json
import math
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from src.cli import main
from src.logger import Logger
from src.parameter_distributions import _plot_distribution, generate_parameter_distributions
from src.patient_heatmaps import build_parameter_matrix
from src.patient_pipeline import analyze_inputs
from src.outcome_rules import CreatinineStager, OutcomeClassifier
from src.pipeline import load_parameter_map
from src.parameter_map import write_parameter_map
from src.postoperative import PostoperativeProcessor
from src.preoperative import PreoperativeProcessor


ROOT = Path(__file__).resolve().parents[1]
PARAMETER_MAP = ROOT / "config" / "pipeline.xml"


class PatientVectorTests(unittest.TestCase):
    headers = [
        "unixtime",
        "datetime",
        "epic.EVENTS.EVENT",
        "epic.DEMOGRAPHICS.age",
        "epic.DEMOGRAPHICS.sex",
        "epic.DEMOGRAPHICS.gewicht",
        "epic.DEMOGRAPHICS.lengte",
        "epic.DEMOGRAPHICS.BMI",
        "epic.DEMOGRAPHICS.ASA",
        "epic.INGREEP.AN_PROC_NAME",
        "epic.LAB.Kreatinine (umol/L)",
        "epic.LAB.Hb (mmol/L)",
        "q_umcg_aki_marco.adm_deceased",
        "q_umcg_aki_marco.adm_ventilationdays",
        "q_umcg_aki_marco.adm_PRISMII",
        "epic.FLUIDS_OUT.Urine (ml)",
        "epic.DEMOGRAPHICS.days mortality",
        "operation result",
    ]

    def make_patient(
        self,
        root: Path,
        filename: str,
        *,
        postop_creatinine: str = "45",
        deceased: str = "0",
        operation_result: str = "",
        postop_ventilation: str = "",
        pre_creatinine: str = "40",
        pre_hemoglobin: str = "8.1",
        days_mortality: str = "",
        later_days_mortality: str = "",
        missing_height: bool = False,
        missing_bmi: bool = False,
    ) -> Path:
        source = root / filename
        epoch = 1_700_000_000
        pre = {
            "epic.DEMOGRAPHICS.age": "10",
            "epic.DEMOGRAPHICS.sex": "F",
            "epic.DEMOGRAPHICS.gewicht": "20",
            "epic.DEMOGRAPHICS.lengte": "110",
            "epic.DEMOGRAPHICS.BMI": "16.5",
            "epic.DEMOGRAPHICS.ASA": "2",
            "epic.INGREEP.AN_PROC_NAME": "synthetic procedure",
            "epic.LAB.Kreatinine (umol/L)": pre_creatinine,
            "epic.LAB.Hb (mmol/L)": pre_hemoglobin,
            "epic.DEMOGRAPHICS.days mortality": days_mortality,
        }
        if missing_height:
            pre["epic.DEMOGRAPHICS.lengte"] = ""
        if missing_bmi:
            pre["epic.DEMOGRAPHICS.BMI"] = ""

        def row(seconds: int, values: dict[str, str]) -> list[str]:
            record = {header: "" for header in self.headers}
            record["unixtime"] = str(epoch + seconds)
            record.update(values)
            return [record[header] for header in self.headers]

        rows = [
            row(0, pre),
            row(3600, {"epic.EVENTS.EVENT": "Start anesthesie"}),
        ]
        rows.append(
            row(
                7200,
                {
                    "epic.LAB.Kreatinine (umol/L)": postop_creatinine,
                    "q_umcg_aki_marco.adm_deceased": deceased,
                    "q_umcg_aki_marco.adm_ventilationdays": postop_ventilation,
                    "q_umcg_aki_marco.adm_PRISMII": "7",
                    "epic.FLUIDS_OUT.Urine (ml)": "15",
                    "epic.DEMOGRAPHICS.days mortality": later_days_mortality,
                    "operation result": operation_result,
                },
            )
        )
        with source.open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream, delimiter="\t")
            writer.writerow(self.headers)
            writer.writerows(rows)
        return source

    def run_analysis(self, inputs: list[Path], output: Path, logs: Path):
        with redirect_stderr(io.StringIO()), Logger(log_root=logs) as logger:
            return analyze_inputs(inputs, output, PARAMETER_MAP, logger)

    def test_bmi_is_derived_from_preoperative_weight_and_height_when_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.make_patient(root, "patient.csv", missing_bmi=True)
            result = PreoperativeProcessor(load_parameter_map(PARAMETER_MAP)).process(source)
            self.assertAlmostEqual(float(result.features["bmi"]), 20 / (1.1 ** 2), places=8)

            source = self.make_patient(root, "missing-height.csv", missing_bmi=True, missing_height=True)
            result = PreoperativeProcessor(load_parameter_map(PARAMETER_MAP)).process(source)
            self.assertEqual(result.features["bmi"], "")

    def test_existing_bmi_is_not_replaced_by_derived_value(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = self.make_patient(Path(temporary), "patient.csv")
            result = PreoperativeProcessor(load_parameter_map(PARAMETER_MAP)).process(source)
            self.assertEqual(result.features["bmi"], "16.5")

    def test_one_file_becomes_one_preoperative_vector_with_binary_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_patient(root, "patient-a.csv", postop_creatinine="100")
            self.make_patient(root, "patient-b.csv", postop_creatinine="45", missing_bmi=True)
            self.make_patient(root, "patient-c.csv", postop_creatinine="", deceased="")
            self.run_analysis([root], root / "results", root / "logs")

            with (root / "results/patient_vectors.csv").open(
                newline="", encoding="utf-8"
            ) as stream:
                patients = list(csv.DictReader(stream))
            self.assertEqual(len(patients), 3)
            self.assertAlmostEqual(float(patients[1]["bmi"]), 20 / (1.1 ** 2), places=8)
            self.assertEqual(
                list(patients[0]),
                ["patient_id", "age", "sex", "weight", "height", "bmi", "asa", "operation_diagnosis_proxy", "baseline_creatinine", "baseline_hemoglobin"],
            )
            self.assertEqual(patients[0]["age"], "10")
            self.assertEqual(patients[0]["baseline_creatinine"], "40")
            self.assertNotIn("postoperative_creatinine_stage_screen", patients[0])
            with (root / "results/patient_outcomes.csv").open(newline="", encoding="utf-8") as stream:
                outcomes = list(csv.DictReader(stream))
            self.assertEqual([row["target"] for row in outcomes], ["-1", "1", "NA"])
            self.assertEqual(outcomes[2]["target_status"], "unclassified")
            with (root / "results/postoperative_measurements.csv").open(newline="", encoding="utf-8") as stream:
                postoperative_measurements = list(csv.DictReader(stream))
            self.assertEqual(postoperative_measurements[0]["aki_kdigo_stage"], "2")
            self.assertNotIn("patient-a.csv", (root / "results/patient_vectors.csv").read_text(encoding="utf-8"))
            csv_outputs = list((root / "results").glob("*.csv"))
            self.assertEqual({path.name for path in csv_outputs}, {"patient_vectors.csv", "patient_outcomes.csv", "postoperative_measurements.csv"})

    def test_numeric_distribution_parses_values_before_annotations(self):
        with tempfile.TemporaryDirectory() as temporary:
            plot = Path(temporary) / "numeric.png"
            kind, values, categories = _plot_distribution(
                plot,
                "Synthetic numeric labs",
                ["8.1 | Te weinig monster.", "10.2 | note", "Te weinig monster."],
                bins=5,
                numeric_expected=True,
            )
            self.assertEqual(kind, "numeric")
            self.assertEqual(values, [8.1, 10.2])
            self.assertFalse(categories)
            self.assertTrue(plot.exists())

    def test_numeric_lab_fields_keep_value_prefix_and_plot_as_numeric(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_patient(
                root,
                "patient.csv",
                pre_creatinine="40 | Te weinig monster.",
                pre_hemoglobin="8.1 | Te weinig monster.",
                postop_creatinine="45 | Te weinig monster.",
            )
            output = root / "results"
            self.run_analysis([root], output, root / "logs")
            with (output / "patient_vectors.csv").open(newline="", encoding="utf-8") as stream:
                patient = next(csv.DictReader(stream))
            self.assertEqual(patient["baseline_creatinine"], "40")
            self.assertEqual(patient["baseline_hemoglobin"], "8.1")
            self.assertNotIn("target", patient)
            with (output / "postoperative_measurements.csv").open(newline="", encoding="utf-8") as stream:
                postoperative = next(csv.DictReader(stream))
            self.assertEqual(postoperative["postop_creatinine_72h"], "45")

            generate_parameter_distributions(output, PARAMETER_MAP, phase="preoperative")
            with (output / "parameter_distribution_summary.csv").open(newline="", encoding="utf-8") as stream:
                summaries = {row["parameter_id"]: row for row in csv.DictReader(stream)}
            for parameter_id in ("baseline_creatinine", "baseline_hemoglobin"):
                self.assertEqual(summaries[parameter_id]["kind"], "numeric")
                self.assertTrue((output / summaries[parameter_id]["plot"]).exists())

    def test_unclassified_outcome_is_not_given_a_binary_target_or_trained(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.make_patient(root, "patient.csv", postop_creatinine="", deceased="")
            with source.open(newline="", encoding="utf-8-sig") as stream:
                rows = list(csv.reader(stream, delimiter="\t"))
            outcome_index = self.headers.index("operation result")
            failure_row = rows[-1].copy()
            success_row = rows[-1].copy()
            failure_row[outcome_index] = "severe problems"
            success_row[outcome_index] = "optimal condition"
            rows.extend([failure_row, success_row])
            with source.open("w", newline="", encoding="utf-8-sig") as stream:
                csv.writer(stream, delimiter="\t").writerows(rows)

            config = load_parameter_map(PARAMETER_MAP)
            config["patient_outcome_rules"]["categorical_rules"] = []
            config_path = root / "parameters.xml"
            write_parameter_map(config_path, config)
            with redirect_stderr(io.StringIO()), Logger(log_root=root / "logs") as logger:
                analyze_inputs([source], root / "results", config_path, logger)

            with (root / "results/patient_outcomes.csv").open(newline="", encoding="utf-8") as stream:
                outcome = next(csv.DictReader(stream))
            self.assertEqual(outcome["target"], "NA")
            self.assertEqual(outcome["target_status"], "unclassified")
            self.assertNotIn("outcome_score", outcome)
            self.assertEqual(len(list((root / "results").glob("*.csv"))), 3)

    def test_parameter_catalog_contains_only_pre_and_postoperative_parameters(self):
        catalog = load_parameter_map(PARAMETER_MAP)
        phases = {parameter["phase"] for parameter in catalog["parameters"]}
        self.assertEqual(phases, {"Pre-operative", "Post-operative"})
        self.assertEqual(len(catalog["parameters"]), 20)
        required = {
            "clinical_parameter", "role", "priority", "exact_field_names",
            "workbook_exact_field_entry", "workbook_row", "extraction_definition",
            "parameter_type", "unit", "value_range",
        }
        self.assertTrue(all(required.issubset(parameter) for parameter in catalog["parameters"]))
        age = next(parameter for parameter in catalog["parameters"] if parameter["id"] == "age")
        self.assertEqual(age["clinical_parameter"], "Age")
        self.assertEqual(age["exact_field_names"], ["epic.DEMOGRAPHICS.age"])
        self.assertEqual(age["workbook_row"], 6)
        self.assertEqual(len(PreoperativeProcessor(catalog).parameters), 9)
        self.assertEqual(len(PostoperativeProcessor(catalog).parameters), 11)
        self.assertNotIn("operation_duration", PreoperativeProcessor(catalog).parameters)
        self.assertNotIn("operation_duration", PostoperativeProcessor(catalog).parameters)

    def test_unknown_patient_record_scope_is_rejected(self):
        mapping = load_parameter_map(PARAMETER_MAP)
        measurement = next(item for item in mapping["postoperative_measurements"] if item["id"] == "recorded_death_days")
        measurement["record_scope"] = "patinet"
        with self.assertRaisesRegex(ValueError, "Unsupported record_scope"):
            PostoperativeProcessor(mapping)

    def test_patient_record_scope_cannot_have_postoperative_window(self):
        mapping = load_parameter_map(PARAMETER_MAP)
        measurement = next(item for item in mapping["postoperative_measurements"] if item["id"] == "recorded_death_days")
        measurement["window_hours"] = 72
        with self.assertRaisesRegex(ValueError, "patient record_scope cannot have window_hours"):
            PostoperativeProcessor(mapping)

    def test_phase_processors_keep_features_and_outcomes_separate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.make_patient(root, "patient.csv", postop_creatinine="100")
            parameter_map = load_parameter_map(PARAMETER_MAP)

            preoperative = PreoperativeProcessor(parameter_map)
            postoperative = PostoperativeProcessor(parameter_map)
            preoperative_result = preoperative.process(source)
            postoperative_result = postoperative.process(source, preoperative_result)

            expected_preoperative = [
                item["id"] for item in parameter_map["parameters"]
                if item["phase"] == "Pre-operative"
            ]
            expected_postoperative = [
                item["id"] for item in parameter_map["parameters"]
                if item["phase"] == "Post-operative"
            ]
            self.assertEqual(list(preoperative.parameters), expected_preoperative)
            self.assertEqual(list(postoperative.parameters), expected_postoperative)
            self.assertEqual(preoperative_result.features["age"], "10")
            self.assertFalse(hasattr(preoperative_result, "target"))
            self.assertEqual(postoperative_result.target, -1)
            self.assertEqual(postoperative_result.measurements["postop_creatinine_72h"], "100")

    def test_preoperative_cutoff_blocks_postoperative_values_from_features(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.make_patient(root, "patient.csv", postop_creatinine="45")
            with source.open(newline="", encoding="utf-8-sig") as stream:
                rows = list(csv.reader(stream, delimiter="\t"))
            rows[1][self.headers.index("operation result")] = "severe problems"
            rows[-1][self.headers.index("operation result")] = "optimal condition"
            rows[-1][self.headers.index("epic.DEMOGRAPHICS.age")] = "99"
            with source.open("w", newline="", encoding="utf-8-sig") as stream:
                csv.writer(stream, delimiter="\t").writerows(rows)
            self.run_analysis([source], root / "results", root / "logs")
            with (root / "results/patient_vectors.csv").open(
                newline="", encoding="utf-8"
            ) as stream:
                patient = next(csv.DictReader(stream))
            self.assertEqual(patient["age"], "10")
            with (root / "results/patient_outcomes.csv").open(newline="", encoding="utf-8") as stream:
                outcome = next(csv.DictReader(stream))
            self.assertEqual(outcome["target"], "1")

    def test_missingness_and_cleanest_complete_case_heatmap(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_patient(root, "patient-a.csv", postop_creatinine="100")
            self.make_patient(root, "patient-b.csv", postop_creatinine="45")
            self.make_patient(root, "patient-c.csv", postop_creatinine="", deceased="", missing_height=True)
            self.run_analysis([root], root / "results", root / "logs")

            with (root / "results/patient_vectors.csv").open(newline="", encoding="utf-8") as stream:
                vectors = list(csv.DictReader(stream))
            self.assertEqual(len(vectors), 3)
            self.assertEqual(vectors[2]["height"], "")
            self.assertTrue((root / "results/preoperative_missingness_heatmap.png").is_file())
            self.assertTrue((root / "results/preoperative_heatmap.png").is_file())
            self.assertTrue((root / "results/postoperative_heatmap.png").is_file())
            with (root / "results/postoperative_measurements.csv").open(
                newline="", encoding="utf-8"
            ) as stream:
                postoperative_reader = csv.DictReader(stream)
                postoperative = list(postoperative_reader)
                configured_postoperative_ids = {
                    parameter["id"]
                    for parameter in load_parameter_map(PARAMETER_MAP)["parameters"]
                    if parameter["phase"] == "Post-operative"
                }
                self.assertTrue(configured_postoperative_ids.issubset(set(postoperative_reader.fieldnames or [])))
            self.assertEqual(postoperative[0]["postop_creatinine_72h"], "100")
            self.assertEqual(postoperative[0]["prism_ii"], "7")
            measurement_specs = load_parameter_map(PARAMETER_MAP)["postoperative_measurements"]
            _, heatmap_labels = build_parameter_matrix(
                [
                    {"patient_id": row["patient_id"], "postoperative_measurements": row}
                    for row in postoperative
                ],
                measurement_specs,
                value_key="postoperative_measurements",
            )
            self.assertTrue(all(any(spec["id"] in label for label in heatmap_labels) for spec in measurement_specs))
            self.assertEqual(len(list((root / "results").glob("*.csv"))), 3)

    def test_death_and_explicit_postoperative_categories_are_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_patient(root, "patient-a.csv", postop_creatinine="", deceased="yes")
            self.make_patient(root, "patient-b.csv", postop_creatinine="", operation_result="severe problems")
            self.run_analysis([root], root / "results", root / "logs")
            with (root / "results/patient_outcomes.csv").open(newline="", encoding="utf-8") as stream:
                targets = [row["target"] for row in csv.DictReader(stream)]
            self.assertEqual(targets, ["-1", "-1"])

    def test_days_mortality_zero_is_failure_even_when_metadata_precedes_operation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.make_patient(
                root, "patient.csv", days_mortality="0", deceased="0", postop_creatinine=""
            )
            self.run_analysis([source], root / "results", root / "logs")
            with (root / "results/patient_vectors.csv").open(newline="", encoding="utf-8") as stream:
                vectors = next(csv.DictReader(stream))
            with (root / "results/postoperative_measurements.csv").open(newline="", encoding="utf-8") as stream:
                measurements = next(csv.DictReader(stream))
            with (root / "results/patient_outcomes.csv").open(newline="", encoding="utf-8") as stream:
                outcome = next(csv.DictReader(stream))
            self.assertNotIn("recorded_death_days", vectors)
            self.assertEqual(measurements["recorded_death_days"], "0")
            self.assertEqual(outcome["target"], "-1")
            self.assertEqual(outcome["target_status"], "failure")
            self.assertIn("recorded_death_days", outcome["label_basis"])

    def test_nonnegative_days_override_negative_value_across_patient_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.make_patient(
                root, "patient.csv", days_mortality="-1", later_days_mortality="4",
                deceased="", postop_creatinine="",
            )
            self.run_analysis([source], root / "results", root / "logs")
            with (root / "results/postoperative_measurements.csv").open(newline="", encoding="utf-8") as stream:
                measurements = next(csv.DictReader(stream))
            with (root / "results/patient_outcomes.csv").open(newline="", encoding="utf-8") as stream:
                outcome = next(csv.DictReader(stream))
            self.assertEqual(measurements["recorded_death_days"], "4")
            self.assertEqual((outcome["target"], outcome["label_basis"]),
                             ("-1", "recorded_death_days"))

    def test_survival_flag_without_other_outcome_evidence_is_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.make_patient(root, "patient.csv", postop_creatinine="", deceased="0")
            self.run_analysis([source], root / "results", root / "logs")
            with (root / "results/patient_outcomes.csv").open(newline="", encoding="utf-8") as stream:
                patient = next(csv.DictReader(stream))
            self.assertEqual(patient["target"], "1")

    def test_cli_runs_and_logs_without_patient_filenames(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_patient(root, "private-name.csv", postop_creatinine="100")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = main([
                    "analyze",
                    str(root),
                    "--output", str(root / "results"),
                    "--log-dir", str(root / "logs"),
                ])
            self.assertEqual(result, 0)
            self.assertIn("1 patient(s) analyzed", stdout.getvalue())
            self.assertNotIn("one patient per file", stdout.getvalue())
            log = next((root / "logs").glob("*/run.log")).read_text(encoding="utf-8")
            self.assertIn("analysis completed", log)
            self.assertNotIn("private-name.csv", log)

    def test_distributions_command_generates_per_parameter_plots_and_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_patient(root, "patient-a.csv", postop_creatinine="100")
            self.make_patient(root, "patient-b.csv", postop_creatinine="45")
            stdout = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                result = main([
                    "distributions",
                    str(root),
                    "--output", str(root / "results"),
                    "--log-dir", str(root / "logs"),
                ])
            self.assertEqual(result, 0)
            plots = root / "results" / "parameter_distributions"
            self.assertTrue((plots / "preoperative_age.png").is_file())
            self.assertTrue((plots / "preoperative_operation_diagnosis_proxy.png").is_file())
            self.assertTrue((plots / "postoperative_postop_creatinine_72h.png").is_file())
            summary = (root / "results" / "parameter_distribution_summary.csv").read_text(encoding="utf-8")
            self.assertIn("operation_diagnosis_proxy,Operation / diagnosis proxy,Pre-operative,categorical,2,2", summary)
            self.assertIn("postop_creatinine_72h,Creatinine first 72 h,Post-operative,numeric,2,2", summary)
            self.assertIn("distribution", stdout.getvalue().lower())

    def test_heatmap_matrix_keeps_all_missing_configured_parameters(self):
        patients = [
            {"patient_id": "patient_001", "postoperative_measurements": {"observed": "12", "empty": ""}},
            {"patient_id": "patient_002", "postoperative_measurements": {"observed": "18", "empty": ""}},
        ]
        matrix, labels = build_parameter_matrix(
            patients,
            [{"id": "observed"}, {"id": "empty"}],
            value_key="postoperative_measurements",
        )
        self.assertEqual(len(labels), 2)
        self.assertEqual(len(matrix[0]), 2)
        self.assertTrue(math.isnan(matrix[0][1]))
        self.assertIn("empty", labels[1])

    def test_unmapped_schema_still_writes_an_explanatory_cleanest_heatmap(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "patient.csv"
            source.write_text("unknown_column\nvalue\n", encoding="utf-8")
            self.run_analysis([source], root / "results", root / "logs")
            self.assertTrue((root / "results/preoperative_heatmap.png").is_file())
            with (root / "results/patient_vectors.csv").open(newline="", encoding="utf-8") as stream:
                vectors = list(csv.DictReader(stream))
            self.assertEqual(len(vectors), 1)
            self.assertNotIn("target", vectors[0])
            self.assertEqual(len(list((root / "results").glob("*.csv"))), 3)

    def test_creatinine_staging_uses_configured_xml_thresholds_and_time_windows(self):
        hour = 3600
        rules = load_parameter_map(PARAMETER_MAP)["creatinine_staging_rules"]
        stager = CreatinineStager(rules)
        self.assertEqual(stager.stage(40, 0, [(hour, 60)]), 1)
        self.assertEqual(stager.stage(40, 0, [(hour, 80)]), 2)
        self.assertEqual(stager.stage(200, 0, [(hour, 360)]), 3)
        self.assertEqual(stager.stage(100, 0, [(hour, 70)]), 0)
        self.assertIsNone(stager.stage(40, 0, [(8 * 24 * hour, 100)]))
        self.assertEqual(
            stager.stage(40, 0, [(8 * 24 * hour, 50), (8 * 24 * hour + hour, 80)]),
            1,
        )

        changed_rules = json.loads(json.dumps(rules))
        changed_rules["ratio_stages"][-1]["minimum_ratio"] = 1.2
        self.assertEqual(CreatinineStager(changed_rules).stage(40, 0, [(hour, 50)]), 1)

    def test_parameter_criteria_classify_measurements_without_a_score(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.make_patient(
                root,
                "patient.csv",
                postop_creatinine="45",
                postop_ventilation="7",
            )
            parameter_map = load_parameter_map(PARAMETER_MAP)
            ventilation = next(item for item in parameter_map["parameters"] if item["id"] == "ventilation_duration")
            ventilation["outcome_criteria"] = {
                "success": {"operator": "lte", "value": 7},
                "failure": {"operator": "gt", "value": 7},
                "status": "synthetic_test",
            }
            config = root / "parameters.xml"
            write_parameter_map(config, parameter_map)
            with redirect_stderr(io.StringIO()), Logger(log_root=root / "logs") as logger:
                analyze_inputs([source], root / "results", config, logger)
            with (root / "results/patient_outcomes.csv").open(newline="", encoding="utf-8") as stream:
                patient = next(csv.DictReader(stream))
            self.assertEqual(patient["target"], "1")
            self.assertNotIn("outcome_score", patient)
            self.assertEqual(patient["target_status"], "success")
            with (root / "results/postoperative_measurements.csv").open(newline="", encoding="utf-8") as stream:
                measurements = next(csv.DictReader(stream))
            self.assertEqual(measurements["ventilation_duration"], "7")

    def test_category_mapping_uses_xml_rules_and_preserves_unknown(self):
        parameter_map = load_parameter_map(PARAMETER_MAP)
        self.assertEqual(PostoperativeProcessor(parameter_map).classifier.classify({}, categories=["optimal condition"]).target, 1)
        self.assertEqual(PostoperativeProcessor(parameter_map).classifier.classify({}, categories=["mid condition"]).target, 1)
        self.assertEqual(PostoperativeProcessor(parameter_map).classifier.classify({}, categories=["severe problems"]).target, -1)
        self.assertEqual(PostoperativeProcessor(parameter_map).classifier.classify({}, categories=["death"]).target, -1)
        self.assertIsNone(PostoperativeProcessor(parameter_map).classifier.classify({}, categories=["other / unknown"]).target)



if __name__ == "__main__":
    unittest.main()
