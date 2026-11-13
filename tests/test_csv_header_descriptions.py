"""Synthetic-only checks for the XML-controlled, header-only CSV inventory."""

import csv
import io
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from src.cli import main
from src.csv_header_descriptions import write_csv_header_descriptions
from src.pipeline_config import load_pipeline_config

ROOT = Path(__file__).resolve().parents[1]


class CsvHeaderDescriptionTests(unittest.TestCase):
    def test_reports_every_header_and_groups_numbered_filenames_without_reading_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = root / "inputs"
            inputs.mkdir()
            (inputs / "nested").mkdir()
            (inputs / "results").mkdir()
            (inputs / "case_1.csv").write_bytes(
                b"\xef\xbb\xbfunixtime\tdatetime\treading A\n"
                b"this is not valid utf-8: \xff\n"
            )
            (inputs / "case_2.csv").write_text(
                "unixtime\tdatetime\treading B\nprivate patient value\n", encoding="utf-8"
            )
            with (inputs / "nested" / "case_1.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["unixtime", "quoted, field"])
                writer.writerow(["secret", "do not report"])
            # An old output must not become an input on a repeat run.
            (inputs / "results" / "previous.csv").write_text("not a patient header\n", encoding="utf-8")
            inventory = inputs / "results" / "csv_headers.csv"
            summary = inputs / "results" / "csv_header_summary.md"
            stats = write_csv_header_descriptions([inputs, inputs / "case_2.csv"], inputs / "results", inventory, summary)
            self.assertEqual(stats, {"files": 3, "unique_headers": 5, "shared_headers": 1, "families": 2})
            with inventory.open(newline="", encoding="utf-8") as stream:
                records = list(csv.DictReader(stream))
            self.assertEqual(len(records), 8)
            self.assertEqual({r["file"] for r in records}, {"case_1.csv", "case_2.csv", "nested/case_1.csv"})
            self.assertEqual(
                [(r["position"], r["header"], r["family"], r["part"]) for r in records if r["file"] == "case_1.csv"],
                [("1", "unixtime", "case", "1"), ("2", "datetime", "case", "1"), ("3", "reading A", "case", "1")],
            )
            self.assertEqual([r["part"] for r in records if r["file"] == "case_2.csv"], ["2"] * 3)
            self.assertEqual([r["family"] for r in records if r["file"] == "nested/case_1.csv"], ["nested/case"] * 2)
            report = summary.read_text(encoding="utf-8")
            self.assertIn("case_1.csv", report)
            self.assertIn("case_2.csv", report)
            self.assertIn("nested/case_1.csv", report)
            self.assertIn("quoted, field", report)
            self.assertIn("reading A", report)
            self.assertIn("## Ordered header schemas", report)
            self.assertNotIn("private patient value", report)
            self.assertNotIn("not a patient header", report)

    def test_report_lists_epic_classes_and_phase_assigned_headers_without_guessing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "synthetic.csv"
            names = [
                "epic.DEMOGRAPHICS.age", "epic.DEMOGRAPHICS.days mortality",
                "epic.LAB.Kreatinine (umol/L)", "epic.MONITOR.heart rate",
                "epic.MONITOR.unknown", "epic.MEDICATION.unknown", "q.other.field",
            ]
            with source.open("w", encoding="utf-8", newline="") as stream:
                csv.writer(stream, delimiter="\t").writerow(names)
            mapping = {"parameters": [
                {"phase": "Pre-operative", "source_fields": [names[0], names[2]]},
                {"phase": "During-operative", "source_fields": [names[3]]},
                {"phase": "Post-operative", "source_fields": [names[1], names[2]]},
            ]}
            inventory, summary = root / "out/inventory.csv", root / "out/summary.md"
            write_csv_header_descriptions([source], root / "out", inventory, summary, parameter_map=mapping)
            report = summary.read_text(encoding="utf-8")
            pre = report.split("## Pre-operative headers", 1)[1].split("## During-operative headers", 1)[0]
            during = report.split("## During-operative headers", 1)[1].split("## Post-operative headers", 1)[0]
            post = report.split("## Post-operative headers", 1)[1].split("## Unassigned headers", 1)[0]
            unassigned = report.split("## Unassigned headers", 1)[1].split("## Epic class index", 1)[0]
            self.assertIn("| epic.DEMOGRAPHICS.age | epic.DEMOGRAPHICS | 1 |", pre)
            self.assertIn("| epic.LAB.Kreatinine (umol/L) | epic.LAB | 1 |", pre)
            self.assertIn("| epic.LAB.Kreatinine (umol/L) | epic.LAB | 1 |", post)
            self.assertIn("| epic.DEMOGRAPHICS.days mortality | epic.DEMOGRAPHICS | 1 |", post)
            self.assertNotIn("epic.DEMOGRAPHICS.days mortality", pre)
            self.assertIn("### Classes in Pre-operative", pre)
            self.assertIn("### Classes in During-operative", during)
            self.assertIn("| epic.MONITOR | 1 |", during)
            self.assertIn("### Classes in Post-operative", post)
            self.assertIn("epic.MEDICATION.unknown", unassigned)
            self.assertIn("epic.MONITOR.unknown", unassigned)
            self.assertIn("q.other.field", unassigned)
            index = report.split("## Epic class index", 1)[1]
            self.assertIn("epic.MEDICATION", index)
            self.assertIn("epic.MONITOR", index)

    def test_padded_patient_identifier_and_numbered_parts_share_a_report_family(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = root / "inputs"
            inputs.mkdir()
            (inputs / "other").mkdir()
            for relative in ("ref_0002.csv", "ref_0002_1.csv", "ref_0003.csv", "other/ref_0002.csv"):
                (inputs / relative).write_text("time\tfield\n", encoding="utf-8")
            inventory, summary = root / "out/inventory.csv", root / "out/summary.md"
            stats = write_csv_header_descriptions([inputs], root / "out", inventory, summary)
            self.assertEqual(stats["families"], 3)
            self.assertIn("- Patient families (by filename): 3", summary.read_text(encoding="utf-8"))
            with inventory.open(newline="", encoding="utf-8") as stream:
                families = {row["file"]: (row["family"], row["part"]) for row in csv.DictReader(stream)}
            self.assertEqual(families, {
                "ref_0002.csv": ("ref_0002", ""),
                "ref_0002_1.csv": ("ref_0002", "1"),
                "ref_0003.csv": ("ref_0003", ""),
                "other/ref_0002.csv": ("other/ref_0002", ""),
            })
            self.assertIn("| 1 | ref_0002 | 2 | ref_0002.csv, ref_0002_1.csv |", summary.read_text(encoding="utf-8"))

    def test_explicit_files_in_same_directory_share_family_across_input_entries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base, part = root / "ref_0002.csv", root / "ref_0002_1.csv"
            for source in (base, part):
                source.write_text("time\tfield\n", encoding="utf-8")
            inventory, summary = root / "out/inventory.csv", root / "out/summary.md"
            stats = write_csv_header_descriptions([base, part], root / "out", inventory, summary)
            self.assertEqual(stats["families"], 1)
            with inventory.open(newline="", encoding="utf-8") as stream:
                self.assertEqual({row["family"] for row in csv.DictReader(stream)}, {"ref_0002"})
            self.assertIn("ref_0002.csv, ref_0002_1.csv", summary.read_text(encoding="utf-8"))

    def test_xml_stage_runs_with_preprocessing_disabled_and_does_not_log_filenames(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            (project / "config").mkdir()
            (project / "data" / "patients").mkdir(parents=True)
            (project / "data" / "patients" / "synthetic_1.csv").write_text("epic.DEMOGRAPHICS.age,x\nprivate\n", encoding="utf-8")
            config_path = project / "config" / "pipeline.xml"
            tree = ET.parse(ROOT / "config" / "pipeline.template.xml")
            root = tree.getroot()
            root.find("preprocessing/inputs/path").text = "data/patients"
            root.find("preprocessing/outputDirectory").text = "results"
            root.find("logging/directory").text = "logs"
            node = root.find("preprocessing/csvHeaderDescriptions")
            self.assertIsNotNone(node)
            node.find("run").text = "true"
            node.find("inventoryOutput").text = "results/csv_headers.csv"
            node.find("summaryOutput").text = "results/csv_header_summary.md"
            tree.write(config_path, encoding="utf-8", xml_declaration=True)
            settings = load_pipeline_config(config_path)["preprocessing"]
            self.assertFalse(settings["run"])
            self.assertTrue(settings["csv_header_descriptions"]["run"])
            self.assertEqual(settings["csv_header_descriptions"]["inventory_output"], project / "results" / "csv_headers.csv")
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(main(["run", "--config", str(config_path)]), 0)
            self.assertTrue((project / "results" / "csv_headers.csv").exists())
            self.assertTrue((project / "results" / "csv_header_summary.md").exists())
            report = (project / "results" / "csv_header_summary.md").read_text(encoding="utf-8")
            pre = report.split("## Pre-operative headers", 1)[1].split("## During-operative headers", 1)[0]
            self.assertIn("| epic.DEMOGRAPHICS.age | epic.DEMOGRAPHICS | 1 |", pre)
            self.assertIn("| epic.DEMOGRAPHICS | 1 |", pre)
            self.assertNotIn("epic.DEMOGRAPHICS.age", report.split("## Unassigned headers", 1)[1])
            self.assertFalse((project / "results" / "patient_vectors.csv").exists())
            self.assertNotIn("synthetic_1.csv", stderr.getvalue())
            for path in (project / "logs").rglob("run.log"):
                self.assertNotIn("synthetic_1.csv", path.read_text(encoding="utf-8"))
            node.find("run").text = "false"
            tree.write(config_path, encoding="utf-8", xml_declaration=True)
            (project / "results" / "csv_headers.csv").unlink()
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["run", "--config", str(config_path)]), 0)
            self.assertFalse((project / "results" / "csv_headers.csv").exists())

    def test_tab_separated_epic_headers_with_commas_are_not_split_at_commas(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "many_columns_1.csv"
            headers = ["unixtime", "datetime", *(
                f"epic.MEDICATION.drug {index}, dose (mg)" for index in range(15)
            )]
            source.write_text("\t".join(headers) + "\n", encoding="utf-8")
            inventory, summary = root / "results" / "inventory.csv", root / "results" / "summary.md"
            write_csv_header_descriptions([source], root / "results", inventory, summary)
            with inventory.open(newline="", encoding="utf-8") as stream:
                actual = [row["header"] for row in csv.DictReader(stream)]
            self.assertEqual(actual, headers)

    def test_quoted_multiline_tab_header_does_not_consume_data_row(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "case_01.csv"
            source.write_bytes(b'"first\nline"\tsecond\tthird\nprivate: \xff\n')
            inventory, summary = root / "results" / "inventory.csv", root / "results" / "summary.md"
            write_csv_header_descriptions([source], root / "results", inventory, summary)
            with inventory.open(newline="", encoding="utf-8") as stream:
                records = list(csv.DictReader(stream))
            self.assertEqual([row["header"] for row in records], ["first\nline", "second", "third"])
            self.assertTrue(all(row["part"] == "01" for row in records))

    def test_summary_escapes_untrusted_header_markup_but_inventory_preserves_exact_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "case.csv"
            with source.open("w", encoding="utf-8", newline="") as stream:
                csv.writer(stream).writerow(["<script>alert(1)</script>|field", "repeated", "repeated"])
            inventory, summary = root / "out" / "inventory.csv", root / "out" / "summary.md"
            write_csv_header_descriptions([source], root / "out", inventory, summary)
            with inventory.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([row["header"] for row in rows], ["<script>alert(1)</script>|field", "repeated", "repeated"])
            report = summary.read_text(encoding="utf-8")
            self.assertNotIn("<script>", report)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;\\|field", report)
            self.assertIn("| repeated | 1 |", report)

    def test_rejects_report_destination_that_is_an_input_csv(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "original.csv"
            other = root / "other.csv"
            original.write_text("original-header\n", encoding="utf-8")
            other.write_text("other-header\n", encoding="utf-8")
            before = original.read_bytes()
            with self.assertRaisesRegex(ValueError, "overlaps a CSV input"):
                write_csv_header_descriptions([root], root / "results", original, root / "results" / "summary.md")
            self.assertEqual(original.read_bytes(), before)

    def test_rejects_oversized_header_before_writing_reports(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "large.csv"
            source.write_bytes(b"x" * 1_000_001 + b"\n")
            inventory, summary = root / "out" / "inventory.csv", root / "out" / "summary.md"
            with self.assertRaisesRegex(ValueError, "CSV header exceeds the supported size"):
                write_csv_header_descriptions([source], root / "out", inventory, summary)
            self.assertFalse(inventory.exists())
            self.assertFalse(summary.exists())

    def test_empty_header_and_missing_inputs_fail_without_creating_reports(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            empty = root / "empty.csv"
            empty.write_text("", encoding="utf-8")
            inventory, summary = root / "out" / "inventory.csv", root / "out" / "summary.md"
            with self.assertRaisesRegex(ValueError, "empty CSV header"):
                write_csv_header_descriptions([empty], root / "out", inventory, summary)
            self.assertFalse(inventory.exists())
            empty.unlink()
            with self.assertRaisesRegex(ValueError, "No CSV files"):
                write_csv_header_descriptions([root], root / "out", inventory, summary)


if __name__ == "__main__":
    unittest.main()
