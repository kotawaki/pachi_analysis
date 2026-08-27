"""Phase 6: locked predictionと実績データの答え合わせ。"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .inputs import load_daily_ohlc_rows_for_date, normalize_date
from .predictions import PredictionAlreadyExists, PredictionNotFound, PredictionStore


RESULT_STATUSES = {"pending", "evaluated", "incomplete", "closed"}


class ResultError(Exception):
    """答え合わせ結果ストアの基底例外。"""


class PredictionNotLocked(ResultError):
    """predictionは存在するがlockedではない。"""


class ResultAlreadyExists(ResultError):
    """同じprediction_dateのresultが既に存在する。"""


class ResultNotFound(ResultError):
    """resultが存在しない。"""


class ResultCorrupt(ResultError):
    """result JSONが壊れている。"""


class ResultSchemaError(ResultError):
    """result JSONのschemaが不正。"""


def _machine(value: Any) -> str:
    text = str(value or "").strip()
    try:
        number = int(text)
    except ValueError:
        return text
    return f"{number:03d}" if number < 1000 else str(number)


def _field(row: dict[str, Any], *names: str) -> str:
    lowered = {str(key).lower(): value for key, value in row.items()}
    for name in names:
        if name.lower() in lowered:
            return str(lowered[name.lower()]).strip()
    return ""


def _number(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").replace("+", "").strip())
    except (TypeError, ValueError):
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate(payload: Any) -> dict[str, Any]:
    required = ("prediction_date", "evaluated_at", "result_version", "source_manifest", "agents", "status")
    if not isinstance(payload, dict):
        raise ResultSchemaError("resultはオブジェクトで指定してください")
    missing = [key for key in required if key not in payload]
    if missing:
        raise ResultSchemaError(f"result必須フィールド不足: {', '.join(missing)}")
    try:
        prediction_date = normalize_date(payload["prediction_date"])
    except (TypeError, ValueError) as exc:
        raise ResultSchemaError("prediction_dateが不正です") from exc
    if payload["status"] not in RESULT_STATUSES:
        raise ResultSchemaError(f"statusは{sorted(RESULT_STATUSES)}のいずれかです")
    if not isinstance(payload["source_manifest"], list):
        raise ResultSchemaError("source_manifestは配列で指定してください")
    if not isinstance(payload["agents"], dict):
        raise ResultSchemaError("agentsはオブジェクトで指定してください")
    result = dict(payload)
    result["prediction_date"] = prediction_date
    return result


class ResultStore:
    """result_YYYYMMDD.jsonを原子的に保存するストア。"""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, prediction_date: str) -> Path:
        return self.directory / f"result_{normalize_date(prediction_date)}.json"

    def save(self, payload: dict[str, Any]) -> Path:
        checked = _validate(payload)
        target = self.path_for(checked["prediction_date"])
        if target.exists():
            raise ResultAlreadyExists(f"結果は既に存在します: {target.name}")
        self._atomic_write(target, checked)
        return target

    def load(self, prediction_date: str) -> dict[str, Any]:
        path = self.path_for(prediction_date)
        if not path.exists():
            raise ResultNotFound(f"結果が存在しません: {path.name}")
        try:
            with path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ResultCorrupt(f"結果JSONが壊れています: {path.name}") from exc
        return _validate(payload)

    @staticmethod
    def _atomic_write(target: Path, payload: dict[str, Any]) -> None:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=target.parent,
                prefix=f".{target.name}.", suffix=".tmp", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except Exception:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            raise


def _actual_map(rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    actual: dict[str, dict[str, Any]] = {}
    sources: dict[str, Path] = {}
    for row in rows:
        machine = _machine(_field(row, "Machine", "machine"))
        opening = _number(_field(row, "Open", "open"))
        high = _number(_field(row, "High", "high"))
        low = _number(_field(row, "Low", "low"))
        close = _number(_field(row, "Close", "close"))
        if not machine:
            continue
        if None in (opening, high, low, close):
            actual[machine] = {"machine": machine, "status": "missing"}
        else:
            actual[machine] = {
                "machine": machine,
                "status": "available",
                "open": opening,
                "high": high,
                "low": low,
                "close": close,
                "direction": "positive" if close > opening else "non_positive",
                "max_up": high - opening,
                "max_down": low - opening,
                "outcome": "success" if close > opening else "failure",
            }
        if row.get("source_path"):
            sources[machine] = Path(str(row["source_path"]))
    manifest = []
    for path in sorted(set(sources.values())):
        manifest.append({"kind": "daily_ohlc", "path": str(path), "sha256": _sha256(path)})
    return actual, manifest


def _selection(machine: Any, actual: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if isinstance(machine, dict):
        machine = machine.get("machine")
    normalized = _machine(machine) if machine is not None else None
    if not normalized:
        return {"machine": None, "status": "not_selected", "outcome": None}
    item = actual.get(normalized)
    if not item or item.get("status") != "available":
        return {"machine": normalized, "status": "missing", "outcome": None}
    return dict(item)


def evaluate_prediction(
    prediction_store: PredictionStore,
    result_store: ResultStore,
    *,
    prediction_date: str,
    ohlc_root: str | Path,
) -> Path:
    """locked predictionとD日のOHLCを照合し、predictionを変更せずresultを保存する。"""
    day = normalize_date(prediction_date)
    prediction = prediction_store.load(day)
    if prediction.get("status") != "locked":
        raise PredictionNotLocked(f"predictionがlockedではありません: {day}")

    rows = load_daily_ohlc_rows_for_date(ohlc_root, day)
    actual, source_manifest = _actual_map(rows)
    pachio = prediction.get("agents", {}).get("pachio", {})
    pachiko = prediction.get("agents", {}).get("pachiko", {})
    god = prediction.get("agents", {}).get("pachikamisama", {})
    pachio_result = _selection(pachio.get("primary_machine"), actual)
    pachiko_result = _selection(pachiko.get("primary_machine"), actual)
    god_results = {
        role: _selection(god.get(role), actual)
        for role in ("honmei", "taikou", "ana")
    }
    selections = [
        pachio_result.get("machine"),
        pachiko_result.get("machine"),
        god_results["honmei"].get("machine"),
        god_results["taikou"].get("machine"),
        god_results["ana"].get("machine"),
    ]
    selected = [machine for machine in selections if machine]
    available_count = sum(_machine(machine) in actual and actual[_machine(machine)].get("status") == "available" for machine in selected)
    if not selected or not rows:
        status = "pending"
    elif available_count < len(selected):
        status = "incomplete"
    else:
        status = "evaluated"

    payload = {
        "prediction_date": day,
        "evaluated_at": datetime.now().astimezone().isoformat(),
        "result_version": "pachi_agents_result_v1",
        "status": status,
        "source_manifest": source_manifest,
        "agents": {
            "pachio": {
                "primary_machine": pachio.get("primary_machine"),
                "result": pachio_result,
            },
            "pachiko": {
                "primary_machine": pachiko.get("primary_machine"),
                "result": pachiko_result,
            },
            "pachikamisama": {
                "honmei": god_results["honmei"],
                "taikou": god_results["taikou"],
                "ana": god_results["ana"],
                "honmei_outcome": god_results["honmei"].get("outcome"),
            },
        },
    }
    return result_store.save(payload)


def record_closed_day(
    prediction_store: PredictionStore,
    result_store: ResultStore,
    *,
    prediction_date: str,
    reason_code: str = "STORE_CLOSED",
) -> Path:
    """Record a closed day without evaluating or scoring its prediction."""
    day = normalize_date(prediction_date)
    prediction = prediction_store.load(day)
    if prediction.get("status") != "locked":
        raise PredictionNotLocked(f"predictionがlockedではありません: {day}")
    payload = {
        "prediction_date": day,
        "evaluated_at": datetime.now().astimezone().isoformat(),
        "result_version": "pachi_agents_result_v1",
        "status": "closed",
        "evaluated": False,
        "closed": True,
        "reason_code": reason_code,
        "success": False,
        "failure": False,
        "source_manifest": [],
        "agents": {
            "pachio": {"primary_machine": prediction.get("agents", {}).get("pachio", {}).get("primary_machine"), "result": None},
            "pachiko": {"primary_machine": prediction.get("agents", {}).get("pachiko", {}).get("primary_machine"), "result": None},
            "pachikamisama": {"honmei": None, "taikou": None, "ana": None, "honmei_outcome": None},
        },
    }
    return result_store.save(payload)
