"""Shared safe capture helpers for the existing user-started Chrome CDP session."""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

BASE_URL = "https://www.pscube.jp/dedamajyoho-P-townDMMpachi/c713848/cgi-bin/nc-v06-001.php"
MACHINE_RE = re.compile(r"\d{3,4}")
MAX_HISTORY_MORE_CLICKS = 10
HISTORY_MORE_WAIT_MS = 15000
LEGACY_VIEWPORT = {"width": 590, "height": 1000, "deviceScaleFactor": 1, "mobile": False}
TOP_BUTTON_WAIT_MS = 5000
RESCUE_CHART_STABLE_WAIT_MS = 20000
RESCUE_CHART_POLL_MS = 500
RESCUE_CHART_STABLE_ROUNDS = 2
MACHINE_DELAY_MIN_SECONDS = 5.0
MACHINE_DELAY_MAX_SECONDS = 8.0


def is_challenge(page: Any) -> bool:
    if page.locator("#divDAI h2").count() > 0:
        return False
    try:
        body = page.locator("body").inner_text(timeout=3000)
        html = page.content().lower()
        return (
            "自動での認証処理" in body
            or "しばらくお待ちください" in page.title()
            or page.locator("#cf-chl-widget-x3tjw_response").count() > 0
            or ("challenge-platform" in html and "#divdai" not in html)
        )
    except Exception:
        return True


def is_rate_limited(page: Any) -> bool:
    """Detect an HTTP-429-style page without attempting any bypass."""
    try:
        title = page.title().lower()
        body = page.locator("body").inner_text(timeout=3000).lower()
        text = f"{title}\n{body}"
        markers = (
            "http 429",
            "429 too many requests",
            "too many requests",
            "rate limit",
            "rate limited",
            "アクセスが集中",
            "時間をおいて",
        )
        return any(marker in text for marker in markers)
    except Exception:
        return False


def normalize_machine(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if not digits:
        raise ValueError(f"invalid machine: {value!r}")
    return digits.zfill(4)


def validate_machine(page: Any, machine: str) -> dict[str, Any]:
    machine_text = page.locator("#divDAI h2").inner_text(timeout=15000)
    actual = normalize_machine(machine_text)
    if actual != machine:
        raise RuntimeError(f"machine mismatch: page={machine_text!r}, expected={machine}")
    return {"divDAI_h2": True, "machine_text": machine_text, "machine_match": True}


def validate_svg(text: str) -> dict[str, Any]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as error:
        # Browser-serialized chart SVGs can contain a small number of HTML
        # named entities (most commonly &nbsp;), which are valid in the DOM
        # but not standalone XML. Keep the SVG content intact and normalize
        # only known entities for structural validation.
        normalized = re.sub(
            r"&(nbsp|thinsp|ensp|emsp|times|minus);",
            lambda match: {
                "nbsp": "&#160;",
                "thinsp": "&#8201;",
                "ensp": "&#8194;",
                "emsp": "&#8195;",
                "times": "&#215;",
                "minus": "&#8722;",
            }[match.group(1)],
            text,
        )
        if normalized == text:
            raise error
        root = ET.fromstring(normalized)
    if root.tag.rsplit("}", 1)[-1].lower() != "svg":
        raise ValueError("SVG root is not svg")
    counts = {
        "path": len(root.findall(".//{*}path")),
        "polyline": len(root.findall(".//{*}polyline")),
        "text": len(root.findall(".//{*}text")),
        "circle": len(root.findall(".//{*}circle")),
        "rect": len(root.findall(".//{*}rect")),
    }
    return {
        "xml": "ok",
        "bytes": len(text.encode("utf-8")),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "elements": counts,
    }


def is_placeholder_svg(info: dict[str, Any]) -> bool:
    size = int(info.get("bytes", 0))
    counts = info.get("elements", {})
    return (
        size < 2000
        and counts.get("text", 0) == 0
        and counts.get("circle", 0) == 0
        and counts.get("path", 0) <= 5
    )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["bonus_id", "time", "start", "status"])
        writer.writeheader()
        writer.writerows(rows)


