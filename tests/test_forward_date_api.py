import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wave_lab.cross_machine_analysis import forward_evaluate, forward_update


class ForwardDateApiTests(unittest.TestCase):
    def make_root(self):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / "docs/wave_lab/data/forward").mkdir(parents=True)
        (root / "csv/daily_ohlc/20260905").mkdir(parents=True)
        return temp, root

    def write_forward(self, root, status="pending"):
        payload = {
            "signal_date": "20260904", "target_date": "20260905",
            "mode": "forward/prospective", "max_input_date": "20260904",
            "future_data_used": False, "evaluation_status": status,
            "machine_signals": [{
                "machine": "039", "UP_UP_UP": True, "score": 1,
                "evaluation_status": status, "actual_bullish": None,
            }],
            "prediction_marker": "preserved",
        }
        path = root / "docs/wave_lab/data/forward/20260904.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def write_ohlc(self, root):
        (root / "csv/daily_ohlc/20260905/20260905_daily_ohlc.csv").write_text(
            "Machine,Open,High,Low,Close\n39,0,10,-2,5\n", encoding="utf-8"
        )

    def test_evaluated_is_noop(self):
        temp, root = self.make_root()
        with temp:
            path = self.write_forward(root, "evaluated")
            before = path.read_bytes()
            result = forward_evaluate.evaluate_forward("20260904", "20260905", root=root)
            self.assertEqual(result["status"], "skipped")
            self.assertEqual(before, path.read_bytes())

    def test_pending_adds_actual_without_changing_prediction(self):
        temp, root = self.make_root()
        with temp:
            path = self.write_forward(root)
            self.write_ohlc(root)
            result = forward_evaluate.evaluate_forward("20260904", "20260905", root=root)
            self.assertEqual(result["status"], "evaluated")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["prediction_marker"], "preserved")
            self.assertTrue(payload["machine_signals"][0]["actual_bullish"])
            self.assertEqual(payload["machine_signals"][0]["actual_close"], "5")

    def test_missing_target_ohlc_does_not_change_file(self):
        temp, root = self.make_root()
        with temp:
            path = self.write_forward(root)
            before = path.read_bytes()
            result = forward_evaluate.evaluate_forward("20260904", "20260905", root=root)
            self.assertEqual(result["status"], "skipped")
            self.assertEqual(before, path.read_bytes())

    def test_lock_creates_once_and_then_skips(self):
        temp, root = self.make_root()
        with temp:
            (root / "csv/analyze/20260905").mkdir(parents=True)
            (root / "csv/analyze/20260905/20260905_analyze.csv").write_text("x\n", encoding="utf-8")
            fake_machine = lambda machine, signal_date=None, target_date=None: {
                "signal_date": signal_date, "target_date": target_date, "machine": machine,
                "group": "g1", "wave_direction_pattern": "UP-UP-UP", "region": "RIGHT",
                "convergence_score": 0.1, "UP_UP_UP": True, "RIGHT": True,
                "LOW_CONVERGENCE_RIGHT": True, "DOWN_DOWN_DOWN": False, "ALL_3": True,
                "score": 3, "evaluation_status": "pending", "actual_bullish": "",
                "actual_open": "", "actual_high": "", "actual_low": "", "actual_close": "",
            }
            argv = ["forward_update.py", "--signal-date", "20260905", "--target-date", "20260906", "--append", "--lock-json"]
            with patch.object(forward_update, "ROOT", root), patch.object(forward_update, "TRACK", root / "tracking"), \
                 patch.object(forward_update, "machine_signal", fake_machine), patch.object(sys, "argv", argv), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(forward_update.main(), 0)
            path = root / "docs/wave_lab/data/forward/20260905.json"
            self.assertTrue(path.exists())
            before = path.read_bytes()
            with patch.object(forward_update, "ROOT", root), patch.object(forward_update, "TRACK", root / "tracking"), \
                 patch.object(sys, "argv", argv), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(forward_update.main(), 0)
            self.assertEqual(before, path.read_bytes())

    def test_holiday_is_rejected(self):
        temp, root = self.make_root()
        with temp:
            result = forward_update.lock_forward("20260827", "20260828", root=root)
            self.assertEqual(result["status"], "error")


if __name__ == "__main__":
    unittest.main()
