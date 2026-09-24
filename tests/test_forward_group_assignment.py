import csv
import tempfile
import unittest
from pathlib import Path

from wave_lab.cross_machine_analysis import forward_update


class ForwardGroupAssignmentTests(unittest.TestCase):
    def write_master(self, root: Path, rows: list[tuple[str, str]]) -> None:
        with (root / "machine_master.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["machine", "group", "island"])
            writer.writerows((machine, group, "s1") for machine, group in rows)

    def master_rows(self) -> list[tuple[str, str]]:
        rows = [(str(machine), str((machine - 1) % 9 + 1)) for machine in range(39, 78)]
        return [("18", "9"), ("19", "1"), *rows]

    def test_current_universe_uses_master_for_all_41_machines(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_master(root, self.master_rows())
            group_map, groups = forward_update.group_machines_for_signal_date(
                "20260907", root=root
            )
            self.assertEqual(len(group_map), 41)
            self.assertEqual(group_map["018"], "g9")
            self.assertEqual(group_map["019"], "g1")
            self.assertEqual(sum(map(len, groups.values())), 41)

    def test_legacy_universe_remains_39_without_018_or_019(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_master(root, self.master_rows())
            group_map, groups = forward_update.group_machines_for_signal_date(
                "20260906", root=root
            )
            self.assertEqual(len(group_map), 39)
            self.assertNotIn("018", group_map)
            self.assertNotIn("019", group_map)
            self.assertEqual(sum(map(len, groups.values())), 39)

    def test_duplicate_assignment_fails_fast(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [(str(machine), str((machine - 1) % 9 + 1)) for machine in range(39, 78)]
            rows.append(("39", "3"))
            self.write_master(root, rows)
            with self.assertRaisesRegex(ValueError, "duplicate.*039"):
                forward_update.load_machine_group_map("20260906", root=root)

    def test_missing_assignment_fails_fast(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [(str(machine), str((machine - 1) % 9 + 1)) for machine in range(40, 78)]
            self.write_master(root, rows)
            with self.assertRaisesRegex(ValueError, "missing.*039"):
                forward_update.load_machine_group_map("20260906", root=root)


if __name__ == "__main__":
    unittest.main()
