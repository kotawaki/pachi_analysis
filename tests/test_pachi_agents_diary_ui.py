from pathlib import Path
import unittest


class DiaryReflectionUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (
            Path(__file__).resolve().parents[1]
            / "docs"
            / "pachi_agents"
            / "app.js"
        ).read_text(encoding="utf-8")

    def test_diary_contains_prediction_result_reflection_flow(self):
        self.assertIn("function diaryWithReflection", self.source)
        self.assertIn("<h4>Prediction</h4>", self.source)
        self.assertIn("<h4>Result</h4>", self.source)
        self.assertIn("🤖 AI Reflection", self.source)
        self.assertIn("Reflectionはまだ生成されていません。", self.source)
        self.assertIn("row.reflection", self.source)
        self.assertIn("DATA.latest_reflection", self.source)

    def test_diary_renders_result_details_and_agent_cards(self):
        self.assertIn("resultDetail", self.source)
        self.assertIn("reflection-card", self.source)
        self.assertIn("ref.pachio?.reason", self.source)
        self.assertIn("ref.pachiko?.evaluation", self.source)
        self.assertIn("ref.pachikamisama?.learning", self.source)

    def test_prediction_machines_link_to_existing_ohlc_viewer(self):
        self.assertIn("function machineLink", self.source)
        self.assertIn("../ohlc.html?machine=", self.source)
        self.assertIn("machineLink(c.machine", self.source)
        self.assertIn("machineLink(a?.taikou", self.source)
        self.assertIn("machineLink(a?.ana", self.source)


if __name__ == "__main__":
    unittest.main()
