"""Generate GitHub Pages JSON views from Phase 2/6/7 primary data.

The files under docs/pachi_agents/data are disposable derived copies. This
command never edits prediction, result, or experience primary files.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .predictions import PredictionError, PredictionStore
from .results import ResultError, ResultStore
from .candidate_origin import enrich_prediction


def _read_json(path: Path, default: Any) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def _latest_prediction(directory: Path) -> dict[str, Any] | None:
    store = PredictionStore(directory)
    for path in sorted(directory.glob("prediction_*.json"), reverse=True):
        try:
            value = store.load(path.stem.removeprefix("prediction_"))
        except (PredictionError, OSError, ValueError, json.JSONDecodeError):
            continue
        if value.get("status") == "locked":
            return value
    return None


def _result_for(directory: Path, date: str | None) -> dict[str, Any] | None:
    if not date:
        return None
    try:
        return ResultStore(directory).load(date)
    except (ResultError, OSError, ValueError, json.JSONDecodeError):
        return None


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=".web.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        if temporary:
            temporary.unlink(missing_ok=True)
        raise


def export_web(data_root: str | Path, output_root: str | Path, mode: str = "production") -> dict[str, Any]:
    data = Path(data_root)
    output = Path(output_root)
    predictions_dir = data / "predictions"
    results_dir = data / "results"
    latest = _latest_prediction(predictions_dir) if predictions_dir.exists() else None
    if latest is not None:
        latest = enrich_prediction(latest)
    date = latest.get("prediction_date") if latest else None
    latest_result = _result_for(results_dir, date) if results_dir.exists() else None
    experience = _read_json(data / "experience" / mode / "experience.json", None)

    history: list[dict[str, Any]] = []
    if predictions_dir.exists():
        result_store = ResultStore(results_dir) if results_dir.exists() else None
        reflections = {item.get("date"): item for item in (experience or {}).get("reflections", [])}
        for path in sorted(predictions_dir.glob("prediction_*.json")):
            day = path.stem.removeprefix("prediction_")
            try:
                prediction = PredictionStore(predictions_dir).load(day)
            except (PredictionError, OSError, ValueError, json.JSONDecodeError):
                continue
            if prediction.get("status") != "locked":
                continue
            result = None
            if result_store:
                result = _result_for(results_dir, day)
            history.append({"prediction_date": day, "prediction": enrich_prediction(prediction), "result": result, "reflection": reflections.get(day)})

    _atomic_json(output / "latest_prediction.json", latest)
    _atomic_json(output / "latest_result.json", latest_result)
    _atomic_json(output / "experience.json", experience)
    _atomic_json(output / "history.json", history)
    return {"prediction": latest, "result": latest_result, "experience": experience, "history": history}


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Pachi Agents data for GitHub Pages")
    parser.add_argument("--data-root", default=str(Path(__file__).with_name("data")))
    parser.add_argument("--output-root", default=str(Path(__file__).parents[1] / "docs" / "pachi_agents" / "data"))
    parser.add_argument("--mode", choices=("production", "backtest"), default="production")
    args = parser.parse_args()
    result = export_web(args.data_root, args.output_root, args.mode)
    print(f"exported: predictions={'yes' if result['prediction'] else 'no'}, history={len(result['history'])}")


if __name__ == "__main__":
    main()
