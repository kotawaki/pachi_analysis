from __future__ import annotations

import argparse
import contextlib
import csv
import html as html_lib
import io
import json
import os
import re
from bisect import bisect_left
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image, ImageDraw

import analyze


ROOT = Path(__file__).resolve().parent
DEFAULT_CAPTURE_ROOT = ROOT / "captures" / "pscube" / "20260627" / "morning"
DEFAULT_OUT_DIR = ROOT / "csv" / "pscube_analyze"
MASTER_PATH = ROOT / "machine_master.csv"

T_START = 10 * 60
T_END = 22 * 60 + 30


def m2t(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def t2m(text: str) -> int:
    hour, minute = map(int, text.split(":"))
    return hour * 60 + minute


def machine_key(value: str) -> str:
    text = str(value).strip()
    return text if len(text) >= 4 else text.zfill(4)


def read_html_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8", "cp932", "shift_jis"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


class HistoryTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_td = False
        self.current_row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() == "td":
            self.in_td = True

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "td":
            self.in_td = False
        elif tag == "tr" and self.current_row:
            self.rows.append(self.current_row)
            self.current_row = []

    def handle_data(self, data: str) -> None:
        if self.in_td:
            text = data.strip()
            if text:
                self.current_row.append(text)


def parse_history_rows(html_path: Path) -> list[list[str]]:
    parser = HistoryTableParser()
    parser.feed(read_html_text(html_path))
    return [
        row for row in parser.rows
        if len(row) >= 4 and row[1].count(":") == 1 and row[2].strip().isdigit()
    ]


def load_capture_notes(capture_root: Path) -> dict[str, dict]:
    notes_path = capture_root / "capture_notes.json"
    if not notes_path.exists():
        return {}
    data = json.loads(notes_path.read_text(encoding="utf-8"))
    result: dict[str, dict] = {}
    for note in data.get("notes", []):
        for machine in note.get("machines", []):
            result[machine_key(machine)] = note
    return result


def history_allowed(machine: str, notes: dict[str, dict]) -> bool:
    note = notes.get(machine_key(machine))
    if not note:
        return True
    return bool(note.get("use_for_history", True))


def events_from_history(rows: list[list[str]], machine: str, pachinko_mode: str) -> list[dict]:
    has_slot_reg = any(row[3] == "REG" for row in rows)
    events = []
    for row in rows:
        number, time_text, start_text, status = row[:4]
        if not (time_text.count(":") == 1 and start_text.isdigit()):
            continue

        if has_slot_reg:
            if status != "REG":
                continue
            game_count = int(start_text)
            event_kind = "slot_reg_initial" if game_count >= 10 else "slot_reg_continue"
        else:
            event_kind = "pachinko_initial" if status == "初当り" else "pachinko_bonus"

        minute = t2m(time_text)
        if minute < T_START or minute > T_END:
            continue
        events.append({
            "no": number,
            "time": time_text,
            "minute": minute,
            "start_count": int(start_text),
            "status": status,
            "event_kind": event_kind,
        })

    events.sort(key=lambda item: (item["minute"], int(item["no"]) if str(item["no"]).isdigit() else 0))
    return events


def load_adjust() -> dict:
    adjust = dict(analyze.DEFAULT_ADJUST)
    adjust_path = Path(analyze.ADJUST_JSON)
    if adjust_path.exists():
        try:
            adjust.update(json.loads(adjust_path.read_text(encoding="utf-8")))
        except Exception:
            pass
    return adjust


def time_to_x(minutes: int, axes: dict) -> int:
    x1000 = axes["x1000_px"]
    x1200 = axes["x1200_px"]
    x1800 = axes["x1800_px"]
    x2230 = axes["x2230_px"]
    if minutes <= 12 * 60:
        ratio = (minutes - 10 * 60) / (2 * 60)
        return round(x1000 + ratio * (x1200 - x1000))
    if minutes <= 18 * 60:
        ratio = (minutes - 12 * 60) / (6 * 60)
        return round(x1200 + ratio * (x1800 - x1200))
    ratio = (minutes - 18 * 60) / (4 * 60 + 30)
    return round(x1800 + ratio * (x2230 - x1800))


def clean_axis_number(text: str) -> int | None:
    cleaned = html_lib.unescape(text).replace("\xa0", "").replace(",", "").strip()
    cleaned = re.sub(r"[^0-9-]", "", cleaned)
    if cleaned in {"", "-"}:
        return None
    return int(cleaned)


def extract_chart_block(html_text: str, date_str: str) -> str:
    marker = f'id="CHART-{date_str}"'
    start = html_text.find(marker)
    if start < 0:
        return ""
    start = html_text.rfind("<ul", 0, start)
    if start < 0:
        return ""
    end = html_text.find("</svg>", start)
    if end < 0:
        return ""
    return html_text[start:end + len("</svg>")]


def extract_group_block(block: str, class_name: str) -> str:
    start = block.find(class_name)
    if start < 0:
        return ""
    start = block.rfind("<g", 0, start)
    end = block.find("</g></g>", start)
    if end < 0:
        end = block.find("</svg>", start)
    return block[start:end]


def parse_svg_labels(block: str) -> list[tuple[float, float, str]]:
    labels = []
    pattern = re.compile(
        r'<text\b[^>]*transform="translate\(([-\d.]+),([-\d.]+)\)"[^>]*>'
        r'.*?<tspan[^>]*>(.*?)</tspan>',
        re.S,
    )
    for match in pattern.finditer(block):
        labels.append((float(match.group(1)), float(match.group(2)), html_lib.unescape(match.group(3))))
    return labels


def interpolate_x(labels: dict[int, float], minute: int) -> float:
    if minute in labels:
        return labels[minute]
    ordered = sorted(labels.items())
    for (left_minute, left_x), (right_minute, right_x) in zip(ordered, ordered[1:]):
        if left_minute <= minute <= right_minute:
            ratio = (minute - left_minute) / (right_minute - left_minute)
            return left_x + ratio * (right_x - left_x)
    if len(ordered) >= 2:
        if minute < ordered[0][0]:
            (m1, x1), (m2, x2) = ordered[0], ordered[1]
        else:
            (m1, x1), (m2, x2) = ordered[-2], ordered[-1]
        ratio = (minute - m1) / (m2 - m1)
        return x1 + ratio * (x2 - x1)
    raise ValueError("not enough time labels")


def extract_svg_points(block: str) -> list[tuple[int, int]]:
    points: list[tuple[int, int]] = []
    path_pattern = re.compile(r'<path\b[^>]*class="[^"]*amcharts-graph-stroke[^"]*"[^>]*', re.S)
    path_match = path_pattern.search(block)
    if path_match:
        d_match = re.search(r'd="([^"]+)"', path_match.group(0))
        if d_match:
            points = [
                (round(float(x)), round(float(y)))
                for x, y in re.findall(r'[ML]\s*([-\d.]+),([-\d.]+)', d_match.group(1))
                if not (float(x) == 0 and float(y) == 0)
            ]

    if not points:
        bullet_pattern = re.compile(
            r'<circle\b(?=[^>]*class="amcharts-graph-bullet")[^>]*'
            r'transform="translate\(([-\d.]+),([-\d.]+)\)"',
            re.S,
        )
        for match in bullet_pattern.finditer(block):
            points.append((round(float(match.group(1))), round(float(match.group(2)))))

    by_x: dict[int, int] = {}
    for x, y in points:
        if x >= 0:
            by_x[x] = y
    return sorted(by_x.items())


def extract_svg_zero_y(block: str) -> float | None:
    for match in re.finditer(r'<path\b[^>]*class="[^"]*amcharts-axis-zero-grid[^"]*"[^>]*>', block, re.S):
        d_match = re.search(r'd="([^"]+)"', match.group(0))
        if not d_match:
            continue
        coord_match = re.search(r'M\s*[-\d.]+,([-\d.]+)', d_match.group(1))
        if coord_match:
            return float(coord_match.group(1))
    return None


def build_axes_and_points_from_svg(html_path: Path, date_str: str) -> tuple[dict, list[tuple[int, int]]]:
    html_text = read_html_text(html_path)
    block = extract_chart_block(html_text, date_str)
    if not block:
        raise ValueError("chart svg block not found")

    svg_width = 774
    svg_height = 712
    svg_match = re.search(r'<svg\b[^>]*width:\s*([\d.]+)px;\s*height:\s*([\d.]+)px', block)
    if svg_match:
        svg_width = round(float(svg_match.group(1)))
        svg_height = round(float(svg_match.group(2)))
    translate_x = 97
    translate_y = 20
    translate_match = re.search(r'class="amcharts-plot-area"[^>]*transform="translate\(([-\d.]+),([-\d.]+)\)"', block)
    if translate_match:
        translate_x = round(float(translate_match.group(1)))
        translate_y = round(float(translate_match.group(2)))

    time_labels = {}
    value_labels = {}
    for x, y, label in parse_svg_labels(block):
        label = label.strip()
        if re.fullmatch(r"\d{1,2}:\d{2}", label):
            time_labels[t2m(label)] = x
            continue
        value = clean_axis_number(label)
        if value is not None and (abs(value) >= 1000 or value == 0):
            value_labels[value] = y
    zero_y = extract_svg_zero_y(block)
    if zero_y is not None:
        value_labels[0] = zero_y

    if 0 not in value_labels or not any(v > 0 for v in value_labels) or not any(v < 0 for v in value_labels):
        raise ValueError("insufficient value axis labels")
    if len(time_labels) < 2:
        raise ValueError("insufficient time axis labels")

    positive_values = [value for value in value_labels if value > 0]
    negative_values = [value for value in value_labels if value < 0]
    y2_value = max(positive_values)
    y1_value = min(negative_values)
    points = extract_svg_points(block)
    if len(points) < 2:
        raise ValueError("chart points not found")

    axes = {
        "graph_left": min(x for x, _y in points),
        "graph_right": max(x for x, _y in points),
        "graph_top": min(y for _x, y in points),
        "graph_bottom": max(y for _x, y in points),
        "y2_px": round(value_labels[y2_value]),
        "y0_px": round(value_labels[0]),
        "y1_px": round(value_labels[y1_value]),
        "x1000_px": round(interpolate_x(time_labels, 10 * 60)),
        "x1200_px": round(interpolate_x(time_labels, 12 * 60)),
        "x1800_px": round(interpolate_x(time_labels, 18 * 60)),
        "x2230_px": round(interpolate_x(time_labels, 22 * 60 + 30)),
        "y2_value": y2_value,
        "y1_value": y1_value,
        "source": "svg",
        "svg_width": svg_width,
        "svg_height": svg_height,
        "svg_translate_x": translate_x,
        "svg_translate_y": translate_y,
    }
    return axes, points


def build_capture_axes(img: Image.Image, y2_value: int = 10000, y1_value: int = -10000) -> dict:
    width, height = img.size
    line_groups = [
        (y, score)
        for y, score in horizontal_line_groups(img, 5, min(height - 1, 930), 70, width - 32)
        if y > 40
    ]
    strong_groups = [(y, score) for y, score in line_groups if score >= width * 0.65]

    bottom_candidates = [
        y
        for index, (y, _score) in enumerate(strong_groups[:-1])
        if y >= 360 and 24 <= strong_groups[index + 1][0] - y <= 45
    ]
    if bottom_candidates:
        y1 = bottom_candidates[-1]
    elif strong_groups:
        y1 = strong_groups[-1][0]
    else:
        y1 = detect_horizontal_line(img, 360, min(height - 1, 820), 60, width - 25)

    left = detect_vertical_line(img, 65, 100, 5, y1, prefer_first_group=True)
    y2 = detect_axis_top(img, left, y1)
    y0_from_frame = round(
        y2 + (abs(y2_value) / max(1, abs(y2_value) + abs(y1_value))) * (y1 - y2)
    )
    zero_candidates = [
        y for y, _score in line_groups
        if 80 <= y <= y1 - 40
    ]
    zero_target = y0_from_frame
    white_zero_candidates = [
        y for y, _score in white_horizontal_line_groups(img, 80, y1 - 40, 70, width - 32)
        if abs(y - zero_target) <= max(35, (y1 - 80) * 0.18)
    ]
    zero_candidates = sorted(set(zero_candidates + white_zero_candidates))
    if zero_candidates:
        candidate = min(zero_candidates, key=lambda y: abs(y - zero_target))
        y0 = candidate if abs(candidate - zero_target) <= 12 else y0_from_frame
    else:
        y0 = y0_from_frame
    frame_right = round(left + 461)
    pre_spacing = (frame_right - left) / 5.0
    x0900 = left
    x2100 = round(left + pre_spacing * 4.0)

    return {
        "graph_left": x0900,
        "graph_right": frame_right,
        "graph_top": y2,
        "graph_bottom": y1,
        "y2_px": y2,
        "y0_px": y0,
        "y1_px": y1,
        "x1000_px": round(x0900 + pre_spacing / 3.0),
        "x1200_px": round(left + pre_spacing),
        "x1800_px": round(left + pre_spacing * 3.0),
        "x2230_px": round(x2100 + pre_spacing * 0.5),
        "y2_value": y2_value,
        "y1_value": y1_value,
        "source": "image",
    }


def build_axes_and_points_from_image(
    chart_path: Path,
    adjust: dict,
    y2_value: int | None = None,
    y1_value: int | None = None,
) -> tuple[dict, list[tuple[int, int]]]:
    img = Image.open(chart_path).convert("RGB")
    axes = build_capture_axes(img, y2_value or 10000, y1_value or -10000)

    with contextlib.redirect_stdout(io.StringIO()):
        color = analyze.detect_color(img, axes)
        points = analyze.trace_line(img, axes, color)
    if points:
        first_x, first_y = points[0]
        y_shift = axes["y0_px"] - first_y
        if abs(y_shift) > 0:
            points = [(x, y + y_shift) for x, y in points]
    return axes, points


def build_axes_and_points(chart_path: Path, html_path: Path, date_str: str, adjust: dict) -> tuple[dict, list[tuple[int, int]]]:
    svg_axes = None
    try:
        return build_axes_and_points_from_svg(html_path, date_str)
    except Exception:
        svg_axes = None
    try:
        return build_axes_and_points_from_image(
            chart_path,
            adjust,
            svg_axes.get("y2_value") if svg_axes else None,
            svg_axes.get("y1_value") if svg_axes else None,
        )
    except Exception:
        if svg_axes is None:
            raise
        return build_axes_and_points_from_svg(html_path, date_str)


def smooth_points(points: list[tuple[int, int]], axes: dict) -> list[dict]:
    converted = [
        {"x": x, "y": y, "t": analyze.px_to_time(x, axes), "v": analyze.px_to_val(y, axes)}
        for x, y in points
    ]
    smoothed = []
    window = 5
    for index, point in enumerate(converted):
        start = max(0, index - window)
        end = min(len(converted) - 1, index + window)
        values = converted[start:end + 1]
        smoothed.append({**point, "sv": sum(item["v"] for item in values) / len(values)})
    return smoothed


def densify_points_by_minute(points: list[tuple[int, int]], axes: dict) -> list[tuple[int, int]]:
    if len(points) < 2:
        return points
    ordered = sorted(points)
    xs = [x for x, _y in ordered]
    dense = []
    for minute in range(T_START, T_END + 1):
        x = time_to_x(minute, axes)
        index = bisect_left(xs, x)
        if index <= 0:
            y = ordered[0][1]
        elif index >= len(ordered):
            y = ordered[-1][1]
        else:
            left_x, left_y = ordered[index - 1]
            right_x, right_y = ordered[index]
            if right_x == left_x:
                y = right_y
            else:
                ratio = (x - left_x) / (right_x - left_x)
                y = left_y + ratio * (right_y - left_y)
        dense.append((x, round(y)))
    return dense


def interpolated_point_at_minute(points: list[dict], axes: dict, minute: int) -> dict:
    target_x = time_to_x(minute, axes)
    ordered = sorted(points, key=lambda point: point["x"])
    xs = [point["x"] for point in ordered]
    index = bisect_left(xs, target_x)
    if index <= 0:
        y = ordered[0]["y"]
    elif index >= len(ordered):
        y = ordered[-1]["y"]
    else:
        left = ordered[index - 1]
        right = ordered[index]
        if right["x"] == left["x"]:
            y = right["y"]
        else:
            ratio = (target_x - left["x"]) / (right["x"] - left["x"])
            y = left["y"] + ratio * (right["y"] - left["y"])
    value = analyze.px_to_val(y, axes)
    return {
        "x": target_x,
        "y": round(y),
        "t": minute,
        "v": value,
        "sv": value,
    }


def pick_event_segment(event: dict, next_event: dict | None, points: list[dict], axes: dict, min_gain: int, big_gain: int) -> dict | None:
    event_minute = event["minute"]
    end_to = min(T_END, event_minute + 55)
    if next_event:
        end_to = min(end_to, max(event_minute + 5, next_event["minute"] - 1))

    start_point = interpolated_point_at_minute(points, axes, event_minute)

    end_candidates = [
        point for point in points
        if start_point["t"] <= point["t"] <= end_to
    ]
    if not end_candidates:
        return None
    end_point = max(end_candidates, key=lambda point: point["sv"])
    gain = round(end_point["v"] - start_point["v"])

    if gain < min_gain:
        end_point = min(end_candidates, key=lambda point: abs(point["t"] - event_minute))
        gain = round(end_point["v"] - start_point["v"])
    if gain <= 0:
        return None

    kind = "大当り" if gain >= big_gain else "当り"
    return {
        "source_time": event["time"],
        "source_status": event["status"],
        "source_kind": event["event_kind"],
        "start_time": m2t(start_point["t"]),
        "start_ball": round(start_point["v"]),
        "end_time": m2t(end_point["t"]),
        "end_ball": round(end_point["v"]),
        "gain": gain,
        "type": kind,
        "x_start": start_point["x"],
        "y_start": start_point["y"],
        "x_end": end_point["x"],
        "y_end": end_point["y"],
        "duration": max(0, end_point["t"] - start_point["t"]),
    }


def is_initial_event(event: dict) -> bool:
    return event.get("event_kind") in {"pachinko_initial", "slot_reg_initial"}


def build_episodes(events: list[dict]) -> list[dict]:
    episodes = []
    current: dict | None = None
    for event in events:
        if is_initial_event(event):
            if current:
                episodes.append(current)
            current = {"events": [event]}
        elif current:
            current["events"].append(event)
        else:
            current = {"events": [event]}
    if current:
        episodes.append(current)

    for episode in episodes:
        items = episode["events"]
        episode["first"] = items[0]
        episode["last"] = items[-1]
        episode["source_time"] = "/".join(item["time"] for item in items)
        episode["source_status"] = "/".join(item["status"] for item in items)
        episode["source_kind"] = "/".join(item["event_kind"] for item in items)
    return episodes


def pick_episode_segment(
    episode: dict,
    next_episode: dict | None,
    points: list[dict],
    axes: dict,
    min_gain: int,
    big_gain: int,
    allow_nonpositive: bool = False,
) -> dict | None:
    first = episode["first"]
    last = episode["last"]
    event_minute = first["minute"]
    end_to = min(T_END, max(event_minute + 55, last["minute"] + 20))
    if next_episode:
        end_to = min(end_to, max(event_minute + 5, next_episode["first"]["minute"] - 1))

    start_point = interpolated_point_at_minute(points, axes, event_minute)

    end_candidates = [
        point for point in points
        if start_point["t"] <= point["t"] <= end_to
    ]
    if not end_candidates:
        return None
    end_point = max(end_candidates, key=lambda point: point["sv"])
    gain = round(end_point["v"] - start_point["v"])
    if gain < min_gain:
        end_point = min(end_candidates, key=lambda point: abs(point["t"] - event_minute))
        gain = round(end_point["v"] - start_point["v"])
    if gain <= 0 and not allow_nonpositive:
        return None

    kind = "大当り" if gain >= big_gain else "当り"
    return {
        "source_time": episode["source_time"],
        "source_status": episode["source_status"],
        "source_kind": episode["source_kind"],
        "start_time": m2t(start_point["t"]),
        "start_ball": round(start_point["v"]),
        "end_time": m2t(end_point["t"]),
        "end_ball": round(end_point["v"]),
        "gain": gain,
        "type": kind,
        "x_start": start_point["x"],
        "y_start": start_point["y"],
        "x_end": end_point["x"],
        "y_end": end_point["y"],
        "duration": max(0, end_point["t"] - start_point["t"]),
    }


def segments_from_events(events: list[dict], points: list[tuple[int, int]], axes: dict, min_gain: int, big_gain: int) -> list[dict]:
    if not events or len(points) < 10:
        return []
    smoothed = smooth_points(densify_points_by_minute(points, axes), axes)
    segments = []
    episodes = build_episodes(events)
    for index, episode in enumerate(episodes):
        next_episode = episodes[index + 1] if index + 1 < len(episodes) else None
        segment = pick_episode_segment(episode, next_episode, smoothed, axes, min_gain, big_gain)
        if not segment:
            segment = pick_episode_segment(
                episode,
                next_episode,
                smoothed,
                axes,
                min_gain,
                big_gain,
                allow_nonpositive=True,
            )
        if segment:
            segments.append(segment)
    return merge_overlapping_segments(segments, big_gain, keep_nonpositive=True)


def no_hit_segment(points: list[tuple[int, int]], axes: dict) -> dict | None:
    if len(points) < 2:
        return None
    dense = smooth_points(densify_points_by_minute(points, axes), axes)
    end_point = min(dense, key=lambda point: abs(point["t"] - T_END))
    return {
        "source_time": "",
        "source_status": "当たりなし",
        "source_kind": "no_hit",
        "start_time": "10:00",
        "start_ball": 0,
        "end_time": "22:30",
        "end_ball": round(end_point["v"]),
        "gain": round(end_point["v"]),
        "type": "当たりなし",
        "x_start": time_to_x(T_START, axes),
        "y_start": axes["y0_px"],
        "x_end": end_point["x"],
        "y_end": end_point["y"],
        "duration": T_END - T_START,
    }


def merge_overlapping_segments(segments: list[dict], big_gain: int, keep_nonpositive: bool = False) -> list[dict]:
    if not segments:
        return []

    merged: list[dict] = []
    for segment in sorted(segments, key=lambda item: (t2m(item["start_time"]), t2m(item["end_time"]))):
        if not merged:
            merged.append(dict(segment))
            continue

        current = merged[-1]
        starts_new_episode = any(
            key in segment.get("source_kind", "")
            for key in ("pachinko_initial", "slot_reg_initial")
        )
        if starts_new_episode or t2m(segment["start_time"]) > t2m(current["end_time"]):
            merged.append(dict(segment))
            continue

        starts = [current, segment]
        ends = [current, segment]
        start_segment = min(starts, key=lambda item: (item["start_ball"], t2m(item["start_time"])))
        end_segment = max(ends, key=lambda item: (item["end_ball"], t2m(item["end_time"])))

        current["source_time"] = "/".join(filter(None, [current.get("source_time", ""), segment.get("source_time", "")]))
        current["source_status"] = "/".join(filter(None, [current.get("source_status", ""), segment.get("source_status", "")]))
        current["source_kind"] = "/".join(filter(None, [current.get("source_kind", ""), segment.get("source_kind", "")]))
        current["start_time"] = start_segment["start_time"]
        current["start_ball"] = start_segment["start_ball"]
        current["x_start"] = start_segment["x_start"]
        current["y_start"] = start_segment["y_start"]
        current["end_time"] = end_segment["end_time"]
        current["end_ball"] = end_segment["end_ball"]
        current["x_end"] = end_segment["x_end"]
        current["y_end"] = end_segment["y_end"]
        current["gain"] = round(current["end_ball"] - current["start_ball"])
        current["duration"] = max(0, t2m(current["end_time"]) - t2m(current["start_time"]))
        current["type"] = "大当り" if current["gain"] >= big_gain else "当り"

    filtered = merged if keep_nonpositive else [segment for segment in merged if segment["gain"] > 0]
    return collapse_same_start_segments(filtered, big_gain, keep_nonpositive=keep_nonpositive)


def collapse_same_start_segments(segments: list[dict], big_gain: int, keep_nonpositive: bool = False) -> list[dict]:
    result: list[dict] = []
    by_start: dict[str, dict] = {}
    for segment in segments:
        key = segment["start_time"]
        if key not in by_start:
            by_start[key] = dict(segment)
            continue
        current = by_start[key]
        if segment["end_ball"] > current["end_ball"]:
            current["end_time"] = segment["end_time"]
            current["end_ball"] = segment["end_ball"]
            current["x_end"] = segment["x_end"]
            current["y_end"] = segment["y_end"]
        current["source_time"] = "/".join(filter(None, [current.get("source_time", ""), segment.get("source_time", "")]))
        current["source_status"] = "/".join(filter(None, [current.get("source_status", ""), segment.get("source_status", "")]))
        current["source_kind"] = "/".join(filter(None, [current.get("source_kind", ""), segment.get("source_kind", "")]))
        current["gain"] = round(current["end_ball"] - current["start_ball"])
        current["duration"] = max(0, t2m(current["end_time"]) - t2m(current["start_time"]))
        current["type"] = "大当り" if current["gain"] >= big_gain else "当り"

    for _start, segment in sorted(by_start.items(), key=lambda item: t2m(item[0])):
        if keep_nonpositive or segment["gain"] > 0:
            result.append(segment)
    return result


def bright_gray(pixel: tuple[int, int, int], threshold: int = 75) -> bool:
    r, g, b = pixel
    return r > threshold and g > threshold and b > threshold and abs(r - g) < 35 and abs(g - b) < 35


def strongest_line(items: list[tuple[int, int]], prefer_first_group: bool = True) -> int:
    if not items:
        raise ValueError("line candidates not found")
    best = max(count for _pos, count in items)
    candidates = [(pos, count) for pos, count in items if count >= best * 0.92]
    candidates.sort()
    if prefer_first_group:
        return candidates[0][0]
    return candidates[-1][0]


def detect_horizontal_line(img: Image.Image, y_start: int, y_end: int, x_start: int, x_end: int) -> int:
    scores = []
    for y in range(y_start, y_end + 1):
        count = sum(1 for x in range(x_start, x_end + 1) if bright_gray(img.getpixel((x, y)), 80))
        scores.append((y, count))
    return strongest_line(scores)


def horizontal_line_groups(img: Image.Image, y_start: int, y_end: int, x_start: int, x_end: int) -> list[tuple[int, int]]:
    rows = []
    for y in range(y_start, y_end + 1):
        count = sum(1 for x in range(x_start, x_end + 1) if bright_gray(img.getpixel((x, y)), 35))
        if count > (x_end - x_start) * 0.60:
            rows.append((y, count))

    groups = []
    current = []
    for item in rows:
        if not current or item[0] <= current[-1][0] + 1:
            current.append(item)
        else:
            groups.append(current)
            current = [item]
    if current:
        groups.append(current)

    result = []
    for group in groups:
        weight = sum(count for _y, count in group)
        y = round(sum(y * count for y, count in group) / weight)
        result.append((y, max(count for _y, count in group)))
    return result


def white_horizontal_line_groups(
    img: Image.Image,
    y_start: int,
    y_end: int,
    x_start: int,
    x_end: int,
    min_ratio: float = 0.20,
) -> list[tuple[int, int]]:
    rows = []
    width = x_end - x_start + 1
    for y in range(y_start, y_end + 1):
        count = sum(1 for x in range(x_start, x_end + 1) if bright_gray(img.getpixel((x, y)), 80))
        if count >= width * min_ratio:
            rows.append((y, count))

    groups = []
    current = []
    for item in rows:
        if not current or item[0] <= current[-1][0] + 1:
            current.append(item)
        else:
            groups.append(current)
            current = [item]
    if current:
        groups.append(current)

    result = []
    for group in groups:
        weight = sum(count for _y, count in group)
        y = round(sum(y * count for y, count in group) / weight)
        result.append((y, max(count for _y, count in group)))
    return result


def detect_axis_top(img: Image.Image, left: int, y1: int) -> int:
    for y in range(5, max(6, y1 - 80)):
        hits = 0
        for dy in range(0, 5):
            yy = y + dy
            if yy >= y1:
                break
            if any(bright_gray(img.getpixel((max(0, min(img.size[0] - 1, left + dx)), yy)), 65) for dx in range(-1, 2)):
                hits += 1
        if hits >= 3:
            return y
    return max(5, y1 - 444)


def detect_vertical_line(img: Image.Image, x_start: int, x_end: int, y_start: int, y_end: int, prefer_first_group: bool = True) -> int:
    scores = []
    for x in range(x_start, x_end + 1):
        count = sum(1 for y in range(y_start, y_end + 1) if bright_gray(img.getpixel((x, y)), 65))
        scores.append((x, count))
    return strongest_line(scores, prefer_first_group=prefer_first_group)


def vertical_line_groups(img: Image.Image, x_start: int, x_end: int, y_start: int, y_end: int) -> list[tuple[int, int]]:
    cols = []
    for x in range(x_start, x_end + 1):
        count = sum(1 for y in range(y_start, y_end + 1) if bright_gray(img.getpixel((x, y)), 45))
        if count > 35:
            cols.append((x, count))

    groups = []
    current = []
    for item in cols:
        if not current or item[0] <= current[-1][0] + 1:
            current.append(item)
        else:
            groups.append(current)
            current = [item]
    if current:
        groups.append(current)

    result = []
    for group in groups:
        weight = sum(count for _x, count in group)
        x = round(sum(x * count for x, count in group) / weight)
        result.append((x, max(count for _x, count in group)))
    return result


def nearest_candidate(candidates: list[tuple[int, int]], target: float, tolerance: float) -> int | None:
    filtered = [(abs(x - target), -score, x) for x, score in candidates if abs(x - target) <= tolerance]
    if not filtered:
        return None
    filtered.sort()
    return filtered[0][2]


def calibrate_overlay_axes(img: Image.Image, axes: dict) -> dict:
    capture_axes = build_capture_axes(
        img,
        axes.get("y2_value", 20000),
        axes.get("y1_value", -20000),
    )
    y2 = capture_axes["y2_px"]
    y0 = capture_axes["y0_px"]
    y1 = capture_axes["y1_px"]
    left = capture_axes["graph_left"]
    # P'sCUBE mobile captures keep the outer chart frame stable, while internal
    # grid-line strength changes by machine/range. Anchor the timeline to the
    # outer 09:00-24:00 frame to avoid per-chart x-axis drift.
    frame_right = round(left + 461)
    pre_spacing = (frame_right - left) / 5.0
    post_spacing = pre_spacing
    x1800 = round(left + pre_spacing * 3.0)
    x2100 = round(left + pre_spacing * 4.0)
    x0900 = round(left)
    x1200 = round(left + pre_spacing)
    x2400 = round(x2100 + post_spacing)
    return {
        "x0900": x0900,
        "x1000": round(x0900 + pre_spacing / 3.0),
        "x1200": x1200,
        "x1800": round(x1800),
        "x2100": round(x2100),
        "x2230": round(x2100 + post_spacing * 0.5),
        "x2400": x2400,
        "y2": y2,
        "y0": y0,
        "y1": y1,
    }


def value_to_image_y(value: int | float, axes: dict, overlay_axes: dict) -> int:
    y0 = overlay_axes["y0"]
    if value >= 0:
        y2 = overlay_axes["y2"]
        y2_value = abs(axes.get("y2_value", 20000))
        return round(y0 - (value / y2_value) * (y0 - y2))
    y1 = overlay_axes["y1"]
    y1_value = abs(axes.get("y1_value", -20000))
    return round(y0 + (abs(value) / y1_value) * (y1 - y0))


def time_to_overlay_x(minutes: int, overlay_axes: dict) -> int:
    mapped_axes = {
        "x1000_px": overlay_axes["x1000"],
        "x1200_px": overlay_axes["x1200"],
        "x1800_px": overlay_axes["x1800"],
        "x2230_px": overlay_axes["x2230"],
    }
    return time_to_x(minutes, mapped_axes)


def svg_to_image_point(x: int | float, y: int | float, axes: dict, img: Image.Image, overlay_axes: dict | None = None) -> tuple[int, int]:
    if axes.get("source") != "svg":
        return round(x), round(y)
    overlay_axes = overlay_axes or calibrate_overlay_axes(img, axes)
    minutes = analyze.px_to_time(x, axes)
    value = analyze.px_to_val(y, axes)
    return time_to_overlay_x(minutes, overlay_axes), value_to_image_y(value, axes, overlay_axes)


def overlay_source_label(source_time: str) -> str:
    parts = [part for part in source_time.split("/") if part]
    if len(parts) <= 1:
        return source_time
    return f"{parts[0]} (+{len(parts) - 1})"


def save_overlay(chart_path: Path, axes: dict, points: list[tuple[int, int]], segments: list[dict], out_path: Path) -> None:
    img = Image.open(chart_path).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    overlay_axes = calibrate_overlay_axes(img.convert("RGB"), axes) if axes.get("source") == "svg" else None

    def p(x: int | float, y: int | float) -> tuple[int, int]:
        return svg_to_image_point(x, y, axes, img, overlay_axes)

    if overlay_axes:
        left = overlay_axes["x0900"]
        right = overlay_axes["x2400"]
        top = overlay_axes["y2"]
        bottom = overlay_axes["y1"]
        draw.rectangle([left, top, right, bottom], outline=(255, 255, 255, 190), width=2)
    else:
        left = min(0, axes["x1000_px"])
        right = axes["x2230_px"]
        top = axes["y2_px"]
        bottom = axes["y1_px"]
        draw.rectangle([p(left, top), p(right, bottom)], outline=(255, 255, 255, 190), width=2)

    for y, color, label in [
        (axes["y2_px"], (100, 200, 255, 220), f"+{axes['y2_value']:,}"),
        (axes["y0_px"], (255, 255, 255, 220), "0"),
        (axes["y1_px"], (255, 80, 80, 220), f"{axes['y1_value']:,}"),
    ]:
        y_img = p(axes["x1200_px"], y)[1]
        draw.line([(left, y_img), (right, y_img)], fill=color, width=2)
        draw.text((left + 4, y_img - 16), label, fill=color)

    for x, label in [
        (axes["x1000_px"], "10:00"),
        (axes["x1200_px"], "12:00"),
        (axes["x1800_px"], "18:00"),
        (axes["x2230_px"], "22:30"),
    ]:
        x_img = p(x, axes["y0_px"])[0]
        draw.line([(x_img, top), (x_img, bottom)], fill=(255, 220, 0, 170), width=2)
        draw.text((x_img + 3, top + 4), label, fill=(255, 220, 0, 230))

    if len(points) > 1:
        draw.line([p(x, y) for x, y in points], fill=(255, 255, 0, 140), width=2)

    radius = max(5, img.size[0] // 120)
    for index, segment in enumerate(segments, 1):
        sx, sy = p(segment["x_start"], segment["y_start"])
        ex, ey = p(segment["x_end"], segment["y_end"])
        end_color = (127, 255, 0, 235) if segment["type"] == "大当り" else (0, 220, 255, 235)
        draw.line([(sx, sy), (ex, ey)], fill=end_color[:3] + (185,), width=2)
        draw.ellipse([sx - radius, sy - radius, sx + radius, sy + radius], fill=(255, 64, 64, 235))
        draw.ellipse([ex - radius, ey - radius, ex + radius, ey + radius], fill=end_color)
        draw.text((ex + radius + 2, ey - 10), f"{index}", fill=end_color)
        draw.text((sx + radius + 2, sy + 2), overlay_source_label(segment["source_time"]), fill=(255, 180, 180, 230))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    Image.alpha_composite(img, overlay).convert("RGB").save(out_path)


def get_machine_info(machine: str) -> tuple[str, str]:
    if not MASTER_PATH.exists():
        return "", ""
    with MASTER_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("machine", "")).strip().lstrip("0") == machine.lstrip("0"):
                return str(row.get("group", "")).strip(), str(row.get("island", "")).strip()
    return "", ""


def chart_daily_ohlc(points: list[tuple[int, int]], axes: dict) -> dict:
    values = [round(analyze.px_to_val(y, axes)) for _x, y in sorted(points)]
    if not values:
        values = [0]
    return {
        "open": values[0],
        "high": max(values),
        "low": min(values),
        "close": values[-1],
        "source": axes.get("source", "chart"),
        "point_count": len(values),
    }


def save_daily_ohlc_row(ohlc: dict, date_label: str, machine: str, group: str, island: str, ohlc_path: Path) -> None:
    fields = [
        "Date", "Machine", "Group", "Island",
        "Open", "High", "Low", "Close", "Source", "PointCount",
    ]
    ohlc_path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "Date": date_label,
        "Machine": machine.zfill(3),
        "Group": group,
        "Island": island,
        "Open": ohlc["open"],
        "High": ohlc["high"],
        "Low": ohlc["low"],
        "Close": ohlc["close"],
        "Source": ohlc["source"],
        "PointCount": ohlc["point_count"],
    }
    existing = []
    if ohlc_path.exists():
        with ohlc_path.open("r", encoding="utf-8-sig", newline="") as handle:
            existing = [item for item in csv.DictReader(handle) if item.get("Machine") != machine.zfill(3)]
    with ohlc_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(existing + [row])


