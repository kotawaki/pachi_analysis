import unittest

from wave_lab.cross_machine_analysis.export_wave_weak_ma import apply_evaluation, qualifies, summarize


class WaveWeakMATest(unittest.TestCase):
    def base(self, **updates):
        row = {"UP_UP_UP": False, "RIGHT": True, "LOW_CONVERGENCE_RIGHT": True,
               "ma5_direction": "DOWN", "ma20_direction": "DOWN", "ma75_direction": "DOWN",
               "close_vs_ma5": "BELOW", "close_vs_ma20": "BELOW", "close_vs_ma75": "BELOW",
               "evaluation_status": "pending", "actual_bullish": None}
        row.update(updates)
        return row

    def test_match(self):
        self.assertTrue(qualifies(self.base()))

    def test_non_match(self):
        self.assertFalse(qualifies(self.base(ma75_direction="UP")))

    def test_overlap_is_one_record(self):
        result = summarize([self.base(evaluation_status="evaluated", actual_bullish=True)])
        self.assertEqual(result["total_samples"], 1)
        self.assertEqual(result["signal_stats"]["RIGHT"]["samples"], 1)
        self.assertEqual(result["signal_stats"]["LOW_CONVERGENCE_RIGHT"]["samples"], 1)

    def test_evaluation_does_not_change_features(self):
        before = self.base(signal_close=100, ma5=110)
        after = apply_evaluation(before, {"evaluation_status": "evaluated", "actual_open": 0,
                                          "actual_high": 10, "actual_low": -2, "actual_close": 5,
                                          "actual_bullish": True})
        self.assertEqual(after["signal_close"], 100)
        self.assertEqual(after["ma5"], 110)
        self.assertTrue(after["actual_bullish"])

    def test_pending_excluded_from_rate(self):
        result = summarize([self.base(evaluation_status="evaluated", actual_bullish=True), self.base()])
        self.assertEqual(result["evaluated_samples"], 1)
        self.assertEqual(result["pending_samples"], 1)
        self.assertEqual(result["bullish_rate"], 1.0)

    def test_zero_sample_day_is_valid(self):
        result = summarize([])
        self.assertEqual(result["total_samples"], 0)
        self.assertIsNone(result["bullish_rate"])


if __name__ == "__main__":
    unittest.main()
