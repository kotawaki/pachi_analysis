"""Candidate provenance helpers for Pachi Agents.

The functions in this module only compare the already-produced candidate
lists. They do not rescore candidates and therefore cannot change prediction
ordering or as-of behavior.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


ORIGIN_TYPES = ("PACHIO_ONLY", "PACHIKO_ONLY", "BOTH", "OTHER")


def _machine(value: Any) -> str | None:
    if isinstance(value, dict):
        value = value.get("machine")
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    try:
        return f"{int(text):03d}"
    except ValueError:
        return text


def _rank_map(agent: dict[str, Any] | None) -> dict[str, int]:
    result: dict[str, int] = {}
    for rank, candidate in enumerate((agent or {}).get("candidates", []), start=1):
        machine = _machine(candidate)
        if machine and machine not in result:
            result[machine] = rank
    return result


def candidate_origin(
    machine: Any,
    pachio: dict[str, Any] | None,
    pachiko: dict[str, Any] | None,
) -> dict[str, Any]:
    machine_key = _machine(machine)
    pachio_ranks = _rank_map(pachio)
    pachiko_ranks = _rank_map(pachiko)
    pachio_rank = pachio_ranks.get(machine_key) if machine_key else None
    pachiko_rank = pachiko_ranks.get(machine_key) if machine_key else None
    pachio_primary = bool(machine_key and machine_key == _machine((pachio or {}).get("primary_machine")))
    pachiko_primary = bool(machine_key and machine_key == _machine((pachiko or {}).get("primary_machine")))
    pachio_selected = pachio_rank is not None
    pachiko_selected = pachiko_rank is not None
    if pachio_selected and pachiko_selected:
        origin_type = "BOTH"
    elif pachio_selected:
        origin_type = "PACHIO_ONLY"
    elif pachiko_selected:
        origin_type = "PACHIKO_ONLY"
    else:
        origin_type = "OTHER"
    agents = []
    if pachio_selected:
        agents.append("pachio")
    if pachiko_selected:
        agents.append("pachiko")
    return {
        "agents": agents,
        "pachio": {
            "selected": pachio_selected,
            "rank": pachio_rank,
            "is_primary": pachio_primary,
        },
        "pachiko": {
            "selected": pachiko_selected,
            "rank": pachiko_rank,
            "is_primary": pachiko_primary,
        },
        "origin_type": origin_type,
    }


def enrich_pachikamisama(
    god: dict[str, Any],
    pachio: dict[str, Any] | None,
    pachiko: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return a copy with per-candidate and role-level provenance."""
    result = deepcopy(god or {})
    enriched = []
    for candidate in result.get("candidates", []):
        item = deepcopy(candidate)
        item["candidate_origin"] = candidate_origin(item.get("machine"), pachio, pachiko)
        enriched.append(item)
    result["candidates"] = enriched
    result["role_origins"] = {
        role: candidate_origin(result.get(role), pachio, pachiko)
        if result.get(role) is not None
        else candidate_origin(None, pachio, pachiko)
        for role in ("honmei", "taikou", "ana")
    }
    return result


def enrich_prediction(prediction: dict[str, Any]) -> dict[str, Any]:
    """Compatibility read model; never writes to the source prediction."""
    result = deepcopy(prediction)
    agents = result.setdefault("agents", {})
    agents["pachikamisama"] = enrich_pachikamisama(
        agents.get("pachikamisama", {}), agents.get("pachio", {}), agents.get("pachiko", {})
    )
    return result


def role_origin(prediction: dict[str, Any], role: str) -> dict[str, Any]:
    god = prediction.get("agents", {}).get("pachikamisama", {})
    stored = god.get("role_origins", {}).get(role)
    if isinstance(stored, dict) and "origin_type" in stored:
        return deepcopy(stored)
    return candidate_origin(
        god.get(role), prediction.get("agents", {}).get("pachio", {}), prediction.get("agents", {}).get("pachiko", {})
    )
