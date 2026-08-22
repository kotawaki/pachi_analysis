"""local_capture Rescue PNGのaxes/OHLC一括診断。

正式daily OHLCへは書き込まず、rescue/diagnosticsへJSONだけを出力する。
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import analyze_pscube


def enabled_machines(targets_path: Path) -> list[str]:
    payload = json.loads(targets_path.read_text(encoding="utf-8"))
    machines = [
        str(machine).zfill(4)
        for target in payload.get("targets", [])
        if target.get("enabled")
        for machine in target.get("machines", [])
    ]
    return list(dict.fromkeys(machines))


def integrity_errors(ohlc: dict, point_count: int, axes: dict) -> list[str]:
    errors: list[str] = []
    if not (
        ohlc["high"] >= ohlc["open"]
        and ohlc["high"] >= ohlc["close"]
        and ohlc["low"] <= ohlc["open"]
        and ohlc["low"] <= ohlc["close"]
        and ohlc["high"] >= ohlc["low"]
    ):
        errors.append("invalid_ohlc")
    if point_count < 10:
        errors.append("point_count_extreme")
    if not (500 <= axes["x2230_px"] <= 570):
        errors.append("x_end_anomaly")
    return errors


def diagnose(date: str, targets_path: Path, screenshot_dir: Path, output_path: Path) -> dict:
    machines = enabled_machines(targets_path)
    records: list[dict] = []
    for machine in machines:
        image_path = screenshot_dir / f"{machine}.png"
        record: dict = {
            "date": date,
            "machine": machine,
            "image": str(image_path),
            "status": "failed",
            "axes_status": None,
            "ocr_labels": [],
            "rejected_ocr_labels": [],
            "ocr_label_count": 0,
            "positive_label_count": 0,
            "negative_label_count": 0,
            "has_zero_label": False,
            "grid_value_step": None,
            "grid_pixel_step": None,
            "fit_a": None,
            "fit_b": None,
            "fit_rmse": None,
            "normalized_rmse": None,
            "inferred_top_value": None,
            "inferred_bottom_value": None,
            "top_frame_estimated_value": None,
            "bottom_frame_estimated_value": None,
            "x_start": None,
            "x_end": None,
            "open": None,
            "high": None,
            "low": None,
            "close": None,
            "source": None,
            "point_count": None,
            "fallback_used": False,
            "fallback_color": None,
            "error": [],
        }
        if not image_path.exists():
            record["error"] = ["missing_image"]
            records.append(record)
            continue
        try:
            axes, points = analyze_pscube.build_axes_and_points_from_local_capture_image(image_path)
            ohlc = analyze_pscube.chart_daily_ohlc(points, axes)
            labels = axes.get("y_labels", [])
            record.update(
                {
                    "axes_status": axes.get("axes_status"),
                    "ocr_labels": labels,
                    "rejected_ocr_labels": axes.get("y_labels_rejected", []),
                    "ocr_label_count": len(labels),
                    "positive_label_count": sum(item["value"] > 0 for item in labels),
                    "negative_label_count": sum(item["value"] < 0 for item in labels),
                    "has_zero_label": any(item["value"] == 0 for item in labels),
                    "grid_value_step": axes.get("grid_step_value"),
                    "grid_pixel_step": axes.get("grid_step_px"),
                    "fit_a": axes.get("fit_a"),
                    "fit_b": axes.get("fit_b"),
                    "fit_rmse": axes.get("fit_rmse"),
                    "normalized_rmse": axes.get("normalized_rmse"),
                    "inferred_top_value": axes.get("top_frame_value"),
                    "inferred_bottom_value": axes.get("bottom_frame_value"),
                    "top_frame_estimated_value": axes.get("top_frame_estimated_value"),
                    "bottom_frame_estimated_value": axes.get("bottom_frame_estimated_value"),
                    "x_start": axes.get("graph_left"),
                    "x_end": axes.get("x2230_px"),
                    "open": ohlc["open"],
                    "high": ohlc["high"],
                    "low": ohlc["low"],
                    "close": ohlc["close"],
                    "source": ohlc["source"],
                    "point_count": ohlc["point_count"],
                    "fallback_used": axes.get("fallback_used", False),
                    "fallback_color": axes.get("fallback_color"),
                }
            )
            errors = integrity_errors(ohlc, ohlc["point_count"], axes)
            if record["ocr_label_count"] < 5:
                errors.append("ocr_label_count_low")
            if record["positive_label_count"] < 2:
                errors.append("positive_labels_low")
            single_negative_label_ok = (
                record["ocr_label_count"] >= 6
                and record["has_zero_label"]
                and record["positive_label_count"] >= 3
                and record["negative_label_count"] >= 1
                and (record["fit_a"] or 0) < 0
                and (record["normalized_rmse"] or math.inf) <= 0.02
                and abs(
                    (record["top_frame_estimated_value"] or 0)
                    - (record["inferred_top_value"] or 0)
                ) <= (record["grid_value_step"] or math.inf) * 0.25
                and abs(
                    (record["bottom_frame_estimated_value"] or 0)
                    - (record["inferred_bottom_value"] or 0)
                ) <= (record["grid_value_step"] or math.inf) * 0.25
                and record["x_start"] == 83
                and 500 <= (record["x_end"] or 0) <= 570
                and record["point_count"] >= 400
                and not errors
            )
            if record["negative_label_count"] < 2 and not single_negative_label_ok:
                errors.append("negative_labels_low")
            if not record["has_zero_label"]:
                errors.append("zero_label_missing")
            if (record["normalized_rmse"] or math.inf) > 0.10:
                errors.append("normalized_rmse_high")
            if not points:
                errors.append("waveform_missing")
            record["error"] = list(dict.fromkeys(errors))
            record["status"] = "failed" if errors and any(
                item in errors for item in ("invalid_ohlc", "waveform_missing", "point_count_extreme")
            ) else ("needs_review" if errors or record["axes_status"] == "needs_review" else "ok")
        except Exception as exc:
            record["error"] = [f"{type(exc).__name__}: {exc}"]
        records.append(record)

    valid_rmse = [row["normalized_rmse"] for row in records if row["normalized_rmse"] is not None]
    label_distribution: dict[str, list[str]] = defaultdict(list)
    range_distribution: dict[str, list[str]] = defaultdict(list)
    point_distribution: dict[str, list[str]] = defaultdict(list)
    xend_distribution: dict[str, list[str]] = defaultdict(list)
    for row in records:
        label_distribution[str(row["ocr_label_count"])].append(row["machine"])
        if row["inferred_top_value"] is not None:
            key = f"{row['inferred_top_value']}/{row['inferred_bottom_value']}"
            range_distribution[key].append(row["machine"])
        if row["point_count"] is not None:
            point_distribution[str(row["point_count"])].append(row["machine"])
        if row["x_end"] is not None:
            xend_distribution[str(row["x_end"])].append(row["machine"])

    summary = {
        "total": len(records),
        "ok": sum(row["status"] == "ok" for row in records),
        "needs_review": sum(row["status"] == "needs_review" for row in records),
        "failed": sum(row["status"] == "failed" for row in records),
        "ocr_label_count_distribution": dict(label_distribution),
        "normalized_rmse_max": max(valid_rmse) if valid_rmse else None,
        "normalized_rmse_median": statistics.median(valid_rmse) if valid_rmse else None,
        "y_axis_range_distribution": dict(range_distribution),
        "point_count_distribution": dict(point_distribution),
        "x_end_distribution": dict(xend_distribution),
        "needs_review_machines": [row["machine"] for row in records if row["status"] == "needs_review"],
        "failed_machines": [row["machine"] for row in records if row["status"] == "failed"],
    }
    payload = {
        "date": date,
        "input": str(screenshot_dir),
        "targets": str(targets_path),
        "formal_daily_ohlc_written": False,
        "summary": summary,
        "records": records,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="20260820")
    parser.add_argument("--targets", type=Path, default=ROOT / "pscube_targets.json")
    parser.add_argument("--screenshots", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    screenshots = args.screenshots or ROOT / "data" / "local_capture" / args.date / "rescue" / "screenshots"
    output = args.output or ROOT / "data" / "local_capture" / args.date / "rescue" / "diagnostics" / f"{args.date}_local_capture_ohlc_diagnostic.json"
    payload = diagnose(args.date, args.targets, screenshots, output)
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(f"output={output}")
    return 0 if payload["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
