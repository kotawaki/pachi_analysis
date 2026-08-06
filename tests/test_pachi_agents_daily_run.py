import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pachi_agents.daily_run import ReportAlignmentError, run_daily
from pachi_agents.predictions import PredictionStore
from pachi_agents.results import ResultStore


def _prediction(day: str) -> dict:
    return {
        "prediction_date": day,
        "cutoff_date": "20260715",
        "created_at": "2026-07-16T00:00:00+09:00",
        "status": "draft",
        "logic_version": "test",
        "input_manifest": [],
        "agents": {
            "pachio": {"primary_machine": "039", "confidence": 0.5, "reason_codes": []},
            "pachiko": {"primary_machine": "047", "confidence": 0.5, "reason_codes": []},
            "pachikamisama": {
                "honmei": "039", "taikou": "047", "ana": None,
                "confidence": 0.5, "agent_weights": {"pachio": 0.5, "pachiko": 0.5},
                "reason_codes": [],
            },
        },
    }


def _agents():
    pachio = {"logic_version": "test", "primary_machine": "039", "candidates": [], "confidence": 0.5, "signals": {}, "reason_codes": []}
    pachiko = {"logic_version": "test", "primary_machine": "047", "candidates": [], "confidence": 0.5, "signals": {}, "reason_codes": []}
    god = {"honmei": "039", "taikou": "047", "ana": None, "confidence": 0.5, "agent_weights": {"pachio": 0.5, "pachiko": 0.5}, "signals": {}, "reason_codes": []}
    return pachio, pachiko, [{"kind": "test", "path": "test.csv", "date": "20260715"}], god


class DailyRunTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        ohlc = self.root / "csv" / "daily_ohlc" / "20260716"
        ohlc.mkdir(parents=True)
        (ohlc / "20260716_daily_ohlc.csv").write_text(
            "Date,Machine,Open,High,Low,Close\n20260716,039,0,10,-2,5\n20260716,047,0,10,-2,-1\n",
            encoding="utf-8-sig",
        )
        prediction_store = PredictionStore(self.root / "pachi_agents" / "data" / "predictions")
        prediction_store.save(_prediction("20260716"))
        prediction_store.lock("20260716")

    def tearDown(self):
        self.temp.cleanup()

    def test_dry_run_evaluates_plan_without_any_primary_writes(self):
        data = self.root / "pachi_agents" / "data"
        before = {str(path): path.read_bytes() for path in data.rglob("*") if path.is_file()}
        with patch("pachi_agents.daily_run._build_agents", return_value=_agents()[:3]):
            report = run_daily(self.root, base_date="20260716", dry_run=True)
        self.assertEqual(report["evaluation"]["status"], "evaluated")
        self.assertEqual(report["next_prediction"]["prediction_date"], "20260717")
        self.assertEqual(report["next_prediction"]["cutoff_date"], "20260716")
        after = {str(path): path.read_bytes() for path in data.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertFalse((data / "results" / "result_20260716.json").exists())

    def test_missing_data_or_non_evaluated_result_blocks_next_prediction(self):
        data = self.root / "pachi_agents" / "data"
        ohlc_path = self.root / "csv" / "daily_ohlc" / "20260716" / "20260716_daily_ohlc.csv"
        prediction_path = data / "predictions" / "prediction_20260716.json"
        prediction_before = prediction_path.read_bytes()

        ohlc_path.unlink()
        with patch("pachi_agents.daily_run._build_agents", return_value=_agents()[:3]):
            pending = run_daily(self.root, base_date="20260716", dry_run=True)
        self.assertEqual(pending["evaluation"]["status"], "pending")
        self.assertEqual(pending["next_prediction"]["action"], "wait_for_evaluated_result")
        self.assertFalse((data / "predictions" / "prediction_20260717.json").exists())

        ohlc_path.write_text(
            "Date,Machine,Open,High,Low,Close\n20260716,039,0,10,-2,5\n",
            encoding="utf-8-sig",
        )
        with patch("pachi_agents.daily_run._build_agents", return_value=_agents()[:3]):
            incomplete = run_daily(self.root, base_date="20260716", dry_run=True)
        self.assertEqual(incomplete["evaluation"]["status"], "incomplete")
        self.assertEqual(incomplete["next_prediction"]["action"], "wait_for_evaluated_result")
        self.assertFalse((data / "predictions" / "prediction_20260717.json").exists())
        self.assertEqual(prediction_before, prediction_path.read_bytes())

    def test_evaluation_experience_then_next_prediction_and_idempotent_rerun(self):
        pachio, pachiko, manifest, god = _agents()
        with patch("pachi_agents.daily_run._build_agents", return_value=(pachio, pachiko, manifest)), \
             patch("pachi_agents.daily_run.build_pachikamisama_agent", return_value=god):
            first = run_daily(self.root, base_date="20260716")
            result_path = self.root / "pachi_agents" / "data" / "results" / "result_20260716.json"
            prediction_path = self.root / "pachi_agents" / "data" / "predictions" / "prediction_20260717.json"
            self.assertEqual(first["evaluation"]["status"], "evaluated")
            self.assertEqual(first["evaluation"]["prediction"]["prediction_date"], "20260716")
            self.assertEqual(first["evaluation"]["prediction"]["pachikamisama_honmei"], "039")
            self.assertEqual(first["evaluation"]["result_prediction_date"], "20260716")
            self.assertTrue(result_path.exists())
            self.assertTrue(prediction_path.exists())
            self.assertEqual(first["experience"]["mode"], "production")
            experience = json.loads((self.root / "pachi_agents" / "data" / "experience" / "production" / "experience.json").read_text(encoding="utf-8"))
            self.assertEqual(experience["evaluated_result_dates"], ["20260716"])
            result_before = result_path.read_bytes()
            prediction_before = prediction_path.read_bytes()
            second = run_daily(self.root, base_date="20260716")
        self.assertEqual(second["evaluation"]["action"], "skip_existing_result")
        self.assertEqual(second["next_prediction"]["action"], "skip_existing_locked")
        self.assertEqual(result_before, result_path.read_bytes())
        self.assertEqual(prediction_before, prediction_path.read_bytes())
        self.assertFalse((self.root / "pachi_agents" / "data" / "backtest").exists())

    def test_report_rejects_prediction_file_with_mismatched_payload_date(self):
        path = self.root / "pachi_agents" / "data" / "predictions" / "prediction_20260716.json"
        payload = _prediction("20260717")
        payload["status"] = "locked"
        path.write_text(json.dumps(payload), encoding="utf-8")
        ResultStore(self.root / "pachi_agents" / "data" / "results").save({
            "prediction_date": "20260716",
            "evaluated_at": "2026-07-17T00:00:00+09:00",
            "result_version": "test",
            "source_manifest": [],
            "agents": {},
            "status": "evaluated",
        })
        with self.assertRaises(ReportAlignmentError):
            run_daily(self.root, base_date="20260716", dry_run=True)


if __name__ == "__main__":
    unittest.main()