def _history_dom(page: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    raw = page.evaluate(
        """() => ({
          rows: [...document.querySelectorAll('#tblHISTb tr')].map(tr =>
            [...tr.querySelectorAll('td')].map(td => (td.textContent || '').replace(/\\s+/g, ' ').trim())
          ).filter(row => row.some(Boolean)),
          moreExists: !!document.querySelector('#tblHISTm'),
          moreVisible: (() => { const e=document.querySelector('#tblHISTm'); return !!e && !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length); })(),
          moreDisabled: (() => { const e=document.querySelector('#tblHISTm'); return !e || e.disabled === true || e.getAttribute('aria-disabled') === 'true'; })()
        })"""
    )
    rows = []
    for row in raw["rows"]:
        if len(row) < 4 or row[0] == "回数":
            continue
        if not re.fullmatch(r"\d+", row[0]) or not re.fullmatch(r"\d{1,2}:\d{2}", row[1]) or not re.fullmatch(r"\d+", row[2]):
            continue
        rows.append({"bonus_id": int(row[0]), "time": row[1], "start": int(row[2]), "status": row[3]})
    return rows, raw


def _history_missing_bonus_ids(rows: list[dict[str, Any]]) -> list[int]:
    ids = [row["bonus_id"] for row in rows]
    if not ids or len(set(ids)) != len(ids):
        return []
    maximum = max(ids)
    if min(ids) != 1 or maximum > 1000:
        return []
    return [value for value in range(1, maximum + 1) if value not in set(ids)]


def extract_history(page: Any, expand_more: bool = False, abort_checker: Any = None, rate_limit_checker: Any = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read history; optionally click #tblHISTm until it is gone."""
    rows, raw = _history_dom(page)
    meta: dict[str, Any] = {
        **raw,
        "beforeRows": len(rows),
        "afterRows": len(rows),
        "moreClicks": 0,
        "history_complete": True,
        "history_missing_bonus_ids": [],
        "history_error": None,
    }
    if expand_more:
        while raw["moreExists"] and raw["moreVisible"] and not raw["moreDisabled"]:
            if abort_checker and abort_checker():
                raise CaptureAborted("ESC pressed during history expansion")
            if rate_limit_checker:
                rate_limit_checker()
            if meta["moreClicks"] >= MAX_HISTORY_MORE_CLICKS:
                meta["history_complete"] = False
                meta["history_error"] = f"maximum more clicks exceeded: {MAX_HISTORY_MORE_CLICKS}"
                break
            before_rows = len(rows)
            button = page.locator("#tblHISTm").first
            try:
                button.click(timeout=5000)
                page.wait_for_function(
                    """before => {
                      const rows = document.querySelectorAll('#tblHISTb tr').length;
                      const e = document.querySelector('#tblHISTm');
                      const visible = !!e && !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length);
                      const disabled = !e || e.disabled === true || e.getAttribute('aria-disabled') === 'true';
                      return rows > before || !visible || disabled;
                    }""",
                    arg=before_rows,
                    timeout=HISTORY_MORE_WAIT_MS,
                )
            except Exception as error:
                meta["history_complete"] = False
                meta["history_error"] = str(error)
                break
            meta["moreClicks"] += 1
            if abort_checker and abort_checker():
                raise CaptureAborted("ESC pressed after history expansion")
            if rate_limit_checker:
                rate_limit_checker()
            rows, raw = _history_dom(page)
            after_rows = len(rows)
            meta["afterRows"] = after_rows
            if after_rows <= before_rows and raw["moreVisible"] and not raw["moreDisabled"]:
                meta["history_complete"] = False
                meta["history_error"] = "history more click did not increase row count"
                break
            meta.update(raw)
        if meta["history_complete"] and raw["moreExists"] and raw["moreVisible"] and not raw["moreDisabled"]:
            meta["history_complete"] = False
            meta["history_error"] = "history more button remains visible"
    meta["afterRows"] = len(rows)
    meta["history_missing_bonus_ids"] = _history_missing_bonus_ids(rows)
    if meta["history_missing_bonus_ids"]:
        meta["history_complete"] = False
        meta["history_error"] = "bonus_id sequence has missing values"
    return rows, meta


def extract_summary(page: Any, machine: str) -> dict[str, Any]:
    raw = page.evaluate(
        """() => {
          const text = el => (el?.textContent || '').replace(/\\s+/g, ' ').trim();
          const labels = [...document.querySelectorAll('#tblDAbv2 .row-header .inner')]
            .map(text).filter(Boolean).filter(x => x !== '本日' && !/^\\d+日前$/.test(x));
          const columns = [...document.querySelectorAll('#tblDAbv2 td.column')]
            .map(col => [...col.querySelectorAll('.inner')].map(text).filter(Boolean));
          return {labels, columns};
        }"""
    )
    fields = {
        "大当り回数": "big_hits",
        "継続回数": "continuations",
        "初当り確率": "first_hit_probability",
        "大当り確率": "big_hit_probability",
        "最終スタート": "final_start",
        "大当り過去最高": "max_big_hits",
    }
    days = []
    for column in raw["columns"]:
        if not column:
            continue
        day = {"label": column[0]}
        for label, value in zip(raw["labels"], column[1:]):
            if label in fields:
                day[fields[label]] = value
        days.append(day)
    return {"machine": machine, "captured_at": dt.datetime.now().astimezone().isoformat(), "source": "pscube_dom", "days": days}


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    for key in ("apikey", "_i", "_t"):
        if key in query:
            query[key] = ["<redacted>"]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), parts.fragment))