def save_csv_rows(segments: list[dict], date_label: str, machine: str, group: str, island: str, csv_path: Path) -> None:
    fields = ["Date", "Machine", "Group", "Island", "種別", "開始時刻", "開始差玉", "終了時刻", "終了差玉", "増減差玉", "時間(分)"]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for segment in segments:
        rows.append({
            "Date": date_label,
            "Machine": machine.zfill(3),
            "Group": group,
            "Island": island,
            "種別": segment["type"],
            "開始時刻": segment["start_time"],
            "開始差玉": segment["start_ball"],
            "終了時刻": segment["end_time"],
            "終了差玉": segment["end_ball"],
            "増減差玉": segment["gain"],
            "時間(分)": segment["duration"],
        })

    existing = []
    if csv_path.exists():
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            existing = [row for row in csv.DictReader(handle) if row.get("Machine") != machine.zfill(3)]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(existing + rows)


def process_machine(chart_path: Path, html_path: Path, capture_root: Path, out_dir: Path, adjust: dict, notes: dict[str, dict], args) -> dict:
    stem_parts = chart_path.stem.split("_")
    date_str = stem_parts[0]
    machine = stem_parts[1]
    date_label = f"{date_str[:4]}/{date_str[4:6]}/{date_str[6:8]}"
    group, island = get_machine_info(machine)

    if not history_allowed(machine, notes):
        return {"machine": machine, "status": "history_skipped_by_note", "events": 0, "segments": 0}
    if not html_path.exists() and not args.allow_missing_html:
        return {"machine": machine, "status": "missing_html", "events": 0, "segments": 0}

    rows = parse_history_rows(html_path) if html_path.exists() else []
    events = events_from_history(rows, machine, args.pachinko_mode)
    axes, points = build_axes_and_points(chart_path, html_path, date_str, adjust)
    if events:
        segments = segments_from_events(events, points, axes, args.min_gain, args.big_gain)
        status = "ok"
    else:
        segment = no_hit_segment(points, axes)
        segments = [segment] if segment else []
        status = "no_history_events"
    csv_path = out_dir / date_str / f"{date_str}_analyze.csv"
    ohlc_path = out_dir / date_str / f"{date_str}_daily_ohlc.csv"
    overlay_path = out_dir / date_str / "overlay" / f"{date_str}_{machine}_overlay.png"
    if not args.dry_run:
        save_csv_rows(segments, date_label, machine, group, island, csv_path)
        save_daily_ohlc_row(chart_daily_ohlc(points, axes), date_label, machine, group, island, ohlc_path)
    if args.overlay:
        save_overlay(chart_path, axes, points, segments, overlay_path)
    return {
        "machine": machine,
        "status": status,
        "events": len(events),
        "segments": len(segments),
        "csv": str(csv_path),
        "daily_ohlc": str(ohlc_path),
        "overlay": str(overlay_path) if args.overlay else None,
    }


