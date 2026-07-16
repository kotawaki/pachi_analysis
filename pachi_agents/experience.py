"""Phase 7: prediction/resultから再構築できる経験記憶。

経験記憶は派生データであり、prediction JSON/result JSONを一次データとして扱う。
productionとbacktestは保存先・modeを分離し、productionの読み込みでbacktestを
暗黙に参照しない。
"""

from __future__ import annotations

import itertools
import json
import os
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .inputs import normalize_date
from .predictions import PredictionStore
from .results import ResultStore


MODES = {"production", "backtest"}
MIN_SAMPLE_DEFAULT = 5
MAX_REASON_CODES = 8
CONFIDENCE_BANDS = (
    ("lt_0_50", 0.0, 0.5),
    ("0_50_0_70", 0.5, 0.7),
    ("0_70_0_85", 0.7, 0.85),
    ("gte_0_85", 0.85, 1.01),
)


class ExperienceError(Exception):
    pass


def _empty_stat() -> dict[str, Any]:
    return {
        "occurrences": 0,
        "evaluated_count": 0,
        "success": 0,
        "failure": 0,
        "success_rate": None,
        "average_confidence": None,
        "insufficient_data": True,
        "_confidence_sum": 0.0,
    }


def _empty_summary() -> dict[str, Any]:
    return {
        "total_predictions": 0,
        "evaluated_count": 0,
        "success": 0,
        "failure": 0,
        "win_rate": None,
        "average_confidence": None,
        "max_win_streak": 0,
        "max_loss_streak": 0,
        "current_streak": {"type": None, "count": 0},
        "_confidence_sum": 0.0,
        "_events": [],
    }


def _empty_agent() -> dict[str, Any]:
    return {
        "summary": _empty_summary(),
        "confidence_bands": {name: _empty_stat() for name, _lo, _hi in CONFIDENCE_BANDS},
        "reason_codes": {},
        "reason_combinations": {},
    }


def _band(confidence: Any) -> str:
    value = float(confidence or 0.0)
    for name, low, high in CONFIDENCE_BANDS:
        if low <= value < high:
            return name
    return "gte_0_85"


def _outcome(value: Any) -> str | None:
    return value if value in {"success", "failure"} else None


def _codes(agent: dict[str, Any]) -> list[str]:
    values = agent.get("reason_codes", [])
    if not isinstance(values, list):
        return []
    codes = {str(value) for value in values if value}
    # Phase 5以前のAGENT_AGREEMENTは候補集合の重複を表していたため、
    # 本命一致とは解釈しない。既存データを安全に移行する。
    if "AGENT_AGREEMENT" in codes:
        codes.discard("AGENT_AGREEMENT")
        codes.add("CANDIDATE_OVERLAP")
    return sorted(codes)[:MAX_REASON_CODES]


CONDITION_CODES = (
    "PRIMARY_AGREEMENT",
    "AGENT_DISAGREEMENT",
    "CROSS_AGENT_TOP5",
    "BOTH_TOP3",
    "CANDIDATE_OVERLAP",
    "DIVERSE_SIGNAL_SUPPORT",
)


def _condition_codes(agent: dict[str, Any]) -> set[str]:
    """Return the shared, explicit definitions used by Phase 5 and Phase 7."""
    codes = set(_codes(agent))
    signals = agent.get("signals", {})
    if signals.get("primary_agreement") is True:
        codes.add("PRIMARY_AGREEMENT")
        codes.discard("AGENT_DISAGREEMENT")
    elif signals.get("primary_agreement") is False:
        codes.add("AGENT_DISAGREEMENT")
        codes.discard("PRIMARY_AGREEMENT")
    if signals.get("candidate_overlap") is True:
        codes.add("CANDIDATE_OVERLAP")
    if signals.get("both_top3") is True:
        codes.add("BOTH_TOP3")
    if signals.get("cross_agent_top5") is True:
        codes.add("CROSS_AGENT_TOP5")
    return codes.intersection(CONDITION_CODES)


def _combinations(codes: list[str]) -> list[str]:
    return ["+".join(pair) for pair in itertools.combinations(codes, 2)]


def _weight_pattern(agent: dict[str, Any]) -> str:
    weights = agent.get("agent_weights", {})
    pachio_weight = float(weights.get("pachio") or 0.5)
    if pachio_weight >= 0.6:
        return "pachio_dominant"
    if pachio_weight <= 0.4:
        return "pachiko_dominant"
    return "balanced"


def _record_prediction_stat(stat: dict[str, Any], confidence: float) -> None:
    stat["occurrences"] += 1
    stat["_confidence_sum"] += confidence


def _record_evaluation(stat: dict[str, Any], outcome: str, confidence: float) -> None:
    stat["evaluated_count"] += 1
    stat[outcome] += 1


