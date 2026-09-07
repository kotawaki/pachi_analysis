import unittest
from unittest.mock import patch

from wave_lab.cross_machine_analysis import forward_update
from wave_lab.universe import machines_for_signal_date


class ForwardInsufficientHistoryTests(unittest.TestCase):
    def test_short_history_is_not_ready_without_fft(self):
        with patch.object(forward_update, "load_machine_rows", return_value=[1, 2, 3]), \
             patch.object(forward_update, "analyze") as analyze:
            row = forward_update.machine_signal("018", "20260907", "20260908")
        analyze.assert_not_called()
        self.assertEqual(row["machine_status"], "insufficient_history")
        self.assertEqual(row["evaluation_status"], "not_ready")
        self.assertEqual(row["history_rows"], 3)
        self.assertIsNone(row["UP_UP_UP"])

    def test_four_rows_use_existing_fft_path(self):
        daily = [{"wave_direction_pattern": "UP-UP-UP"}]
        convergence = [{"centroid_region": "RIGHT", "convergence_score": 0.4}]
        with patch.object(forward_update, "load_machine_rows", return_value=[1, 2, 3, 4]), \
             patch.object(forward_update, "analyze", return_value=([], daily, None, None)), \
             patch.object(forward_update, "phase_convergence_analysis", return_value=(convergence, None)):
            row = forward_update.machine_signal("039", "20260907", "20260908")
        self.assertEqual(row["machine_status"], "ready")
        self.assertTrue(row["UP_UP_UP"])
        self.assertTrue(row["RIGHT"])

    def test_unexpected_analysis_error_is_not_hidden(self):
        with patch.object(forward_update, "load_machine_rows", return_value=[1, 2, 3, 4]), \
             patch.object(forward_update, "analyze", side_effect=RuntimeError("unexpected")):
            with self.assertRaisesRegex(RuntimeError, "unexpected"):
                forward_update.machine_signal("039", "20260907", "20260908")

    def test_current_wave_universe_remains_41(self):
        self.assertEqual(len(machines_for_signal_date("20260907")), 41)

    def test_current_universe_can_build_with_two_not_ready_machines(self):
        universe = machines_for_signal_date("20260907")
        def rows_for(machine, _date):
            return [1, 2, 3] if machine in {"018", "019"} else [1, 2, 3, 4]
        daily = [{"wave_direction_pattern": "DOWN-DOWN-DOWN"}]
        convergence = [{"centroid_region": "LEFT", "convergence_score": 0.8}]
        with patch.object(forward_update, "load_machine_rows", side_effect=rows_for), \
             patch.object(forward_update, "analyze", return_value=([], daily, None, None)), \
             patch.object(forward_update, "phase_convergence_analysis", return_value=(convergence, None)):
            rows = [forward_update.machine_signal(machine, "20260907", "20260908") for machine in universe]
        self.assertEqual(len(rows), 41)
        self.assertEqual([row["machine"] for row in rows if row["machine_status"] == "insufficient_history"], ["018", "019"])
        self.assertEqual(sum(row["machine_status"] == "ready" for row in rows), 39)


if __name__ == "__main__":
    unittest.main()
