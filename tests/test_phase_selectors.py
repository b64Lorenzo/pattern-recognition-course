"""Synthetic configuration tests for phase-owned parameter definitions."""

import csv
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path

from src.outcome_rules import OutcomeClassifier
from src.patient_pipeline import analyze_inputs
from src.pipeline_config import load_pipeline_config
from src.postoperative import PostoperativeProcessor
from src.preoperative import PreoperativeProcessor
from src.pipeline_runner import run_pipeline

ROOT = Path(__file__).resolve().parents[1]


class PhaseSelectorTests(unittest.TestCase):
    @staticmethod
    def empty_phases(catalog):
        pre, during, post = (catalog.find(tag) for tag in ("pre", "during", "post"))
        for section in (pre, during, post):
            section.clear()
        return pre, during, post

    @staticmethod
    def parameter(catalog, phase, parameter_id):
        return next(p for p in catalog.find(phase).iter("parameter") if p.get("id") == parameter_id)

    @staticmethod
    def keep_parameters(catalog, ids):
        for section in (catalog.find(tag) for tag in ("pre", "during", "post")):
            for group in list(section):
                if group.tag == "class":
                    for parameter in list(group):
                        if parameter.get("id") not in ids:
                            group.remove(parameter)
                    if not len(group):
                        section.remove(group)
                elif group.get("id") not in ids:
                    section.remove(group)

    def test_class_expands_only_catalogued_parameters_of_its_phase(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = ET.parse(ROOT / "config/pipeline.template.xml").getroot()
            catalog = root.find("preprocessing/relevantParameters")
            ids = {"age", "sex", "weight", "height", "bmi", "asa", "recorded_death_days"}
            self.keep_parameters(catalog, ids)
            path = Path(temporary) / "config" / "pipeline.xml"
            path.parent.mkdir()
            ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
            mapping = load_pipeline_config(path)["preprocessing"]["parameter_map"]
            self.assertEqual([item["id"] for item in mapping["parameters"]],
                             ["age", "sex", "weight", "height", "bmi", "asa", "recorded_death_days"])
            self.assertEqual([item["id"] for item in mapping["postoperative_measurements"]],
                             ["recorded_death_days"])

    def test_post_outcome_rule_is_read_from_its_definition(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = ET.parse(ROOT / "config/pipeline.template.xml").getroot()
            catalog = root.find("preprocessing/relevantParameters")
            death = self.parameter(catalog, "post", "recorded_death_days")
            death.find("entry[@name='outcome_criteria']/object/entry[@name='failure']/object/entry[@name='value']/number").text = "2"
            self.keep_parameters(catalog, {"recorded_death_days"})
            path = Path(temporary) / "config" / "pipeline.xml"
            path.parent.mkdir()
            ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
            classifier = OutcomeClassifier(load_pipeline_config(path)["preprocessing"]["parameter_map"])
            self.assertEqual(classifier.classify({"recorded_death_days": 2}).target, -1)
            self.assertIsNone(classifier.classify({"recorded_death_days": 1}).target)

    def test_post_class_nonempty_failure_only_uses_selected_fields_after_start(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = ET.parse(ROOT / "config/pipeline.template.xml").getroot()
            catalog = root.find("preprocessing/relevantParameters")
            self.keep_parameters(catalog, {"postop_urine_72h"})
            catalog.find("post/class[@name='epic.FLUIDS_OUT']").set("failure", "nonempty")
            config = Path(temporary) / "config" / "pipeline.xml"
            config.parent.mkdir()
            ET.ElementTree(root).write(config, encoding="utf-8", xml_declaration=True)
            mapping = load_pipeline_config(config)["preprocessing"]["parameter_map"]
            mapping["missing_value_tokens"] = ["NA"]
            self.assertEqual(mapping["post_class_failures"], ["epic.FLUIDS_OUT"])
            self.assertEqual([p["id"] for p in mapping["parameters"]], ["postop_urine_72h"])
            mapping["parameters"][0]["outcome_criteria"]["success"] = {"operator": "equals", "value": 0}
            source = Path(temporary) / "synthetic.csv"
            cases = (("5", "0", "300", -1), ("5", "NA", "300", None),
                     ("", "", "300", None), ("", "0", "300000", None))
            for before, after, after_time, expected in cases:
                with self.subTest(before=before, after=after, after_time=after_time):
                    with source.open("w", encoding="utf-8", newline="") as stream:
                        writer = csv.writer(stream)
                        writer.writerow(["unixtime", "event", "epic.FLUIDS_OUT.Urine (ml)", "epic.FLUIDS_OUT.unmapped"])
                        writer.writerow(["100", "", before, ""])
                        writer.writerow(["200", "start surgery", "", ""])
                        writer.writerow([after_time, "", after, "unexpected value"])
                    pre_result = PreoperativeProcessor(mapping).process(source)
                    result = PostoperativeProcessor(mapping).process(source, pre_result)
                    self.assertEqual(result.target, expected)
                    self.assertEqual(result.target_status, "failure" if expected == -1 else "unclassified")
    def test_during_operative_extraction_fails_before_creating_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = load_pipeline_config(ROOT / "config/pipeline.template.xml")
            mapping = config["preprocessing"]["parameter_map"]
            mapping["parameters"].append({"id": "during_oxygen", "phase": "During-operative"})
            output = Path(temporary) / "results"
            with self.assertRaisesRegex(ValueError, "During-operative extraction needs an operation-end rule"):
                analyze_inputs([], output, mapping, None, generate_plots=False)
            self.assertFalse(output.exists())

    def test_during_phase_definition_loads_for_header_reports(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = ET.parse(ROOT / "config/pipeline.template.xml").getroot()
            catalog = root.find("preprocessing/relevantParameters")
            during_parameter = deepcopy(self.parameter(catalog, "pre", "age"))
            during_parameter.set("id", "during_age")
            catalog.find("during").append(during_parameter)
            self.keep_parameters(catalog, {"during_age"})
            path = Path(temporary) / "config" / "pipeline.xml"
            path.parent.mkdir()
            ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
            mapping = load_pipeline_config(path)["preprocessing"]["parameter_map"]
            self.assertEqual([p["id"] for p in mapping["parameters"]], ["during_age"])
    def test_xml_runner_rejects_during_extraction_before_header_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            root = ET.parse(ROOT / "config/pipeline.template.xml").getroot()
            root.find("preprocessing/run").text = "true"
            root.find("preprocessing/csvHeaderDescriptions/run").text = "true"
            root.find("preprocessing/inputs/path").text = "data/patients"
            catalog = root.find("preprocessing/relevantParameters")
            during_parameter = deepcopy(self.parameter(catalog, "pre", "age"))
            during_parameter.set("id", "during_age")
            catalog.find("during").append(during_parameter)
            path = project / "config/pipeline.xml"
            path.parent.mkdir()
            ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
            source = project / "data/patients/case.csv"
            source.parent.mkdir(parents=True)
            source.write_text("epic.DEMOGRAPHICS.age\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "During-operative extraction needs an operation-end rule"):
                run_pipeline(path)
            self.assertFalse((project / "results").exists())
            self.assertFalse((project / "logs").exists())
            root.find("preprocessing/run").text = "false"
            ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
            self.assertIn("CSV header descriptions", run_pipeline(path))
            summary = (project / "results/csv_header_summary.md").read_text(encoding="utf-8")
            during = summary.split("## During-operative headers", 1)[1].split("## Post-operative headers", 1)[0]
            self.assertIn("| epic.DEMOGRAPHICS.age | epic.DEMOGRAPHICS | 1 |", during)
            self.assertIn("| epic.DEMOGRAPHICS | 1 |", during)
            self.assertFalse((project / "results/patient_vectors.csv").exists())
            root.find("preprocessing/plotting/run").text = "true"
            root.find("preprocessing/plotting/distributions").text = "true"
            ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
            with self.assertRaisesRegex(ValueError, "During-operative extraction and plotting need an operation-end rule"):
                run_pipeline(path)

    def test_empty_phase_selections_still_allow_header_only_inventory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = ET.parse(ROOT / "config/pipeline.template.xml").getroot()
            catalog = root.find("preprocessing/relevantParameters")
            self.empty_phases(catalog)
            path = Path(temporary) / "config" / "pipeline.xml"
            path.parent.mkdir()
            ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
            mapping = load_pipeline_config(path)["preprocessing"]["parameter_map"]
            self.assertEqual(mapping["parameters"], [])

    def test_shared_settings_cannot_redefine_parameter_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = ET.parse(ROOT / "config/pipeline.template.xml").getroot()
            settings = root.find("preprocessing/relevantParameters/settings")
            ET.SubElement(ET.SubElement(settings, "entry", {"name": "parameters"}), "array")
            path = Path(temporary) / "config/pipeline.xml"
            path.parent.mkdir()
            ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
            with self.assertRaisesRegex(ValueError, "Define parameters and measurements in their phase"):
                load_pipeline_config(path)

    def test_class_group_rejects_unrelated_parameter_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = ET.parse(ROOT / "config/pipeline.template.xml").getroot()
            catalog = root.find("preprocessing/relevantParameters")
            age = self.parameter(catalog, "pre", "age")
            age.find("entry[@name='source_fields']/array/item/string").text = "epic.LAB.age"
            path = Path(temporary) / "config/pipeline.xml"
            path.parent.mkdir()
            ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
            with self.assertRaisesRegex(ValueError, "no source field in epic.DEMOGRAPHICS"):
                load_pipeline_config(path)

    def test_null_post_measurement_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = ET.parse(ROOT / "config/pipeline.template.xml").getroot()
            catalog = root.find("preprocessing/relevantParameters")
            parameter = self.parameter(catalog, "post", "recorded_death_days")
            parameter.attrib.pop("measurementOrder")
            measurement = parameter.find("entry[@name='measurement']")
            measurement.remove(measurement[0])
            ET.SubElement(measurement, "null")
            path = Path(temporary) / "config/pipeline.xml"
            path.parent.mkdir()
            ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
            with self.assertRaisesRegex(ValueError, "Invalid post-operative measurement"):
                load_pipeline_config(path)

    def test_duplicate_parameter_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = ET.parse(ROOT / "config/pipeline.template.xml").getroot()
            catalog = root.find("preprocessing/relevantParameters")
            self.parameter(catalog, "post", "ventilation_duration").set("id", "age")
            path = Path(temporary) / "config/pipeline.xml"
            path.parent.mkdir()
            ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
            with self.assertRaisesRegex(ValueError, "Parameter IDs must be present and unique"):
                load_pipeline_config(path)

    def test_embedded_map_requires_all_three_phase_sections(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = ET.parse(ROOT / "config/pipeline.template.xml").getroot()
            catalog = root.find("preprocessing/relevantParameters")
            for tag in ("pre", "during", "post"):
                catalog.remove(catalog.find(tag))
            path = Path(temporary) / "config" / "pipeline.xml"
            path.parent.mkdir()
            ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
            with self.assertRaisesRegex(ValueError, "pre, during, and post"):
                load_pipeline_config(path)

    @unittest.skipUnless(shutil.which("xmllint"), "xmllint is not installed")
    def test_schema_accepts_phase_sections_and_rejects_nonpost_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = ET.parse(ROOT / "config/pipeline.template.xml").getroot()
            catalog = root.find("preprocessing/relevantParameters")
            pre = catalog.find("pre")
            self.keep_parameters(catalog, {"age", "recorded_death_days"})
            path = Path(temporary) / "synthetic.xml"
            ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
            command = ["xmllint", "--noout", "--schema", str(ROOT / "config/pipeline.xsd"), str(path)]
            valid = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(valid.returncode, 0, valid.stderr)
            pre.find("class").set("failure", "nonempty")
            ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
            invalid = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertNotEqual(invalid.returncode, 0)


if __name__ == "__main__":
    unittest.main()
