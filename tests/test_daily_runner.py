import unittest
from pathlib import Path

from tools.run_daily import (
    ROOT,
    add_days,
    daily_command,
    planned_stages,
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
        ok, message = validate_weak_ma("20260906")
        self.assertTrue(ok, message)


if __name__ == "__main__":
    unittest.main()