def request_meta(url: str, method: str | None = None) -> dict[str, Any]:
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    return {
        "url": redact_url(url),
        "method": method or "GET",
        "endpoint": parts.path.rsplit("/", 1)[-1],
        "cd_dai": query.get("cd_dai", [None])[0],
        "YMD_biz": query.get("YMD_biz", [None])[0],
        "page": query.get("page", [None])[0],
        "apikey_present": bool(query.get("apikey")),
        "_i_present": bool(query.get("_i")),
        "_t_present": bool(query.get("_t")),
    }


def get_page(browser: Any, machine: str) -> Any:
    pages = [page for context in browser.contexts for page in context.pages]
    pscube_pages = [page for page in pages if "pscube.jp" in page.url]
    candidates = [page for page in pscube_pages if not is_challenge(page)]
    if not candidates:
        if pscube_pages:
            return pscube_pages[0]
        raise RuntimeError("no PSCUBE page found in existing Chrome")
    page = next((p for p in candidates if f"cd_dai={machine}" in p.url), candidates[0])
    return page


def open_machine(page: Any, machine: str, timeout_ms: int = 60000) -> None:
    response = page.goto(f"{BASE_URL}?cd_dai={machine}", wait_until="domcontentloaded", timeout=timeout_ms)
    page.wait_for_timeout(4000)
    if response is not None and response.status == 429:
        raise RateLimited(f"HTTP 429 while opening machine {machine}")
    if is_rate_limited(page):
        raise RateLimited(f"rate limit page while opening machine {machine}")
    if is_challenge(page):
        raise ChallengeDetected("Cloudflare challenge detected; stopping without bypass")
    validate_machine(page, machine)


def apply_legacy_viewport(page: Any) -> Any:
    """Apply the legacy IAB viewport to this CDP page only."""
    session = page.context.new_cdp_session(page)
    session.send("Emulation.setDeviceMetricsOverride", LEGACY_VIEWPORT)
    return session


def clear_legacy_viewport(session: Any) -> None:
    if session is not None:
        session.send("Emulation.clearDeviceMetricsOverride")


def wait_for_top_button_hidden(page: Any) -> dict[str, Any]:
    """Wait for #icon-top to be hidden; continue with a warning on timeout."""
    try:
        page.wait_for_function(
            """() => {
              const e = document.querySelector('#icon-top');
              if (!e) return true;
              const s = getComputedStyle(e);
              const r = e.getBoundingClientRect();
              return s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity) === 0 || r.width === 0 || r.height === 0;
            }""",
            timeout=TOP_BUTTON_WAIT_MS,
        )
        return {"status": "hidden", "warning": None}
    except Exception as error:
        logging.warning("#icon-top did not become hidden before screenshot: %s", error)
        return {"status": "timeout", "warning": str(error)}


class CaptureAborted(RuntimeError):
    """Raised when the user requests a safe stop during capture."""


class RateLimited(RuntimeError):
    """Raised when PSCUBE reports HTTP 429 or a rate-limit page."""


class ChallengeDetected(RuntimeError):
    """Raised when a challenge page is shown; no bypass is attempted."""


