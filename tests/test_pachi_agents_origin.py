import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pachi_agents.candidate_origin import candidate_origin, enrich_prediction
from pachi_agents.experience import ExperienceBuilder
from pachi_agents.pachikamisama import build_pachikamisama_agent


def _agent(primary, machines):
    return {
        "primary_machine": primary,
        "candidates": [
            {"machine": machine, "score": 10 - index, "confidence": 0.8, "signals": {}, "reason_codes": []}
            for index, machine in enumerate(machines)
        ],
        "confidence": 0.8,
    }


class CandidateOriginTest(unittest.TestCase):
    def setUp(self):
        self.pachio = _agent("062", ["062", "057"])
        self.pachiko = _agent("075", ["075", "057"])

    def test_origin_types_rank_and_primary(self):
        pachio_only = candidate_origin("062", self.pachio, self.pachiko)
        pachiko_only = candidate_origin("075", self.pachio, self.pachiko)
        both = candidate_origin("057", self.pachio, self.pachiko)
        self.assertEqual(pachio_only["origin_type"], "PACHIO_ONLY")
        self.assertEqual(pachio_only["pachio"], {"selected": True, "rank": 1, "is_primary": True})
        self.assertEqual(pachiko_only["origin_type"], "PACHIKO_ONLY")
        self.assertEqual(pachiko_only["pachiko"], {"selected": True, "rank": 1, "is_primary": True})
        self.assertEqual(both["origin_type"], "BOTH")
        self.assertEqual(both["pachio"]["rank"], 2)
        self.assertEqual(both["pachiko"]["rank"], 2)
        self.assertFalse(both["pachio"]["is_primary"])

    def test_god_role_origins_are_separate_from_adoption_result(self):
        god = build_pachikamisama_agent(self.pachio, self.pachiko)
        self.assertEqual(god["role_origins"]["honmei"]["origin_type"], "BOTH")
        self.assertEqual(god["role_origins"]["taikou"]["origin_type"], "PACHIKO_ONLY")
        self.assertEqual(god["role_origins"]["ana"]["origin_type"], "PACHIO_ONLY")
        self.assertTrue(any(c.get("candidate_origin") for c in god["candidates"]))

    def test_legacy_prediction_is_enriched_without_writeback(self):
        prediction = {
            "prediction_date": "20260718",
            "agents": {
                "pachio": self.pachio,
                "pachiko": self.pachiko,
                "pachikamisama": {"honmei": "057", "taikou": "075", "ana": "062", "candidates": []},
            },
        }
        original = json.dumps(prediction, sort_keys=True)
        enriched = enrich_prediction(prediction)
        self.assertEqual(enriched["agents"]["pachikamisama"]["role_origins"]["ana"]["origin_type"], "PACHIO_ONLY")
        self.assertEqual(json.dumps(prediction, sort_keys=True), original)

    def test_origin_experience_keeps_discovery_and_adoption_separate(self):
        god = build_pachikamisama_agent(self.pachio, self.pachiko)
        prediction = {
            "prediction_date": "20260718",
            "status": "locked",
            "agents": {"pachio": self.pachio, "pachiko": self.pachiko, "pachikamisama": god},
        }
        result = {
            "prediction_date": "20260718",
            "status": "evaluated",
            "agents": {
                "pachio": {"result": {"outcome": "success"}},
                "pachiko": {"result": {"outcome": "failure"}},
                "pachikamisama": {
                    "honmei": {"outcome": "failure"},
                    "taikou": {"outcome": "failure"},
                    "ana": {"outcome": "success"},
                },
            },
        }
        builder = ExperienceBuilder("production")
        builder.register_prediction(prediction)
        builder.add_result(result)
        memory = builder.finalize()
        origins = memory["pachikamisama"]["origin_stats"]
        self.assertEqual(origins["roles"]["ana"]["PACHIO_ONLY"]["success"], 1)
        self.assertEqual(origins["roles"]["taikou"]["PACHIKO_ONLY"]["failure"], 1)
        self.assertEqual(origins["roles"]["honmei"]["BOTH"]["failure"], 1)
        self.assertEqual(memory["agents"]["pachio"]["summary"]["success"], 1)
        self.assertEqual(memory["agents"]["pachikamisama"]["summary"]["success"], 0)


if __name__ == "__main__":
    unittest.main()