def _finalize_stat(stat: dict[str, Any], minimum: int) -> dict[str, Any]:
    evaluated = stat["evaluated_count"]
    stat["sample_count"] = evaluated
    stat["success_rate"] = round(stat["success"] / evaluated, 4) if evaluated else None
    stat["average_confidence"] = round(stat["_confidence_sum"] / stat["occurrences"], 4) if stat["occurrences"] else None
    stat["insufficient_data"] = evaluated < minimum
    stat.pop("_confidence_sum", None)
    return stat


def _finalize_summary(summary: dict[str, Any]) -> dict[str, Any]:
    events = summary.pop("_events", [])
    summary["win_rate"] = round(summary["success"] / summary["evaluated_count"], 4) if summary["evaluated_count"] else None
    summary["average_confidence"] = round(summary["_confidence_sum"] / summary["evaluated_count"], 4) if summary["evaluated_count"] else None
    summary.pop("_confidence_sum", None)
    current_type = None
    current_count = 0
    for outcome in events:
        if outcome == current_type:
            current_count += 1
        else:
            current_type = outcome
            current_count = 1
        if outcome == "success":
            summary["max_win_streak"] = max(summary["max_win_streak"], current_count)
        else:
            summary["max_loss_streak"] = max(summary["max_loss_streak"], current_count)
    if events:
        summary["current_streak"] = {"type": events[-1], "count": current_count}
    return summary


def _finalize_agent(agent: dict[str, Any], minimum: int) -> None:
    agent["summary"] = _finalize_summary(agent["summary"])
    for stat in agent["confidence_bands"].values():
        _finalize_stat(stat, minimum)
    for stat in agent["reason_codes"].values():
        _finalize_stat(stat, minimum)
    for stat in agent["reason_combinations"].values():
        _finalize_stat(stat, minimum)


def _reflection(
    date: str,
    agent_name: str,
    agent: dict[str, Any],
    outcome: str | None,
    confidence: float,
) -> dict[str, Any]:
    return {
        "date": date,
        "agent": agent_name,
        "outcome": outcome,
        "confidence": confidence,
        "events": [
            {"event": "reason_outcome", "reason_code": code, "outcome": outcome}
            for code in _codes(agent)
        ],
    }


