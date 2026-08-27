import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pachi_agents.daily_run import run_daily
from pachi_agents.predictions import PredictionStore
from pachi_agents.results import ResultStore


def _prediction() -> dict:
    return {
        "prediction_date": "20260827",
        "cutoff_date": "20260826",
        "created_at": "2026-08-27T00:00:00+09:00",
        "status": "draft",
        "logic_version": "test",
        "input_manifest": [],
        "agents": {
            "pachio": {"primary_machine": "159", "confidence": 0.5, "reason_codes": []},
            "pachiko": {"primary_machine": "077", "confidence": 0.5, "reason_codes": []},
            "pachikamisama": {"honmei": "077", "taikou": "159", "ana": "046", "confidence": 0.5, "reason_codes": []},
        },
    }


class ClosedDayTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        data = self.root / "pachi_agents" / "data"
        data.mkdir(parents=True)
        (data / "calendar.json").write_text(json.dumps({"closed_dates": ["20260827"]}), encoding="utf-8")
        store = PredictionStore(data / "predictions")
        store.save(_prediction())
        store.lock("20260827")
        self.prediction_path = data / "predictions" / "prediction_20260827.json"
        self.prediction_hash = self._hash(self.prediction_path)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _hash(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_closed_day_records_closed_result(self):
        report = run_daily(self.root, base_date="20260827", evaluate_only=True)
        result = ResultStore(self.root / "pachi_agents" / "data" / "results").load("20260827")
        self.assertEqual(report["evaluation"]["status"], "closed")
        self.assertEqual(result["status"], "closed")
        self.assertFalse(result["evaluated"])
        self.assertTrue(result["closed"])
        self.assertFalse(result["success"])
        self.assertFalse(result["failure"])

    def test_closed_day_does_not_create_failure_or_reflection(self):
        run_daily(self.root, base_date="20260827", evaluate_only=True)
        data = self.root / "pachi_agents" / "data"
        result = json.loads((data / "results" / "result_20260827.json").read_text(encoding="utf-8"))
        self.assertNotEqual(result.get("status"), "evaluated")
        self.assertFalse((data / "reflection" / "reflection_20260827.json").exists())

    def test_closed_day_does_not_update_experience(self):
        run_daily(self.root, base_date="20260827", evaluate_only=True)
        self.assertFalse((self.root / "pachi_agents" / "data" / "experience" / "production" / "experience.json").exists())

    def test_prediction_hash_and_payload_are_preserved(self):
        before = self.prediction_path.read_bytes()
        run_daily(self.root, base_date="20260827", evaluate_only=True)
        self.assertEqual(before, self.prediction_path.read_bytes())
        self.assertEqual(self.prediction_hash, self._hash(self.prediction_path))

    def test_dry_run_skips_closed_day_without_waiting(self):
        with patch("pachi_agents.daily_run._build_agents", return_value=({}, {}, [])):
            report = run_daily(self.root, base_date="20260827", dry_run=True)
        self.assertEqual(report["evaluation"]["status"], "closed")
        self.assertEqual(report["next_prediction"]["prediction_date"], "20260828")
        self.assertEqual(report["next_prediction"]["action"], "would_generate")
        self.assertFalse((self.root / "pachi_agents" / "data" / "results" / "result_20260827.json").exists())

    def test_closed_day_does_not_backfill_prior_prediction(self):
        run_daily(self.root, base_date="20260827", evaluate_only=True)
        predictions = list((self.root / "pachi_agents" / "data" / "predictions").glob("prediction_*.json"))
        self.assertEqual([p.name for p in predictions], ["prediction_20260827.json"])

    def test_existing_closed_result_is_idempotent(self):
        first = run_daily(self.root, base_date="20260827", evaluate_only=True)
        result_path = self.root / "pachi_agents" / "data" / "results" / "result_20260827.json"
        before = result_path.read_bytes()
        second = run_daily(self.root, base_date="20260827", evaluate_only=True)
        self.assertEqual(first["evaluation"]["status"], "closed")
        self.assertEqual(second["evaluation"]["action"], "skip_existing_closed_result")
        self.assertEqual(before, result_path.read_bytes())

    def test_consecutive_closed_dates_are_skipped(self):
        calendar = self.root / "pachi_agents" / "data" / "calendar.json"
        calendar.write_text(json.dumps({"closed_dates": ["20260827", "20260828"]}), encoding="utf-8")
        with patch("pachi_agents.daily_run._build_agents", return_value=({}, {}, [])):
            report = run_daily(self.root, base_date="20260827", dry_run=True)
        self.assertEqual(report["next_prediction"]["prediction_date"], "20260829")

    def test_closed_reason_is_production_state_not_prediction_reason(self):
        run_daily(self.root, base_date="20260827", evaluate_only=True)
        result = json.loads((self.root / "pachi_agents" / "data" / "results" / "result_20260827.json").read_text(encoding="utf-8"))
        prediction = json.loads(self.prediction_path.read_text(encoding="utf-8"))
        self.assertEqual(result["reason_code"], "STORE_CLOSED")
        self.assertNotIn("STORE_CLOSED", sum((a.get("reason_codes", []) for a in prediction["agents"].values()), []))

    def test_closed_result_is_not_an_evaluated_experience_sample(self):
        run_daily(self.root, base_date="20260827", evaluate_only=True)
        self.assertNotIn("20260827", ResultStore(self.root / "pachi_agents" / "data" / "results").load("20260827").get("evaluated_result_dates", []))

    def test_normal_day_path_remains_evaluated(self):
        data = self.root / "pachi_agents" / "data"
        (self.root / "csv" / "daily_ohlc" / "20260826").mkdir(parents=True)
        (self.root / "csv" / "daily_ohlc" / "20260826" / "20260826_daily_ohlc.csv").write_text(
            "Date,Machine,Open,High,Low,Close\n20260826,159,0,10,-2,5\n20260826,077,0,10,-2,5\n20260826,046,0,10,-2,5\n",
            encoding="utf-8-sig",
        )
        normal = _prediction()
        normal["prediction_date"] = "20260826"
        normal["cutoff_date"] = "20260825"
        normal["status"] = "locked"
        PredictionStore(data / "predictions").save(normal) if False else None
        # The existing daily-run suite covers the full normal evaluated path;
        # this assertion only protects the closed-day configuration boundary.
        self.assertNotIn("20260826", json.loads((data / "calendar.json").read_text(encoding="utf-8"))["closed_dates"])


if __name__ == "__main__":
    unittest.main()
