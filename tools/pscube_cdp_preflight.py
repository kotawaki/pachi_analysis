"""Preflight checks for the user-started Chrome CDP morning batch."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


def fetch_json(url: str) -> object:
    with urllib.request.urlopen(url, timeout=3) as response:
        return json.loads(response.read().decode("utf-8"))


def enabled_machines(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    machines = [str(machine).zfill(4) for target in data.get("targets", []) if target.get("enabled") for machine in target.get("machines", [])]
    return list(dict.fromkeys(machines))


def normalize_requested(values: list[str]) -> list[str]:
    normalized = []
    for value in values:
        digits = "".join(ch for ch in str(value) if ch.isdigit())
        if not digits:
            raise ValueError(f"invalid machine: {value}")
        normalized.append(digits.zfill(4))
    return list(dict.fromkeys(normalized))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--targets-file", type=Path, required=True)
    parser.add_argument("--expected-count", type=int, default=71)
    parser.add_argument("--machines", nargs="+", help="validate only these enabled machines")
    parser.add_argument("--date")
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    args = parser.parse_args()

    if args.date:
        try:
            dt.datetime.strptime(args.date, "%Y%m%d")
        except ValueError:
            print(f"ERROR: Invalid business date: {args.date} (expected YYYYMMDD).", file=sys.stderr)
            return 2

    try:
        version = fetch_json(args.cdp_url.rstrip("/") + "/json/version")
        tabs = fetch_json(args.cdp_url.rstrip("/") + "/json/list")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as error:
        print(f"ERROR: CDP connection failed: {args.cdp_url} ({error})", file=sys.stderr)
        print("Start Chrome with remote debugging port 9222 and open PSCUBE, then retry.", file=sys.stderr)
        return 3

    page_tabs = [tab for tab in tabs if tab.get("type") == "page"] if isinstance(tabs, list) else []
    pscube_tabs = [tab for tab in page_tabs if str(tab.get("url", "")).startswith("https://www.pscube.jp/")]
    if not pscube_tabs:
        print("ERROR: No PSCUBE page found in existing Chrome.", file=sys.stderr)
        print("Open PSCUBE in regular Chrome, then retry.", file=sys.stderr)
        return 4

    try:
        machines = enabled_machines(args.targets_file)
    except (OSError, json.JSONDecodeError, KeyError) as error:
        print(f"ERROR: Cannot read targets file: {args.targets_file} ({error})", file=sys.stderr)
        return 5
    requested = None
    if args.machines:
        try:
            requested = normalize_requested(args.machines)
        except ValueError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 6
        unknown = [machine for machine in requested if machine not in machines]
        if unknown:
            print(f"ERROR: Requested machines are not enabled targets: {unknown}", file=sys.stderr)
            return 6
        machines = requested
    expected_count = args.expected_count if not args.machines else len(machines)
    if len(machines) != expected_count:
        print(f"ERROR: Target count mismatch: expected={args.expected_count}, actual={len(machines)}", file=sys.stderr)
        print(f"Targets file: {args.targets_file}", file=sys.stderr)
        print("Capture will not start. Check the targets file; no machine will be guessed.", file=sys.stderr)
        return 6

    print(json.dumps({
        "cdp": "ok",
        "browser": version.get("Browser") if isinstance(version, dict) else None,
        "pscube_pages": len(pscube_tabs),
        "targets_file": str(args.targets_file),
        "machine_count": len(machines),
        "date": args.date,
        "status": "ready",
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
