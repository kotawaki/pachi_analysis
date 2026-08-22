"""Rescue only the PNG for one existing prior-day chart through Chrome CDP."""

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

from pscube_cdp_capture_common import (
    ChallengeDetected,
    CaptureAborted,
    RateLimited,
    apply_legacy_viewport,
    capture_rescue_screenshot,
    clear_legacy_viewport,
    get_page,
    is_challenge,
    is_rate_limited,
    open_machine,
    redact_url,
    write_manifest,
)

ROOT = Path(__file__).resolve().parents[1]


def esc_pressed() -> bool:
    if os.name != "nt":
        return False
    import msvcrt

    pressed = False
    while msvcrt.kbhit():
        if msvcrt.getwch() == "\x1b":
            pressed = True
    return pressed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="one target business date YYYYMMDD")
    parser.add_argument("--targets-file", default="pscube_targets.json")
    parser.add_argument("--expected-count", type=int, default=71)
    parser.add_argument("--machine", "--machines", dest="requested_machines", nargs="+")
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--delay-min", type=float, default=3.0)
    parser.add_argument("--delay-max", type=float, default=5.0)
    return parser.parse_args()


def load_enabled(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    machines = [
        str(machine).zfill(4)
        for target in data.get("targets", [])
        if target.get("enabled")
        for machine in target.get("machines", [])
    ]
    return list(dict.fromkeys(machines))


def main() -> int:
    args = parse_args()
    if not __import__("re").fullmatch(r"\d{8}", args.date):
        raise SystemExit("--date must be YYYYMMDD")
    if args.delay_min < 0 or args.delay_max < args.delay_min:
        raise SystemExit("delay range is invalid: require 0 <= delay-min <= delay-max")

    enabled = load_enabled(Path(args.targets_file))
    machines = enabled
    if args.requested_machines:
        machines = list(dict.fromkeys(str(int(machine)).zfill(4) for machine in args.requested_machines))
        unknown = [machine for machine in machines if machine not in enabled]
        if unknown:
            print(f"ERROR: Requested machines are not enabled targets: {unknown}", file=sys.stderr)
            return 2
        expected_count = len(machines)
    else:
        expected_count = args.expected_count
    if len(machines) != expected_count:
        print(f"ERROR: expected_count={expected_count} actual_count={len(machines)}", file=sys.stderr)
        print("Rescue will not start.", file=sys.stderr)
        return 2

    out = ROOT / "data" / "local_capture" / args.date / "rescue"
    logs = out / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(logs / "capture.log", encoding="utf-8"), logging.StreamHandler()],
    )
    manifest = {
        "mode": "rescue_screenshot",
        "target_date": args.date,
        "date": args.date,
        "expected_count": expected_count,
        "actual_count": len(machines),
        "machines": machines,
        "results": [],
        "retries": args.retries,
        "delay_min": args.delay_min,
        "delay_max": args.delay_max,
        "rate_limited": False,
        "aborted": False,
        "challenge_detected": False,
    }

    pw = sync_playwright().start()
    viewport_session = None
    try:
        browser = pw.chromium.connect_over_cdp(args.cdp_url, timeout=15000)
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
                print("ESC pressed. Stopping rescue safely...", flush=True)
                manifest.update({"aborted": True, "aborted_at_machine": machine, "remaining_machines": machines[index:]})
                break

            result = {"machine": machine, "target_date": args.date, "status": "failed", "delay_seconds": 0}
            for attempt in range(1, args.retries + 1):
                try:
                    if is_rate_limited(page):
                        raise RateLimited("rate limit page detected before navigation")
                    if is_challenge(page):
                        raise ChallengeDetected("Cloudflare challenge detected before navigation")
                    open_machine(page, machine)
                    result = capture_rescue_screenshot(
                        page,
                        machine,
                        args.date,
                        out,
                        abort_checker=esc_pressed,
                        rate_limit_checker=raise_if_rate_limited,
                    )
                    result["attempts"] = attempt
                    break
                except CaptureAborted as exc:
                    print("ESC pressed. Stopping rescue safely...", flush=True)
                    result = {"machine": machine, "target_date": args.date, "status": "aborted", "missing_items": ["aborted"], "error": str(exc), "attempts": attempt}
                    manifest.update({"aborted": True, "aborted_at_machine": machine, "remaining_machines": machines[index:]})
                    break
                except RateLimited as exc:
                    print("HTTP 429 detected. Stopping rescue safely; no retry will be attempted.", flush=True)
                    result = {"machine": machine, "target_date": args.date, "status": "rate_limited", "rate_limited": True, "missing_items": ["rate_limited"], "error": str(exc), "attempts": attempt}
                    manifest.update({"rate_limited": True, "rate_limited_at_machine": machine, "rate_limited_at": dt.datetime.now().astimezone().isoformat(), "rate_limited_response": rate_signal, "remaining_machines": machines[index:]})
                    break
                except ChallengeDetected as exc:
                    print("Cloudflare challenge detected. Stopping rescue safely; no bypass will be attempted.", flush=True)
                    result = {"machine": machine, "target_date": args.date, "status": "challenge", "challenge": True, "missing_items": ["challenge"], "error": str(exc), "attempts": attempt}
                    manifest.update({"challenge_detected": True, "challenge_at_machine": machine, "challenge_at": dt.datetime.now().astimezone().isoformat(), "remaining_machines": machines[index:]})
                    break
                except Exception as exc:
                    if rate_signal or is_rate_limited(page):
                        print("HTTP 429 detected. Stopping rescue safely; no retry will be attempted.", flush=True)
                        result = {"machine": machine, "target_date": args.date, "status": "rate_limited", "rate_limited": True, "missing_items": ["rate_limited"], "error": str(exc), "attempts": attempt}
                        manifest.update({"rate_limited": True, "rate_limited_at_machine": machine, "rate_limited_at": dt.datetime.now().astimezone().isoformat(), "rate_limited_response": rate_signal, "remaining_machines": machines[index:]})
                        break
                    if is_challenge(page):
                        print("Cloudflare challenge detected. Stopping rescue safely; no bypass will be attempted.", flush=True)
                        result = {"machine": machine, "target_date": args.date, "status": "challenge", "challenge": True, "missing_items": ["challenge"], "error": str(exc), "attempts": attempt}
                        manifest.update({"challenge_detected": True, "challenge_at_machine": machine, "challenge_at": dt.datetime.now().astimezone().isoformat(), "remaining_machines": machines[index:]})
                        break
                    result = {"machine": machine, "target_date": args.date, "status": "failed", "missing_items": ["capture"], "error": str(exc), "attempts": attempt}
                    logging.exception("rescue machine=%s attempt=%s failed", machine, attempt)
            manifest["results"].append(result)
            logging.info("rescue machine=%s status=%s", machine, result["status"])
            if manifest.get("aborted") or manifest.get("rate_limited") or manifest.get("challenge_detected"):
                break
            if index < len(machines) - 1:
                delay = round(random.uniform(args.delay_min, args.delay_max), 3)
                result["delay_seconds"] = delay
                logging.info("rescue machine=%s delay_seconds=%.3f", machine, delay)
                try:
                    wait_between_machines(delay)
                except CaptureAborted:
                    print("ESC pressed. Stopping rescue safely...", flush=True)
                    manifest.update({"aborted": True, "aborted_at_machine": machines[index + 1], "remaining_machines": machines[index + 1:]})
                    break
                except RateLimited:
                    print("HTTP 429 detected. Stopping rescue safely; no retry will be attempted.", flush=True)
                    manifest.update({"rate_limited": True, "rate_limited_at_machine": machines[index + 1], "rate_limited_at": dt.datetime.now().astimezone().isoformat(), "rate_limited_response": rate_signal, "remaining_machines": machines[index + 1:]})
                    break
    finally:
        clear_legacy_viewport(viewport_session)
        pw.stop()

    manifest_path = out / "manifest.json"
    write_manifest(manifest_path, manifest)
    complete = manifest.get("complete_count", 0)
    no_data = sum(1 for result in manifest["results"] if result.get("status") == "no_data")
    manifest["no_data_count"] = no_data
    write_manifest(manifest_path, manifest)
    print(f"status={manifest['status']}")
    print(f"complete={complete}")
    print(f"no_data={no_data}")
    print(f"incomplete={manifest.get('incomplete_count', 0)}")
    print(f"failed_machines={manifest.get('failed_machines', [])}")
    if manifest.get("remaining_machines"):
        print("Remaining machines: " + " ".join(manifest["remaining_machines"]))
    print(f"manifest={manifest_path}")
    return 0 if manifest["status"] == "complete" else 1


if __name__ == "__main__":
    sys.exit(main())