def png_dimensions(path: Path) -> tuple[int, int] | None:
    data = path.read_bytes()
    if len(data) < 24 or data[:8] != bytes([137, 80, 78, 71, 13, 10, 26, 10]):
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def rescue_chart_snapshot(page: Any, target_date: str) -> dict[str, Any]:
    return page.evaluate(
        """date => {
          const chart = document.querySelector(`#CHART-${date}`);
          const svg = chart?.querySelector('svg');
          const visible = e => {
            if (!e) return false;
            const s = getComputedStyle(e);
            const r = e.getBoundingClientRect();
            return s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity) !== 0 && r.width > 0 && r.height > 0;
          };
          const sr = svg?.getBoundingClientRect();
          const html = svg?.outerHTML || '';
          return {
            chart_exists: !!chart,
            chart_visible: visible(chart),
            chart_bbox: chart ? (() => { const r=chart.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height}; })() : null,
            svg_exists: !!svg,
            svg_visible: visible(svg),
            svg_bbox: sr ? {x:sr.x,y:sr.y,width:sr.width,height:sr.height} : null,
            svg_size: html.length,
            path: svg?.querySelectorAll('path').length || 0,
            polyline: svg?.querySelectorAll('polyline').length || 0,
            text: svg?.querySelectorAll('text').length || 0,
            rect: svg?.querySelectorAll('rect').length || 0,
            circle: svg?.querySelectorAll('circle').length || 0,
            svg_html: html
          };
        }""",
        target_date,
    )