class ExperienceBuilder:
    """prediction/resultを日付順に一度だけ集計する。"""

    def __init__(self, mode: str, minimum_sample: int = MIN_SAMPLE_DEFAULT):
        if mode not in MODES:
            raise ValueError(f"modeは{sorted(MODES)}のいずれかです")
        self.mode = mode
        self.minimum_sample = minimum_sample
        self.memory: dict[str, Any] = {
            "schema_version": 1,
            "mode": mode,
            "minimum_sample": minimum_sample,
            "generated_at": None,
            "processed_prediction_dates": [],
            "evaluated_result_dates": [],
            "agents": {name: _empty_agent() for name in ("pachio", "pachiko", "pachikamisama")},
            "pachikamisama": {
                "roles": {role: _empty_agent()["summary"] for role in ("honmei", "taikou", "ana")},
                "weight_patterns": {name: _empty_agent()["summary"] for name in ("pachio_dominant", "balanced", "pachiko_dominant")},
                "honmei_confidence_bands": {name: _empty_stat() for name, _lo, _hi in CONFIDENCE_BANDS},
                "conditions": {code: _empty_stat() for code in CONDITION_CODES},
            },
            "reflections": [],
            "data_quality": {
                "prediction_files_seen": 0,
                "locked_predictions": 0,
                "evaluated_results_seen": 0,
                "pending_results_skipped": 0,
                "incomplete_results_skipped": 0,
                "unlocked_predictions_skipped": 0,
                "empty_reasons": [],
            },
        }
        self._predictions: dict[str, dict[str, Any]] = {}
        self._evaluated: set[str] = set()

    def register_prediction(self, prediction: dict[str, Any]) -> bool:
        self.memory["data_quality"]["prediction_files_seen"] += 1
        if prediction.get("status") != "locked":
            self.memory["data_quality"]["unlocked_predictions_skipped"] += 1
            return False
        self.memory["data_quality"]["locked_predictions"] += 1
        date = normalize_date(prediction["prediction_date"])
        if date in self._predictions:
            return False
        self._predictions[date] = prediction
        self.memory["processed_prediction_dates"].append(date)
        for name in ("pachio", "pachiko", "pachikamisama"):
            agent = prediction.get("agents", {}).get(name, {})
            if name == "pachikamisama":
                active = bool(agent.get("honmei"))
            else:
                active = bool(agent.get("primary_machine"))
            if not active:
                continue
            target = self.memory["agents"][name]
            target["summary"]["total_predictions"] += 1
            confidence = float(agent.get("confidence") or 0.0)
            codes = _codes(agent)
            for code in codes:
                _record_prediction_stat(target["reason_codes"].setdefault(code, _empty_stat()), confidence)
            for combo in _combinations(codes):
                _record_prediction_stat(target["reason_combinations"].setdefault(combo, _empty_stat()), confidence)
            band = target["confidence_bands"][_band(confidence)]
            _record_prediction_stat(band, confidence)
            if name == "pachikamisama":
                details = self.memory["pachikamisama"]
                for role in ("honmei", "taikou", "ana"):
                    if agent.get(role) is not None:
                        details["roles"][role]["total_predictions"] += 1
                pattern = _weight_pattern(agent)
                details["weight_patterns"][pattern]["total_predictions"] += 1
                _record_prediction_stat(details["honmei_confidence_bands"][_band(confidence)], confidence)
                condition_codes = _condition_codes(agent)
                for condition in CONDITION_CODES:
                    if condition in condition_codes:
                        _record_prediction_stat(details["conditions"][condition], confidence)
        return True

    def add_result(self, result: dict[str, Any]) -> bool:
        date = normalize_date(result["prediction_date"])
        if result.get("status") == "pending":
            self.memory["data_quality"]["pending_results_skipped"] += 1
        elif result.get("status") == "incomplete":
            self.memory["data_quality"]["incomplete_results_skipped"] += 1
        if date in self._evaluated or date not in self._predictions or result.get("status") != "evaluated":
            return False
        prediction = self._predictions[date]
        self._evaluated.add(date)
        self.memory["evaluated_result_dates"].append(date)
        self.memory["data_quality"]["evaluated_results_seen"] += 1
        reflection = {"date": date, "events": [], "agents": {}}
        for name in ("pachio", "pachiko"):
            pred_agent = prediction.get("agents", {}).get(name, {})
            outcome = _outcome(result.get("agents", {}).get(name, {}).get("result", {}).get("outcome"))
            if not outcome:
                continue
            self._add_agent_result(name, pred_agent, outcome, date)
            reflection["agents"][name] = {"outcome": outcome, "confidence": float(pred_agent.get("confidence") or 0.0)}
            reflection["events"].append({"event": f"{name.upper()}_{outcome.upper()}", "outcome": outcome})
            reflection["events"].extend({"event": "reason_outcome", "reason_code": code, "outcome": outcome} for code in _codes(pred_agent))

        pred_god = prediction.get("agents", {}).get("pachikamisama", {})
        god_result = result.get("agents", {}).get("pachikamisama", {})
        honmei_outcome = _outcome(god_result.get("honmei", {}).get("outcome"))
        if honmei_outcome:
            self._add_agent_result("pachikamisama", pred_god, honmei_outcome, date)
            role_outcomes = {
                role: _outcome(god_result.get(role, {}).get("outcome"))
                for role in ("honmei", "taikou", "ana")
            }
            reflection["agents"]["pachikamisama"] = {"role_outcomes": role_outcomes, "confidence": float(pred_god.get("confidence") or 0.0)}
            for role, outcome in role_outcomes.items():
                if outcome:
                    reflection["events"].append({"event": f"KAMISAMA_{role.upper()}_{outcome.upper()}", "outcome": outcome})
            pattern = _weight_pattern(pred_god)
            reflection["events"].append({"event": pattern.upper()})
            reflection["events"].extend({"event": code} for code in sorted(_condition_codes(pred_god)))
            reflection["agent_weights"] = pred_god.get("agent_weights", {})
            reflection["events"].extend({"event": "reason_outcome", "reason_code": code, "outcome": honmei_outcome} for code in _codes(pred_god))
            self._add_god_details(pred_god, god_result, honmei_outcome)
        self.memory["reflections"].append(reflection)
        return True

    def _add_agent_result(self, name: str, prediction: dict[str, Any], outcome: str, date: str) -> None:
        agent = self.memory["agents"][name]
        confidence = float(prediction.get("confidence") or 0.0)
        summary = agent["summary"]
        summary["evaluated_count"] += 1
        summary[outcome] += 1
        summary["_confidence_sum"] += confidence
        summary["_events"].append(outcome)
        _record_evaluation(agent["confidence_bands"][_band(confidence)], outcome, confidence)
        for code in _codes(prediction):
            _record_evaluation(agent["reason_codes"][code], outcome, confidence)
        for combo in _combinations(_codes(prediction)):
            _record_evaluation(agent["reason_combinations"][combo], outcome, confidence)

    def _add_god_details(self, prediction: dict[str, Any], result: dict[str, Any], honmei_outcome: str) -> None:
        details = self.memory["pachikamisama"]
        for role in ("honmei", "taikou", "ana"):
            outcome = _outcome(result.get(role, {}).get("outcome"))
            if outcome:
                summary = details["roles"][role]
                summary["evaluated_count"] += 1
                summary[outcome] += 1
                summary["_confidence_sum"] += float(prediction.get("confidence") or 0.0)
                summary["_events"].append(outcome)
        confidence = float(prediction.get("confidence") or 0.0)
        _record_evaluation(details["honmei_confidence_bands"][_band(confidence)], honmei_outcome, confidence)
        weights = prediction.get("agent_weights", {})
        pattern = _weight_pattern(prediction)
        pattern_summary = details["weight_patterns"][pattern]
        pattern_summary["evaluated_count"] += 1
        pattern_summary[honmei_outcome] += 1
        pattern_summary["_confidence_sum"] += confidence
        pattern_summary["_events"].append(honmei_outcome)
        condition_codes = _condition_codes(prediction)
        for code in CONDITION_CODES:
            if code in condition_codes:
                _record_evaluation(details["conditions"][code], honmei_outcome, confidence)

    def finalize(self) -> dict[str, Any]:
        self.memory["processed_prediction_dates"] = sorted(self.memory["processed_prediction_dates"])
        self.memory["evaluated_result_dates"] = sorted(self.memory["evaluated_result_dates"])
        for agent in self.memory["agents"].values():
            _finalize_agent(agent, self.minimum_sample)
        details = self.memory["pachikamisama"]
        for role in details["roles"].values():
            _finalize_summary(role)
        for pattern in details["weight_patterns"].values():
            _finalize_summary(pattern)
        for stat in details["conditions"].values():
            _finalize_stat(stat, self.minimum_sample)
        for stat in details["honmei_confidence_bands"].values():
            _finalize_stat(stat, self.minimum_sample)
        empty_reasons = self.memory["data_quality"]["empty_reasons"]
        for name, agent in self.memory["agents"].items():
            if agent["summary"]["total_predictions"] == 0:
                empty_reasons.append({"scope": name, "reason": "no_locked_predictions"})
            elif not agent["reason_codes"]:
                empty_reasons.append({"scope": name, "reason": "no_reason_codes_in_predictions"})
            if agent["summary"]["evaluated_count"] == 0:
                empty_reasons.append({"scope": name, "reason": "no_evaluated_results"})
        if not self.memory["reflections"]:
            empty_reasons.append({"scope": "reflections", "reason": "no_evaluated_predictions"})
        self.memory["generated_at"] = datetime.now().astimezone().isoformat()
        return self.memory


