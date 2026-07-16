"""Phase 9.5: bounded, as-of experience feedback for backtests."""
from __future__ import annotations

import itertools
from copy import deepcopy
from typing import Any


MIN_SAMPLE_DEFAULT = 5
MAX_SCORE_ADJUSTMENT_RATIO = 0.20
MAX_WEIGHT_DELTA = 0.05


def _stat(memory: dict[str, Any], agent: str, key: str, section: str = "reason_codes") -> dict[str, Any] | None:
    return memory.get("agents", {}).get(agent, {}).get(section, {}).get(key)


def _usable(stat: dict[str, Any] | None, minimum_sample: int) -> bool:
    return bool(stat and int(stat.get("evaluated_count", 0)) >= minimum_sample and _rate(stat) is not None)


def _rate(stat: dict[str, Any] | None) -> float | None:
    if not stat:
        return None
    if stat.get("success_rate") is not None:
        return float(stat["success_rate"])
    if stat.get("win_rate") is not None:
        return float(stat["win_rate"])
    count = int(stat.get("evaluated_count", 0))
    return float(stat.get("success", 0)) / count if count else None


def _codes(candidate: dict[str, Any]) -> list[str]:
    return sorted({str(code) for code in candidate.get("reason_codes", []) if code})


def adjust_agent_candidates(
    agent: dict[str, Any],
    memory: dict[str, Any] | None,
    agent_name: str,
    *,
    minimum_sample: int = MIN_SAMPLE_DEFAULT,
) -> dict[str, Any]:
    """Apply small condition-level adjustments and rerank candidates.

    The adjustment is proportional to ``success_rate - 0.5`` and capped at
    20% of the base score. No usable statistic means a zero adjustment.
    """
    result = deepcopy(agent)
    candidates = []
    memory = memory or {}
    for original in agent.get("candidates", []):
        candidate = deepcopy(original)
        base = float(candidate.get("score") or 0.0)
        reason_adjustment = 0.0
        combo_adjustment = 0.0
        confidence_adjustment = 0.0
        used: list[str] = []
        codes = _codes(candidate)
        for code in codes:
            stat = _stat(memory, agent_name, code)
            if _usable(stat, minimum_sample):
                reason_adjustment += base * 0.05 * (_rate(stat) - 0.5) * 2
                used.append(code)
        for pair in itertools.combinations(codes, 2):
            key = "+".join(pair)
            stat = _stat(memory, agent_name, key, "reason_combinations")
            if _usable(stat, minimum_sample):
                combo_adjustment += base * 0.03 * (_rate(stat) - 0.5) * 2
                used.append(key)
        band = _confidence_band(float(candidate.get("confidence") or 0.0))
        stat = _stat(memory, agent_name, band, "confidence_bands")
        if _usable(stat, minimum_sample):
            confidence_adjustment = base * 0.02 * (_rate(stat) - 0.5) * 2
            used.append(f"confidence:{band}")
        raw_adjustment = reason_adjustment + combo_adjustment + confidence_adjustment
        cap = max(abs(base) * MAX_SCORE_ADJUSTMENT_RATIO, 0.001) if base else 0.0
        adjustment = max(-cap, min(cap, raw_adjustment))
        final = round(base + adjustment, 4)
        candidate["score"] = final
        candidate["experience_adjustment"] = {
            "base_score": base,
            "reason_adjustment": round(reason_adjustment, 4),
            "combo_adjustment": round(combo_adjustment, 4),
            "confidence_adjustment": round(confidence_adjustment, 4),
            "adjustment_cap": round(cap, 4),
            "final_score": final,
            "experience_sample_count": sum(int(_stat(memory, agent_name, key).get("evaluated_count", 0)) for key in used if ":" not in key and _stat(memory, agent_name, key)),
            "used_stats": used,
        }
        candidates.append(candidate)
    candidates.sort(key=lambda item: (float(item.get("score") or 0.0), str(item.get("machine", ""))), reverse=True)
    result["candidates"] = candidates
    if candidates:
        primary = candidates[0]
        result["primary_machine"] = primary.get("machine")
        result["confidence"] = primary.get("confidence", result.get("confidence", 0.0))
        result["signals"] = primary.get("signals", {})
        result["reason_codes"] = primary.get("reason_codes", [])
        result["experience_adjustment"] = primary.get("experience_adjustment")
    return result


