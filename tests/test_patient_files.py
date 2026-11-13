"""Synthetic filename-only and extraction checks for CSV parts per patient."""

import csv
import io
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from src.cli import main
from src.logger import Logger
from src.patient_pipeline import analyze_inputs

ROOT = Path(__file__).resolve().parents[1]


class PatientFileGroupingTests(unittest.TestCase):
    def test_base_file_and_numbered_parts_are_one_patient_not_other_padded_ids(self):
        from src.pipeline import group_patient_files

        groups = group_patient_files([
            Path("cohort/ref_0003.csv"),
            Path("cohort/ref_0002_2.csv"),
            Path("ward/ref_0002.csv"),
            Path("cohort/ref_0002_1.csv"),
            Path("cohort/ref_0002.csv"),
        ])
        self.assertEqual(groups, [
            (Path("cohort/ref_0002.csv"), Path("cohort/ref_0002_1.csv"), Path("cohort/ref_0002_2.csv")),
            (Path("cohort/ref_0003.csv"),),
            (Path("ward/ref_0002.csv"),),
        ])

    def test_numbered_parts_without_base_share_family_but_folders_do_not(self):
        from src.pipeline import group_patient_files

        groups = group_patient_files([
            Path("ward-a/case_2.csv"), Path("ward-a/case_1.csv"),
            Path("ward-b/case_1.csv"), Path("ward-a/ref_0002_2.csv"), Path("ward-a/ref_0002_1.csv"),
        ])
        self.assertEqual(groups, [
            (Path("ward-a/case_1.csv"), Path("ward-a/case_2.csv")),
            (Path("ward-a/ref_0002_1.csv"), Path("ward-a/ref_0002_2.csv")),
            (Path("ward-b/case_1.csv"),),
        ])

    def test_two_csv_parts_produce_one_vector_and_one_outcome_with_cross_file_cutoff(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = root / "input"
            inputs.mkdir()

            def write(name, header, rows):
                with (inputs / name).open("w", newline="", encoding="utf-8") as stream:
                    writer = csv.writer(stream, delimiter="\t")
                    writer.writerow(header)
                    writer.writerows(rows)

            write("ref_0002.csv", ["unixtime", "epic.EVENTS.EVENT", "epic.DEMOGRAPHICS.age"], [
                [100, "", 10], [200, "Start surgery", ""],
            ])
            write("ref_0002_1.csv", ["epic.DEMOGRAPHICS.age", "unixtime", "epic.DEMOGRAPHICS.days mortality", "operation result"], [
                [15, 150, "", ""], ["", 300, 0, "optimal condition"],
            ])
            write("ref_0003.csv", ["unixtime", "epic.EVENTS.EVENT", "epic.DEMOGRAPHICS.age", "operation result"], [
                [100, "", 12, ""], [200, "Start surgery", "", ""], [300, "", "", "optimal condition"],
            ])
            with redirect_stderr(io.StringIO()), Logger(log_root=root / "logs") as logger:
                patients = analyze_inputs([inputs], root / "results", ROOT / "config/pipeline.xml", logger, generate_plots=False)
            with (root / "results/patient_vectors.csv").open(newline="", encoding="utf-8") as stream:
                vectors = list(csv.DictReader(stream))
            with (root / "results/patient_outcomes.csv").open(newline="", encoding="utf-8") as stream:
                outcomes = list(csv.DictReader(stream))
            with (root / "results/postoperative_measurements.csv").open(newline="", encoding="utf-8") as stream:
                measurements = list(csv.DictReader(stream))
            self.assertEqual(len(patients), 2)
            self.assertEqual([row["patient_id"] for row in vectors], ["patient_001", "patient_002"])
            self.assertEqual([row["age"] for row in vectors], ["15", "12"])
            self.assertNotIn("recorded_death_days", vectors[0])
            self.assertEqual([(row["target"], row["target_status"]) for row in outcomes],
                             [("-1", "failure"), ("1", "success")])
            self.assertIn("recorded_death_days", outcomes[0]["label_basis"])
            self.assertEqual(len(measurements), 2)
            self.assertEqual(measurements[0]["recorded_death_days"], "0")
            report = (root / "results/extraction_report.md").read_text(encoding="utf-8")
            self.assertIn("- Input CSV files: 3", report)
            self.assertIn("- Patients: 2", report)
            self.assertNotIn("ref_0002", report)
            logs = list((root / "logs").rglob("run.log"))
            self.assertTrue(logs)
            for log in logs:
                text = log.read_text(encoding="utf-8")
                self.assertIn("input_files=3; patients=2", text)
                self.assertNotIn("ref_0002", text)

    def test_xml_runner_produces_one_patient_from_two_numbered_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "config").mkdir()
            (project / "inputs").mkdir()
            base = project / "inputs/ref_0002.csv"
            part = project / "inputs/ref_0002_1.csv"
            base.write_text("unixtime\tepic.EVENTS.EVENT\tepic.DEMOGRAPHICS.age\n100\t\t10\n200\tStart surgery\t\n", encoding="utf-8")
            part.write_text("unixtime\tepic.DEMOGRAPHICS.days mortality\n300\t0\n", encoding="utf-8")
            tree = ET.parse(ROOT / "config/pipeline.template.xml")
            config = tree.getroot()
            config.find("logging/directory").text = "logs"
            config.find("preprocessing/run").text = "true"
            config.find("preprocessing/inputs/path").text = "inputs"
            config.find("preprocessing/outputDirectory").text = "results"
            config_path = project / "config/pipeline.xml"
            tree.write(config_path, encoding="utf-8", xml_declaration=True)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["run", "--config", str(config_path)]), 0)
            with (project / "results/patient_vectors.csv").open(newline="", encoding="utf-8") as stream:
                vectors = list(csv.DictReader(stream))
            with (project / "results/patient_outcomes.csv").open(newline="", encoding="utf-8") as stream:
                outcomes = list(csv.DictReader(stream))
            self.assertEqual(len(vectors), 1)
            self.assertEqual(len(outcomes), 1)
            self.assertEqual(vectors[0]["age"], "10")
            self.assertEqual(outcomes[0]["target"], "-1")


if __name__ == "__main__":
    unittest.main()