def iter_chart_paths(capture_root: Path, machines: set[str] | None) -> list[Path]:
    chart_dir = capture_root / "chart"
    paths = sorted(chart_dir.glob("*_chart.png"))
    if machines:
        paths = [path for path in paths if path.stem.split("_")[1] in machines]
    return paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze P'sCUBE HTML history + chart screenshots.")
    parser.add_argument("capture_root", nargs="?", type=Path, default=DEFAULT_CAPTURE_ROOT)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--machines", nargs="*", help="Machine numbers, e.g. 0039 0040 1177")
    parser.add_argument("--pachinko-mode", choices=["all", "initial"], default="all")
    parser.add_argument("--min-gain", type=int, default=300)
    parser.add_argument("--big-gain", type=int, default=1500)
    parser.add_argument("--overlay", action="store_true", help="Save visual check images with detected segments.")
    parser.add_argument(
        "--allow-missing-html",
        action="store_true",
        help="Treat missing HTML as no-history and analyze the chart image only.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    capture_root = args.capture_root
    notes = load_capture_notes(capture_root)
    adjust = load_adjust()
    machines = {machine_key(item) for item in args.machines} if args.machines else None

    results = []
    for chart_path in iter_chart_paths(capture_root, machines):
        date_str, machine, *_ = chart_path.stem.split("_")
        html_path = capture_root / "html" / f"{date_str}_{machine}.html"
        print(f"\n=== {machine} ===")
        result = process_machine(chart_path, html_path, capture_root, args.out_dir, adjust, notes, args)
        results.append(result)
        print(result)

    ok = sum(1 for result in results if result["status"] == "ok")
    skipped = len(results) - ok
    print(f"\nsummary: ok={ok} skipped_or_empty={skipped} total={len(results)} out={args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
