"""Pachi Agents Phase 1: 既存データの読み取りアダプター。

このモジュールは既存の分析スクリプトや履歴ファイルを書き換えない。
予測生成側は ``cutoff_date`` を必ず指定し、cutoff より後の日付を入力しない。

経験記憶は Phase 7 で次のように分離する前提とする::

    experience/production/...
    experience/backtest/...

本モジュールは経験記憶を更新しない。
"""

from __future__ import annotations

import csv
import json
from datetime import date as Date
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


class AsOfViolation(ValueError):
    """指定された cutoff より未来のデータを読もうとした。"""


def normalize_date(value: str | Date) -> str:
    """YYYYMMDD に正規化し、日付として妥当性を検証する。"""
    text = value.strftime("%Y%m%d") if isinstance(value, Date) else str(value).strip()
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"日付はYYYYMMDDで指定してください: {value!r}")
    datetime.strptime(text, "%Y%m%d").date()
    return text


def assert_as_of(data_date: str, cutoff_date: str | Date | None) -> None:
    """data_date が cutoff_date 以前であることを検証する。"""
    if cutoff_date is None:
        return
    data = normalize_date(data_date)
    cutoff = normalize_date(cutoff_date)
    if data > cutoff:
        raise AsOfViolation(f"未来データを検出: data_date={data}, cutoff_date={cutoff}")


def _date_dirs(root: Path) -> Iterable[tuple[str, Path]]:
    if not root.exists():
        return
    for path in sorted(root.iterdir()):
        if path.is_dir() and len(path.name) == 8 and path.name.isdigit():
            try:
                normalize_date(path.name)
            except ValueError:
                continue
            yield path.name, path


def available_analyze_dates(root: str | Path) -> list[str]:
    """csv/analyze 配下の、analyze CSVが存在する日付一覧を返す。"""
    analyze_root = Path(root)
    result = []
    for day, path in _date_dirs(analyze_root):
        if any(path.glob("*_analyze.csv")):
            result.append(day)
    return result


def available_snapshot_dates(root: str | Path) -> list[str]:
    """csv/replay 配下の snapshot JSONの日付一覧を返す。"""
    replay_root = Path(root)
    if not replay_root.exists():
        return []
    result = []
    for path in sorted(replay_root.glob("*_snapshot.json")):
        stem = path.stem.removesuffix("_snapshot")
        try:
            result.append(normalize_date(stem))
        except ValueError:
            continue
    return result


def _analyze_path(analyze_root: Path, day: str) -> Path:
    paths = sorted((analyze_root / day).glob("*_analyze.csv"))
    if not paths:
        raise FileNotFoundError(f"analyze CSVが見つかりません: {day}")
    return paths[0]


def load_analyze_rows(
    root: str | Path,
    data_date: str,
    *,
    cutoff_date: str | Date | None = None,
) -> list[dict[str, str]]:
    """1日分のanalyze CSVを読み取る。

    ``cutoff_date`` を指定した場合、対象日そのものも含めて検証する。
    Phase 1では、CSVの内容を加工せずDictReaderの行として返す。
    """
    day = normalize_date(data_date)
    assert_as_of(day, cutoff_date)
    path = _analyze_path(Path(root), day)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def load_snapshot(
    root: str | Path,
    data_date: str,
    *,
    cutoff_date: str | Date | None = None,
) -> dict[str, Any]:
    """1日分のsnapshot JSONを読み取る。"""
    day = normalize_date(data_date)
    assert_as_of(day, cutoff_date)
    path = Path(root) / f"{day}_snapshot.json"
    if not path.exists():
        raise FileNotFoundError(f"snapshotが見つかりません: {day}")
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("date"):
        assert_as_of(str(payload["date"]).replace("/", ""), cutoff_date)
    return payload


def load_pair_history(path: str | Path) -> dict[str, Any]:
    """pair_history.jsonを読み取り専用でロードする。"""
    with Path(path).open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or not isinstance(payload.get("pairs", {}), dict):
        raise ValueError("pair_history.jsonの形式が不正です")
    return payload


def load_pair_history_as_of(
    path: str | Path,
    cutoff_date: str | Date,
) -> dict[str, Any]:
    """pair_historyからcutoff以前の日次履歴だけを抽出する。

    累積値は未来日を含む可能性があるため再利用せず、dailyから再計算する。
    """
    cutoff = normalize_date(cutoff_date)
    source = load_pair_history(path)
    result = {"meta": dict(source.get("meta", {})), "pairs": {}}
    result["meta"]["as_of"] = cutoff
    for key, pair in source["pairs"].items():
        daily = [
            dict(item)
            for item in pair.get("daily", [])
            if normalize_date(str(item["date"])) <= cutoff
        ]
        if not daily:
            continue
        item = {k: v for k, v in pair.items() if k != "daily"}
        item["daily"] = daily
        item["days_seen"] = len(daily)
        item["total_count"] = sum(int(d.get("count", 0)) for d in daily)
        total = item["total_count"]
        item["mean_lift"] = (
            sum(float(d.get("lift", 0)) * int(d.get("count", 0)) for d in daily) / total
            if total else 0.0
        )
        item["days_lift_over_threshold"] = sum(float(d.get("lift", 0)) >= 1.5 for d in daily)
        item["reproducibility"] = (
            item["days_lift_over_threshold"] / item["days_seen"]
            if item["days_seen"] else 0.0
        )
        result["pairs"][key] = item
    return result


def load_daily_ohlc_rows(
    root: str | Path,
    *,
    cutoff_date: str | Date | None = None,
) -> list[dict[str, Any]]:
    """csv/daily_ohlc配下のOHLC行を読み取る。

    既存のdaily_ohlc.pyを変更せず、Pachi Agents側でas-of制約を適用する。
    """
    result: list[dict[str, Any]] = []
    for day, directory in _date_dirs(Path(root)):
        assert_as_of(day, cutoff_date)
        for path in sorted(directory.glob("*_daily_ohlc.csv")):
            with path.open(encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    item = dict(row)
                    item["date"] = day
                    result.append(item)
    return result


def load_daily_ohlc_rows_for_date(root: str | Path, data_date: str) -> list[dict[str, Any]]:
    """指定した1日だけのOHLCを読み取る。

    結果日の答え合わせでは、リポジトリ内に存在する将来日ディレクトリを
    誤って走査しないよう、日付ディレクトリを直接指定する。
    """
    day = normalize_date(data_date)
    directory = Path(root) / day
    if not directory.exists():
        return []
    result: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*_daily_ohlc.csv")):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                item = dict(row)
                item["date"] = day
                item["source_path"] = str(path)
                result.append(item)
    return result
