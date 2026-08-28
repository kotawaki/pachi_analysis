"""Normal capture: current-day tab only, using the user's existing Chrome via CDP."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import random
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from pscube_cdp_capture_common import ChallengeDetected, CaptureAborted, MACHINE_DELAY_MAX_SECONDS, MACHINE_DELAY_MIN_SECONDS, RateLimited, apply_legacy_viewport, capture_today, clear_legacy_viewport, get_page, is_challenge, is_rate_limited, open_machine, redact_url, write_manifest


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
    parser.add_argument("--delay-min", type=float, default=MACHINE_DELAY_MIN_SECONDS)
    parser.add_argument("--delay-max", type=float, default=MACHINE_DELAY_MAX_SECONDS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.delay_min < 0 or args.delay_max < args.delay_min:
        raise SystemExit("delay range is invalid: require 0 <= delay-min <= delay-max")
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
    manifest = {"date": args.date, "mode": "morning", "browser": "existing_chrome_cdp", "machines": machines, "results": [], "retry_limit": args.retries, "delay_min": args.delay_min, "delay_max": args.delay_max, "aborted": False, "rate_limited": False, "challenge_detected": False}

    pw = sync_playwright().start()
    viewport_session = None
    try:
        browser = pw.chromium.connect_over_cdp(args.cdp_url, timeout=15000)
        manifest["connection"] = {"status": "ok", "contexts": len(browser.contexts), "pages": sum(len(c.pages) for c in browser.contexts)}
        page = get_page(browser, machines[0])
        viewport_session = apply_legacy_viewport(page)
        manifest["viewport"] = {"width": 590, "height": 1000, "deviceScaleFactor": 1, "mobile": False, "source": "legacy_iab"}
        rate_signal: dict[str, object] = {}

        def on_response(response: object) -> None:
            if getattr(response, "status", None) == 429 and "pscube.jp" in getattr(response, "url", ""):
                rate_signal.update({"status": 429, "url": redact_url(response.url)})

        def raise_if_rate_limited() -> None:
            if rate_signal:
                raise RateLimited(f"HTTP 429 response observed: {rate_signal.get('url', '<redacted>')}")
            if is_rate_limited(page):
                raise RateLimited("rate limit page detected")

        page.on("response", on_response)

        def wait_between_machines(seconds: float) -> None:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                if esc_pressed():
                    raise CaptureAborted("ESC pressed during inter-machine delay")
                raise_if_rate_limited()
                time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))

        for index, machine in enumerate(machines):
            if esc_pressed():
                print("ESC pressed. Stopping capture safely...", flush=True)
                manifest.update({"aborted": True, "aborted_at_machine": machine, "completed_machines": [r["machine"] for r in manifest["results"] if r.get("status") == "complete"], "remaining_machines": machines[index:]})
                break
            result = {"machine": machine, "status": "failed", "missing_items": [], "delay_seconds": 0}
            for attempt in range(1, args.retries + 1):
                try:
                    if is_rate_limited(page):
                        raise RateLimited("rate limit page detected before navigation")
                    if is_challenge(page):
                        raise ChallengeDetected("Cloudflare challenge detected before navigation")
                    open_machine(page, machine)
                    raise_if_rate_limited()
                    if esc_pressed():
                        raise CaptureAborted("ESC pressed after page navigation")
                    selected = page.locator("#YMD-ul li.selected").get_attribute("data-ymd") if page.locator("#YMD-ul li.selected").count() else None
                    if selected and selected != args.date:
                        raise RuntimeError(f"morning requires current tab {args.date}, selected={selected}")
                    result = capture_today(page, machine, args.date, out, abort_checker=esc_pressed, rate_limit_checker=raise_if_rate_limited)
                    result["attempts"] = attempt
                    if esc_pressed():
                        raise CaptureAborted("ESC pressed after machine capture")
                    break
                except CaptureAborted as exc:
                    print("ESC pressed. Stopping capture safely...", flush=True)
                    result = {"machine": machine, "status": "aborted", "missing_items": ["aborted"], "delay_seconds": 0, "attempts": attempt, "error": str(exc), "url": page.url, "title": page.title()}
                    manifest["aborted"] = True
                    manifest["aborted_at_machine"] = machine
                    manifest["completed_machines"] = [r["machine"] for r in manifest["results"] if r.get("status") == "complete"]
                    manifest["remaining_machines"] = machines[index:]
                    break
                except RateLimited as exc:
                    print("HTTP 429 detected. Stopping capture safely; no retry will be attempted.", flush=True)
                    result = {"machine": machine, "status": "rate_limited", "rate_limited": True, "missing_items": ["rate_limited"], "delay_seconds": 0, "attempts": attempt, "error": str(exc), "url": page.url, "title": page.title()}
                    manifest.update({"rate_limited": True, "rate_limited_at_machine": machine, "rate_limited_at": dt.datetime.now().astimezone().isoformat(), "rate_limited_response": rate_signal})
                    manifest["remaining_machines"] = machines[index:]
                    break
                except ChallengeDetected as exc:
                    print("Cloudflare challenge detected. Stopping capture safely; no bypass will be attempted.", flush=True)
                    result = {"machine": machine, "status": "challenge", "challenge": True, "missing_items": ["challenge"], "delay_seconds": 0, "attempts": attempt, "error": str(exc), "url": page.url, "title": page.title()}
                    manifest.update({"challenge_detected": True, "challenge_at_machine": machine, "challenge_at": dt.datetime.now().astimezone().isoformat(), "remaining_machines": machines[index:]})
                    break
                except Exception as exc:
                    if rate_signal or is_rate_limited(page):
                        print("HTTP 429 detected. Stopping capture safely; no retry will be attempted.", flush=True)
                        result = {"machine": machine, "status": "rate_limited", "rate_limited": True, "missing_items": ["rate_limited"], "delay_seconds": 0, "attempts": attempt, "error": str(exc), "url": page.url, "title": page.title()}
                        manifest.update({"rate_limited": True, "rate_limited_at_machine": machine, "rate_limited_at": dt.datetime.now().astimezone().isoformat(), "rate_limited_response": rate_signal, "remaining_machines": machines[index:]})
                        break
                    if is_challenge(page):
                        print("Cloudflare challenge detected. Stopping capture safely; no bypass will be attempted.", flush=True)
                        result = {"machine": machine, "status": "challenge", "challenge": True, "missing_items": ["challenge"], "delay_seconds": 0, "attempts": attempt, "error": str(exc), "url": page.url, "title": page.title()}
                        manifest.update({"challenge_detected": True, "challenge_at_machine": machine, "challenge_at": dt.datetime.now().astimezone().isoformat(), "remaining_machines": machines[index:]})
                        break
                    result = {"machine": machine, "status": "failed", "missing_items": ["capture"], "delay_seconds": 0, "attempts": attempt, "error": str(exc), "url": page.url, "title": page.title()}
                    logging.exception("morning machine=%s attempt=%s failed", machine, attempt)
            manifest["results"].append(result)
            logging.info("morning machine=%s status=%s", machine, result["status"])
            if manifest.get("aborted") or manifest.get("rate_limited") or manifest.get("challenge_detected"):
                break
            if result.get("status") == "complete" and index < len(machines) - 1:
                delay_seconds = round(random.uniform(args.delay_min, args.delay_max), 3)
                result["delay_seconds"] = delay_seconds
                logging.info("morning machine=%s delay_seconds=%.3f", machine, delay_seconds)
                try:
                    wait_between_machines(delay_seconds)
                except CaptureAborted as exc:
                    print("ESC pressed. Stopping capture safely...", flush=True)
                    manifest.update({"aborted": True, "aborted_at_machine": machines[index + 1], "aborted_at": dt.datetime.now().astimezone().isoformat(), "completed_machines": [r["machine"] for r in manifest["results"] if r.get("status") == "complete"], "remaining_machines": machines[index + 1:]})
                    break
                except RateLimited as exc:
                    print("HTTP 429 detected. Stopping capture safely; no retry will be attempted.", flush=True)
                    manifest.update({"rate_limited": True, "rate_limited_at_machine": machines[index + 1], "rate_limited_at": dt.datetime.now().astimezone().isoformat(), "rate_limited_response": rate_signal, "remaining_machines": machines[index + 1:]})
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
    print(f"complete={manifest.get('complete_count', complete_count)}")
    print(f"incomplete={manifest.get('incomplete_count', len(manifest['results']) - complete_count)}")
    print(f"failed_machines={manifest.get('failed_machines', [])}")
    if manifest.get("aborted"):
        print("aborted=true")
        print("Capture aborted by user.")
    if manifest.get("rate_limited"):
        print(f"rate_limited=true at_machine={manifest.get('rate_limited_at_machine')}")
        print("Remaining machines: " + " ".join(manifest.get("remaining_machines", [])))
    if manifest.get("challenge_detected"):
        print(f"challenge=true at_machine={manifest.get('challenge_at_machine')}")
    print(f"manifest={manifest_path}")
    return 0 if manifest["status"] == "complete" else (2 if manifest.get("aborted") else 1)


if __name__ == "__main__":
    sys.exit(main())