class ExperienceStore:
    def __init__(self, root: str | Path, mode: str = "production"):
        if mode not in MODES:
            raise ValueError(f"modeは{sorted(MODES)}のいずれかです")
        self.root = Path(root)
        self.mode = mode
        self.directory = self.root / mode
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "experience.json"

    def save(self, memory: dict[str, Any]) -> Path:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory, prefix=".experience.", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(memory, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except Exception:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise
        return self.path

    def load(self) -> dict[str, Any]:
        with self.path.open(encoding="utf-8") as handle:
            return json.load(handle)


def rebuild_experience(
    prediction_directory: str | Path,
    result_directory: str | Path,
    experience_store: ExperienceStore,
    *,
    minimum_sample: int = MIN_SAMPLE_DEFAULT,
) -> dict[str, Any]:
    """prediction/resultを日付順に読み、指定modeの経験記憶を再構築する。"""
    prediction_store = PredictionStore(prediction_directory)
    result_store = ResultStore(result_directory)
    prediction_paths = sorted(Path(prediction_directory).glob("prediction_*.json"))
    result_paths = sorted(Path(result_directory).glob("result_*.json"))
    predictions = {}
    for path in prediction_paths:
        date = normalize_date(path.stem.removeprefix("prediction_"))
        predictions[date] = prediction_store.load(date)
    results = {}
    for path in result_paths:
        date = normalize_date(path.stem.removeprefix("result_"))
        results[date] = result_store.load(date)
    builder = ExperienceBuilder(experience_store.mode, minimum_sample)
    for date in sorted(predictions):
        builder.register_prediction(predictions[date])
        if date in results:
            builder.add_result(results[date])
    memory = builder.finalize()
    experience_store.save(memory)
    return memory


def get_agent_summary(memory: dict[str, Any], agent: str) -> dict[str, Any]:
    return memory["agents"][agent]["summary"]


def get_reason_stats(memory: dict[str, Any], agent: str, reason_code: str) -> dict[str, Any] | None:
    return memory["agents"][agent]["reason_codes"].get(reason_code)


def get_combo_stats(memory: dict[str, Any], agent: str, combo: str) -> dict[str, Any] | None:
    return memory["agents"][agent]["reason_combinations"].get(combo)


def get_weight_pattern_stats(memory: dict[str, Any], pattern: str) -> dict[str, Any] | None:
    return memory["pachikamisama"]["weight_patterns"].get(pattern)
