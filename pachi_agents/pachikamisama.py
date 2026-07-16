"""Phase 5: パチ神様のメタ統合。

パチ神様自身は台のOHLCや伝播を再分析せず、パチお・パチこの確定payloadを
順位、confidence、根拠の多様性の観点から統合する。過去成績による重み補正は
Phase 7まで行わない。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .inputs import normalize_date
from .predictions import PredictionStore


LOGIC_VERSION = "pachi_agents_pachikamisama_v1"


def _valid_agent(agent: Any) -> bool:
    return isinstance(agent, dict) and bool(agent.get("primary_machine")) and bool(agent.get("candidates"))


def _weights(pachio: dict[str, Any], pachiko: dict[str, Any]) -> dict[str, float]:
    """現在の入力充足度だけで重みを決める。過去成績は参照しない。"""
    pachio_ok = _valid_agent(pachio)
    pachiko_ok = _valid_agent(pachiko)
    if pachio_ok and pachiko_ok:
        # 現在のconfidence差だけを小さく反映し、片寄り過ぎないようにする。
        po = float(pachio.get("confidence") or 0.0)
        pk = float(pachiko.get("confidence") or 0.0)
        pachio_weight = min(0.6, max(0.4, 0.5 + (po - pk) * 0.1))
    elif pachio_ok:
        pachio_weight = 0.75
    elif pachiko_ok:
        pachio_weight = 0.25
    else:
        pachio_weight = 0.5
    return {
        "pachio": round(pachio_weight, 3),
        "pachiko": round(1.0 - pachio_weight, 3),
    }


def _rank_map(agent: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {}
    for rank, candidate in enumerate(agent.get("candidates", []), start=1):
        if not isinstance(candidate, dict) or not candidate.get("machine"):
            continue
        item = dict(candidate)
        item["rank"] = rank
        result[str(candidate["machine"]).zfill(3)] = item
    return result


def build_pachikamisama_agent(pachio: dict[str, Any], pachiko: dict[str, Any], *, top_n: int = 5) -> dict[str, Any]:
    """パチお・パチこのpayloadを説明可能なルールで統合する。"""
    weights = _weights(pachio, pachiko)
    pachi_o = _rank_map(pachio)
    pachi_k = _rank_map(pachiko)
    machines = set(pachi_o) | set(pachi_k)
    scored = []

    for machine in machines:
        po = pachi_o.get(machine)
        pk = pachi_k.get(machine)
        po_rank = po["rank"] if po else None
        pk_rank = pk["rank"] if pk else None
        po_conf = float(po.get("confidence") or 0.0) if po else 0.0
        pk_conf = float(pk.get("confidence") or 0.0) if pk else 0.0
        # 1位を最大評価し、5位までを順位に応じて減衰させる。
        po_rank_factor = 1.0 / po_rank if po_rank else 0.0
        pk_rank_factor = 1.0 / pk_rank if pk_rank else 0.0
        pachio_part = weights["pachio"] * po_conf * po_rank_factor
        pachiko_part = weights["pachiko"] * pk_conf * pk_rank_factor
        pachio_primary = str(pachio.get("primary_machine") or "").zfill(3)
        pachiko_primary = str(pachiko.get("primary_machine") or "").zfill(3)
        primary_agreement = bool(pachio_primary and pachio_primary == pachiko_primary)
        candidate_overlap = bool(po and pk)
        both_top3 = bool(candidate_overlap and po_rank <= 3 and pk_rank <= 3)
        # CROSS_AGENT_TOP5は本命の片側混入ではなく、両エージェントの
        # top-5集合に対象台が共通していることを表す。
        cross_top5 = bool(machine in set(list(pachi_o)[:5]) and machine in set(list(pachi_k)[:5]))
        diverse_support = bool(
            po and pk and po.get("reason_codes") and pk.get("reason_codes")
        )
        primary_agreement_bonus = 0.35 if primary_agreement else 0.0
        overlap_bonus = 0.25 if candidate_overlap else 0.0
        top3_bonus = 0.2 if both_top3 else 0.0
        cross_bonus = 0.2 if cross_top5 else 0.0
        diverse_bonus = 0.15 if diverse_support else 0.0
        score = round(
            pachio_part + pachiko_part + primary_agreement_bonus + overlap_bonus + top3_bonus + cross_bonus + diverse_bonus,
            4,
        )
        reason_codes = []
        if primary_agreement:
            reason_codes.append("PRIMARY_AGREEMENT")
        else:
            reason_codes.append("AGENT_DISAGREEMENT")
        if candidate_overlap:
            reason_codes.append("CANDIDATE_OVERLAP")
        if both_top3:
            reason_codes.append("BOTH_TOP3")
        if cross_top5:
            reason_codes.append("CROSS_AGENT_TOP5")
        if po_conf * weights["pachio"] + pk_conf * weights["pachiko"] >= 0.65:
            reason_codes.append("HIGH_COMBINED_CONFIDENCE")
        if pachio_part > pachiko_part:
            reason_codes.append("PACHIO_WEIGHTED")
        elif pachiko_part > pachio_part:
            reason_codes.append("PACHIKO_WEIGHTED")
        if diverse_support:
            reason_codes.append("DIVERSE_SIGNAL_SUPPORT")
        if not reason_codes:
            reason_codes.append("LOW_EVIDENCE")
        scored.append({
            "machine": machine,
            "score": score,
            "confidence": round(min(0.95, max(0.05, score / 2)), 3),
            "signals": {
                "pachio_rank": po_rank,
                "pachiko_rank": pk_rank,
                "pachio_score": po.get("score") if po else None,
                "pachiko_score": pk.get("score") if pk else None,
                "pachio_confidence": po_conf if po else None,
                "pachiko_confidence": pk_conf if pk else None,
                "pachio_contribution": round(pachio_part, 4),
                "pachiko_contribution": round(pachiko_part, 4),
                "primary_agreement": primary_agreement,
                "candidate_overlap": candidate_overlap,
                "both_top3": both_top3,
                "cross_agent_top5": cross_top5,
                "diverse_signal_support": diverse_support,
            },
            "reason_codes": reason_codes,
        })

    scored.sort(key=lambda item: (item["score"], item["machine"]), reverse=True)
    if not scored:
        return {
            "logic_version": LOGIC_VERSION,
            "honmei": None,
            "taikou": None,
            "ana": None,
            "confidence": 0.0,
            "candidates": [],
            "agent_weights": weights,
            "signals": {"candidate_count": 0},
            "reason_codes": ["INSUFFICIENT_AGENT_INPUT"],
            "comment": "パチお・パチこの候補入力が不足しているため、啓示を出せません。",
        }

    top = scored[:top_n]
    honmei = top[0]
    taikou = top[1] if len(top) > 1 else None
    ana = next((item for item in top[2:] if not (item["machine"] in pachi_o and item["machine"] in pachi_k)), None)
    ana = ana or (top[2] if len(top) > 2 else taikou)
    return {
        "logic_version": LOGIC_VERSION,
        "honmei": honmei["machine"],
        "taikou": taikou["machine"] if taikou else None,
        "ana": ana["machine"] if ana else None,
        "confidence": honmei["confidence"],
        "candidates": top,
        "agent_weights": weights,
        "signals": honmei["signals"],
        "reason_codes": honmei["reason_codes"],
        "comment": f"{honmei['machine']}番を、パチお・パチこの順位、confidence、根拠の一致度から本命としました。",
    }


def generate_pachikamisama_prediction(
    store: PredictionStore,
    *,
    prediction_date: str,
    cutoff_date: str,
    pachio: dict[str, Any],
    pachiko: dict[str, Any],
    input_manifest: list[dict[str, Any]] | None = None,
    experience_agent_weights: dict[str, float] | None = None,
    experience_adjustment: dict[str, Any] | None = None,
    top_n: int = 5,
) -> Path:
    """2エージェントのpayloadを統合し、完全な予測をlocked保存する。"""
    target = normalize_date(prediction_date)
    cutoff = normalize_date(cutoff_date)
    god = build_pachikamisama_agent(pachio, pachiko, top_n=top_n)
    if experience_agent_weights is not None:
        god["base_agent_weights"] = dict(god.get("agent_weights", {}))
        god["experience_adjusted_weights"] = dict(experience_agent_weights)
        god["agent_weights"] = dict(experience_agent_weights)
    if experience_adjustment is not None:
        god["experience_adjustment"] = dict(experience_adjustment)
    payload = {
        "prediction_date": target,
        "cutoff_date": cutoff,
        "created_at": datetime.now().astimezone().isoformat(),
        "status": "draft",
        "logic_version": "pachi_agents_v1",
        "input_manifest": input_manifest or [],
        "agents": {
            "pachio": pachio,
            "pachiko": pachiko,
            "pachikamisama": god,
        },
    }
    store.save(payload)
    return store.lock(target)
