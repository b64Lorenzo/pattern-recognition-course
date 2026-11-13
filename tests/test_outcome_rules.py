import unittest
from pathlib import Path
from src.outcome_rules import OutcomeClassifier
from src.parameter_map import load_parameter_map

class OutcomeClassifierTests(unittest.TestCase):
    def config(self):
        return {"parameters": [{"id":"measurement","phase":"Post-operative","outcome_criteria":{"success":{"operator":"lte","value":5},"failure":{"operator":"gt","value":5}}}]}
    def test_threshold_criteria_produce_binary_labels(self):
        c=OutcomeClassifier(self.config())
        self.assertEqual(c.classify({"measurement":5}).target,1)
        self.assertEqual(c.classify({"measurement":5.1}).target,-1)
    def test_missing_evidence_is_unclassified(self):
        r=OutcomeClassifier(self.config()).classify({})
        self.assertIsNone(r.target); self.assertEqual(r.status,"unclassified")
    def test_failure_precedes_success(self):
        d=self.config(); d["parameters"].append({"id":"second","phase":"Post-operative","outcome_criteria":{"success":{"operator":"equals","value":1},"failure":{"operator":"equals","value":0}}})
        r=OutcomeClassifier(d).classify({"measurement":1,"second":0})
        self.assertEqual((r.target,r.status),(-1,"failure"))
    def test_overlapping_criteria_are_rejected(self):
        d=self.config(); d["parameters"][0]["outcome_criteria"]={"success":{"operator":"equals","value":1},"failure":{"operator":"in","values":[1,2]}}
        with self.assertRaisesRegex(ValueError,"overlap"): OutcomeClassifier(d)
    def test_catalog_has_per_parameter_criteria_and_no_rewards(self):
        p=Path(__file__).resolve().parents[1]/"config"/"pipeline.xml"
        d=load_parameter_map(p); ps=[x for x in d["parameters"] if x["phase"]=="Post-operative"]
        self.assertEqual(len(d["parameters"]),20); self.assertEqual({x["phase"] for x in d["parameters"]},{"Pre-operative","Post-operative"})
        self.assertEqual(len(ps),11); self.assertTrue(all({"success","failure"}<=x["outcome_criteria"].keys() for x in ps))
        self.assertFalse(any("reward" in x for x in d["parameters"])); self.assertNotIn("outcome_scoring",d); self.assertNotIn("severity_bands",d)
        self.assertEqual(sum(x["outcome_criteria"]["success"] is not None for x in ps),2)
        self.assertTrue(all(x["outcome_criteria"]["status"]=="pending_clinical_definition" for x in ps if x["outcome_criteria"]["success"] is None and x["outcome_criteria"]["failure"] is None))
    def test_recorded_days_indicates_death_without_thirty_day_limit(self):
        mapping = load_parameter_map(Path(__file__).resolve().parents[1] / "config" / "pipeline.xml")
        classifier = OutcomeClassifier(mapping)
        for days in (0, 4, 60):
            with self.subTest(days=days):
                result = classifier.classify({"recorded_death_days": days})
                self.assertEqual((result.target, result.status, result.label_basis),
                                 (-1, "failure", "recorded_death_days"))

    def test_missing_or_invalid_days_are_not_success_or_failure_evidence(self):
        mapping = load_parameter_map(Path(__file__).resolve().parents[1] / "config" / "pipeline.xml")
        classifier = OutcomeClassifier(mapping)
        for days in (None, "", "NA", "unknown", -1):
            with self.subTest(days=days):
                result = classifier.classify({"recorded_death_days": days})
                self.assertEqual((result.target, result.status), (None, "unclassified"))

if __name__=="__main__": unittest.main()
