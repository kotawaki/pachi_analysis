import json
import unittest
from pathlib import Path

from wave_lab.universe import LEGACY_WAVE_MACHINES, CURRENT_WAVE_MACHINES, machines_for_signal_date


ROOT = Path(__file__).resolve().parents[1]


class UniverseBoundaryTests(unittest.TestCase):
    def test_capture_universe_has_66_machines(self):
        payload = json.loads((ROOT / "pscube_targets.json").read_text(encoding="utf-8"))
        current_date = "20260907"
        machines = {
            str(machine).zfill(4)
            for target in payload["targets"] if target.get("enabled", True)
            if (not target.get("active_from") or current_date >= target["active_from"])
            if (not target.get("active_until") or current_date <= target["active_until"])
            for machine in target["machines"]
        }
        self.assertEqual(len(machines), 66)
        self.assertTrue({"0008", "0009", "0010", "0011", "0012", "0018", "0019"} <= machines)
        self.assertTrue({f"0{n:03d}" for n in range(154, 160)} <= machines)
        self.assertFalse({"1015", "1016", "1017", "1018"} & machines)
        self.assertFalse({str(n) for n in range(1173, 1181)} & machines)

    def test_wave_boundary_is_not_retroactive(self):
        self.assertEqual(len(LEGACY_WAVE_MACHINES), 39)
        self.assertEqual(len(CURRENT_WAVE_MACHINES), 41)
        self.assertNotIn("018", machines_for_signal_date("20260906"))
        self.assertNotIn("019", machines_for_signal_date("20260906"))
        self.assertIn("018", machines_for_signal_date("20260907"))
        self.assertIn("019", machines_for_signal_date("20260907"))
        self.assertEqual(machines_for_signal_date("20260827"), LEGACY_WAVE_MACHINES)

    def test_chart_has_current_ranges_and_manual_fib(self):
        source = (ROOT / "ohlc_chart.py").read_text(encoding="utf-8")
        web = (ROOT / "docs" / "ohlc.html").read_text(encoding="utf-8")
        for value in ("r008_012", "r018_019", "r118_121", "r148_153", "r154_159", "fib-partial-toggle", "fib-partial-clear"):
            self.assertIn(value, source)
            self.assertIn(value, web)
        for value in ("r1173_1180", "r1015_1018", "fib-local-toggle", "gc-badge", "Swing High / Low"):
            self.assertNotIn(value, web)
        self.assertNotIn("const gcEvents", web)
        self.assertNotIn("gcEvents.forEach", web)


if __name__ == "__main__":
    unittest.main()
