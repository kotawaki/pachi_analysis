from __future__ import annotations

import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from pachi_agents.inputs import (
    AsOfViolation,
    available_analyze_dates,
    available_snapshot_dates,
    load_analyze_rows,
    load_daily_ohlc_rows,
    load_pair_history_as_of,
    load_snapshot,
)
from pachi_agents.predictions import (
    PredictionAlreadyExists,
    PredictionCorrupt,
    PredictionNotFound,
    PredictionStore,
    PredictionSchemaError,
)
from pachi_agents.pachio import build_pachio_agent, generate_pachio_prediction
from pachi_agents.pachiko import build_pachiko_agent
from pachi_agents.pachikamisama import build_pachikamisama_agent, generate_pachikamisama_prediction
from pachi_agents.results import (
    PredictionNotLocked,
    ResultAlreadyExists,
    ResultCorrupt,
    ResultNotFound,
    ResultStore,
    evaluate_prediction,
)
from pachi_agents.experience import (
    ExperienceBuilder,
    ExperienceStore,
    get_agent_summary,
    get_combo_stats,
    get_reason_stats,
    get_weight_pattern_stats,
    rebuild_experience,
)
from pachi_agents.ui_data import load_dashboard_data
from pachi_agents.export_web import export_web
from pachi_agents.backtest import run_walk_forward
from pachi_agents.experience_feedback import adjust_agent_candidates, adjust_god_weights


class InputsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "analyze" / "20260715").mkdir(parents=True)
        (self.root / "replay").mkdir()
        (self.root / "daily_ohlc" / "20260715").mkdir(parents=True)

        (self.root / "analyze" / "20260715" / "20260715_analyze.csv").write_text(
            "Date,Machine,Group,Island,種別,開始時刻,開始差玉,終了時刻,終了差玉,増減差玉,時間(分)\n"
            "20260715,039,1,s3,当り,10:00,0,10:10,100,100,10\n",
            encoding="utf-8-sig",
        )
        (self.root / "replay" / "20260715_snapshot.json").write_text(
            json.dumps({"date": "20260715", "steps": [], "machines": []}),
            encoding="utf-8",
        )
        (self.root / "daily_ohlc" / "20260715" / "20260715_daily_ohlc.csv").write_text(
            "Date,Machine,Group,Island,Open,High,Low,Close\n"
            "20260715,039,1,s3,0,120,-10,100\n",
            encoding="utf-8-sig",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_date_catalog_and_readers(self) -> None:
        self.assertEqual(available_analyze_dates(self.root / "analyze"), ["20260715"])
        self.assertEqual(available_snapshot_dates(self.root / "replay"), ["20260715"])
        rows = load_analyze_rows(self.root / "analyze", "20260715", cutoff_date="20260715")
        self.assertEqual(rows[0]["Machine"], "039")
        snapshot = load_snapshot(self.root / "replay", "20260715", cutoff_date="20260715")
        self.assertEqual(snapshot["date"], "20260715")
        ohlc = load_daily_ohlc_rows(self.root / "daily_ohlc", cutoff_date="20260715")
        self.assertEqual(ohlc[0]["Machine"], "039")

    def test_future_input_is_rejected(self) -> None:
        with self.assertRaises(AsOfViolation):
            load_analyze_rows(self.root / "analyze", "20260715", cutoff_date="20260714")
        with self.assertRaises(AsOfViolation):
            load_snapshot(self.root / "replay", "20260715", cutoff_date="20260714")

    def test_pair_history_is_recomputed_as_of_cutoff(self) -> None:
        path = self.root / "pair_history.json"
        path.write_text(json.dumps({
            "meta": {},
            "pairs": {
                "G1|039|047": {
                    "group": "1", "A": "039", "B": "047",
                    "daily": [
                        {"date": "20260714", "count": 2, "lift": 2.0},
                        {"date": "20260716", "count": 8, "lift": 5.0},
                    ],
                    "total_count": 999,
                    "mean_lift": 999,
                }
            },
        }), encoding="utf-8")
        result = load_pair_history_as_of(path, "20260715")
        pair = result["pairs"]["G1|039|047"]
        self.assertEqual([d["date"] for d in pair["daily"]], ["20260714"])
        self.assertEqual(pair["total_count"], 2)
        self.assertEqual(pair["mean_lift"], 2.0)
        self.assertEqual(pair["reproducibility"], 1.0)


class PredictionsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = PredictionStore(Path(self.temp.name) / "predictions")
        self.payload = {
            "prediction_date": "20260717",
            "cutoff_date": "20260716",
            "created_at": "2026-07-16T23:00:00+09:00",
            "status": "draft",
            "logic_version": "pachi_agents_v1",
            "input_manifest": [
                {"kind": "analyze_csv", "path": "csv/analyze/20260716/x.csv", "date": "20260716"},
                {"kind": "snapshot", "path": "csv/replay/20260716_snapshot.json", "date": "20260716"},
                {"kind": "ohlc", "path": "csv/daily_ohlc/20260716/x.csv", "date": "20260716"},
                {"kind": "propagation_history", "path": "pair_history.json", "date": "20260716"},
            ],
            "agents": {"pachio": {}, "pachiko": {}, "pachikamisama": {}},
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_save_and_load(self) -> None:
        path = self.store.save(self.payload)
        self.assertEqual(path.name, "prediction_20260717.json")
        loaded = self.store.load("20260717")
        self.assertEqual(loaded["logic_version"], "pachi_agents_v1")
        self.assertEqual(set(loaded["agents"]), {"pachio", "pachiko", "pachikamisama"})

    def test_cutoff_and_manifest_future_are_rejected(self) -> None:
        invalid_cutoff = dict(self.payload, cutoff_date="20260717")
        with self.assertRaises(AsOfViolation):
            self.store.save(invalid_cutoff)
        invalid_manifest = dict(self.payload, input_manifest=[
            {"kind": "analyze_csv", "path": "future.csv", "date": "20260717"}
        ])
        with self.assertRaises(AsOfViolation):
            self.store.save(invalid_manifest)

    def test_locked_prediction_cannot_be_saved_or_locked_twice(self) -> None:
        self.store.save(self.payload)
        self.store.lock("20260717")
        self.assertEqual(self.store.load("20260717")["status"], "locked")
        with self.assertRaises(PredictionAlreadyExists):
            self.store.save(dict(self.payload, status="locked"))
        with self.assertRaises(PredictionAlreadyExists):
            self.store.lock("20260717")

    def test_atomic_write_cleans_temp_file_on_replace_failure(self) -> None:
        with mock.patch("pachi_agents.predictions.os.replace", side_effect=OSError("simulated failure")):
            with self.assertRaises(OSError):
                self.store.save(self.payload)
        self.assertFalse(self.store.path_for("20260717").exists())
        self.assertEqual(list(Path(self.temp.name, "predictions").glob("*.tmp")), [])

    def test_load_errors_are_distinct(self) -> None:
        with self.assertRaises(PredictionNotFound):
            self.store.load("20260717")
        path = self.store.path_for("20260717")
        path.write_text("{not-json", encoding="utf-8")
        with self.assertRaises(PredictionCorrupt):
            self.store.load("20260717")
        path.write_text(json.dumps({"prediction_date": "20260717"}), encoding="utf-8")
        with self.assertRaises(PredictionSchemaError):
            self.store.load("20260717")


class PachioTest(unittest.TestCase):
    def test_insufficient_history_is_explicit(self) -> None:
        rows = [
            {"date": f"202607{day:02d}", "Machine": "039", "Open": "0", "High": "10", "Low": "-5", "Close": str(day)}
            for day in range(1, 21)
        ]
        result = build_pachio_agent(rows)
        self.assertIsNone(result["primary_machine"])
        self.assertEqual(result["reason_codes"], ["INSUFFICIENT_HISTORY"])

    def test_machine_features_are_structured(self) -> None:
        rows = [
            {"date": f"202606{day:02d}", "Machine": "039", "Open": "0", "High": str(day + 10), "Low": "-5", "Close": str(day * 10)}
            for day in range(1, 31)
        ]
        result = build_pachio_agent(rows)
        self.assertEqual(result["primary_machine"], "039")
        self.assertIn("signals", result)
        self.assertIn("reason_codes", result)
        self.assertIn("ma5_slope", result["signals"])
        self.assertEqual(result["signals"], result["candidates"][0]["signals"])
        self.assertEqual(result["reason_codes"], result["candidates"][0]["reason_codes"])

    def test_generation_uses_cutoff_and_locks_prediction(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "daily_ohlc"
            dates = [f"202606{day:02d}" for day in range(1, 31)] + [f"202607{day:02d}" for day in range(1, 17)]
            for index, day in enumerate(dates, start=1):
                directory = root / day
                directory.mkdir(parents=True)
                (directory / f"{day}_daily_ohlc.csv").write_text(
                    "Date,Machine,Group,Island,Open,High,Low,Close\n"
                    f"{day},039,1,s3,0,{index + 10},-5,{index * 10}\n",
                    encoding="utf-8-sig",
                )
            store = PredictionStore(Path(temp) / "predictions")
            path = generate_pachio_prediction(
                store, root, prediction_date="20260717", cutoff_date="20260716"
            )
            saved = store.load("20260717")
            self.assertEqual(path.name, "prediction_20260717.json")
            self.assertEqual(saved["status"], "locked")
            self.assertEqual(saved["agents"]["pachio"]["signals"]["last_date"], "20260716")


class PachikoTest(unittest.TestCase):
    def test_statistical_candidates_and_primary_reasons(self) -> None:
        snapshots = [{
            "date": "20260716",
            "steps": ["10:00"],
            "machines": [
                {"machine": "039", "group": "1", "island": "s3", "active": True,
                 "kind": ["当り"], "ball": [100]},
                {"machine": "047", "group": "1", "island": "s3", "active": True,
                 "kind": ["当り"], "ball": [50]},
            ],
        }]
        history = {"pairs": {
            "G1|039|047": {
                "A": "039", "B": "047", "daily": [
                    {"date": "20260715", "count": 4, "lift": 2.0},
                    {"date": "20260716", "count": 4, "lift": 2.0},
                ],
                "total_count": 8, "days_seen": 2, "mean_lift": 2.0,
            }
        }}
        result = build_pachiko_agent(
            snapshots=snapshots,
            pair_history=history,
            daytime_hit_days={"047": 2},
            ohlc_rows=[],
            top_n=5,
        )
        self.assertEqual(result["primary_machine"], "047")
        self.assertLessEqual(len(result["candidates"]), 5)
        self.assertEqual(result["signals"], result["candidates"][0]["signals"])
        self.assertIn("PROPAGATION_REPEATED", result["reason_codes"])

    def test_no_statistical_evidence_is_explicit(self) -> None:
        result = build_pachiko_agent(snapshots=[], pair_history={}, daytime_hit_days={}, ohlc_rows=[])
        self.assertIsNone(result["primary_machine"])
        self.assertEqual(result["confidence"], 0.0)
        self.assertEqual(result["reason_codes"], ["INSUFFICIENT_STATISTICAL_EVIDENCE"])


class PachikamisamaTest(unittest.TestCase):
    def setUp(self) -> None:
        self.pachio = {
            "logic_version": "pachi_agents_pachio_v1",
            "primary_machine": "039",
            "confidence": 0.8,
            "signals": {"bullish_structure": True},
            "reason_codes": ["BULL_STRUCTURE"],
            "candidates": [
                {"machine": "039", "score": 5.0, "confidence": 0.8, "signals": {"x": 1}, "reason_codes": ["BULL_STRUCTURE"]},
                {"machine": "047", "score": 4.0, "confidence": 0.7, "signals": {"x": 2}, "reason_codes": ["MA5_UP"]},
            ],
        }
        self.pachiko = {
            "logic_version": "pachi_agents_pachiko_v1",
            "primary_machine": "047",
            "confidence": 0.75,
            "signals": {"group_strength": 1.0},
            "reason_codes": ["PROPAGATION_REPEATED"],
            "candidates": [
                {"machine": "047", "score": 4.5, "confidence": 0.75, "signals": {"y": 1}, "reason_codes": ["PROPAGATION_REPEATED"]},
                {"machine": "039", "score": 4.2, "confidence": 0.7, "signals": {"y": 2}, "reason_codes": ["GROUP_STRENGTH_HIGH"]},
            ],
        }

    def test_integration_is_ranked_and_reproducible(self) -> None:
        result = build_pachikamisama_agent(self.pachio, self.pachiko)
        self.assertEqual(len(result["candidates"]), 2)
        self.assertIn(result["honmei"], {"039", "047"})
        self.assertEqual(sum(result["agent_weights"].values()), 1.0)
        self.assertEqual(result["signals"], result["candidates"][0]["signals"])
        self.assertIn("AGENT_DISAGREEMENT", result["reason_codes"])
        self.assertIn("CANDIDATE_OVERLAP", result["reason_codes"])
        self.assertIn("BOTH_TOP3", result["reason_codes"])
        self.assertIn("CROSS_AGENT_TOP5", result["reason_codes"])
        self.assertIn("DIVERSE_SIGNAL_SUPPORT", result["reason_codes"])

    def test_primary_agreement_is_distinct_from_candidate_overlap(self) -> None:
        pachiko = dict(self.pachiko)
        pachiko["primary_machine"] = "039"
        pachiko["candidates"] = [
            {"machine": "039", "score": 4.5, "confidence": 0.75, "signals": {}, "reason_codes": []},
            {"machine": "047", "score": 4.2, "confidence": 0.7, "signals": {}, "reason_codes": []},
        ]
        result = build_pachikamisama_agent(self.pachio, pachiko)
        self.assertIn("PRIMARY_AGREEMENT", result["reason_codes"])
        self.assertNotIn("AGENT_DISAGREEMENT", result["reason_codes"])
        self.assertIn("CANDIDATE_OVERLAP", result["reason_codes"])

    def test_primary_disagreement_with_both_top3(self) -> None:
        pachio = dict(self.pachio)
        pachiko = dict(self.pachiko)
        pachio["candidates"] = [
            {"machine": "039", "score": 5, "confidence": .8, "signals": {}, "reason_codes": []},
            {"machine": "047", "score": 4, "confidence": .7, "signals": {}, "reason_codes": []},
            {"machine": "055", "score": 3, "confidence": .6, "signals": {}, "reason_codes": []},
        ]
        pachiko["candidates"] = [
            {"machine": "047", "score": 5, "confidence": .75, "signals": {}, "reason_codes": []},
            {"machine": "039", "score": 4, "confidence": .7, "signals": {}, "reason_codes": []},
            {"machine": "055", "score": 3, "confidence": .6, "signals": {}, "reason_codes": []},
        ]
        result = build_pachikamisama_agent(pachio, pachiko)
        self.assertIn("AGENT_DISAGREEMENT", result["reason_codes"])
        self.assertIn("BOTH_TOP3", result["reason_codes"])

    def test_primary_disagreement_with_top5_overlap(self) -> None:
        pachio = dict(self.pachio)
        pachiko = dict(self.pachiko)
        pachio["candidates"] = [{"machine": str(i), "score": 6-i, "confidence": .6, "signals": {}, "reason_codes": []} for i in (39, 40, 41, 42, 55)]
        pachiko["candidates"] = [{"machine": str(i), "score": 6-i, "confidence": .6, "signals": {}, "reason_codes": []} for i in (47, 48, 49, 50, 55)]
        pachio["candidates"] = [dict(candidate, machine=f"{int(candidate['machine']):03d}") for candidate in pachio["candidates"]]
        pachiko["candidates"] = [dict(candidate, machine=f"{int(candidate['machine']):03d}") for candidate in pachiko["candidates"]]
        result = build_pachikamisama_agent(pachio, pachiko)
        self.assertIn("AGENT_DISAGREEMENT", result["reason_codes"])
        self.assertIn("CANDIDATE_OVERLAP", result["reason_codes"])
        overlap_candidate = next(candidate for candidate in result["candidates"] if candidate["machine"] == "055")
        self.assertIn("CROSS_AGENT_TOP5", overlap_candidate["reason_codes"])

    def test_no_candidate_overlap(self) -> None:
        pachio = dict(self.pachio, candidates=[{"machine": "039", "score": 5, "confidence": .8, "signals": {}, "reason_codes": []}])
        pachiko = dict(self.pachiko, candidates=[{"machine": "047", "score": 5, "confidence": .8, "signals": {}, "reason_codes": []}])
        result = build_pachikamisama_agent(pachio, pachiko)
        self.assertIn("AGENT_DISAGREEMENT", result["reason_codes"])
        self.assertNotIn("CANDIDATE_OVERLAP", result["reason_codes"])
        self.assertNotIn("BOTH_TOP3", result["reason_codes"])

    def test_insufficient_agent_is_downweighted_without_history(self) -> None:
        empty = {"primary_machine": None, "candidates": [], "confidence": 0.0}
        result = build_pachikamisama_agent(self.pachio, empty)
        self.assertEqual(result["agent_weights"], {"pachio": 0.75, "pachiko": 0.25})
        self.assertEqual(result["honmei"], "039")

    def test_generation_locks_combined_prediction(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = PredictionStore(Path(temp) / "predictions")
            path = generate_pachikamisama_prediction(
                store,
                prediction_date="20260717",
                cutoff_date="20260716",
                pachio=self.pachio,
                pachiko=self.pachiko,
            )
            saved = store.load("20260717")
            self.assertEqual(path.name, "prediction_20260717.json")
            self.assertEqual(saved["status"], "locked")
            self.assertIn("honmei", saved["agents"]["pachikamisama"])
            self.assertEqual(saved["agents"]["pachikamisama"]["signals"], saved["agents"]["pachikamisama"]["candidates"][0]["signals"])
            self.assertEqual(saved["agents"]["pachikamisama"]["reason_codes"], saved["agents"]["pachikamisama"]["candidates"][0]["reason_codes"])
            self.assertTrue(saved["agents"]["pachio"]["candidates"])
            self.assertTrue(saved["agents"]["pachiko"]["candidates"])


class ResultsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.predictions = PredictionStore(root / "predictions")
        self.results = ResultStore(root / "results")
        self.ohlc = root / "daily_ohlc"
        self.prediction = {
            "prediction_date": "20260717",
            "cutoff_date": "20260716",
            "created_at": "2026-07-16T23:00:00+09:00",
            "status": "locked",
            "logic_version": "pachi_agents_v1",
            "input_manifest": [],
            "agents": {
                "pachio": {"primary_machine": "039", "candidates": [{"machine": "039"}]},
                "pachiko": {"primary_machine": "047", "candidates": [{"machine": "047"}]},
                "pachikamisama": {"honmei": "047", "taikou": "039", "ana": None, "candidates": []},
            },
        }
        self.predictions.save(self.prediction)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_actuals(self, rows: str) -> None:
        directory = self.ohlc / "20260717"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "20260717_daily_ohlc.csv").write_text(
            "Date,Machine,Group,Island,Open,High,Low,Close\n" + rows,
            encoding="utf-8-sig",
        )

    def test_positive_and_negative_results_and_prediction_unchanged(self) -> None:
        self._write_actuals(
            "20260717,039,1,s3,0,120,-20,100\n"
            "20260717,047,1,s3,100,120,80,90\n"
            "20260717,055,1,s3,0,10,-5,5\n"
        )
        before = self.predictions.path_for("20260717").read_bytes()
        path = evaluate_prediction(self.predictions, self.results, prediction_date="20260717", ohlc_root=self.ohlc)
        after = self.predictions.path_for("20260717").read_bytes()
        result = self.results.load("20260717")
        self.assertEqual(path.name, "result_20260717.json")
        self.assertEqual(result["status"], "evaluated")
        self.assertEqual(result["agents"]["pachio"]["result"]["outcome"], "success")
        self.assertEqual(result["agents"]["pachiko"]["result"]["outcome"], "failure")
        self.assertEqual(result["agents"]["pachikamisama"]["honmei"]["machine"], "047")
        self.assertEqual(result["agents"]["pachikamisama"]["honmei"]["outcome"], "failure")
        self.assertEqual(result["agents"]["pachikamisama"]["taikou"]["machine"], "039")
        self.assertEqual(result["agents"]["pachikamisama"]["taikou"]["outcome"], "success")
        self.assertEqual(result["agents"]["pachikamisama"]["ana"], {"machine": None, "status": "not_selected", "outcome": None})
        self.assertEqual(result["agents"]["pachikamisama"]["honmei_outcome"], "failure")
        self.assertEqual(before, after)

    def test_no_actual_data_is_pending(self) -> None:
        evaluate_prediction(self.predictions, self.results, prediction_date="20260717", ohlc_root=self.ohlc)
        self.assertEqual(self.results.load("20260717")["status"], "pending")

    def test_partial_actual_data_is_incomplete(self) -> None:
        self._write_actuals("20260717,039,1,s3,0,120,-20,100\n")
        evaluate_prediction(self.predictions, self.results, prediction_date="20260717", ohlc_root=self.ohlc)
        result = self.results.load("20260717")
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["agents"]["pachiko"]["result"]["status"], "missing")

    def test_missing_or_unlocked_prediction_is_distinguished(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            missing_store = PredictionStore(Path(temp) / "predictions")
            with self.assertRaises(PredictionNotFound):
                evaluate_prediction(missing_store, ResultStore(Path(temp) / "results"), prediction_date="20260717", ohlc_root=self.ohlc)
        draft_store = PredictionStore(Path(self.temp.name) / "draft_predictions")
        draft = dict(self.prediction, status="draft")
        draft_store.save(draft)
        with self.assertRaises(PredictionNotLocked):
            evaluate_prediction(draft_store, ResultStore(Path(self.temp.name) / "draft_results"), prediction_date="20260717", ohlc_root=self.ohlc)

    def test_result_cannot_be_regenerated_normally(self) -> None:
        self._write_actuals("20260717,039,1,s3,0,120,-20,100\n20260717,047,1,s3,100,120,80,90\n20260717,055,1,s3,0,10,-5,5\n")
        evaluate_prediction(self.predictions, self.results, prediction_date="20260717", ohlc_root=self.ohlc)
        with self.assertRaises(ResultAlreadyExists):
            evaluate_prediction(self.predictions, self.results, prediction_date="20260717", ohlc_root=self.ohlc)

    def _run_case(self, *, pachio: str, pachiko: str, honmei: str | None, taikou: str | None, ana: str | None, rows: str):
        case = Path(self.temp.name) / f"case_{getattr(self, '_case_no', 0)}"
        self._case_no = getattr(self, "_case_no", 0) + 1
        prediction_store = PredictionStore(case / "predictions")
        result_store = ResultStore(case / "results")
        prediction = json.loads(json.dumps(self.prediction))
        prediction["agents"]["pachio"]["primary_machine"] = pachio
        prediction["agents"]["pachiko"]["primary_machine"] = pachiko
        prediction["agents"]["pachikamisama"].update({"honmei": honmei, "taikou": taikou, "ana": ana})
        prediction_store.save(prediction)
        self._write_actuals(rows)
        evaluate_prediction(prediction_store, result_store, prediction_date="20260717", ohlc_root=self.ohlc)
        return result_store.load("20260717")

    def test_god_honmei_success_and_null_ana(self) -> None:
        result = self._run_case(
            pachio="039", pachiko="047", honmei="039", taikou="047", ana=None,
            rows="20260717,039,1,s3,0,120,-20,100\n20260717,047,1,s3,100,120,80,90\n",
        )
        god = result["agents"]["pachikamisama"]
        self.assertEqual(god["honmei"]["outcome"], "success")
        self.assertEqual(god["honmei_outcome"], god["honmei"]["outcome"])
        self.assertEqual(god["ana"]["status"], "not_selected")

    def test_god_honmei_same_as_pachio_uses_same_ohlc(self) -> None:
        result = self._run_case(
            pachio="039", pachiko="047", honmei="039", taikou="047", ana=None,
            rows="20260717,039,1,s3,0,120,-20,100\n20260717,047,1,s3,100,120,80,90\n",
        )
        self.assertEqual(result["agents"]["pachio"]["result"], result["agents"]["pachikamisama"]["honmei"])

    def test_god_honmei_same_as_pachiko_uses_same_ohlc(self) -> None:
        result = self._run_case(
            pachio="039", pachiko="047", honmei="047", taikou="039", ana=None,
            rows="20260717,039,1,s3,0,120,-20,100\n20260717,047,1,s3,100,120,80,90\n",
        )
        self.assertEqual(result["agents"]["pachiko"]["result"], result["agents"]["pachikamisama"]["honmei"])

    def test_all_agents_same_machine_share_identical_actual(self) -> None:
        result = self._run_case(
            pachio="039", pachiko="039", honmei="039", taikou="039", ana="039",
            rows="20260717,039,1,s3,0,120,-20,100\n",
        )
        actual = result["agents"]["pachio"]["result"]
        self.assertEqual(actual, result["agents"]["pachiko"]["result"])
        self.assertEqual(actual, result["agents"]["pachikamisama"]["honmei"])
        self.assertEqual(actual, result["agents"]["pachikamisama"]["taikou"])
        self.assertEqual(actual, result["agents"]["pachikamisama"]["ana"])

    def test_result_load_distinguishes_missing_and_corrupt(self) -> None:
        with self.assertRaises(ResultNotFound):
            self.results.load("20260717")
        path = self.results.path_for("20260717")
        path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(ResultCorrupt):
            self.results.load("20260717")


class ExperienceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.predictions = PredictionStore(root / "predictions")
        self.results = ResultStore(root / "results")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _prediction(self, day: str, *, pachio_confidence: float = 0.8, weight: tuple[float, float] = (0.45, 0.55)) -> dict:
        return {
            "prediction_date": day,
            "cutoff_date": "20260716",
            "created_at": "2026-07-16T23:00:00+09:00",
            "status": "locked",
            "logic_version": "pachi_agents_v1",
            "input_manifest": [],
            "agents": {
                "pachio": {
                    "logic_version": "pachio_v1", "primary_machine": "039", "confidence": pachio_confidence,
                    "reason_codes": ["MA5_UP", "BULL_STRUCTURE"],
                    "signals": {"ma5_slope": 10}, "candidates": [{"machine": "039"}],
                },
                "pachiko": {
                    "logic_version": "pachiko_v1", "primary_machine": "047", "confidence": 0.6,
                    "reason_codes": ["PROPAGATION_REPEATED", "GROUP_STRENGTH_HIGH"],
                    "signals": {"group_strength": 1}, "candidates": [{"machine": "047"}],
                },
                "pachikamisama": {
                    "logic_version": "pachikamisama_v1", "honmei": "047", "taikou": "039", "ana": None,
                    "confidence": 0.7, "agent_weights": {"pachio": weight[0], "pachiko": weight[1]},
                    "reason_codes": ["AGENT_DISAGREEMENT", "CANDIDATE_OVERLAP", "BOTH_TOP3", "CROSS_AGENT_TOP5", "DIVERSE_SIGNAL_SUPPORT"],
                    "signals": {"primary_agreement": False, "candidate_overlap": True, "both_top3": True, "cross_agent_top5": True, "diverse_signal_support": True}, "candidates": [],
                },
            },
        }

    def _result(self, day: str, *, status: str = "evaluated", pachio: str = "success", pachiko: str = "failure") -> dict:
        return {
            "prediction_date": day,
            "evaluated_at": "2026-07-17T23:00:00+09:00",
            "result_version": "result_v1",
            "status": status,
            "source_manifest": [],
            "agents": {
                "pachio": {"result": {"outcome": pachio}},
                "pachiko": {"result": {"outcome": pachiko}},
                "pachikamisama": {
                    "honmei": {"outcome": pachiko},
                    "taikou": {"outcome": pachio},
                    "ana": {"status": "not_selected", "outcome": None},
                    "honmei_outcome": pachiko,
                },
            },
        }

    def test_success_failure_and_confidence_band(self) -> None:
        builder = ExperienceBuilder("production", minimum_sample=5)
        prediction = self._prediction("20260717")
        builder.register_prediction(prediction)
        builder.add_result(self._result("20260717"))
        memory = builder.finalize()
        summary = get_agent_summary(memory, "pachio")
        self.assertEqual(summary["success"], 1)
        self.assertEqual(summary["failure"], 0)
        self.assertEqual(summary["win_rate"], 1.0)
        self.assertEqual(get_agent_summary(memory, "pachiko")["failure"], 1)
        self.assertEqual(memory["agents"]["pachio"]["confidence_bands"]["0_70_0_85"]["success"], 1)
        for name in ("pachio", "pachiko", "pachikamisama"):
            self.assertIn("summary", memory["agents"][name])
            self.assertTrue(memory["agents"][name]["confidence_bands"])
            self.assertTrue(memory["agents"][name]["reason_codes"])
            self.assertTrue(memory["agents"][name]["reason_combinations"])

    def test_reason_codes_combinations_and_insufficient_sample(self) -> None:
        builder = ExperienceBuilder("production", minimum_sample=5)
        prediction = self._prediction("20260717")
        builder.register_prediction(prediction)
        builder.add_result(self._result("20260717"))
        memory = builder.finalize()
        reason = get_reason_stats(memory, "pachio", "MA5_UP")
        self.assertEqual(reason["occurrences"], 1)
        self.assertTrue(reason["insufficient_data"])
        combo = get_combo_stats(memory, "pachio", "BULL_STRUCTURE+MA5_UP")
        self.assertEqual(combo["success"], 1)
        self.assertEqual(combo["sample_count"], 1)

    def test_god_roles_weights_patterns_and_reflection(self) -> None:
        builder = ExperienceBuilder("production")
        builder.register_prediction(self._prediction("20260717", weight=(0.65, 0.35)))
        builder.add_result(self._result("20260717"))
        memory = builder.finalize()
        god = memory["pachikamisama"]
        self.assertEqual(god["roles"]["honmei"]["failure"], 1)
        self.assertEqual(god["roles"]["taikou"]["success"], 1)
        self.assertEqual(god["roles"]["ana"]["evaluated_count"], 0)
        self.assertTrue(god["honmei_confidence_bands"])
        self.assertEqual(god["conditions"]["AGENT_DISAGREEMENT"]["success"], 0)
        self.assertEqual(god["conditions"]["CANDIDATE_OVERLAP"]["failure"], 1)
        self.assertEqual(get_weight_pattern_stats(memory, "pachio_dominant")["failure"], 1)
        reflection_events = {event["event"] for event in memory["reflections"][0]["events"]}
        self.assertIn("PACHIO_SUCCESS", reflection_events)
        self.assertIn("PACHIKO_FAILURE", reflection_events)
        self.assertIn("KAMISAMA_HONMEI_FAILURE", reflection_events)
        self.assertIn("AGENT_DISAGREEMENT", reflection_events)
        self.assertIn("CANDIDATE_OVERLAP", reflection_events)
        self.assertIn("BOTH_TOP3", reflection_events)
        self.assertIn("CROSS_AGENT_TOP5", reflection_events)

    def test_pending_incomplete_and_duplicate_are_excluded(self) -> None:
        builder = ExperienceBuilder("production")
        prediction = self._prediction("20260717")
        self.assertTrue(builder.register_prediction(prediction))
        self.assertFalse(builder.register_prediction(prediction))
        self.assertFalse(builder.add_result(self._result("20260717", status="incomplete")))
        self.assertTrue(builder.add_result(self._result("20260717")))
        self.assertFalse(builder.add_result(self._result("20260717")))

    def test_production_backtest_separation_and_rebuild_order(self) -> None:
        for day, pachio in (("20260716", "failure"), ("20260717", "success")):
            prediction = self._prediction(day)
            if day == "20260716":
                prediction["cutoff_date"] = "20260715"
            self.predictions.save(prediction)
            self.results.save(self._result(day, pachio=pachio, pachiko="success"))
        production_store = ExperienceStore(Path(self.temp.name) / "experience", "production")
        backtest_store = ExperienceStore(Path(self.temp.name) / "experience", "backtest")
        production = rebuild_experience(self.predictions.directory, self.results.directory, production_store)
        backtest = rebuild_experience(self.predictions.directory, self.results.directory, backtest_store)
        self.assertEqual(production["mode"], "production")
        self.assertEqual(backtest["mode"], "backtest")
        self.assertNotEqual(production_store.path, backtest_store.path)
        self.assertEqual(production["processed_prediction_dates"], ["20260716", "20260717"])
        again = rebuild_experience(self.predictions.directory, self.results.directory, production_store)
        production["generated_at"] = None
        again["generated_at"] = None
        self.assertEqual(production, again)

    def test_prediction_and_result_files_are_not_modified_by_rebuild(self) -> None:
        prediction = self._prediction("20260717")
        self.predictions.save(prediction)
        self.results.save(self._result("20260717"))
        prediction_before = self.predictions.path_for("20260717").read_bytes()
        result_before = self.results.path_for("20260717").read_bytes()
        rebuild_experience(self.predictions.directory, self.results.directory, ExperienceStore(Path(self.temp.name) / "experience"))
        self.assertEqual(prediction_before, self.predictions.path_for("20260717").read_bytes())
        self.assertEqual(result_before, self.results.path_for("20260717").read_bytes())


class UiDataTest(unittest.TestCase):
    def test_ui_assets_have_required_views_and_labels(self) -> None:
        ui = Path(__file__).parents[1] / "pachi_agents" / "ui"
        html = (ui / "index.html").read_text(encoding="utf-8")
        js = (ui / "app.js").read_text(encoding="utf-8")
        self.assertIn("Pachi Agents", html)
        self.assertIn("AI会議室 Coming Soon", html)
        for code in ("PRIMARY_AGREEMENT", "AGENT_DISAGREEMENT", "CANDIDATE_OVERLAP", "BOTH_TOP3", "CROSS_AGENT_TOP5", "DIVERSE_SIGNAL_SUPPORT"):
            self.assertIn(code, js)

    def _prediction(self, root: Path) -> None:
        store = PredictionStore(root / "predictions")
        store.save({
            "prediction_date": "20260717", "cutoff_date": "20260716",
            "created_at": "2026-07-16T23:00:00+09:00", "status": "locked",
            "logic_version": "pachi_agents_v1", "input_manifest": [],
            "agents": {
                "pachio": {"primary_machine": "039", "confidence": .8, "candidates": []},
                "pachiko": {"primary_machine": "047", "confidence": .7, "candidates": []},
                "pachikamisama": {"honmei": "047", "taikou": "039", "ana": None, "confidence": .7},
            },
        })

    def test_empty_ui_state_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            data = load_dashboard_data(Path(temp) / "data")
            self.assertIsNone(data["prediction"])
            self.assertIsNone(data["result"])
            self.assertIsNone(data["experience"])

    def test_prediction_result_experience_and_incomplete_are_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "data"
            self._prediction(root)
            result_store = ResultStore(root / "results")
            result_store.save({
                "prediction_date": "20260717", "evaluated_at": "2026-07-17T23:00:00+09:00",
                "result_version": "result_v1", "status": "incomplete", "source_manifest": [],
                "agents": {},
            })
            exp = ExperienceStore(root / "experience")
            memory = {"mode": "production", "minimum_sample": 5, "agents": {
                "pachio": {"summary": {"success": 0, "failure": 0, "evaluated_count": 0, "win_rate": None, "current_streak": {"type": None, "count": 0}}},
            }}
            exp.save(memory)
            data = load_dashboard_data(root)
            self.assertEqual(data["prediction"]["agents"]["pachio"]["primary_machine"], "039")
            self.assertEqual(data["result"]["status"], "incomplete")
            self.assertEqual(data["experience"]["minimum_sample"], 5)

    def test_export_web_creates_empty_static_views_without_primary_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "data"
            output = Path(temp) / "docs" / "pachi_agents" / "data"
            export_web(root, output)
            self.assertEqual(json.loads((output / "latest_prediction.json").read_text()), None)
            self.assertEqual(json.loads((output / "latest_result.json").read_text()), None)
            self.assertEqual(json.loads((output / "experience.json").read_text()), None)
            self.assertEqual(json.loads((output / "history.json").read_text()), [])

    def test_github_pages_entry_keeps_existing_links_and_adds_agents(self) -> None:
        html = (Path(__file__).parents[1] / "docs" / "index.html").read_text(encoding="utf-8")
        for link in ("ohlc.html", "propagation_lookup.html", "combined_signal_analysis.html", "groups.html", "pachi_agents/index.html"):
            self.assertIn(f'href="{link}"', html)


class BacktestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "input"
        self.ohlc = self.root / "csv" / "daily_ohlc"
        self.replay = self.root / "csv" / "replay"
        self.replay.mkdir(parents=True)
        (self.root / "data").mkdir(parents=True)
        (self.root / "pair_history.json").write_text(json.dumps({"meta": {}, "pairs": {}}), encoding="utf-8")
        for day in range(1, 24):
            date = f"202607{day:02d}"
            directory = self.ohlc / date
            directory.mkdir(parents=True)
            (directory / f"{date}_daily_ohlc.csv").write_text(
                "Date,Machine,Group,Island,Open,High,Low,Close\n"
                f"{date},039,1,s3,0,{day + 10},-5,{day * 10}\n",
                encoding="utf-8-sig",
            )
        (self.replay / "20260721_snapshot.json").write_text(json.dumps({"date": "20260721", "steps": [], "machines": []}), encoding="utf-8")
        (self.replay / "20260722_snapshot.json").write_text(json.dumps({"date": "20260722", "steps": [], "machines": []}), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_walk_forward_as_of_and_experience_order(self) -> None:
        backtest = Path(self.temp.name) / "backtest"
        summary = run_walk_forward(self.root, backtest, start_date="20260722", end_date="20260723")
        prediction_22 = PredictionStore(backtest / "predictions").load("20260722")
        prediction_23 = PredictionStore(backtest / "predictions").load("20260723")
        dates_22 = {entry.get("date") for entry in prediction_22["input_manifest"]}
        dates_23 = {entry.get("date") for entry in prediction_23["input_manifest"]}
        self.assertNotIn("20260722", dates_22)
        self.assertIn("20260722", dates_23)
        self.assertEqual(summary["experience"]["mode"], "backtest")
        self.assertEqual(summary["experience"]["evaluated_result_dates"], ["20260722", "20260723"])
        self.assertEqual(prediction_22["agents"]["pachio"]["primary_machine"], "039")
        self.assertEqual(prediction_22["agents"]["pachio"]["signals"]["history_days"], 21)
        self.assertEqual(summary["days"][0]["status"], "evaluated")
        self.assertEqual(summary["days"][0]["experience_after"]["agents"]["pachiko"]["evaluated_count"], 1)

    def test_dry_run_writes_nothing_and_reports_history_entry(self) -> None:
        backtest = Path(self.temp.name) / "dry-run"
        summary = run_walk_forward(self.root, backtest, start_date="20260722", end_date="20260722", dry_run=True)
        self.assertEqual(summary["days"][0]["status"], "dry_run")
        self.assertFalse(backtest.exists())

    def test_missing_result_day_is_pending_and_not_in_experience(self) -> None:
        (self.ohlc / "20260722" / "20260722_daily_ohlc.csv").unlink()
        backtest = Path(self.temp.name) / "missing-day"
        summary = run_walk_forward(self.root, backtest, start_date="20260722", end_date="20260722")
        result = ResultStore(backtest / "results").load("20260722")
        self.assertEqual(result["status"], "pending")
        self.assertEqual(summary["experience"]["evaluated_result_dates"], [])

    def test_rerun_does_not_overwrite_prediction_or_result(self) -> None:
        backtest = Path(self.temp.name) / "rerun"
        run_walk_forward(self.root, backtest, start_date="20260722", end_date="20260722")
        prediction_path = backtest / "predictions" / "prediction_20260722.json"
        result_path = backtest / "results" / "result_20260722.json"
        prediction_before = prediction_path.read_bytes()
        result_before = result_path.read_bytes()
        run_walk_forward(self.root, backtest, start_date="20260722", end_date="20260722")
        self.assertEqual(prediction_before, prediction_path.read_bytes())
        self.assertEqual(result_before, result_path.read_bytes())


class ExperienceFeedbackTest(unittest.TestCase):
    def _memory(self, rate: float = 1.0, count: int = 5) -> dict:
        stat = {"evaluated_count": count, "success_rate": rate}
        return {
            "agents": {
                "pachiko": {
                    "reason_codes": {"PROPAGATION_REPEATED": stat},
                    "reason_combinations": {},
                    "confidence_bands": {},
                    "summary": {"evaluated_count": count, "win_rate": rate},
                },
                "pachio": {"summary": {"evaluated_count": count, "win_rate": 1.0 - rate}},
            }
        }

    def test_insufficient_sample_has_no_strong_adjustment(self) -> None:
        agent = {"primary_machine": "039", "candidates": [{"machine": "039", "score": 10.0, "confidence": .8, "reason_codes": ["PROPAGATION_REPEATED"]}]}
        adjusted = adjust_agent_candidates(agent, self._memory(rate=1.0, count=4), "pachiko")
        self.assertEqual(adjusted["primary_machine"], "039")
        self.assertEqual(adjusted["candidates"][0]["experience_adjustment"]["final_score"], 10.0)

    def test_reason_and_combo_adjustment_is_bounded(self) -> None:
        agent = {"primary_machine": "039", "candidates": [{"machine": "039", "score": 10.0, "confidence": .8, "reason_codes": ["PROPAGATION_REPEATED", "GROUP_STRENGTH_HIGH"]}]}
        memory = self._memory(rate=1.0, count=5)
        memory["agents"]["pachiko"]["reason_codes"]["GROUP_STRENGTH_HIGH"] = {"evaluated_count": 5, "success_rate": 1.0}
        memory["agents"]["pachiko"]["reason_combinations"]["GROUP_STRENGTH_HIGH+PROPAGATION_REPEATED"] = {"evaluated_count": 5, "success_rate": 1.0}
        adjusted = adjust_agent_candidates(agent, memory, "pachiko")
        detail = adjusted["candidates"][0]["experience_adjustment"]
        self.assertGreater(detail["final_score"], detail["base_score"])
        self.assertLessEqual(detail["final_score"] - detail["base_score"], 2.0)

    def test_god_weight_adjustment_is_bounded_and_requires_sample(self) -> None:
        weights, detail = adjust_god_weights({"pachio": .5, "pachiko": .5}, {}, {}, self._memory(rate=1.0, count=5))
        self.assertEqual(weights, {"pachio": .45, "pachiko": .55})
        self.assertEqual(detail["experience_sample_count"], 5)
        weights, _ = adjust_god_weights({"pachio": .5, "pachiko": .5}, {}, {}, self._memory(rate=1.0, count=4))
        self.assertEqual(weights, {"pachio": .5, "pachiko": .5})


if __name__ == "__main__":
    unittest.main()
