import json
import tempfile
import unittest
from pathlib import Path

from pachi_agents.export_web import export_web
from pachi_agents.reflection import ReflectionStore, build_reflection, generate_reflection_for_date


class ReflectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def prediction(self):
        return {
            "prediction_date": "20260725",
            "cutoff_date": "20260724",
            "status": "locked",
            "agents": {
                "pachio": {
                    "primary_machine": "118",
                    "confidence": 0.95,
                    "reason_codes": ["MA5_UP", "BULL_STRUCTURE"],
                    "signals": {"ma5_slope": 100, "ma20_slope": 20, "bullish_structure": True},
                },
                "pachiko": {
                    "primary_machine": "056",
                    "confidence": 0.9,
                    "reason_codes": ["PROPAGATION_REPEATED", "GROUP_STRENGTH_HIGH"],
                    "signals": {},
                },
                "pachikamisama": {
                    "honmei": "118", "taikou": "056", "ana": "062", "confidence": 0.8,
                    "agent_weights": {"pachio": 0.55, "pachiko": 0.45},
                    "role_origins": {"honmei": {"origin_type": "PACHIO_ONLY"}, "taikou": {"origin_type": "PACHIKO_ONLY"}, "ana": {"origin_type": "BOTH"}},
                },
            },
        }

    def experience(self):
        return {
            "agents": {
                "pachio": {"reason_codes": {"MA5_UP": {"evaluated_count": 14, "success_rate": 0.61}}, "confidence_bands": {"gte_0_85": {"evaluated_count": 38, "success_rate": 0.68}}},
                "pachiko": {"reason_codes": {"PROPAGATION_REPEATED": {"evaluated_count": 8, "success_rate": 0.75}}, "confidence_bands": {"gte_0_85": {"evaluated_count": 30, "success_rate": 0.7}}},
            },
            "pachikamisama": {"weight_patterns": {"balanced": {"evaluated_count": 8, "success_rate": 0.625}}},
        }

    def result(self):
        return {
            "prediction_date": "20260725", "status": "evaluated", "evaluated_at": "2026-07-25T00:00:00+09:00", "result_version": "test", "source_manifest": [],
            "agents": {
                "pachio": {"result": {"machine": "118", "outcome": "failure", "direction": "non_positive", "close": -100, "max_up": 50}},
                "pachiko": {"result": {"machine": "056", "outcome": "success", "direction": "positive", "close": 500}},
                "pachikamisama": {
                    "honmei": {"machine": "118", "outcome": "failure", "direction": "non_positive", "close": -100},
                    "taikou": {"machine": "056", "outcome": "success", "direction": "positive", "close": 500},
                    "ana": {"machine": "062", "outcome": "failure", "direction": "non_positive", "close": -20},
                },
            },
        }

    def test_reflection_uses_prediction_result_and_experience(self):
        reflection = build_reflection(self.prediction(), self.result(), self.experience())
        self.assertEqual(reflection["prediction_date"], "20260725")
        self.assertIn("MA5の傾き", reflection["pachio"]["reason"])
        self.assertIn("過去14件中61.0%成功", reflection["pachio"]["learning"])
        self.assertIn("一致しました", reflection["pachiko"]["evaluation"] or "")
        self.assertIn("本命", reflection["pachikamisama"]["reason"])

    def test_reflection_store_is_atomic_and_separate(self):
        store = ReflectionStore(self.root / "reflection")
        reflection = build_reflection(self.prediction(), self.result(), self.experience())
        path = store.save(reflection)
        self.assertTrue(path.exists())
        self.assertEqual(path.parent.name, "reflection")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["reflection_version"], "pachi_agents_reflection_v1")

    def test_generate_and_export_reflection(self):
        data = self.root / "data"
        (data / "predictions").mkdir(parents=True)
        (data / "results").mkdir(parents=True)
        (data / "experience" / "production").mkdir(parents=True)
        prediction = dict(self.prediction())
        prediction.update({"created_at": "2026-07-25T00:00:00+09:00", "logic_version": "test", "input_manifest": []})
        (data / "predictions" / "prediction_20260725.json").write_text(json.dumps(prediction), encoding="utf-8")
        (data / "results" / "result_20260725.json").write_text(json.dumps(self.result()), encoding="utf-8")
        (data / "experience" / "production" / "experience.json").write_text(json.dumps(self.experience()), encoding="utf-8")
        path = generate_reflection_for_date(data, "20260725")
        self.assertTrue(path.exists())
        output = self.root / "docs"
        exported = export_web(data, output)
        self.assertIsNotNone(exported["reflection"])
        self.assertTrue((output / "latest_reflection.json").exists())

    def test_reflection_does_not_change_inputs(self):
        prediction = self.prediction()
        result = self.result()
        before = json.dumps((prediction, result), sort_keys=True)
        build_reflection(prediction, result, self.experience())
        self.assertEqual(before, json.dumps((prediction, result), sort_keys=True))


if __name__ == "__main__":
    unittest.main()
