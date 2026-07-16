"""Pachi Agents Phase 2: 予測JSONの保存・ロック・読み込み。

既存の分析データやスクリプトには書き込まない。予測ファイルは同一ディレクトリの
一時ファイルへ書き、fsync後にos.replaceで確定する。通常APIでは既存ファイルを
置換できないため、locked予測の再生成を防止できる。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .inputs import AsOfViolation, assert_as_of, normalize_date


REQUIRED_FIELDS = (
    "prediction_date",
    "cutoff_date",
    "created_at",
    "status",
    "logic_version",
    "input_manifest",
    "agents",
)
STATUSES = {"draft", "locked"}
AGENT_KEYS = ("pachio", "pachiko", "pachikamisama")


class PredictionError(Exception):
    """Pachi Agents予測ストアの基底例外。"""


class PredictionAlreadyExists(PredictionError):
    """対象日付の予測が既に存在する。"""


class PredictionNotFound(PredictionError):
    """対象日付の予測が存在しない。"""


class PredictionCorrupt(PredictionError):
    """予測ファイルがJSONとして壊れている。"""


class PredictionSchemaError(PredictionError):
    """予測ファイルのschemaが不正。"""


def sha256_file(path: str | Path) -> str:
    """ファイルのSHA-256を返す。"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_manifest_entry(
    path: str | Path,
    *,
    kind: str,
    data_date: str | None = None,
    include_hash: bool = True,
) -> dict[str, Any]:
    """予測入力を追跡するmanifest項目を作る。

    ``data_date`` は必須ではないが、日付を持つ入力では指定を推奨する。
    cutoff検証は保存時にも再実行される。
    """
    source = Path(path)
    entry: dict[str, Any] = {
        "kind": str(kind),
        "path": str(source),
    }
    if data_date is not None:
        entry["date"] = normalize_date(data_date)
    if include_hash:
        entry["sha256"] = sha256_file(source)
    return entry


def _manifest_dates(value: Any) -> list[str]:
    """manifest内のdate/datesフィールドを正規化して返す。"""
    if isinstance(value, list):
        return [normalize_date(str(item)) for item in value]
    if isinstance(value, (str, datetime)):
        return [normalize_date(str(value))]
    return []


def _validate_manifest(manifest: Any, prediction_date: str, cutoff_date: str) -> None:
    if not isinstance(manifest, list):
        raise PredictionSchemaError("input_manifestは配列で指定してください")
    for index, entry in enumerate(manifest):
        if not isinstance(entry, dict):
            raise PredictionSchemaError(f"input_manifest[{index}]がオブジェクトではありません")
        if not entry.get("kind") or not entry.get("path"):
            raise PredictionSchemaError(f"input_manifest[{index}]にkind/pathが必要です")
        dates = []
        if "date" in entry:
            dates.extend(_manifest_dates(entry["date"]))
        if "dates" in entry:
            dates.extend(_manifest_dates(entry["dates"]))
        for data_date in dates:
            # cutoffより後の入力は、D日の実績でなくても予測には使えない。
            assert_as_of(data_date, cutoff_date)
            if data_date >= prediction_date:
                raise AsOfViolation(
                    f"prediction_date以降のmanifest入力: data_date={data_date}, "
                    f"prediction_date={prediction_date}"
                )


def validate_prediction(payload: Any) -> dict[str, Any]:
    """予測payloadを検証し、JSON保存可能なdictとして返す。"""
    if not isinstance(payload, dict):
        raise PredictionSchemaError("prediction payloadはオブジェクトで指定してください")
    missing = [field for field in REQUIRED_FIELDS if field not in payload]
    if missing:
        raise PredictionSchemaError(f"必須フィールド不足: {', '.join(missing)}")

    result = dict(payload)
    prediction_date = normalize_date(result["prediction_date"])
    cutoff_date = normalize_date(result["cutoff_date"])
    if cutoff_date >= prediction_date:
        raise AsOfViolation(
            f"cutoff_dateはprediction_dateより前である必要があります: "
            f"cutoff_date={cutoff_date}, prediction_date={prediction_date}"
        )
    if not isinstance(result["created_at"], str) or not result["created_at"].strip():
        raise PredictionSchemaError("created_atは空でない文字列が必要です")
    if result["status"] not in STATUSES:
        raise PredictionSchemaError(f"statusは{sorted(STATUSES)}のいずれかです")
    if not isinstance(result["logic_version"], str) or not result["logic_version"].strip():
        raise PredictionSchemaError("logic_versionは空でない文字列が必要です")
    if not isinstance(result["agents"], dict):
        raise PredictionSchemaError("agentsはオブジェクトで指定してください")
    if not isinstance(result["input_manifest"], list):
        raise PredictionSchemaError("input_manifestは配列で指定してください")

    # 将来のキャラクター別logic_versionを許容するため、agent payloadは自由形にする。
    result["agents"] = {key: result["agents"].get(key, {}) for key in AGENT_KEYS} | {
        key: value for key, value in result["agents"].items() if key not in AGENT_KEYS
    }
    result["prediction_date"] = prediction_date
    result["cutoff_date"] = cutoff_date
    _validate_manifest(result["input_manifest"], prediction_date, cutoff_date)
    return result


class PredictionStore:
    """prediction_YYYYMMDD.jsonを管理するストア。"""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, prediction_date: str) -> Path:
        return self.directory / f"prediction_{normalize_date(prediction_date)}.json"

    def save(self, payload: dict[str, Any]) -> Path:
        """新規予測を保存する。既存ファイルはstatusに関係なく拒否する。"""
        checked = validate_prediction(payload)
        target = self.path_for(checked["prediction_date"])
        if target.exists():
            raise PredictionAlreadyExists(f"予測は既に存在します: {target.name}")
        self._atomic_write(target, checked, replace=False)
        return target

    def lock(self, prediction_date: str) -> Path:
        """draft予測をlockedへ一度だけ遷移させる。"""
        payload = self.load(prediction_date)
        if payload["status"] == "locked":
            raise PredictionAlreadyExists(f"予測は既にlockedです: {self.path_for(prediction_date).name}")
        payload["status"] = "locked"
        checked = validate_prediction(payload)
        self._atomic_write(self.path_for(prediction_date), checked, replace=True)
        return self.path_for(prediction_date)

    def load(self, prediction_date: str) -> dict[str, Any]:
        """保存済み予測を読み込む。欠損・壊れ・schema不正を別例外にする。"""
        path = self.path_for(prediction_date)
        if not path.exists():
            raise PredictionNotFound(f"予測が存在しません: {path.name}")
        try:
            with path.open(encoding="utf-8") as handle:
                payload = json.load(handle)
        except json.JSONDecodeError as exc:
            raise PredictionCorrupt(f"JSONが壊れています: {path.name}") from exc
        try:
            return validate_prediction(payload)
        except (AsOfViolation, PredictionSchemaError):
            raise
        except (TypeError, ValueError, KeyError) as exc:
            raise PredictionSchemaError(f"schemaが不正です: {path.name}") from exc

    @staticmethod
    def _atomic_write(target: Path, payload: dict[str, Any], *, replace: bool) -> None:
        if target.exists() and not replace:
            raise PredictionAlreadyExists(f"予測は既に存在します: {target.name}")
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, target)
        except Exception:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise
