import json
import tempfile
import unittest
from pathlib import Path

import tools.run_daily as daily_runner
from tools.run_daily import (
    ROOT,
    add_days,
    daily_command,
    planned_stages,
    validate_wave_forward,
    validate_date,
    validate_weak_ma,
)


class DailyRunnerTest(unittest.TestCase):
    def test_repo_root_and_date_command(self):
        command = daily_command("20260906", ROOT / "data/local_capture/20260906/morning")
        self.assertEqual(command[0], __import__("sys").executable)
        self.assertEqual(command[1], str(ROOT / "tools/run_pscube_morning_pipeline.py"))
        self.assertIn("20260906", command)
        self.assertIn("--pachi-agents-external", command)

    def test_stage_order(self):
        names = [stage["name"] for stage in planned_stages("20260906")]
        self.assertEqual(names[:4], ["01_analyze", "02_canonical_ohlc", "03_ohlc_web", "04_daily_ingest"])
        self.assertLess(names.index("10_wave_forward"), names.index("18_wave_weak_ma"))
        self.assertEqual(names[-1], "final_validation")

    def test_holiday_is_rejected(self):
        with self.assertRaises(Exception):
            validate_date("20260827")

    def test_business_date_helper(self):
        self.assertEqual(add_days("20260906", 1), "20260907")

    def test_current_09_validation_has_no_stale_evaluated_pending(self):
        summary_path = ROOT / "wave_lab/cross_machine_analysis/tracking/wave_weak_ma_summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        processed_date = summary.get("processed_signal_date")
        self.assertIsInstance(processed_date, str)
        ok, message = validate_weak_ma(processed_date)
        self.assertTrue(ok, message)

    def test_wave_forward_validation_previous_current_and_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            forward_dir = root / "docs/wave_lab/data/forward"
            forward_dir.mkdir(parents=True)
            (root / "csv/daily_ohlc/20260907").mkdir(parents=True)
            (root / "docs/wave_lab").mkdir(parents=True, exist_ok=True)
            (root / "docs/wave_lab/index.html").write_text("fetch('./data/forward/${date}.json')", encoding="utf-8")
            (root / "csv/daily_ohlc/20260907/20260907_daily_ohlc.csv").write_text(
                "Date,Machine,Open,High,Low,Close\n2026/09/07,001,0,10,-2,5\n", encoding="utf-8"
            )
            history = {"rows": [{"signal_date": "20260906", "target_date": "20260907", "evaluation_status": "evaluated"}]}
            (forward_dir / "history.json").write_text(json.dumps(history), encoding="utf-8")
            previous = {
                "signal_date": "20260906", "target_date": "20260907", "evaluation_status": "evaluated",
                "future_data_used": False, "actual_source": "csv/daily_ohlc/20260907/20260907_daily_ohlc.csv",
                "machine_signals": [{"machine": "001", "evaluation_status": "evaluated", "actual_open": 0,
                                     "actual_high": 10, "actual_low": -2, "actual_close": 5, "actual_bullish": True}],
            }
            current = {"signal_date": "20260907", "target_date": "20260908", "max_input_date": "20260907",
                       "future_data_used": False, "machine_signals": [{"machine": "001", "machine_status": "ready",
                       "evaluation_status": "pending"}]}
            (forward_dir / "20260906.json").write_text(json.dumps(previous), encoding="utf-8")
            (forward_dir / "20260907.json").write_text(json.dumps(current), encoding="utf-8")
            original_root = daily_runner.ROOT
            original_universe = daily_runner.machines_for_signal_date
            daily_runner.ROOT = root
            daily_runner.machines_for_signal_date = lambda _date: ("001",)
            try:
                ok, message = validate_wave_forward("20260907")
                self.assertTrue(ok, message)
                cases = [
                    ("pending", lambda: previous.update(evaluation_status="pending")),
                    ("missing actual", lambda: previous["machine_signals"][0].pop("actual_close")),
                    ("OHLC mismatch", lambda: previous["machine_signals"][0].update(actual_close=6)),
                    ("bullish mismatch", lambda: previous["machine_signals"][0].update(actual_close=5, actual_bullish=False)),
                    ("history missing", lambda: history.update(rows=[])),
                ]
                for name, mutate in cases:
                    with self.subTest(name=name):
                        previous["evaluation_status"] = "evaluated"
                        previous["machine_signals"][0].update(actual_open=0, actual_high=10, actual_low=-2, actual_close=5, actual_bullish=True)
                        history["rows"] = [{"signal_date": "20260906", "target_date": "20260907", "evaluation_status": "evaluated"}]
                        mutate()
                        (forward_dir / "history.json").write_text(json.dumps(history), encoding="utf-8")
                        (forward_dir / "20260906.json").write_text(json.dumps(previous), encoding="utf-8")
                        result, _ = validate_wave_forward("20260907")
                        self.assertFalse(result)
                previous["machine_signals"][0].update(actual_open=0, actual_high=10, actual_low=-2, actual_close=5, actual_bullish=True)
                history["rows"] = [{"signal_date": "20260906", "target_date": "20260907", "evaluation_status": "evaluated"}]
                current["machine_signals"][0] = {"machine": "001", "machine_status": "insufficient_history", "evaluation_status": "not_ready"}
                (forward_dir / "history.json").write_text(json.dumps(history), encoding="utf-8")
                (forward_dir / "20260906.json").write_text(json.dumps(previous), encoding="utf-8")
                (forward_dir / "20260907.json").write_text(json.dumps(current), encoding="utf-8")
                ok, message = validate_wave_forward("20260907")
                self.assertTrue(ok, message)
            finally:
                daily_runner.ROOT = original_root
                daily_runner.machines_for_signal_date = original_universe


if __name__ == "__main__":
    unittest.main()