def _confidence_band(value: float) -> str:
    if value < 0.5:
        return "lt_0_50"
    if value < 0.7:
        return "0_50_0_70"
    if value < 0.85:
        return "0_70_0_85"
    return "gte_0_85"


def adjust_god_weights(
    base_weights: dict[str, Any],
    pachio: dict[str, Any],
    pachiko: dict[str, Any],
    memory: dict[str, Any] | None,
    *,
    god: dict[str, Any] | None = None,
    minimum_sample: int = MIN_SAMPLE_DEFAULT,
) -> tuple[dict[str, float], dict[str, Any]]:
    """Move weights slightly toward agents with sufficient prior evidence."""
    base_pachio = float(base_weights.get("pachio") or 0.5)
    base_pachiko = float(base_weights.get("pachiko") or (1.0 - base_pachio))
    pachio_summary = (memory or {}).get("agents", {}).get("pachio", {}).get("summary", {})
    pachiko_summary = (memory or {}).get("agents", {}).get("pachiko", {}).get("summary", {})
    pachio_rate = _rate(pachio_summary)
    pachiko_rate = _rate(pachiko_summary)
    pachio_usable = int(pachio_summary.get("evaluated_count", 0)) >= minimum_sample and pachio_rate is not None
    pachiko_usable = int(pachiko_summary.get("evaluated_count", 0)) >= minimum_sample and pachiko_rate is not None
    delta = 0.0
    used: list[str] = []
    condition_rates: dict[str, float] = {}
    condition_samples: list[int] = []
    if pachio_usable and pachiko_usable:
        delta += max(-MAX_WEIGHT_DELTA, min(MAX_WEIGHT_DELTA, 0.10 * (pachiko_rate - pachio_rate)))
        used.extend(["pachio_summary", "pachiko_summary"])
    god_details = (memory or {}).get("pachikamisama", {})
    condition_stats = god_details.get("conditions", {})
    for code in set((god or {}).get("reason_codes", [])):
        stat = condition_stats.get(code)
        rate = _rate(stat) if _usable(stat, minimum_sample) else None
        if rate is None:
            continue
        condition_rates[code] = round(rate, 4)
        condition_samples.append(int(stat.get("evaluated_count", 0)))
        used.append(f"condition:{code}")
        # A successful integration condition reinforces the current dominant
        # side slightly; a poor one pulls a dominant weight toward balance.
        if base_pachiko >= 0.6:
            delta += 0.01 if rate >= 0.5 else -0.01
        elif base_pachio >= 0.6:
            delta += -0.01 if rate >= 0.5 else 0.01
    base_pattern = "pachiko_dominant" if base_pachiko >= 0.6 else "pachio_dominant" if base_pachio >= 0.6 else "balanced"
    pattern_stat = god_details.get("weight_patterns", {}).get(base_pattern)
    pattern_rate = _rate(pattern_stat) if _usable(pattern_stat, minimum_sample) else None
    if pattern_rate is not None:
        condition_samples.append(int(pattern_stat.get("evaluated_count", 0)))
        used.append(f"weight_pattern:{base_pattern}")
        if base_pachiko >= 0.6:
            delta += 0.01 if pattern_rate >= 0.5 else -0.01
        elif base_pachio >= 0.6:
            delta += -0.01 if pattern_rate >= 0.5 else 0.01
    delta = max(-MAX_WEIGHT_DELTA, min(MAX_WEIGHT_DELTA, delta))
    adjusted_pachiko = max(0.0, min(1.0, base_pachiko + delta))
    adjusted = {"pachio": round(1.0 - adjusted_pachiko, 4), "pachiko": round(adjusted_pachiko, 4)}
    return adjusted, {
        "base_agent_weights": {"pachio": round(base_pachio, 4), "pachiko": round(base_pachiko, 4)},
        "experience_adjusted_weights": adjusted,
        "weight_delta": round(adjusted_pachiko - base_pachiko, 4),
        "experience_sample_count": max(condition_samples + [int(pachio_summary.get("evaluated_count", 0)), int(pachiko_summary.get("evaluated_count", 0))]),
        "used_stats": used,
        "condition_rates": condition_rates,
        "weight_pattern": base_pattern,
        "weight_pattern_rate": pattern_rate,
        "max_weight_delta": MAX_WEIGHT_DELTA,
    }
