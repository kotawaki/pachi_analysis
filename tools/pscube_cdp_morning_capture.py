"""Normal capture: current-day tab only, using the user's existing Chrome via CDP."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from pscube_cdp_capture_common import CaptureAborted, apply_legacy_viewport, capture_today, clear_legacy_viewport, get_page, open_machine, write_manifest


ROOT = Path(__file__).resolve().parents[1]


def esc_pressed() -> bool:
    if os.name != "nt":
        return False
    import msvcrt
    pressed = False
    while msvcrt.kbhit():
        if msvcrt.getwch() == "\\x1b":
            pressed = True
    return pressed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--machine", "--machines", dest="machines", nargs="+")
    parser.add_argument("--machines-file")
    parser.add_argument("--targets-file", help="pscube_targets.json形式のenabled台リスト")
    parser.add_argument("--expected-count", type=int)
    parser.add_argument("--date", default=dt.datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument("--retries", type=int, default=2)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    machines = list(args.machines or [])
    if args.targets_file:
        data = json.loads(Path(args.targets_file).read_text(encoding="utf-8"))
        machines.extend(
            str(machine).zfill(4)
            for target in data.get("targets", [])
            if target.get("enabled")
            for machine in target.get("machines", [])
        )
    if args.machines_file:
        machines.extend(line.strip() for line in Path(args.machines_file).read_text(encoding="utf-8-sig").splitlines() if line.strip())
    machines = list(dict.fromkeys(str(int(m)).zfill(4) for m in machines))
    if not machines:
        raise SystemExit("--machine または --machines-file が必要です")
    if args.expected_count is not None and len(machines) != args.expected_count:
        raise SystemExit(f"対象台数が期待値と一致しません: expected={args.expected_count}, actual={len(machines)}")
    out = ROOT / "data" / "local_capture" / args.date / "morning"
    logs = out / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[logging.FileHandler(logs / "capture.log", encoding="utf-8"), logging.StreamHandler()])
    manifest = {"date": args.date, "mode": "morning", "browser": "existing_chrome_cdp", "machines": machines, "results": [], "retry_limit": args.retries, "aborted": False}

    pw = sync_playwright().start()
    viewport_session = None
    try:
        browser = pw.chromium.connect_over_cdp(args.cdp_url, timeout=15000)
        manifest["connection"] = {"status": "ok", "contexts": len(browser.contexts), "pages": sum(len(c.pages) for c in browser.contexts)}
        page = get_page(browser, machines[0])
        viewport_session = apply_legacy_viewport(page)
        manifest["viewport"] = {"width": 590, "height": 1000, "deviceScaleFactor": 1, "mobile": False, "source": "legacy_iab"}
        for index, machine in enumerate(machines):
            if esc_pressed():
                print("ESC pressed. Stopping capture safely...", flush=True)
                manifest.update({"aborted": True, "aborted_at_machine": machine, "completed_machines": [r["machine"] for r in manifest["results"] if r.get("status") == "complete"], "remaining_machines": machines[index:]})
                break
            result = {"machine": machine, "status": "failed", "missing_items": []}
            for attempt in range(1, args.retries + 1):
                try:
                    open_machine(page, machine)
                    if esc_pressed():
                        raise CaptureAborted("ESC pressed after page navigation")
                    selected = page.locator("#YMD-ul li.selected").get_attribute("data-ymd") if page.locator("#YMD-ul li.selected").count() else None
                    if selected and selected != args.date:
                        raise RuntimeError(f"morning requires current tab {args.date}, selected={selected}")
                    result = capture_today(page, machine, args.date, out, abort_checker=esc_pressed)
                    result["attempts"] = attempt
                    if esc_pressed():
                        raise CaptureAborted("ESC pressed after machine capture")
                    break
                except CaptureAborted as exc:
                    print("ESC pressed. Stopping capture safely...", flush=True)
                    result = {"machine": machine, "status": "aborted", "missing_items": ["aborted"], "attempts": attempt, "error": str(exc), "url": page.url, "title": page.title()}
                    manifest["aborted"] = True
                    manifest["aborted_at_machine"] = machine
                    manifest["completed_machines"] = [r["machine"] for r in manifest["results"] if r.get("status") == "complete"]
                    manifest["remaining_machines"] = machines[index:]
                    break
                except Exception as exc:
                    result = {"machine": machine, "status": "failed", "missing_items": ["capture"], "attempts": attempt, "error": str(exc), "url": page.url, "title": page.title()}
                    logging.exception("morning machine=%s attempt=%s failed", machine, attempt)
            manifest["results"].append(result)
            logging.info("morning machine=%s status=%s", machine, result["status"])
            if manifest.get("aborted"):
                break
    except Exception as exc:
        manifest["connection_error"] = str(exc)
        logging.exception("morning connection failed")
    finally:
        clear_legacy_viewport(viewport_session)
        pw.stop()

    manifest_path = out / "manifest.json"
    write_manifest(manifest_path, manifest)
    logging.info("manifest=%s status=%s", manifest_path, manifest["status"])
    complete_count = sum(1 for result in manifest["results"] if result.get("status") == "complete")
    print(f"status={manifest['status']}")
    print(f"complete={complete_count}")
    print(f"incomplete={len(manifest['results']) - complete_count}")
    print(f"failed_machines={manifest.get('failed_machines', [])}")
    if manifest.get("aborted"):
        print("aborted=true")
        print("Capture aborted by user.")
    print(f"manifest={manifest_path}")
    return 0 if manifest["status"] == "complete" else (2 if manifest.get("aborted") else 1)


if __name__ == "__main__":
    sys.exit(main())
