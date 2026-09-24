import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from run_pscube_morning_pipeline import completion_validations


class DeferredWaveWeakMaCompletionTests(unittest.TestCase):
    def test_normal_morning_accepts_valid_weak_ma(self):
        required, deferred = completion_validations(
            {"01_ohlc": True, "09_wave_weak_ma": True}, defer_wave_weak_ma=False
        )
        self.assertTrue(all(required.values()))
        self.assertEqual(deferred, [])

    def test_normal_morning_rejects_invalid_weak_ma(self):
        required, deferred = completion_validations(
            {"01_ohlc": True, "09_wave_weak_ma": False}, defer_wave_weak_ma=False
        )
        self.assertFalse(all(required.values()))
        self.assertEqual(deferred, [])

    def test_defer_records_not_success_and_does_not_fail_morning(self):
        original = {"01_ohlc": True, "09_wave_weak_ma": False}
        required, deferred = completion_validations(original, defer_wave_weak_ma=True)
        self.assertTrue(all(required.values()))
        self.assertEqual(deferred, ["09_wave_weak_ma"])
        self.assertFalse(original["09_wave_weak_ma"])

    def test_defer_does_not_hide_other_failure(self):
        required, deferred = completion_validations(
            {"01_ohlc": False, "09_wave_weak_ma": False}, defer_wave_weak_ma=True
        )
        self.assertFalse(all(required.values()))
        self.assertEqual(deferred, ["09_wave_weak_ma"])


if __name__ == "__main__":
    unittest.main()
