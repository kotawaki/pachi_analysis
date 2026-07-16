"""Phase 8 UI read model; no prediction or experience recalculation."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from .inputs import normalize_date
from .predictions import PredictionStore, PredictionError
from .results import ResultStore, ResultError

def _latest_locked(store: PredictionStore) -> dict[str, Any] | None:
    for path in sorted(store.directory.glob("prediction_*.json"), reverse=True):
        try:
            value = store.load(path.stem.removeprefix("prediction_"))
        except (PredictionError, ValueError, OSError, json.JSONDecodeError):
            continue
        if value.get("status") == "locked":
            return value
    return None

def _load_result(store: ResultStore, date: str | None) -> dict[str, Any] | None:
    if not date:
        return None
    try:
        return store.load(date)
    except (ResultError, ValueError, OSError, json.JSONDecodeError):
        return None

def _load_experience(root: Path, mode: str) -> dict[str, Any] | None:
    try:
        with (root / "experience" / mode / "experience.json").open(encoding="utf-8") as f:
            value = json.load(f)
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None

def load_dashboard_data(data_root: str | Path, mode: str = "production") -> dict[str, Any]:
    root = Path(data_root)
    prediction = _latest_locked(PredictionStore(root / "predictions"))
    date = normalize_date(prediction["prediction_date"]) if prediction else None
    return {"prediction": prediction, "result": _load_result(ResultStore(root / "results"), date),
            "experience": _load_experience(root, mode), "meta": {"mode": mode, "data_root": str(root)}}