def rescue_chart_diagnostic(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return safe chart diagnostics without embedding the SVG source in the manifest."""
    diagnostic = {key: value for key, value in snapshot.items() if key != "svg_html"}
    html = snapshot.get("svg_html") or ""
    diagnostic["svg_sha256"] = hashlib.sha256(html.encode("utf-8")).hexdigest() if html else None
    return diagnostic


def wait_for_rescue_chart_stable(page: Any, target_date: str) -> dict[str, Any]:
    deadline = time.monotonic() + RESCUE_CHART_STABLE_WAIT_MS / 1000
    previous_signature = None
    stable_rounds = 0
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        latest = rescue_chart_snapshot(page, target_date)
        html = latest.get("svg_html", "")
        signature = (
            len(html),
            latest.get("path"),
            latest.get("polyline"),
            latest.get("text"),
            latest.get("rect"),
            latest.get("circle"),
            tuple((latest.get("svg_bbox") or {}).get(key) for key in ("width", "height")),
        )
        if latest.get("chart_visible") and latest.get("svg_visible") and signature == previous_signature:
            stable_rounds += 1
            if stable_rounds >= RESCUE_CHART_STABLE_ROUNDS:
                return latest
        else:
            stable_rounds = 0
        previous_signature = signature
        page.wait_for_timeout(RESCUE_CHART_POLL_MS)
    latest["stability_timeout"] = True
    return latest


def capture_rescue_screenshot(page: Any, machine: str, target_date: str, out: Path, abort_checker: Any = None, rate_limit_checker: Any = None) -> dict[str, Any]:
    """Capture only a pre-existing date-specific chart PNG from the DOM."""
    result: dict[str, Any] = {
        "machine": machine,
        "target_date": target_date,
        "chart_selector": f"#CHART-{target_date}",
        "status": "failed",
        "missing_items": [],
        "delay_seconds": 0,
    }
    screenshot_dir = out / "screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    result.update(validate_machine(page, machine))
    result.update({"url": page.url, "title": page.title(), "challenge": is_challenge(page)})
    if result["challenge"]:
        raise ChallengeDetected("Cloudflare challenge detected")
    if is_rate_limited(page):
        raise RateLimited("rate limit page detected")
    if rate_limit_checker:
        rate_limit_checker()
    if abort_checker and abort_checker():
        raise CaptureAborted("ESC pressed before rescue screenshot")

    tab = page.locator(f"#YMD-ul li[data-ymd='{target_date}']").first
    if tab.count() and "selected" not in (tab.get_attribute("class") or ""):
        tab.click(timeout=10000)

    chart = page.locator(f"#CHART-{target_date}").first
    before_snapshot = rescue_chart_snapshot(page, target_date)
    result["chart_diagnostic_before"] = rescue_chart_diagnostic(before_snapshot)
    if chart.count() == 0:
        result["status"] = "no_data"
        result["error"] = "target chart element not found"
        return result
    try:
        page.wait_for_function(
            "selector => { const e=document.querySelector(selector); return !!e && !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length); }",
            arg=f"#CHART-{target_date}",
            timeout=10000,
        )
    except Exception:
        result["status"] = "no_data"
        result["error"] = "target chart is not visible"
        return result

    after_visibility = rescue_chart_snapshot(page, target_date)
    selected = page.locator("#YMD-ul li.selected").get_attribute("data-ymd") if page.locator("#YMD-ul li.selected").count() else None
    result["selected_ymd"] = selected
    if selected and selected != target_date:
        result["status"] = "no_data"
        result["error"] = f"selected tab mismatch: expected={target_date}, actual={selected}"
        return result

    stable_snapshot = wait_for_rescue_chart_stable(page, target_date)
    result["chart_diagnostic_after_visibility"] = rescue_chart_diagnostic(after_visibility)
    result["chart_diagnostic_after_stable"] = rescue_chart_diagnostic(stable_snapshot)
    svg = page.locator(f"#CHART-{target_date} svg").first
    result["svg_exists"] = bool(svg.count())
    if not result["svg_exists"]:
        result["status"] = "no_data"
        result["error"] = "target chart SVG not found"
        return result
    svg_text = stable_snapshot.get("svg_html") or svg.evaluate("el => new XMLSerializer().serializeToString(el)")
    svg_info = validate_svg(svg_text)
    result["svg_xml"] = svg_info["xml"]
    result["svg_size"] = svg_info["bytes"]
    result["svg_elements"] = svg_info["elements"]
    result["placeholder"] = is_placeholder_svg(svg_info)
    if result["placeholder"]:
        result["status"] = "no_data"
        result["error"] = "placeholder SVG"
        return result

    chart.evaluate("el => el.scrollIntoView({block: 'start', inline: 'nearest'})")
    top_button = wait_for_top_button_hidden(page)
    result["top_button_state"] = top_button["status"]
    if top_button["warning"]:
        result["warnings"] = ["page_top_button_visibility_timeout"]
    if rate_limit_checker:
        rate_limit_checker()
    if abort_checker and abort_checker():
        raise CaptureAborted("ESC pressed before rescue screenshot")

    png_path = screenshot_dir / f"{machine}.png"
    page.screenshot(path=str(png_path), full_page=False)
    dimensions = png_dimensions(png_path)
    result["png"] = "ok" if png_path.stat().st_size else "failed"
    result["png_path"] = str(png_path)
    result["png_size"] = png_path.stat().st_size
    result["png_width"] = dimensions[0] if dimensions else None
    result["png_height"] = dimensions[1] if dimensions else None
    if dimensions != (590, 1000):
        result["missing_items"].append("png_size")
    result["status"] = "complete" if not result["missing_items"] else "incomplete"
    result["captured_at"] = dt.datetime.now().astimezone().isoformat()
    return result


def capture_today(page: Any, machine: str, date: str, out: Path, include_png: bool = True, abort_checker: Any = None, rate_limit_checker: Any = None) -> dict[str, Any]:
    result: dict[str, Any] = {"machine": machine, "status": "failed", "missing_items": []}
    screenshot_dir, svg_dir, history_dir, summary_dir = out / "screenshots", out / "svg", out / "history", out / "summary"
    for directory in (screenshot_dir, svg_dir, history_dir, summary_dir):
        directory.mkdir(parents=True, exist_ok=True)
    result.update(validate_machine(page, machine))
    result.update({"url": page.url, "title": page.title(), "challenge": is_challenge(page), "selected_ymd": page.locator("#YMD-ul li.selected").get_attribute("data-ymd") if page.locator("#YMD-ul li.selected").count() else None})
    if result["challenge"]:
        raise ChallengeDetected("Cloudflare challenge detected")
    if is_rate_limited(page):
        raise RateLimited("rate limit page detected")
    if rate_limit_checker:
        rate_limit_checker()
    if abort_checker and abort_checker():
        raise CaptureAborted("ESC pressed before screenshot")

    chart = page.locator(f"#CHART-{date}").first
    if chart.count() == 0:
        chart = page.locator("#divCHART").first
    chart.evaluate("el => el.scrollIntoView({block: 'start', inline: 'nearest'})")
    top_button = wait_for_top_button_hidden(page)
    result["top_button_wait"] = top_button["status"]
    if top_button["warning"]:
        result["warnings"] = ["page_top_button_visibility_timeout"]
    if include_png:
        if abort_checker and abort_checker():
            raise CaptureAborted("ESC pressed before screenshot")
        if rate_limit_checker:
            rate_limit_checker()
        png_path = screenshot_dir / f"{machine}.png"
        page.screenshot(path=str(png_path), full_page=False)
        result["png"] = "ok" if png_path.stat().st_size else "failed"
        result["png_path"] = str(png_path)
        result["png_bytes"] = png_path.stat().st_size
        result["screenshot_method"] = "legacy_viewport_full_viewport"
        if not png_path.stat().st_size:
            result["missing_items"].append("png")
    svg = page.locator(f"#CHART-{date} svg").first
    if svg.count() == 0:
        raise RuntimeError(f"#CHART-{date} svg not found")
    svg_text = svg.evaluate("el => new XMLSerializer().serializeToString(el)")
    svg_info = validate_svg(svg_text)
    svg_path = svg_dir / f"{machine}.svg"
    svg_path.write_text(svg_text, encoding="utf-8")
    result["svg"] = "ok" if svg_info["bytes"] else "failed"
    result["svg_path"] = str(svg_path)
    result["svg_xml"] = svg_info["xml"]
    result["svg_bytes"] = svg_info["bytes"]
    result["svg_sha256"] = svg_info["sha256"]
    if not svg_info["bytes"]:
        result["missing_items"].append("svg")

    rows, history_meta = extract_history(page, expand_more=True, abort_checker=abort_checker, rate_limit_checker=rate_limit_checker)
    history_path = history_dir / f"{machine}_history.csv"
    write_csv(history_path, rows)
    result.update({
        "history_csv": "ok",
        "history_path": str(history_path),
        "history_rows": len(rows),
        "history_more_clicks": history_meta["moreClicks"],
        "history_complete": history_meta["history_complete"],
        "history_missing_bonus_ids": history_meta["history_missing_bonus_ids"],
        "history_more_available": bool(history_meta["moreExists"] and history_meta["moreVisible"] and not history_meta["moreDisabled"]),
        "history_before_rows": history_meta["beforeRows"],
        "history_after_rows": history_meta["afterRows"],
    })
    if history_meta["history_error"]:
        result["history_error"] = history_meta["history_error"]
    if not history_meta["history_complete"]:
        result["missing_items"].append("history_incomplete")

    summary = extract_summary(page, machine)
    summary_path = summary_dir / f"{machine}_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    result.update({"summary_json": "ok", "summary_path": str(summary_path), "summary_days": len(summary["days"])})
    result["status"] = "complete" if not result["missing_items"] else "incomplete"
    result["captured_at"] = dt.datetime.now().astimezone().isoformat()
    return result


def capture_rescue(page: Any, machine: str, date: str, out: Path, include_png: bool = False, include_summary: bool = True) -> dict[str, Any]:
    result: dict[str, Any] = {"machine": machine, "date": date, "status": "failed", "missing_items": []}
    svg_dir, history_dir, summary_dir = out / "svg", out / "history", out / "summary"
    for directory in (svg_dir, history_dir, summary_dir):
        directory.mkdir(parents=True, exist_ok=True)
    result.update(validate_machine(page, machine))
    if is_challenge(page):
        raise RuntimeError("Cloudflare challenge detected")

    requests: list[dict[str, Any]] = []
    responses: list[Any] = []
    page.on("request", lambda req: requests.append(request_meta(req.url, req.method)) if "nc-m06-001.php" in req.url else None)
    page.on("response", lambda resp: responses.append(resp) if "nc-m06-001.php" in resp.url else None)
    call_result = page.evaluate(
        """(args) => new Promise(resolve => {
          if (!window.api06 || typeof window.api06.show !== 'function') {
            resolve({ok:false, error:'api06.show unavailable'}); return;
          }
          window.api06._page = 1;
          const jq = window.api06.show(args.machine, args.date, true);
          jq.done(() => resolve({ok:true}));
          jq.fail((xhr, statusText) => resolve({ok:false, statusText:String(statusText || '')}));
        })""",
        {"machine": machine, "date": date},
    )
    page.wait_for_timeout(1000)
    result["api06_show_result"] = call_result
    result["requests"] = requests
    response_meta = []
    response_json = None
    for response in responses:
        info = {"status": response.status, "url": redact_url(response.url)}
        try:
            response_json = response.json()
            info["json"] = True
        except Exception:
            info["json"] = False
        response_meta.append(info)
    result["responses"] = response_meta
    result["response_summary"] = {
        "YMD_biz": response_json.get("YMD_biz") if isinstance(response_json, dict) else None,
        "page": response_json.get("page") if isinstance(response_json, dict) else None,
        "pageMax": response_json.get("pageMax") if isinstance(response_json, dict) else None,
        "HistCount": response_json.get("HistCount") if isinstance(response_json, dict) else None,
    }
    if result["response_summary"]["YMD_biz"] != date:
        raise RuntimeError(
            f"rescue response date mismatch: requested={date}, response={result['response_summary']['YMD_biz']}"
        )
    if not requests or requests[0].get("cd_dai") != machine or requests[0].get("YMD_biz") != date or requests[0].get("page") != "1":
        raise RuntimeError("rescue request metadata mismatch")
    if not response_meta or response_meta[0].get("status") != 200 or not response_meta[0].get("json"):
        raise RuntimeError("rescue response was not HTTP 200 JSON")
    if result["response_summary"]["page"] not in (None, 1) or result["response_summary"]["pageMax"] not in (None, 1):
        raise RuntimeError("rescue initial request was not page=1")

    rows, history_meta = extract_history(page)
    history_path = history_dir / f"{machine}_history.csv"
    if rows:
        write_csv(history_path, rows)
        result.update({"history_csv": "ok", "history_path": str(history_path), "history_rows": len(rows)})
    else:
        result.update({"history_csv": "empty", "history_rows": 0})
        result["missing_items"].append("history_csv")
    result["history_more_available"] = bool(history_meta["moreExists"] and history_meta["moreVisible"])

    svg = page.locator(f"#CHART-{date} svg").first
    if svg.count() == 0:
        result["missing_items"].append("svg")
    else:
        svg_text = svg.evaluate("el => new XMLSerializer().serializeToString(el)")
        svg_info = validate_svg(svg_text)
        result["svg_validation"] = {**svg_info, "placeholder": is_placeholder_svg(svg_info)}
        if svg_info["xml"] == "ok" and not result["svg_validation"]["placeholder"]:
            svg_path = svg_dir / f"{machine}_{date}.svg"
            svg_path.write_text(svg_text, encoding="utf-8")
            result.update({"svg": "ok", "svg_path": str(svg_path)})
        else:
            result["svg"] = "placeholder" if result["svg_validation"]["placeholder"] else "invalid"
            result["missing_items"].append("svg")

    if include_summary:
        summary = extract_summary(page, machine)
        summary_path = summary_dir / f"{machine}_{date}_summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        result.update({"summary_json": "ok", "summary_path": str(summary_path), "summary_days": len(summary["days"])})
    else:
        result["summary_json"] = "skipped"

    if include_png:
        screenshots = out / "screenshots"
        screenshots.mkdir(parents=True, exist_ok=True)
        chart = page.locator(f"#CHART-{date}").first
        if chart.count() and chart.is_visible():
            png_path = screenshots / f"{machine}_{date}.png"
            chart.screenshot(path=str(png_path))
            result["png"] = "ok" if png_path.stat().st_size else "failed"
            result["png_path"] = str(png_path)
            result["png_bytes"] = png_path.stat().st_size
            if not png_path.stat().st_size:
                result["missing_items"].append("png")
        else:
            result["png"] = "skipped_not_visible"

    result["status"] = "complete" if not result["missing_items"] else "incomplete"
    result["captured_at"] = dt.datetime.now().astimezone().isoformat()
    return result


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest["timestamp"] = dt.datetime.now().astimezone().isoformat()
    completed = [r["machine"] for r in manifest.get("results", []) if r.get("status") == "complete"]
    manifest.setdefault("completed_machines", completed)
    remaining = list(manifest.get("remaining_machines", []))
    failed = [r["machine"] for r in manifest.get("results", []) if r.get("status") != "complete"]
    manifest["failed_machines"] = list(dict.fromkeys(failed + remaining))
    missing: dict[str, list[str]] = {}
    for result in manifest.get("results", []):
        if result.get("missing_items"):
            missing[result["machine"]] = result["missing_items"]
    manifest["missing_items"] = missing
    manifest["complete_count"] = len(completed)
    manifest["incomplete_count"] = max(0, len(manifest.get("machines", [])) - len(completed))
    if manifest.get("rate_limited"):
        manifest["status"] = "rate_limited"
    elif manifest.get("challenge_detected"):
        manifest["status"] = "challenge"
    elif manifest.get("aborted"):
        manifest["status"] = "aborted"
    else:
        manifest["status"] = "complete" if manifest.get("results") and not manifest["failed_machines"] else "incomplete"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
