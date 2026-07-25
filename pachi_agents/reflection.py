"""Prediction/Result/Experienceから説明可能なReflectionを生成する。"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .experience import CONFIDENCE_BANDS, ExperienceBuilder
from .inputs import normalize_date
from .predictions import PredictionError, PredictionStore
from .results import ResultError, ResultStore


REFLECTION_VERSION = "pachi_agents_reflection_v1"
AGENTS = ("pachio", "pachiko", "pachikamisama")
ROLES = ("honmei", "taikou", "ana")


class ReflectionError(Exception):
    """Reflectionの入力または保存に関するエラー。"""


def _machine(value: Any) -> str | None:
    if value is None or isinstance(value, dict):
        return None
    text = str(value).strip()
    return text or None


def _outcome(value: Any) -> str | None:
    return value if value in {"success", "failure"} else None


def _codes(agent: dict[str, Any]) -> list[str]:
    return [str(code) for code in agent.get("reason_codes", []) if code]


def _band(confidence: float) -> str:
    for name, lower, upper in CONFIDENCE_BANDS:
        if lower <= confidence < upper:
            return name
    return "gte_0_85"


def _stat(memory: dict[str, Any], agent: str, key: str) -> dict[str, Any] | None:
    return memory.get("agents", {}).get(agent, {}).get("reason_codes", {}).get(key)


def _confidence_stat(memory: dict[str, Any], agent: str, confidence: float) -> dict[str, Any] | None:
    return memory.get("agents", {}).get(agent, {}).get("confidence_bands", {}).get(_band(confidence))


def _rate_text(stat: dict[str, Any] | None) -> str | None:
    if not stat or stat.get("evaluated_count", 0) <= 0 or stat.get("success_rate") is None:
        return None
    count = int(stat["evaluated_count"])
    rate = float(stat["success_rate"]) * 100
    return f"過去{count}件中{rate:.1f}%成功"


def _signal_phrase(agent: dict[str, Any], name: str) -> str:
    signals = agent.get("signals", {})
    codes = _codes(agent)
    if name == "pachio":
        parts = []
        if signals.get("ma5_slope") is not None:
            parts.append("MA5の傾き")
        if signals.get("ma20_slope") is not None:
            parts.append("MA20の傾き")
        if signals.get("cycle_forecast") is not None:
            parts.append("サイクル予測")
        if signals.get("bullish_structure"):
            parts.append("強気構造")
        return "、".join(parts or codes or ["テクニカル指標"])
    parts = []
    labels = {
        "PROPAGATION_REPEATED": "伝播の反復性",
        "GROUP_STRENGTH_HIGH": "グループ強度",
        "DAYTIME_HIT_RECENT": "最近の日中hit",
        "CYCLE_POSITIVE": "周期傾向",
    }
    for code in codes:
        if code in labels:
            parts.append(labels[code])
    return "、".join(parts or codes or ["統計・伝播指標"])


def _reason(agent: dict[str, Any], name: str, memory: dict[str, Any]) -> str:
    if name == "pachikamisama":
        honmei = _machine(agent.get("honmei")) or "未選出"
        taikou = _machine(agent.get("taikou")) or "未選出"
        ana = _machine(agent.get("ana")) or "未選出"
        weights = agent.get("agent_weights") or {}
        weight_text = ""
        if weights:
            weight_text = f"（パチお{float(weights.get('pachio', 0)):.3f}、パチこ{float(weights.get('pachiko', 0)):.3f}）"
        origins = agent.get("role_origins", {})
        origin_text = []
        for role, label in (("honmei", "本命"), ("taikou", "対抗"), ("ana", "穴")):
            origin = origins.get(role, {})
            if origin.get("origin_type"):
                origin_text.append(f"{label}は{origin['origin_type']}")
        suffix = "。" if not origin_text else "（" + "、".join(origin_text) + "）。"
        return f"パチおとパチこの候補を統合し、本命{honmei}、対抗{taikou}、穴{ana}を選択しました{weight_text}{suffix}"
    machine = _machine(agent.get("primary_machine")) or "未選出"
    confidence = float(agent.get("confidence") or 0.0)
    codes = "、".join(_codes(agent)) or "明示されたreason_codeなし"
    return f"{machine}番は{_signal_phrase(agent, name)}を評価し、reason_code（{codes}）とconfidence {confidence:.3f}をもとに本命候補としました。"


def _result_text(item: dict[str, Any] | None, label: str) -> str:
    if not item or item.get("status") in {"missing", "not_selected"}:
        return f"{label}は結果データがなく、判定できませんでした。"
    outcome = _outcome(item.get("outcome"))
    if not outcome:
        return f"{label}は結果データが未確定です。"
    direction = item.get("direction")
    close = item.get("close")
    if outcome == "success":
        return f"{label}は{('陽線' if direction == 'positive' else '終値がプラス')}{f'（終値{close}）' if close is not None else ''}となり、予測時の上昇判断と一致しました。"
    if item.get("max_up", 0) and float(item.get("max_up") or 0) > 0:
        return f"{label}は一時{item.get('max_up')}まで上昇しましたが、最終的には{('陰線' if direction != 'positive' else '期待未達')}となりました。"
    return f"{label}は{('陰線' if direction != 'positive' else '期待未達')}となり、期待した上昇には至りませんでした。"


def _learning(agent: dict[str, Any], name: str, memory: dict[str, Any]) -> str:
    if name == "pachikamisama":
        details = memory.get("pachikamisama", {})
        pattern = agent.get("experience_adjustment", {}).get("weight_pattern") or "balanced"
        stat = details.get("weight_patterns", {}).get(pattern)
        quote = _rate_text(stat)
        if quote:
            return f"weight pattern「{pattern}」は{quote}です。1日の結果だけで大きく変更せず、今後も本命・対抗の条件別成績を観察します。"
        return "本命・対抗の結果とagent weightの組み合わせを蓄積し、十分なサンプルが集まるまで大きな判断変更は行いません。"
    codes = _codes(agent)
    quotes = []
    for code in codes:
        quote = _rate_text(_stat(memory, name, code))
        if quote:
            quotes.append(f"{code}は{quote}")
    confidence = float(agent.get("confidence") or 0.0)
    band_quote = _rate_text(_confidence_stat(memory, name, confidence))
    if quotes and band_quote:
        return f"{quotes[0]}、confidence帯も{band_quote}です。条件単独と組み合わせの両方を継続して観察します。"
    if quotes:
        return f"{quotes[0]}。同じreason_codeの組み合わせが再現するか、次回以降も継続して確認します。"
    if band_quote:
        return f"confidence {_band(confidence)}帯は{band_quote}です。confidenceと条件別成績を分けて再評価します。"
    return "まだ十分な評価済みサンプルがないため、同じ条件の結果を蓄積してから判断します。"


def build_reflection(prediction: dict[str, Any], result: dict[str, Any] | None, experience: dict[str, Any] | None) -> dict[str, Any]:
    if prediction.get("status") != "locked":
        raise ReflectionError("Reflectionの対象predictionはlockedである必要があります")
    prediction_date = normalize_date(prediction["prediction_date"])
    cutoff_date = normalize_date(prediction["cutoff_date"])
    if cutoff_date >= prediction_date:
        raise ReflectionError("cutoff_dateはprediction_dateより前である必要があります")
    if result is not None and normalize_date(result.get("prediction_date")) != prediction_date:
        raise ReflectionError("predictionとresultの日付が一致していません")
    memory = experience or {"agents": {}, "pachikamisama": {}}
    agents = prediction.get("agents", {})
    result_agents = (result or {}).get("agents", {})
    pachio_result = result_agents.get("pachio", {}).get("result")
    pachiko_result = result_agents.get("pachiko", {}).get("result")
    god_result = result_agents.get("pachikamisama", {})
    god_evaluation = "／".join(
        _result_text(god_result.get(role), label)
        for role, label in (("honmei", "本命"), ("taikou", "対抗"), ("ana", "穴"))
        if god_result.get(role, {}).get("status") != "not_selected"
    ) or "パチ神様の結果データは未確定です。"
    return {
        "reflection_version": REFLECTION_VERSION,
        "prediction_date": prediction_date,
        "cutoff_date": cutoff_date,
        "generated_at": datetime.now().astimezone().isoformat(),
        "result_status": (result or {}).get("status", "pending"),
        "pachio": {
            "reason": _reason(agents.get("pachio", {}), "pachio", memory),
            "evaluation": _result_text(pachio_result, "パチおの本命"),
            "learning": _learning(agents.get("pachio", {}), "pachio", memory),
        },
        "pachiko": {
            "reason": _reason(agents.get("pachiko", {}), "pachiko", memory),
            "evaluation": _result_text(pachiko_result, "パチこの本命"),
            "learning": _learning(agents.get("pachiko", {}), "pachiko", memory),
        },
        "pachikamisama": {
            "reason": _reason(agents.get("pachikamisama", {}), "pachikamisama", memory),
            "evaluation": god_evaluation,
            "learning": _learning(agents.get("pachikamisama", {}), "pachikamisama", memory),
        },
    }


class ReflectionStore:
    def __init__(self, root: str | Path, mode: str = "production"):
        self.directory = Path(root) if mode == "production" else Path(root) / mode
        self.directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, prediction_date: str) -> Path:
        return self.directory / f"reflection_{normalize_date(prediction_date)}.json"

    def save(self, reflection: dict[str, Any]) -> Path:
        path = self.path_for(reflection["prediction_date"])
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".reflection.", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(reflection, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            if temporary:
                temporary.unlink(missing_ok=True)
            raise
        return path

    def load(self, prediction_date: str) -> dict[str, Any]:
        with self.path_for(prediction_date).open(encoding="utf-8") as handle:
            return json.load(handle)


def _as_of_experience(data_root: Path, through: str, mode: str) -> dict[str, Any]:
    builder = ExperienceBuilder(mode)
    predictions = data_root / "predictions"
    results = data_root / "results"
    for path in sorted(predictions.glob("prediction_*.json")):
        day = path.stem.removeprefix("prediction_")
        try:
            day = normalize_date(day)
            if day > through:
                continue
            prediction = PredictionStore(predictions).load(day)
        except (ValueError, PredictionError, OSError, json.JSONDecodeError):
            continue
        builder.register_prediction(prediction)
        try:
            result = ResultStore(results).load(day)
        except (ResultError, OSError, json.JSONDecodeError):
            continue
        builder.add_result(result)
    return builder.finalize()


def generate_reflection_for_date(data_root: str | Path, prediction_date: str, mode: str = "production") -> Path:
    data = Path(data_root)
    day = normalize_date(prediction_date)
    prediction = PredictionStore(data / "predictions").load(day)
    result = ResultStore(data / "results").load(day)
    experience = _as_of_experience(data, day, mode)
    return ReflectionStore(data / "reflection", mode).save(build_reflection(prediction, result, experience))


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Pachi Agents AI Reflection")
    parser.add_argument("--data-root", default=str(Path(__file__).with_name("data")))
    parser.add_argument("--prediction-date")
    parser.add_argument("--mode", choices=("production", "backtest"), default="production")
    args = parser.parse_args()
    data = Path(args.data_root)
    dates = [normalize_date(args.prediction_date)] if args.prediction_date else sorted(
        path.stem.removeprefix("prediction_") for path in (data / "predictions").glob("prediction_*.json")
    )
    generated = []
    for day in dates:
        try:
            generated.append(str(generate_reflection_for_date(data, day, args.mode)))
        except (ReflectionError, PredictionError, ResultError, OSError, ValueError, json.JSONDecodeError):
            continue
    print(json.dumps({"mode": args.mode, "generated": generated}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
