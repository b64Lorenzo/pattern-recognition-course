import tempfile
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET

from src.parameter_map import load_parameter_map, write_parameter_map

ROOT = Path(__file__).resolve().parents[1]
PARAMETER_MAP = ROOT / "config" / "pipeline.xml"


class ParameterMapXmlTests(unittest.TestCase):
    def test_embedded_definitions_are_owned_by_phase_without_duplicate_catalog(self):
        for name in ("pipeline.xml", "pipeline.template.xml"):
            with self.subTest(name=name):
                root = ET.parse(ROOT / "config" / name).getroot()
                relevant = root.find("preprocessing/relevantParameters")
                self.assertEqual([node.tag for node in relevant], ["settings", "pre", "during", "post"])
                settings = relevant.find("settings")
                self.assertIsNone(settings.find("entry[@name='parameters']"))
                self.assertIsNone(settings.find("entry[@name='postoperative_measurements']"))
                definitions = [node for phase in ("pre", "during", "post")
                               for child in relevant.find(phase)
                               for node in (list(child) if child.tag == "class" else [child])]
                self.assertEqual(len(definitions), 20)
                self.assertEqual(len({node.get("id") for node in definitions}), 20)
                self.assertTrue(all(node.tag == "parameter" and node.find("entry[@name='phase']") is None
                                    and node.find("entry[@name='id']") is None for node in definitions))
                death = next(node for node in definitions if node.get("id") == "recorded_death_days")
                self.assertIsNotNone(death.find("entry[@name='measurement']"))
                self.assertIsNotNone(death.find("entry[@name='outcome_criteria']"))

    def test_runtime_map_loads_only_pre_and_postoperative_parameters(self):
        mapping = load_parameter_map(PARAMETER_MAP)
        self.assertEqual(mapping["included_phases"], ["Pre-operative", "During-operative", "Post-operative"])
        self.assertEqual(mapping["catalog_phases"], ["Pre-operative", "During-operative", "Post-operative"])
        self.assertEqual({item["phase"] for item in mapping["parameters"]},
                         {"Pre-operative", "Post-operative"})
        self.assertEqual(sum(item["phase"] == "Pre-operative" for item in mapping["parameters"]), 9)
        self.assertEqual(sum(item["phase"] == "Post-operative" for item in mapping["parameters"]), 11)

    def test_measurement_order_and_source_inheritance_remain_stable(self):
        mapping = load_parameter_map(PARAMETER_MAP)
        self.assertEqual([item["id"] for item in mapping["postoperative_measurements"]], [
            "postop_creatinine_72h", "postop_urine_72h", "aki_kdigo_stage",
            "ventilation_duration", "icu_los", "hospital_los", "mortality_30d",
            "recorded_death_days", "postop_ecmo", "prism_ii", "pim",
        ])
        specs = {item["id"]: item for item in mapping["postoperative_measurements"]}
        self.assertEqual(specs["postop_creatinine_72h"]["source_fields"],
                         ["epic.LAB.Kreatinine (umol/L)"])
        self.assertEqual(specs["icu_los"]["source_fields"], [])
        self.assertNotIn("source_fields", specs["aki_kdigo_stage"])

    def test_recorded_death_days_is_user_added_postoperative_metadata(self):
        mapping = load_parameter_map(PARAMETER_MAP)
        parameter = next(item for item in mapping["parameters"] if item["id"] == "recorded_death_days")
        measurement = next(item for item in mapping["postoperative_measurements"] if item["id"] == "recorded_death_days")
        self.assertEqual(parameter["phase"], "Post-operative")
        self.assertEqual(parameter["source_fields"], ["epic.DEMOGRAPHICS.days mortality"])
        self.assertEqual(parameter["workbook_row"], None)
        self.assertIsNone(parameter["outcome_criteria"]["success"])
        self.assertEqual(parameter["outcome_criteria"]["failure"], {"operator": "gte", "value": 0})
        self.assertEqual(measurement["record_scope"], "patient")
        self.assertIsNone(measurement["window_hours"])

    def test_round_trip_preserves_nested_xml_values_and_types(self):
        expected = {
            "text": "A & B < C",
            "integer": 3,
            "decimal": 2.5,
            "enabled": True,
            "unset": None,
            "array": ["x", 4, {"name": "nested"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "map.xml"
            write_parameter_map(path, expected)
            self.assertEqual(load_parameter_map(path, validate_runtime=False), expected)

    def test_duplicate_xml_entry_names_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.xml"
            path.write_text(
                '<parameterMap formatVersion="1"><object>'
                '<entry name="x"><string>1</string></entry>'
                '<entry name="x"><string>2</string></entry>'
                '</object></parameterMap>',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Duplicate XML entry"):
                load_parameter_map(path, validate_runtime=False)

    def test_single_schema_describes_pipeline_and_parameter_map_elements(self):
        self.assertFalse((ROOT / "data" / "relevant_parameters.xsd").exists())
        schema = ET.parse(ROOT / "config" / "pipeline.xsd").getroot()
        xs = "{http://www.w3.org/2001/XMLSchema}"
        top_level = {
            element.get("name")
            for element in schema.findall(f"{xs}element")
        }
        self.assertEqual(top_level, {"pipelineConfiguration", "parameterMap"})
        elements = list(schema.iter(f"{xs}element"))
        undocumented = [
            element.get("name")
            for element in elements
            if element.find(f"{xs}annotation/{xs}documentation") is None
        ]
        self.assertEqual(undocumented, [])
        for kind in ("complexType", "simpleType"):
            undocumented_types = [
                node.get("name")
                for node in schema.findall(f"{xs}{kind}")
                if node.get("name") and node.find(f"{xs}annotation/{xs}documentation") is None
            ]
            self.assertEqual(undocumented_types, [])

    def test_schema_and_template_files_exist(self):
        self.assertTrue((ROOT / "config" / "pipeline.xsd").is_file())
        self.assertTrue((ROOT / "config" / "pipeline.template.xml").is_file())


if __name__ == "__main__":
    unittest.main()
