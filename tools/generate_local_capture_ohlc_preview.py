"""local_capture診断JSONから正式daily OHLC形式のプレビューCSVを生成する。"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import analyze_pscube

FIELDS = [
    "Date", "Machine", "Group", "Island",
    "Open", "High", "Low", "Close", "Source", "PointCount",
]


def enabled_machines(targets_path: Path) -> list[str]:
    payload = json.loads(targets_path.read_text(encoding="utf-8"))
    machines = [
        str(machine).zfill(4)
        for target in payload.get("targets", [])
        if target.get("enabled")
        for machine in target.get("machines", [])
    ]
    return list(dict.fromkeys(machines))


def build_preview(date: str, diagnostic_path: Path, targets_path: Path, output_path: Path) -> dict:
    machines = enabled_machines(targets_path)
    master: dict[str, tuple[str, str]] = {}
    with analyze_pscube.MASTER_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            key = str(row.get("machine", "")).strip().lstrip("0") or "0"
            master[key] = (str(row.get("group", "")).strip(), str(row.get("island", "")).strip())

    payload = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    by_machine = {str(row["machine"]).zfill(4): row for row in payload.get("records", [])}
    rows: list[dict[str, object]] = []
    unresolved: list[str] = []
    errors: list[str] = []
    for machine in machines:
        record = by_machine.get(machine)
        master_row = master.get(machine.lstrip("0") or "0")
        if record is None:
            errors.append(f"{machine}: missing diagnostic record")
            continue
        if master_row is None or not master_row[0] or not master_row[1]:
            unresolved.append(machine)
            continue
        if record.get("status") != "ok":
            errors.append(f"{machine}: diagnostic status={record.get('status')}")
            continue
        values = {key: record.get(key) for key in ("open", "high", "low", "close")}
        if any(value is None for value in values.values()):
            errors.append(f"{machine}: missing OHLC")
            continue
        if not (
            values["high"] >= values["open"]
            and values["high"] >= values["close"]
            and values["low"] <= values["open"]
            and values["low"] <= values["close"]
            and values["high"] >= values["low"]
        ):
            errors.append(f"{machine}: invalid OHLC")
            continue
        if record.get("source") != "image" or not isinstance(record.get("point_count"), int):
            errors.append(f"{machine}: invalid source/point_count")
            continue
        rows.append({
            "Date": f"{date[:4]}/{date[4:6]}/{date[6:8]}",
            "Machine": machine,
            "Group": master_row[0],
            "Island": master_row[1],
            "Open": int(values["open"]),
            "High": int(values["high"]),
            "Low": int(values["low"]),
            "Close": int(values["close"]),
            "Source": "image",
            "PointCount": int(record["point_count"]),
        })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(rows)
    machines_out = [str(row["Machine"]) for row in rows]
    return {
        "date": date,
        "rows": len(rows),
        "expected": len(machines),
        "missing": sorted(set(machines) - set(machines_out)),
        "duplicates": len(machines_out) - len(set(machines_out)),
        "unresolved_group_island": unresolved,
        "errors": errors,
        "output": str(output_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True)
    parser.add_argument("--diagnostic", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--targets", type=Path, default=ROOT / "pscube_targets.json")
    args = parser.parse_args()
    result = build_preview(args.date, args.diagnostic, args.targets, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["rows"] == result["expected"] and not result["errors"] and not result["unresolved_group_island"] and result["duplicates"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
