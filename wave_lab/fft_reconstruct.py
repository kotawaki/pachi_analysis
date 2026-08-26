from __future__ import annotations

import argparse
import csv
import html
import itertools
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from daily_ohlc import load_chart_daily_ohlc


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_ROOT = ROOT / "wave_lab" / "output"
PAGES_OUTPUT_ROOT = ROOT / "docs" / "wave_lab"
PAGES_MACHINES = ("075", "077", "049", "056")

# Analysis settings are intentionally explicit and easy to change for later experiments.
TOP_COMPONENTS = 3
MIN_PERIOD_DAYS = 2.0
MAX_PERIOD_FRACTION_OF_DATA = 0.80
PHASE_BIN_WIDTH_DEGREES = 45.0
DIRECTION_SLOPE_EPSILON_FRACTION = 0.02
# Main Wave Lab history is refreshed through the latest formal daily OHLC.
# The frozen 2026-08-16 -> 2026-08-17 answer-check constants below remain
# unchanged and continue to protect the historical prediction artifacts.
REGIME_CUTOFF_DATE = "2026-08-26"
MIN_REGIME_OBSERVATIONS = 21
REGIME_REFERENCE_CHANGE = 0.25
REGIME_SHIFT_PCT = 0.20
REGIME_STABLE_SCORE_THRESHOLD = 0.80
REGIME_UNSTABLE_SCORE_THRESHOLD = 0.40
PHASE_SPACE_CENTER_X = 300.0
PHASE_SPACE_CENTER_Y = 190.0
PHASE_SPACE_BASE_RADIUS = 78.0
PHASE_SPACE_WAVE_RADIUS = 32.0
PHASE_CONVERGENCE_QUANTILE = 0.80
CONVERGENCE_SCORE_BIN_WIDTH = 0.20
TRANSFORMATION_MIN_IMPROVEMENT = 0.20
TRANSFORMATION_MIN_SHAPE_SIMILARITY = 0.65
TRANSFORMATION_MIN_ROTATION_DEG = 15.0
IDENTITY_DISTANCE_QUANTILE = 0.25
IDENTITY_CENTROID_QUANTILE = 0.25
TRANSFORMATION_PROBABILITY_TYPES = frozenset({
    "ROLE_SWAP", "ROTATION", "ROTATION_PLUS_ROLE_SWAP", "INVERSION_180",
    "MIRROR_VERTICAL", "MIRROR_HORIZONTAL",
})
PROBABILITY_GEOMETRY_DISTANCE_THRESHOLD = 0.35
PROBABILITY_MIN_LEVEL1_SUPPORT = 3
PROBABILITY_MEDIUM_SUPPORT = 3
PROBABILITY_HIGH_SUPPORT = 6
PROBABILITY_BUCKET_WIDTH = 0.20


def next_power_of_two(value: int) -> int:
    result = 1
    while result < value:
        result *= 2
    return result


def fft(values: list[complex]) -> list[complex]:
    """Iterative radix-2 FFT. Input length must be a power of two."""
    n = len(values)
    if n == 1:
        return values[:]
    if n & (n - 1):
        raise ValueError("FFT input length must be a power of two")

    result = values[:]
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            result[i], result[j] = result[j], result[i]

    length = 2
    while length <= n:
        angle = -2.0 * math.pi / length
        root = complex(math.cos(angle), math.sin(angle))
        for start in range(0, n, length):
            factor = 1.0 + 0.0j
            half = length // 2
            for offset in range(half):
                even = result[start + offset]
                odd = factor * result[start + offset + half]
                result[start + offset] = even + odd
                result[start + offset + half] = even - odd
                factor *= root
        length *= 2
    return result


def linear_detrend(values: list[float]) -> list[float]:
    n = len(values)
    if n < 2:
        return values[:]
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    denominator = sum((i - mean_x) ** 2 for i in range(n))
    slope = sum((i - mean_x) * (value - mean_y) for i, value in enumerate(values)) / denominator
    intercept = mean_y - slope * mean_x
    return [value - (intercept + slope * i) for i, value in enumerate(values)]


def parse_machine(value: str) -> str:
    try:
        return str(int(value)).zfill(3)
    except ValueError:
        return value.strip().zfill(3)


def clean_number(value: object) -> float:
    return float(str(value).replace(",", "").strip())


def normalize_date(value: str) -> str:
    text = value.replace("/", "-").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def load_machine_rows(machine: str, cutoff_date: str | None = None) -> list[dict]:
    daily, _meta = load_chart_daily_ohlc({machine})
    rows = []
    for date, values in sorted(daily.get(str(int(machine)), {}).items()):
        normalized = normalize_date(date)
        if cutoff_date and normalized > cutoff_date:
            continue
        rows.append({
            "date": normalized,
            "machine": machine,
            "open": clean_number(values["open"]),
            "high": clean_number(values["high"]),
            "low": clean_number(values["low"]),
            "close": clean_number(values["close"]),
        })
    return rows


def phase_position(index: int, frequency: float, coefficient: complex, fft_length: int) -> float:
    """0=trough, 90=rising, 180=crest, 270=falling for the displayed wave."""
    coefficient_phase = math.atan2(coefficient.imag, coefficient.real)
    angle = 2.0 * math.pi * frequency * index + coefficient_phase + math.pi
    return math.degrees(angle) % 360.0


def add_period_roles(components: list[dict]) -> list[dict]:
    roles = {id(component): role for component, role in zip(
        sorted(components, key=lambda item: item["period_days"], reverse=True),
        ("LONG", "MID", "SHORT"),
    )}
    for component in components:
        component["role"] = roles[id(component)]
    return components


def extract_components(signal: list[float], n: int, preprocessing: str) -> list[dict]:
    fft_length = next_power_of_two(n)
    spectrum = fft([complex(value, 0.0) for value in signal] + [0.0j] * (fft_length - n))
    max_period = n * MAX_PERIOD_FRACTION_OF_DATA
    candidates = []
    for k in range(1, fft_length // 2 + 1):
        frequency = k / fft_length
        period = 1.0 / frequency
        if period < MIN_PERIOD_DAYS or period > max_period:
            continue
        coefficient = spectrum[k]
        candidates.append({
            "frequency": frequency, "period_days": period,
            "amplitude": 2.0 * abs(coefficient) / n,
            "phase_radians": math.atan2(coefficient.imag, coefficient.real),
            "power": abs(coefficient) ** 2, "coefficient": coefficient,
        })
    total_power = sum(item["power"] for item in candidates)
    candidates.sort(key=lambda item: item["power"], reverse=True)
    selected = candidates[:TOP_COMPONENTS]
    components = [{
        "preprocessing": preprocessing, "rank": rank,
        "frequency": item["frequency"], "period_days": item["period_days"],
        "amplitude": item["amplitude"],
        "phase": math.degrees(item["phase_radians"]) % 360.0,
        "relative_power": item["power"] / total_power if total_power else 0.0,
        "phase_definition": "0=trough, 90=rising, 180=crest, 270=falling",
        "n_observations": n,
        "n_fft": fft_length,
        "sampling_interval": 1.0,
        "frequency_unit": "cycles/observation",
        "period_basis": "observations (not calendar days)",
        "_coefficient": item["coefficient"],
    } for rank, item in enumerate(selected, 1)]
    return add_period_roles(components)


def direction_series(values: list[float], amplitude: float) -> list[str]:
    """Classify local slope; turning labels require opposite slopes on both sides."""
    epsilon = max(abs(amplitude) * DIRECTION_SLOPE_EPSILON_FRACTION, 1e-9)
    result = []
    for index, value in enumerate(values):
        previous_slope = values[index] - values[index - 1] if index else values[min(1, len(values) - 1)] - value
        next_slope = values[index + 1] - value if index + 1 < len(values) else value - values[index - 1]
        if previous_slope < -epsilon and next_slope > epsilon:
            result.append("turning_up")
        elif previous_slope > epsilon and next_slope < -epsilon:
            result.append("turning_down")
        elif (previous_slope + next_slope) >= 0:
            result.append("rising")
        else:
            result.append("falling")
    return result


def direction_is_up(direction: str) -> bool:
    return direction in {"rising", "turning_up"}


def validate_reconstruction() -> None:
    """Numerical regression: known two-bin signal must be recovered by FFT components."""
    n = 64
    signal = [
        3.0 * math.cos(2.0 * math.pi * 4 * index / n + 0.2)
        + 2.0 * math.cos(2.0 * math.pi * 10 * index / n - 0.3)
        for index in range(n)
    ]
    components = [component for component in extract_components(signal, n, "synthetic_test") if component["amplitude"] > 1e-8]
    periods = {round(component["period_days"], 6) for component in components}
    assert periods == {6.4, 16.0}, periods
    reconstructed = []
    for index in range(n):
        total = 0.0
        for component in components:
            coefficient = component["_coefficient"]
            theta = 2.0 * math.pi * component["frequency"] * index + math.atan2(coefficient.imag, coefficient.real)
            total += component["amplitude"] * math.cos(theta)
        reconstructed.append(total)
    max_error = max(abs(expected - actual) for expected, actual in zip(signal, reconstructed))
    assert max_error < 1e-9, max_error


def validate_daily_reconstruction(daily: list[dict], components: list[dict]) -> None:
    """Check component metadata, phase bounds, and same-index combined-wave alignment."""
    for component in components:
        assert abs(component["frequency"] * component["period_days"] - 1.0) < 1e-12
        assert component["n_observations"] == len(daily)
        assert component["n_fft"] >= component["n_observations"]
    for row in daily:
        total = row["wave1_value"] + row["wave2_value"] + row["wave3_value"]
        assert abs(row["combined_wave"] - total) < 1e-6
        for wave in (1, 2, 3):
            assert 0.0 <= row[f"wave{wave}_phase"] < 360.0


def analyze(rows: list[dict]) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    n = len(rows)
    if n < 4:
        raise ValueError("FFT解析には4件以上のOHLC履歴が必要です")
    raw = [row["close"] for row in rows]
    detrended = linear_detrend(raw)
    centered = [value - sum(detrended) / n for value in detrended]
    variant_signals = [
        ("raw_close", raw),
        ("linear_trend_removed", detrended),
        ("mean_removed", [value - sum(raw) / n for value in raw]),
        ("linear_trend_and_mean_removed", centered),
    ]
    comparison_components = [component for label, signal in variant_signals for component in extract_components(signal, n, label)]
    components = extract_components(centered, n, "linear_trend_and_mean_removed")
    fft_length = next_power_of_two(n)

    all_wave_values = []
    for component in components:
        coefficient = component["_coefficient"]
        all_wave_values.append([
            component["amplitude"] * math.cos(
                2.0 * math.pi * component["frequency"] * index
                + math.atan2(coefficient.imag, coefficient.real)
            )
            for index in range(n)
        ])
    all_directions = [
        direction_series(values, component["amplitude"])
        for values, component in zip(all_wave_values, components)
    ]

    daily = []
    for index, row in enumerate(rows):
        values = []
        phases = []
        for component in components:
            coefficient = component["_coefficient"]
            theta = 2.0 * math.pi * component["frequency"] * index + math.atan2(coefficient.imag, coefficient.real)
            values.append(component["amplitude"] * math.cos(theta))
            phases.append(phase_position(index, component["frequency"], coefficient, fft_length))
        while len(values) < TOP_COMPONENTS:
            values.append(0.0)
            phases.append(0.0)
        directions = [all_directions[wave][index] for wave in range(TOP_COMPONENTS)]
        next_row = rows[index + 1] if index + 1 < n else None
        daily.append({
            "date": row["date"],
            "machine": row["machine"],
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "bullish": row["close"] > row["open"],
            "next_day_bullish": (next_row["close"] > next_row["open"]) if next_row else None,
            "wave1_phase": phases[0], "wave2_phase": phases[1], "wave3_phase": phases[2],
            "wave1_value": values[0], "wave2_value": values[1], "wave3_value": values[2],
            "combined_wave": sum(values),
            "wave1_direction": directions[0], "wave2_direction": directions[1], "wave3_direction": directions[2],
            "wave1_up": direction_is_up(directions[0]), "wave2_up": direction_is_up(directions[1]), "wave3_up": direction_is_up(directions[2]),
            "wave_direction_pattern": "-".join("UP" if direction_is_up(direction) else "DOWN" for direction in directions),
        })
    return components, daily, centered, comparison_components


def write_csv(path: Path, rows: Iterable[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def add_pages_navigation(document: str) -> str:
    """Add a small relative link from an individual Pages page to the selector."""
    navigation = (
        '<nav class="wave-lab-pages-nav"><a href="../">← Wave Lab</a></nav>'
        '<style>.wave-lab-pages-nav{max-width:1400px;margin:8px auto;padding:0 18px;'
        'font:600 13px system-ui,sans-serif}.wave-lab-pages-nav a{color:#9fd8ff;'
        'text-decoration:none}.wave-lab-pages-nav a:hover{text-decoration:underline}</style>'
    )
    return document.replace("<body>", "<body>" + navigation, 1)


def _pages_summary(machine: str) -> dict:
    output_dir = OUTPUT_ROOT / machine
    components = list(csv.DictReader((output_dir / "fft_components.csv").open(encoding="utf-8", newline="")))
    components = [row for row in components if row.get("preprocessing") == "linear_trend_and_mean_removed"]
    by_role = {row.get("role"): row for row in components}
    regimes = list(csv.DictReader((output_dir / "period_regime_daily.csv").open(encoding="utf-8", newline="")))
    latest = regimes[-1] if regimes else {}
    return {
        "machine": machine,
        "long_period": float(by_role["LONG"]["period_days"]),
        "mid_period": float(by_role["MID"]["period_days"]),
        "short_period": float(by_role["SHORT"]["period_days"]),
        "regime": latest.get("regime", ""),
    }


def write_pages_index() -> Path:
    """Write the self-contained four-machine selector for GitHub Pages."""
    cards = []
    for machine in PAGES_MACHINES:
        try:
            summary = _pages_summary(machine)
        except (FileNotFoundError, KeyError, ValueError):
            continue
        cards.append(
            f'''<article class="card"><div class="machine">{summary["machine"]}</div>
<div class="experimental">Experimental research</div>
<dl><dt>LONG</dt><dd>{summary["long_period"]:.3f} observations</dd>
<dt>MID</dt><dd>{summary["mid_period"]:.3f} observations</dd>
<dt>SHORT</dt><dd>{summary["short_period"]:.3f} observations</dd>
<dt>2026-08-15 regime</dt><dd>{html.escape(summary["regime"])}</dd></dl>
<a class="open" href="./{summary["machine"]}/">Open Wave Lab →</a></article>'''
        )
    document = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wave Lab</title><style>
:root{{color-scheme:dark;background:#07111c;color:#eaf3fb;font-family:system-ui,sans-serif}}
body{{margin:0;min-height:100vh;background:radial-gradient(circle at 20% 0%,#17314a,#07111c 55%)}}
main{{max-width:1100px;margin:0 auto;padding:56px 22px 80px}}
h1{{font-size:clamp(34px,6vw,64px);margin:0 0 8px;letter-spacing:-.04em}}
.lead{{color:#a9bfd0;margin:0 0 34px;max-width:720px;line-height:1.6}}
.notice{{border:1px solid #2a536d;background:#0c2030;padding:14px 16px;border-radius:12px;color:#b9d8e9;margin-bottom:24px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:18px}}
.card{{background:rgba(14,31,47,.92);border:1px solid #29465c;border-radius:18px;padding:22px;box-shadow:0 14px 34px #0004}}
.machine{{font-size:38px;font-weight:800;letter-spacing:.04em}}
.experimental{{color:#72d0ff;font-size:12px;text-transform:uppercase;letter-spacing:.12em;margin:3px 0 18px}}
dl{{display:grid;grid-template-columns:auto 1fr;gap:8px 12px;margin:0 0 22px;font-size:14px}}
dt{{color:#86a9bf}}dd{{margin:0;text-align:right}}
.open{{display:block;text-align:center;background:#50b8ed;color:#03111b;font-weight:800;padding:11px;border-radius:10px;text-decoration:none}}
.open:hover{{background:#8bd8ff}}
</style></head><body><main><h1>Wave Lab</h1>
<p class="lead">FFT / 周期 / 位相を使った波形解析の研究ページ。台番号を選択して、Full-period / As-of Phase Space、Period Regime、Alignment / Convergenceを確認できます。</p>
<div class="notice">全期間FFTを用いたretrospective / exploratory analysisです。表示される関連性は予測性能を意味しません。</div>
<section class="grid">{"".join(cards)}</section></main></body></html>'''
    PAGES_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    path = PAGES_OUTPUT_ROOT / "index.html"
    path.write_text(document, encoding="utf-8")
    return path


def phase_stats(daily: list[dict]) -> list[dict]:
    result = []
    for wave in (1, 2, 3):
        phase_key = f"wave{wave}_phase"
        for bin_start in range(0, 360, int(PHASE_BIN_WIDTH_DEGREES)):
            bin_end = bin_start + int(PHASE_BIN_WIDTH_DEGREES)
            samples = [row for row in daily if bin_start <= row[phase_key] < bin_end]
            bullish = sum(row["bullish"] for row in samples)
            result.append({
                "wave": f"wave{wave}", "phase_start": bin_start, "phase_end": bin_end,
                "samples": len(samples), "bullish_count": bullish,
                "bullish_rate": bullish / len(samples) * 100.0 if samples else "",
            })
    return result


def phase_nextday_stats(daily: list[dict], components: list[dict]) -> list[dict]:
    result = []
    role_order = {"LONG": 0, "MID": 1, "SHORT": 2}
    ordered_components = sorted(enumerate(components, 1), key=lambda item: role_order[item[1]["role"]])
    for wave, component in ordered_components:
        phase_key = f"wave{wave}_phase"
        for bin_start in range(0, 360, int(PHASE_BIN_WIDTH_DEGREES)):
            bin_end = bin_start + int(PHASE_BIN_WIDTH_DEGREES)
            samples = [
                row for row in daily
                if row["next_day_bullish"] is not None and bin_start <= row[phase_key] < bin_end
            ]
            bullish = sum(row["next_day_bullish"] for row in samples)
            result.append({
                "wave": f"wave{wave}", "role": component["role"],
                "phase_bin": f"{bin_start}-{bin_end}",
                "samples": len(samples), "next_day_bullish_count": bullish,
                "next_day_bullish_rate": bullish / len(samples) * 100.0 if samples else "",
            })
    return result


def pattern_nextday_stats(daily: list[dict]) -> list[dict]:
    result = []
    patterns = ("-".join(pattern) for pattern in itertools.product(("UP", "DOWN"), repeat=3))
    for pattern in patterns:
        samples = [
            row for row in daily
            if row["next_day_bullish"] is not None and row["wave_direction_pattern"] == pattern
        ]
        bullish = sum(row["next_day_bullish"] for row in samples)
        result.append({
            "pattern": pattern, "samples": len(samples),
            "next_day_bullish_count": bullish,
            "next_day_bullish_rate": bullish / len(samples) * 100.0 if samples else "",
        })
    return result


def phase_space_point(row: dict, wave: int, component: dict) -> tuple[float, float]:
    """Return the same wrapped-wave coordinate used by the browser Phase Space."""
    return phase_space_xy(
        float(row[f"wave{wave}_phase"]),
        float(row[f"wave{wave}_value"]),
        float(component["amplitude"]),
    )


def phase_space_xy(phase_degrees: float, value: float, amplitude: float) -> tuple[float, float]:
    """Return the shared Phase Space coordinate from phase/value/amplitude."""
    amplitude = abs(float(amplitude))
    angle = math.radians(phase_degrees)
    radius = PHASE_SPACE_BASE_RADIUS
    if amplitude > 0.0:
        radius += PHASE_SPACE_WAVE_RADIUS * value / amplitude
    return (
        PHASE_SPACE_CENTER_X + radius * math.sin(angle),
        PHASE_SPACE_CENTER_Y + radius * math.cos(angle),
    )


def clip01(value: float) -> float:
    return max(0.0, min(1.0, value))


def centroid_region(x: float, y: float) -> str:
    """Classify by the dominant centroid axis; exact ties use horizontal regions."""
    dx = x - PHASE_SPACE_CENTER_X
    dy = y - PHASE_SPACE_CENTER_Y
    if abs(dx) >= abs(dy):
        return "RIGHT" if dx >= 0.0 else "LEFT"
    # SVG y grows downward, so smaller y is TOP and larger y is BOTTOM.
    return "BOTTOM" if dy >= 0.0 else "TOP"


def quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def phase_convergence_analysis(daily: list[dict], components: list[dict]) -> tuple[list[dict], float]:
    """Calculate exploratory convergence metrics from the existing Phase Space coordinates."""
    result = []
    for row in daily:
        points = [phase_space_point(row, wave, components[wave - 1]) for wave in (1, 2, 3)]
        distances = {
            "distance_long_mid": math.dist(points[0], points[1]),
            "distance_mid_short": math.dist(points[1], points[2]),
            "distance_short_long": math.dist(points[2], points[0]),
        }
        centroid_x = sum(point[0] for point in points) / 3.0
        centroid_y = sum(point[1] for point in points) / 3.0
        max_pair_distance = max(distances.values())
        mean_pair_distance = sum(distances.values()) / 3.0
        # 220 is the maximum diameter of the display's base+wave-radius envelope.
        convergence_score = clip01(1.0 - max_pair_distance / (2.0 * (PHASE_SPACE_BASE_RADIUS + PHASE_SPACE_WAVE_RADIUS)))
        result.append({
            "date": row["date"], "machine": row["machine"],
            "long_phase": row["wave1_phase"], "mid_phase": row["wave2_phase"], "short_phase": row["wave3_phase"],
            "long_x": points[0][0], "long_y": points[0][1],
            "mid_x": points[1][0], "mid_y": points[1][1],
            "short_x": points[2][0], "short_y": points[2][1],
            **distances, "mean_pair_distance": mean_pair_distance,
            "max_pair_distance": max_pair_distance, "min_pair_distance": min(distances.values()),
            "convergence_score": convergence_score,
            "centroid_x": centroid_x, "centroid_y": centroid_y,
            "centroid_region": centroid_region(centroid_x, centroid_y),
            "bullish": row["bullish"], "next_day_bullish": row["next_day_bullish"],
            "wave_direction_pattern": row["wave_direction_pattern"],
        })
    threshold = quantile([row["convergence_score"] for row in result], PHASE_CONVERGENCE_QUANTILE)
    for row in result:
        row["phase_convergence"] = row["convergence_score"] >= threshold
    return result, threshold


def phase_convergence_stats(rows: list[dict]) -> list[dict]:
    result = []
    for index in range(5):
        start = index * CONVERGENCE_SCORE_BIN_WIDTH
        end = start + CONVERGENCE_SCORE_BIN_WIDTH
        samples = [row for row in rows if (start <= row["convergence_score"] <= end if index == 4 else start <= row["convergence_score"] < end)]
        next_samples = [row for row in samples if row["next_day_bullish"] is not None]
        bullish = sum(row["bullish"] for row in samples)
        next_bullish = sum(row["next_day_bullish"] for row in next_samples)
        result.append({
            "score_bin": f"{start:.1f}-{end:.1f}", "samples": len(samples), "bullish_count": bullish,
            "bullish_rate": bullish / len(samples) * 100.0 if samples else "",
            "next_day_samples": len(next_samples), "next_day_bullish_count": next_bullish,
            "next_day_bullish_rate": next_bullish / len(next_samples) * 100.0 if next_samples else "",
        })
    return result


def phase_convergence_region_stats(rows: list[dict]) -> list[dict]:
    result = []
    for region in ("TOP", "RIGHT", "BOTTOM", "LEFT"):
        for converged in (False, True):
            samples = [row for row in rows if row["centroid_region"] == region and row["phase_convergence"] is converged]
            next_samples = [row for row in samples if row["next_day_bullish"] is not None]
            bullish = sum(row["bullish"] for row in samples)
            next_bullish = sum(row["next_day_bullish"] for row in next_samples)
            result.append({
                "centroid_region": region, "phase_convergence": converged, "samples": len(samples),
                "bullish_count": bullish, "bullish_rate": bullish / len(samples) * 100.0 if samples else "",
                "next_day_samples": len(next_samples), "next_day_bullish_count": next_bullish,
                "next_day_bullish_rate": next_bullish / len(next_samples) * 100.0 if next_samples else "",
            })
    return result


def phase_position_rows(rows: list[dict], convergence_rows: list[dict]) -> list[dict]:
    """Export the exact Phase Space coordinates plus simple vertical-position labels."""
    result = []
    for row, convergence in zip(rows, convergence_rows):
        points = {
            "LONG": (float(convergence["long_x"]), float(convergence["long_y"]), convergence["centroid_region"]),
            "MID": (float(convergence["mid_x"]), float(convergence["mid_y"]), convergence["centroid_region"]),
            "SHORT": (float(convergence["short_x"]), float(convergence["short_y"]), convergence["centroid_region"]),
        }
        regions = {}
        top_roles = []
        for role, (x, y, _unused) in points.items():
            dx = x - PHASE_SPACE_CENTER_X
            dy = y - PHASE_SPACE_CENTER_Y
            # The four-region centroid/point classifier is axis-dominant; it is distinct from
            # per-wave TOP/BOTTOM, which uses only y < center_y (TOP) or y >= center_y (BOTTOM).
            if abs(dx) >= abs(dy):
                region = "RIGHT" if dx >= 0.0 else "LEFT"
            else:
                region = "BOTTOM" if dy >= 0.0 else "TOP"
            regions[role] = region
            if y < PHASE_SPACE_CENTER_Y:
                top_roles.append(role)
        pattern = "+".join(top_roles) if top_roles else "NONE"
        result.append({
            "date": row["date"], "machine": row["machine"],
            "long_x": points["LONG"][0], "long_y": points["LONG"][1], "long_region": regions["LONG"],
            "long_top_side": points["LONG"][1] < PHASE_SPACE_CENTER_Y,
            "mid_x": points["MID"][0], "mid_y": points["MID"][1], "mid_region": regions["MID"],
            "mid_top_side": points["MID"][1] < PHASE_SPACE_CENTER_Y,
            "short_x": points["SHORT"][0], "short_y": points["SHORT"][1], "short_region": regions["SHORT"],
            "short_top_side": points["SHORT"][1] < PHASE_SPACE_CENTER_Y,
            "top_wave_count": len(top_roles), "top_wave_pattern": pattern,
            "centroid_x": convergence["centroid_x"], "centroid_y": convergence["centroid_y"],
            "centroid_y_offset": float(convergence["centroid_y"]) - PHASE_SPACE_CENTER_Y,
            "centroid_region": convergence["centroid_region"],
            "bullish": row["bullish"], "next_day_bullish": row["next_day_bullish"],
        })
    return result


def _position_rate_row(category: str, value: str, samples: list[dict]) -> dict:
    next_samples = [row for row in samples if row["next_day_bullish"] is not None]
    bullish = sum(row["bullish"] for row in samples)
    next_bullish = sum(row["next_day_bullish"] for row in next_samples)
    return {
        "category": category, "value": value, "samples": len(samples),
        "bullish_count": bullish, "bullish_rate": bullish / len(samples) * 100.0 if samples else "",
        "next_day_samples": len(next_samples), "next_day_bullish_count": next_bullish,
        "next_day_bullish_rate": next_bullish / len(next_samples) * 100.0 if next_samples else "",
    }


def phase_position_stats(rows: list[dict]) -> list[dict]:
    result = []
    for role in ("LONG", "MID", "SHORT"):
        field = role.lower() + "_region"
        for region in ("TOP", "RIGHT", "BOTTOM", "LEFT"):
            result.append(_position_rate_row(role + "_REGION", region, [row for row in rows if row[field] == region]))
        side_field = role.lower() + "_top_side"
        for side, is_top in (("TOP", True), ("BOTTOM", False)):
            result.append(_position_rate_row(role + "_SIDE", side, [row for row in rows if row[side_field] is is_top]))
    for region in ("TOP", "RIGHT", "BOTTOM", "LEFT"):
        result.append(_position_rate_row("CENTROID_REGION", region, [row for row in rows if row["centroid_region"] == region]))
    for count in range(4):
        result.append(_position_rate_row("TOP_WAVE_COUNT", str(count), [row for row in rows if row["top_wave_count"] == count]))
    result.append(_position_rate_row("TOP_WAVE_COUNT", "2+", [row for row in rows if row["top_wave_count"] >= 2]))
    return result


def phase_position_pattern_stats(rows: list[dict]) -> list[dict]:
    patterns = ("NONE", "LONG", "MID", "SHORT", "LONG+MID", "LONG+SHORT", "MID+SHORT", "LONG+MID+SHORT")
    return [_position_rate_row("TOP_WAVE_PATTERN", pattern, [row for row in rows if row["top_wave_pattern"] == pattern]) for pattern in patterns]


def validate_phase_position_rows(rows: list[dict], convergence_rows: list[dict]) -> None:
    assert len(rows) == len(convergence_rows)
    for row, convergence in zip(rows, convergence_rows):
        assert 0 <= row["top_wave_count"] <= 3
        assert row["top_wave_pattern"] in {"NONE", "LONG", "MID", "SHORT", "LONG+MID", "LONG+SHORT", "MID+SHORT", "LONG+MID+SHORT"}
        assert math.isclose(row["centroid_x"], convergence["centroid_x"])
        assert math.isclose(row["centroid_y"], convergence["centroid_y"])
        for key in ("long_x", "long_y", "mid_x", "mid_y", "short_x", "short_y", "centroid_x", "centroid_y"):
            assert math.isfinite(float(row[key]))


ASOF_THRESHOLD_MIN_SAMPLES = 5


def _asof_region(x: float, y: float) -> str:
    return centroid_region(x, y)


def _asof_top_pattern(top_roles: list[str]) -> str:
    return "+".join(top_roles) if top_roles else "NONE"


def _asof_metric_fields() -> list[str]:
    return [
        "long_k", "long_frequency", "long_period", "long_rank", "long_relative_power",
        "mid_k", "mid_frequency", "mid_period", "mid_rank", "mid_relative_power",
        "short_k", "short_frequency", "short_period", "short_rank", "short_relative_power",
        "long_phase", "long_wave_value", "long_amplitude", "long_x", "long_y", "long_region", "long_top_side",
        "mid_phase", "mid_wave_value", "mid_amplitude", "mid_x", "mid_y", "mid_region", "mid_top_side",
        "short_phase", "short_wave_value", "short_amplitude", "short_x", "short_y", "short_region", "short_top_side",
        "top_wave_count", "top_wave_pattern", "centroid_x", "centroid_y", "centroid_y_offset", "centroid_region",
        "phase_alignment_score", "alignment_threshold_asof", "high_alignment_asof",
        "max_pair_distance", "convergence_score", "convergence_threshold_asof", "phase_convergence_asof",
    ]


def asof_phase_space_history(rows: list[dict], regime_rows: list[dict]) -> list[dict]:
    """Build a point-in-time Phase Space using only each date's expanding FFT prefix."""
    regime_by_date = {row["date"]: row for row in regime_rows}
    history = []
    alignment_scores: list[float] = []
    convergence_scores: list[float] = []
    metric_fields = _asof_metric_fields()
    for index, row in enumerate(rows):
        as_of = row["date"]
        regime = regime_by_date.get(as_of, {})
        base = {
            "date": as_of, "machine": row["machine"], "status": "INSUFFICIENT_HISTORY",
            "n_observations": index + 1, "n_fft": "", "regime": regime.get("regime", "INSUFFICIENT_HISTORY"),
            "bullish": row["bullish"], "next_day_bullish": row["next_day_bullish"],
        }
        base.update({field: "" for field in metric_fields})
        if index + 1 < MIN_REGIME_OBSERVATIONS:
            history.append(base)
            continue

        prefix_rows = rows[: index + 1]
        assert prefix_rows[-1]["date"] == as_of
        assert max(prefix_row["date"] for prefix_row in prefix_rows) <= as_of
        prefix_components, prefix_daily, _centered, _comparison = analyze(prefix_rows)
        prefix_convergence, _unused_threshold = phase_convergence_analysis(prefix_daily, prefix_components)
        current_daily = prefix_daily[-1]
        current_convergence = prefix_convergence[-1]
        role_components = {component["role"]: component for component in prefix_components}
        role_order = ("LONG", "MID", "SHORT")
        role_wave = {"LONG": 1, "MID": 2, "SHORT": 3}
        current = {
            **base, "status": "VALID", "n_fft": int(prefix_components[0]["n_fft"]),
        }
        for role in role_order:
            component = role_components[role]
            wave = role_wave[role]
            current.update({
                role.lower() + "_k": round(float(component["frequency"]) * int(component["n_fft"])),
                role.lower() + "_frequency": component["frequency"],
                role.lower() + "_period": component["period_days"],
                role.lower() + "_rank": component["rank"],
                role.lower() + "_relative_power": component["relative_power"],
                role.lower() + "_phase": current_convergence[role.lower() + "_phase"],
                role.lower() + "_wave_value": current_daily[f"wave{wave}_value"],
                role.lower() + "_amplitude": component["amplitude"],
                role.lower() + "_x": current_convergence[role.lower() + "_x"],
                role.lower() + "_y": current_convergence[role.lower() + "_y"],
                role.lower() + "_region": current_convergence[role.lower() + "_x"] is not None and _asof_region(float(current_convergence[role.lower() + "_x"]), float(current_convergence[role.lower() + "_y"])),
                role.lower() + "_top_side": float(current_convergence[role.lower() + "_y"]) < PHASE_SPACE_CENTER_Y,
            })
        top_roles = [role for role in role_order if current[role.lower() + "_top_side"]]
        current.update({
            "top_wave_count": len(top_roles), "top_wave_pattern": _asof_top_pattern(top_roles),
            "centroid_x": current_convergence["centroid_x"], "centroid_y": current_convergence["centroid_y"],
            "centroid_y_offset": float(current_convergence["centroid_y"]) - PHASE_SPACE_CENTER_Y,
            "centroid_region": current_convergence["centroid_region"],
            "phase_alignment_score": phase_alignment_score({
                "wave1_phase": current_convergence["long_phase"],
                "wave2_phase": current_convergence["mid_phase"],
                "wave3_phase": current_convergence["short_phase"],
            }),
            "max_pair_distance": current_convergence["max_pair_distance"],
            "convergence_score": current_convergence["convergence_score"],
        })
        alignment_scores.append(float(current["phase_alignment_score"]))
        convergence_scores.append(float(current["convergence_score"]))
        if len(alignment_scores) >= ASOF_THRESHOLD_MIN_SAMPLES:
            current["alignment_threshold_asof"] = quantile(alignment_scores, PHASE_CONVERGENCE_QUANTILE)
            current["high_alignment_asof"] = current["phase_alignment_score"] >= current["alignment_threshold_asof"]
        if len(convergence_scores) >= ASOF_THRESHOLD_MIN_SAMPLES:
            current["convergence_threshold_asof"] = quantile(convergence_scores, PHASE_CONVERGENCE_QUANTILE)
            current["phase_convergence_asof"] = current["convergence_score"] >= current["convergence_threshold_asof"]
        history.append(current)
    return history


def validate_asof_phase_space(history: list[dict], cutoff_date: str) -> None:
    assert len(history) > 0
    for index, row in enumerate(history):
        assert row["date"] <= cutoff_date
        assert row["n_observations"] == index + 1
        if index + 1 < MIN_REGIME_OBSERVATIONS:
            assert row["status"] == "INSUFFICIENT_HISTORY"
            assert row["long_phase"] == ""
        else:
            assert row["status"] == "VALID"
            assert row["regime"] != "INSUFFICIENT_HISTORY"
            for role in ("long", "mid", "short"):
                assert 0.0 <= float(row[role + "_phase"]) < 360.0
                assert math.isfinite(float(row[role + "_x"]))
                assert math.isfinite(float(row[role + "_y"]))
            assert 0 <= int(row["top_wave_count"]) <= 3
            assert 0.0 <= float(row["phase_alignment_score"]) <= 1.0
            assert 0.0 <= float(row["convergence_score"]) <= 1.0


def _angular_error(predicted: float, actual: float) -> float:
    difference = abs(float(predicted) - float(actual)) % 360.0
    return min(difference, 360.0 - difference)


def _prediction_geometry(points: list[tuple[float, float]]) -> dict:
    centroid_x = sum(point[0] for point in points) / 3.0
    centroid_y = sum(point[1] for point in points) / 3.0
    top_roles = [role for role, point in zip(("LONG", "MID", "SHORT"), points) if point[1] < PHASE_SPACE_CENTER_Y]
    return {
        "centroid_x": centroid_x,
        "centroid_y": centroid_y,
        "centroid_y_offset": centroid_y - PHASE_SPACE_CENTER_Y,
        "centroid_region": centroid_region(centroid_x, centroid_y),
        "top_wave_count": len(top_roles),
        "top_wave_pattern": _asof_top_pattern(top_roles),
    }


def _sum_point_distances(left: list[tuple[float, float]], right: list[tuple[float, float]]) -> float:
    return sum(math.dist(a, b) for a, b in zip(left, right))


def _rotate_points(points: list[tuple[float, float]], degrees: float) -> list[tuple[float, float]]:
    angle = math.radians(degrees)
    cosine, sine = math.cos(angle), math.sin(angle)
    result = []
    for x, y in points:
        dx, dy = x - PHASE_SPACE_CENTER_X, y - PHASE_SPACE_CENTER_Y
        result.append((PHASE_SPACE_CENTER_X + cosine * dx - sine * dy, PHASE_SPACE_CENTER_Y + sine * dx + cosine * dy))
    return result


def _mirror_points(points: list[tuple[float, float]], mirror_type: str) -> list[tuple[float, float]]:
    result = []
    for x, y in points:
        if mirror_type == "VERTICAL":
            result.append((2.0 * PHASE_SPACE_CENTER_X - x, y))
        else:
            result.append((x, 2.0 * PHASE_SPACE_CENTER_Y - y))
    return result


def _best_rotation(points: list[tuple[float, float]], actual: list[tuple[float, float]]) -> tuple[float, list[tuple[float, float]], float]:
    dot = cross = 0.0
    for (px, py), (ax, ay) in zip(points, actual):
        pdx, pdy = px - PHASE_SPACE_CENTER_X, py - PHASE_SPACE_CENTER_Y
        adx, ady = ax - PHASE_SPACE_CENTER_X, ay - PHASE_SPACE_CENTER_Y
        dot += pdx * adx + pdy * ady
        cross += pdx * ady - pdy * adx
    degrees = math.degrees(math.atan2(cross, dot)) if dot or cross else 0.0
    transformed = _rotate_points(points, degrees)
    return degrees, transformed, _sum_point_distances(transformed, actual)


def _shape_metrics(points: list[tuple[float, float]]) -> dict:
    sides = {
        "long_mid": math.dist(points[0], points[1]),
        "mid_short": math.dist(points[1], points[2]),
        "short_long": math.dist(points[2], points[0]),
    }
    perimeter = sum(sides.values())
    area = abs((points[1][0] - points[0][0]) * (points[2][1] - points[0][1]) - (points[1][1] - points[0][1]) * (points[2][0] - points[0][0])) / 2.0
    normalized = {key: value / perimeter if perimeter else 0.0 for key, value in sides.items()}
    return {"sides": sides, "perimeter": perimeter, "area": area, "normalized_sides": normalized}


def _shape_similarity(predicted: dict, actual: dict) -> float:
    difference = sum(abs(predicted["normalized_sides"][key] - actual["normalized_sides"][key]) for key in predicted["sides"])
    return clip01(1.0 - difference / 2.0)


def _permutation_name(permutation: tuple[int, int, int]) -> str:
    names = ("LONG", "MID", "SHORT")
    if permutation == (0, 1, 2):
        return "IDENTITY"
    return "->".join(names[index] for index in permutation)


def _permuted_points(points: list[tuple[float, float]], permutation: tuple[int, int, int]) -> list[tuple[float, float]]:
    # The tuple says which predicted role is compared with actual LONG/MID/SHORT.
    return [points[index] for index in permutation]


def phase_transformation_rows(predictions: list[dict], daily: list[dict], asof_by_date: dict[str, dict] | None = None) -> list[dict]:
    """Classify historical D -> D+1 geometry only; never reads beyond the prediction CSV."""
    source_bullish = {row["date"]: bool(row["bullish"]) for row in daily}
    results = []
    permutations = {
        "IDENTITY": (0, 1, 2),
        "LONG_MID_SWAP": (1, 0, 2),
        "LONG_SHORT_SWAP": (2, 1, 0),
        "MID_SHORT_SWAP": (0, 2, 1),
        "3_CYCLE_A": (1, 2, 0),
        "3_CYCLE_B": (2, 0, 1),
    }
    for prediction in predictions:
        if prediction.get("status") != "VALID_PREDICTION" or prediction.get("long_angular_error_deg", "") == "":
            continue
        predicted = [(float(prediction[f"{role}_predicted_x"]), float(prediction[f"{role}_predicted_y"])) for role in ("long", "mid", "short")]
        actual = [(float(prediction[f"{role}_actual_x"]), float(prediction[f"{role}_actual_y"])) for role in ("long", "mid", "short")]
        identity_distance = _sum_point_distances(predicted, actual)
        rotation_deg, rotation_points, rotation_distance = _best_rotation(predicted, actual)
        inversion_points = _rotate_points(predicted, 180.0)
        inversion_distance = _sum_point_distances(inversion_points, actual)
        mirror_candidates = {name: _mirror_points(predicted, name) for name in ("VERTICAL", "HORIZONTAL")}
        mirror_distances = {name: _sum_point_distances(points, actual) for name, points in mirror_candidates.items()}
        best_mirror_type = min(mirror_distances, key=mirror_distances.get)
        permutation_distances = {name: _sum_point_distances(_permuted_points(predicted, permutation), actual) for name, permutation in permutations.items()}
        best_permutation = min(permutation_distances, key=permutation_distances.get)
        best_permutation_points = _permuted_points(predicted, permutations[best_permutation])
        rotation_permutation = {}
        for name, permutation in permutations.items():
            degrees, points, distance = _best_rotation(best_permutation_points if name == best_permutation else _permuted_points(predicted, permutation), actual)
            rotation_permutation[name] = (degrees, points, distance)
        best_rotation_permutation = min(rotation_permutation, key=lambda name: rotation_permutation[name][2])
        best_rp_angle, best_rp_points, best_rp_distance = rotation_permutation[best_rotation_permutation]
        candidates = {
            "ROTATION": (rotation_points, rotation_distance),
            "INVERSION_180": (inversion_points, inversion_distance),
            "MIRROR_VERTICAL": (mirror_candidates["VERTICAL"], mirror_distances["VERTICAL"]),
            "MIRROR_HORIZONTAL": (mirror_candidates["HORIZONTAL"], mirror_distances["HORIZONTAL"]),
            "ROLE_SWAP": (best_permutation_points, permutation_distances[best_permutation]),
            "ROTATION_PLUS_ROLE_SWAP": (best_rp_points, best_rp_distance),
        }
        candidate_name = min(candidates, key=lambda name: candidates[name][1])
        transformed_points, transformed_distance = candidates[candidate_name]
        improvement = max(0.0, 1.0 - transformed_distance / identity_distance) if identity_distance else 0.0
        predicted_shape, actual_shape = _shape_metrics(predicted), _shape_metrics(actual)
        shape_similarity = _shape_similarity(predicted_shape, actual_shape)
        if shape_similarity < TRANSFORMATION_MIN_SHAPE_SIMILARITY:
            transformation_type = "IRREGULAR"
        elif improvement < TRANSFORMATION_MIN_IMPROVEMENT:
            transformation_type = "IDENTITY_STABLE"
        elif candidate_name == "ROTATION_PLUS_ROLE_SWAP" and best_rotation_permutation != "IDENTITY":
            transformation_type = "ROTATION_PLUS_ROLE_SWAP" if abs(best_rp_angle) >= TRANSFORMATION_MIN_ROTATION_DEG else "ROLE_SWAP"
        elif candidate_name == "ROLE_SWAP" and best_permutation != "IDENTITY":
            transformation_type = "ROLE_SWAP"
        elif candidate_name == "INVERSION_180":
            transformation_type = "INVERSION_180"
        elif candidate_name.startswith("MIRROR_"):
            transformation_type = candidate_name
        elif candidate_name == "ROTATION" and abs(rotation_deg) >= TRANSFORMATION_MIN_ROTATION_DEG:
            transformation_type = "ROTATION"
        else:
            transformation_type = "IRREGULAR"
        centroid_pred = _prediction_geometry(predicted)
        centroid_actual = _prediction_geometry(actual)
        source_asof = (asof_by_date or {}).get(prediction["source_date"], {})
        source_shape = _shape_metrics([(float(source_asof[role + "_x"]), float(source_asof[role + "_y"])) for role in ("long", "mid", "short")]) if source_asof.get("status") == "VALID" else {}
        row = {
            "source_date": prediction["source_date"], "target_date": prediction["target_date"], "machine": prediction["machine"], "comparison_scope": prediction.get("comparison_scope", "HISTORICAL_BACKTEST"),
            "source_regime": prediction.get("source_regime", ""), "source_n_fft": prediction.get("source_n_fft", ""),
            "source_component_reorder": prediction.get("source_component_reorder", ""), "source_bullish": source_bullish.get(prediction["source_date"], ""),
            "target_bullish": prediction.get("target_bullish", ""), "transformation_type": transformation_type,
            "best_permutation": best_permutation, "identity_distance": identity_distance, "best_permutation_distance": permutation_distances[best_permutation],
            "permutation_improvement": max(0.0, 1.0 - permutation_distances[best_permutation] / identity_distance) if identity_distance else 0.0,
            "best_rotation_deg": rotation_deg, "rotation_residual": rotation_distance, "inversion_distance": inversion_distance,
            "inversion_improvement_vs_identity": max(0.0, 1.0 - inversion_distance / identity_distance) if identity_distance else 0.0,
            "vertical_mirror_distance": mirror_distances["VERTICAL"], "horizontal_mirror_distance": mirror_distances["HORIZONTAL"],
            "best_mirror_type": best_mirror_type, "best_mirror_distance": mirror_distances[best_mirror_type],
            "best_rotation_permutation": best_rotation_permutation, "best_rotation_plus_swap_deg": best_rp_angle,
            "best_transformation_distance": transformed_distance, "transformation_improvement": improvement,
            "classification_confidence": improvement * shape_similarity, "shape_similarity": shape_similarity,
            "centroid_distance": math.dist((centroid_pred["centroid_x"], centroid_pred["centroid_y"]), (centroid_actual["centroid_x"], centroid_actual["centroid_y"])),
            "centroid_region_match": centroid_pred["centroid_region"] == centroid_actual["centroid_region"],
            "predicted_centroid_region": centroid_pred["centroid_region"], "actual_centroid_region": centroid_actual["centroid_region"],
            "predicted_perimeter": predicted_shape["perimeter"], "actual_perimeter": actual_shape["perimeter"],
            "predicted_area": predicted_shape["area"], "actual_area": actual_shape["area"],
            "area_ratio": actual_shape["area"] / predicted_shape["area"] if predicted_shape["area"] else "",
            "source_top_wave_count": source_asof.get("top_wave_count", ""), "source_top_wave_pattern": source_asof.get("top_wave_pattern", ""),
            "source_centroid_region": source_asof.get("centroid_region", ""), "source_phase_alignment_score": source_asof.get("phase_alignment_score", ""),
            "source_convergence_score": source_asof.get("convergence_score", ""), "source_long_k": source_asof.get("long_k", ""), "source_mid_k": source_asof.get("mid_k", ""), "source_short_k": source_asof.get("short_k", ""),
            "source_perimeter": source_shape.get("perimeter", ""), "source_area": source_shape.get("area", ""),
        }
        for key, value in predicted_shape["sides"].items():
            row["predicted_" + key + "_side"] = value
            row["actual_" + key + "_side"] = actual_shape["sides"][key]
            row[key + "_side_ratio"] = actual_shape["sides"][key] / value if value else ""
        for role, point in zip(("long", "mid", "short"), transformed_points):
            row["transformed_" + role + "_x"], row["transformed_" + role + "_y"] = point
        for role in ("long", "mid", "short"):
            for side in ("predicted", "actual"):
                row[side + "_" + role + "_phase"] = prediction[role + "_" + side + "_phase"]
            row[role + "_angular_error"] = prediction.get(role + "_angular_error_deg", "")
            row[role + "_xy_distance"] = prediction.get(role + "_xy_error", "")
            row["predicted_" + role + "_x"] = prediction[role + "_predicted_x"]
            row["predicted_" + role + "_y"] = prediction[role + "_predicted_y"]
            row["actual_" + role + "_x"] = prediction[role + "_actual_x"]
            row["actual_" + role + "_y"] = prediction[role + "_actual_y"]
        results.append(row)
    return results


def refine_identity_classification(rows: list[dict], reference_rows: list[dict] | None = None) -> tuple[float, float]:
    reference = reference_rows or rows
    identity_threshold = quantile([float(row["identity_distance"]) for row in reference], IDENTITY_DISTANCE_QUANTILE) if reference else 0.0
    centroid_threshold = quantile([float(row["centroid_distance"]) for row in reference], IDENTITY_CENTROID_QUANTILE) if reference else 0.0
    for row in rows:
        row["identity_distance_threshold"] = identity_threshold
        row["identity_centroid_threshold"] = centroid_threshold
        if row.get("transformation_type") == "IDENTITY_STABLE" and (
            float(row["identity_distance"]) > identity_threshold
            or float(row["centroid_distance"]) > centroid_threshold
            or float(row["shape_similarity"]) < TRANSFORMATION_MIN_SHAPE_SIMILARITY
        ):
            row["transformation_type"] = "NO_CLEAR_TRANSFORM"
    return identity_threshold, centroid_threshold


def phase_transformation_stats(rows: list[dict], key: str = "transformation_type", values: tuple | None = None) -> list[dict]:
    values = values or ("IDENTITY_STABLE", "ROTATION", "INVERSION_180", "MIRROR_VERTICAL", "MIRROR_HORIZONTAL", "ROLE_SWAP", "ROTATION_PLUS_ROLE_SWAP", "NO_CLEAR_TRANSFORM", "IRREGULAR")
    result = []
    for value in values:
        subset = [row for row in rows if row.get(key) == value]
        target = [_csv_bool(row, "target_bullish") for row in subset]
        source = [bool(row["source_bullish"]) for row in subset]
        result.append({"group": key, "value": value, "samples": len(subset), "percentage": len(subset) / len(rows) * 100.0 if rows else "", "source_bullish_count": sum(source), "source_bullish_rate": sum(source) / len(source) * 100.0 if source else "", "target_bullish_count": sum(target), "target_bullish_rate": sum(target) / len(target) * 100.0 if target else "", "mean_improvement": _safe_mean([float(row["transformation_improvement"]) for row in subset]), "mean_shape_similarity": _safe_mean([float(row["shape_similarity"]) for row in subset])})
    return result


def _probability_state_from_asof(row: dict, regime: str | None = None) -> dict:
    """Create a comparable source-state record from an as-of Phase Space row."""
    state = {
        "date": row.get("date", ""),
        "machine": row.get("machine", ""),
        "source_regime": regime if regime is not None else row.get("regime", ""),
        "source_n_fft": row.get("n_fft", ""),
        "source_top_wave_count": row.get("top_wave_count", ""),
        "source_top_wave_pattern": row.get("top_wave_pattern", ""),
        "source_centroid_region": row.get("centroid_region", ""),
        "source_phase_alignment_score": row.get("phase_alignment_score", row.get("asof_phase_alignment_score", "")),
        "source_convergence_score": row.get("convergence_score", row.get("asof_phase_convergence_score", "")),
        "source_long_k": row.get("long_k", ""),
        "source_mid_k": row.get("mid_k", ""),
        "source_short_k": row.get("short_k", ""),
    }
    points = []
    for role in ("long", "mid", "short"):
        try:
            points.append((float(row[role + "_x"]), float(row[role + "_y"])))
        except (KeyError, TypeError, ValueError):
            points = []
            break
    if len(points) == 3:
        state["points"] = points
        state["shape"] = _shape_metrics(points)
        state["centroid"] = (
            sum(point[0] for point in points) / 3.0,
            sum(point[1] for point in points) / 3.0,
        )
    else:
        state["points"] = []
        state["shape"] = {}
        state["centroid"] = None
    return state


def _probability_geometry_distance(candidate: dict, source: dict) -> float:
    """Explainable normalized geometry distance used only by Level 1 support."""
    candidate_state = candidate.get("_state", {})
    candidate_points = candidate_state.get("points", [])
    source_points = source.get("points", [])
    if len(candidate_points) != 3 or len(source_points) != 3:
        return 1.0
    angular = []
    for left, right in zip(candidate_points, source_points):
        left_phase = _phase_from_xy(left)
        right_phase = _phase_from_xy(right)
        difference = abs(left_phase - right_phase)
        angular.append(min(difference, 360.0 - difference) / 180.0)
    centroid_distance = math.dist(candidate_state["centroid"], source["centroid"]) / (2.0 * (PHASE_SPACE_BASE_RADIUS + PHASE_SPACE_WAVE_RADIUS))
    top_difference = abs(int(float(candidate.get("source_top_wave_count", 0) or 0)) - int(float(source.get("source_top_wave_count", 0) or 0))) / 3.0
    region_difference = 0.0 if candidate.get("source_centroid_region", "") == source.get("source_centroid_region", "") else 1.0
    candidate_shape = candidate_state.get("shape", {})
    source_shape = source.get("shape", {})
    shape_difference = sum(abs(candidate_shape.get("normalized_sides", {}).get(key, 0.0) - source_shape.get("normalized_sides", {}).get(key, 0.0)) for key in ("long_mid", "mid_short", "short_long")) / 2.0 if candidate_shape and source_shape else 1.0
    return clip01(0.35 * _safe_mean(angular) + 0.25 * clip01(centroid_distance) + 0.15 * clip01(top_difference) + 0.10 * region_difference + 0.15 * clip01(shape_difference))


def _probability_confidence(support_samples: int) -> str:
    if support_samples < PROBABILITY_MEDIUM_SUPPORT:
        return "LOW"
    if support_samples < PROBABILITY_HIGH_SUPPORT:
        return "MEDIUM"
    return "HIGH"


def _probability_prior(history: list[dict], source_date: str) -> list[dict]:
    # A target answer is usable only once its observation date has arrived.
    # Thus target_date <= source_date is allowed, while target_date > source_date
    # (including 2026-08-18 for the 8/17 freeze) is excluded.
    return [row for row in history if row.get("target_date", "") <= source_date]


def estimate_transformation_probability(source: dict, history: list[dict], asof_by_date: dict[str, dict]) -> dict:
    """Estimate P(transformation on next observation | source state) walk-forward."""
    source_date = source.get("date", "")
    prior = _probability_prior(history, source_date)
    enriched = []
    for row in prior:
        item = dict(row)
        item["_state"] = _probability_state_from_asof(asof_by_date.get(row.get("source_date", ""), {}), row.get("source_regime", ""))
        enriched.append(item)
    same_regime_nfft = [row for row in enriched if row.get("source_regime", "") == source.get("source_regime", "") and str(row.get("source_n_fft", "")) == str(source.get("source_n_fft", ""))]
    near = [row for row in same_regime_nfft if _probability_geometry_distance(row, source) <= PROBABILITY_GEOMETRY_DISTANCE_THRESHOLD]
    if len(near) >= PROBABILITY_MIN_LEVEL1_SUPPORT:
        support = near
        level = "LEVEL_1"
        basis = "same machine + same regime + same n_fft + geometry distance <= 0.35"
    elif len(same_regime_nfft) >= PROBABILITY_MIN_LEVEL1_SUPPORT:
        support = same_regime_nfft
        level = "LEVEL_2"
        basis = "same machine + same regime + same n_fft"
    else:
        same_regime = [row for row in enriched if row.get("source_regime", "") == source.get("source_regime", "")]
        if len(same_regime) >= PROBABILITY_MIN_LEVEL1_SUPPORT:
            support = same_regime
            level = "LEVEL_3"
            basis = "same machine + same regime"
        else:
            support = enriched
            level = "LEVEL_4"
            basis = "same machine historical transformation records"
    transform_samples = sum(row.get("transformation_type") in TRANSFORMATION_PROBABILITY_TYPES for row in support)
    non_transform_samples = len(support) - transform_samples
    probability = transform_samples / len(support) if support else ""
    transform_counts = {}
    for row in support:
        if row.get("transformation_type") in TRANSFORMATION_PROBABILITY_TYPES:
            transform_counts[row["transformation_type"]] = transform_counts.get(row["transformation_type"], 0) + 1
    most_likely = min(transform_counts, key=lambda key: (-transform_counts[key], key)) if transform_counts else ""
    conditional = transform_counts.get(most_likely, 0) / transform_samples if transform_samples else ""
    return {
        "selected_support_level": level, "support_level": level, "support_samples": len(support),
        "transform_samples": transform_samples, "non_transform_samples": non_transform_samples,
        "transform_probability": probability, "confidence": _probability_confidence(len(support)),
        "most_likely_transform_type": most_likely, "conditional_type_probability": conditional,
        "selection_basis": basis, "geometry_distance_threshold": PROBABILITY_GEOMETRY_DISTANCE_THRESHOLD,
        "source_date": source_date, "source_regime": source.get("source_regime", ""), "source_n_fft": source.get("source_n_fft", ""),
    }


def _probability_bucket(probability: object) -> str:
    if probability == "" or probability is None:
        return "NO_SUPPORT"
    index = min(4, max(0, int(float(probability) / PROBABILITY_BUCKET_WIDTH)))
    return f"{index * 20}-{(index + 1) * 20}%"


def transformation_probability_daily_rows(transformation_history: list[dict], asof_rows: list[dict], extra_history: list[dict] | None = None) -> list[dict]:
    """Build strictly walk-forward probability rows for known D -> D+1 answers."""
    history = list(transformation_history) + list(extra_history or [])
    asof_by_date = {row.get("date", ""): _probability_state_from_asof(row) for row in asof_rows if row.get("status") == "VALID"}
    rows = []
    for actual in sorted(history, key=lambda row: (row.get("source_date", ""), row.get("target_date", ""))):
        source_state = asof_by_date.get(actual.get("source_date", ""))
        if not source_state:
            continue
        estimate = estimate_transformation_probability(source_state, history, asof_by_date)
        rows.append({
            "machine": actual.get("machine", ""), "source_date": actual.get("source_date", ""), "target_date": actual.get("target_date", ""),
            "source_regime": source_state.get("source_regime", ""), "source_n_fft": source_state.get("source_n_fft", ""),
            "selected_support_level": estimate["selected_support_level"], "support_samples": estimate["support_samples"],
            "transform_samples": estimate["transform_samples"], "non_transform_samples": estimate["non_transform_samples"],
            "transform_probability": estimate["transform_probability"], "confidence": estimate["confidence"],
            "most_likely_transform_type": estimate["most_likely_transform_type"], "conditional_type_probability": estimate["conditional_type_probability"],
            "selection_basis": estimate["selection_basis"], "geometry_distance_threshold": estimate["geometry_distance_threshold"],
            "actual_transform": actual.get("transformation_type", "") in TRANSFORMATION_PROBABILITY_TYPES,
            "actual_transformation_type": actual.get("transformation_type", ""), "actual_bullish": actual.get("target_bullish", ""),
            "probability_bucket": _probability_bucket(estimate["transform_probability"]), "status": "WALK_FORWARD_VALIDATION",
        })
    return rows


def transformation_probability_stats(rows: list[dict]) -> list[dict]:
    result = []
    groups = [("overall", rows)] + [(bucket, [row for row in rows if row.get("probability_bucket") == bucket]) for bucket in ("0-20%", "20-40%", "40-60%", "60-80%", "80-100%", "NO_SUPPORT")]
    for bucket, subset in groups:
        actual = [1.0 if row.get("actual_transform") in (True, "True", "true", 1, "1") else 0.0 for row in subset]
        probabilities = [float(row["transform_probability"]) for row in subset if row.get("transform_probability", "") != ""]
        brier = _safe_mean([(float(row["transform_probability"]) - (1.0 if row.get("actual_transform") in (True, "True", "true", 1, "1") else 0.0)) ** 2 for row in subset if row.get("transform_probability", "") != ""])
        result.append({"scope": bucket, "samples": len(subset), "mean_predicted_probability": _safe_mean(probabilities), "actual_transform_count": int(sum(actual)), "actual_transform_rate": sum(actual) / len(actual) if actual else "", "brier_score": brier})
    return result


def frozen_transformation_probability(source: dict, history: list[dict], asof_by_date: dict[str, dict], target_date: str) -> dict:
    estimate = estimate_transformation_probability(source, history, asof_by_date)
    return {**estimate, "machine": source.get("machine", ""), "source_date": source.get("date", ""), "target_date": target_date, "source_cutoff": source.get("date", ""), "status": "PROBABILITY_FROZEN_BEFORE_ACTUAL", "logic_version": "walk_forward_transformation_probability_v1", "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def probability_source_from_target_validation(row: dict) -> dict:
    """Use the known 8/17 as-of row as the 8/17 source state; never reads 8/18."""
    source = {
        "date": FROZEN_TWO_WAY_TARGET_DATE, "machine": row.get("machine", ""),
        "source_regime": row.get("target_regime", ""), "source_n_fft": row.get("target_n_fft", ""),
        "source_top_wave_count": row.get("actual_top_wave_count", ""), "source_top_wave_pattern": row.get("actual_top_wave_pattern", ""),
        "source_centroid_region": row.get("actual_centroid_region", ""),
        "source_phase_alignment_score": row.get("actual_phase_alignment_score", ""), "source_convergence_score": row.get("actual_convergence_score", ""),
        "source_long_k": row.get("actual_long_k", ""), "source_mid_k": row.get("actual_mid_k", ""), "source_short_k": row.get("actual_short_k", ""),
    }
    source["points"] = [(float(row["actual_" + role + "_x"]), float(row["actual_" + role + "_y"])) for role in ("long", "mid", "short")]
    source["shape"] = _shape_metrics(source["points"])
    source["centroid"] = (float(row["actual_centroid_x"]), float(row["actual_centroid_y"]))
    return source


def transformation_belt_state(probability: object) -> str:
    value = float(probability) if probability not in ("", None) else 0.0
    if value < 0.40:
        return "BELT_OFF"
    if value < 0.60:
        return "BELT_STANDBY"
    if value < 0.80:
        return "BELT_GLOWING"
    return "BELT_ACTIVE"


def _frozen_prediction_geometry(points: list[tuple[float, float]]) -> dict:
    geometry = _prediction_geometry(points)
    geometry["points"] = points
    return geometry


def frozen_phase_prediction_0817_0818(source_prediction: dict, source_asof: dict, probability: dict, history: list[dict]) -> list[dict]:
    """Create BASELINE and gated TRANSFORMATION-AWARE predictions without target data."""
    roles = ("long", "mid", "short")
    baseline_points = [(float(source_prediction[role + "_predicted_x"]), float(source_prediction[role + "_predicted_y"])) for role in roles]
    belt = transformation_belt_state(probability.get("transform_probability", ""))
    if belt == "BELT_OFF":
        selection = {"selected_transformation": "NO_TRANSFORM_APPLIED", "selection_basis": "BELT_OFF: baseline retained; transformation is reference-only", "support_samples": probability.get("support_samples", ""), "transformation_probability": probability.get("transform_probability", ""), "selected_case": {}}
        aware_points = baseline_points[:]
        applied = False
    elif belt == "BELT_STANDBY":
        selection = {"selected_transformation": "REFERENCE_ONLY", "selection_basis": "BELT_STANDBY: baseline retained; historical transformation is reference-only", "support_samples": probability.get("support_samples", ""), "transformation_probability": probability.get("transform_probability", ""), "selected_case": {}}
        aware_points = baseline_points[:]
        applied = False
    else:
        selection = select_transformation(source_prediction, source_asof, history)
        aware_points = _apply_transformation(baseline_points, selection.get("selected_case", {}))
        applied = selection.get("selected_transformation") not in ("", "NO_CLEAR_TRANSFORM", "IDENTITY_STABLE")
    baseline_geometry = _frozen_prediction_geometry(baseline_points)
    aware_geometry = _frozen_prediction_geometry(aware_points)
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    common = {
        "machine": source_prediction.get("machine", ""), "source_date": "2026-08-17", "target_date": "2026-08-18", "source_cutoff": "2026-08-17",
        "prediction_status": "FROZEN_BEFORE_ACTUAL", "logic_version": "asof_phase_extrapolation_plus_probability_gated_transformation_v1", "generated_at": generated_at, "source_commit": "PENDING_COMMIT",
        "source_regime": source_prediction.get("source_regime", ""), "source_n_fft": source_prediction.get("source_n_fft", ""),
        "transform_probability": probability.get("transform_probability", ""), "probability_confidence": probability.get("confidence", ""),
        "probability_support_samples": probability.get("support_samples", ""), "probability_transform_samples": probability.get("transform_samples", ""), "probability_non_transform_samples": probability.get("non_transform_samples", ""),
        "belt_state": belt, "most_likely_type": probability.get("most_likely_transform_type", ""), "conditional_probability": probability.get("conditional_type_probability", ""),
        "selection_support_samples": selection.get("support_samples", ""), "selection_basis": selection.get("selection_basis", ""),
        "selected_transformation_type": selection.get("selected_transformation", ""), "selected_permutation": selection.get("selected_case", {}).get("best_permutation", ""),
        "transform_applied": applied,
    }
    for role in roles:
        source_key = role
        common["baseline_" + role + "_phase"] = source_prediction[source_key + "_predicted_phase"]
        common["baseline_" + role + "_wave_value"] = source_prediction[source_key + "_predicted_wave_value"]
        common["baseline_" + role + "_amplitude"] = source_prediction[source_key + "_predicted_amplitude"]
        common["baseline_" + role + "_radius"] = source_prediction[source_key + "_predicted_radius"]
        common["baseline_" + role + "_x"] = baseline_points[roles.index(role)][0]
        common["baseline_" + role + "_y"] = baseline_points[roles.index(role)][1]
        common["baseline_" + role + "_top_side"] = baseline_points[roles.index(role)][1] < PHASE_SPACE_CENTER_Y
        common["aware_" + role + "_phase"] = _phase_from_xy(aware_points[roles.index(role)])
        common["aware_" + role + "_x"] = aware_points[roles.index(role)][0]
        common["aware_" + role + "_y"] = aware_points[roles.index(role)][1]
        common["aware_" + role + "_top_side"] = aware_points[roles.index(role)][1] < PHASE_SPACE_CENTER_Y
    for prefix, geometry in (("baseline", baseline_geometry), ("aware", aware_geometry)):
        common[prefix + "_top_wave_count"] = geometry["top_wave_count"]
        common[prefix + "_top_wave_pattern"] = geometry["top_wave_pattern"]
        common[prefix + "_centroid_x"] = geometry["centroid_x"]
        common[prefix + "_centroid_y"] = geometry["centroid_y"]
        common[prefix + "_centroid_region"] = geometry["centroid_region"]
    baseline_row = {**common, "prediction_type": "BASELINE", "selected_transformation_type": "NO_TRANSFORM_APPLIED", "selected_permutation": "IDENTITY", "transform_applied": False}
    aware_row = {**common, "prediction_type": "TRANSFORMATION_AWARE"}
    return [baseline_row, aware_row]


def forward_transformation_input(forward: dict, daily: list[dict]) -> dict:
    """Adapt the already-frozen 8/15 -> 8/16 row without recalculating it."""
    row = {"source_date": forward["source_date"], "target_date": forward["target_date"], "machine": forward["machine"], "status": "VALID_PREDICTION", "source_regime": forward.get("source_regime", ""), "source_n_fft": forward.get("source_n_fft", ""), "source_component_reorder": "", "target_bullish": forward.get("actual_bullish", ""), "comparison_scope": "FORWARD_FROZEN"}
    source = next((item for item in daily if item["date"] == forward["source_date"]), None)
    row["source_bullish"] = source["bullish"] if source else ""
    for role in ("long", "mid", "short"):
        row[role + "_predicted_x"] = forward["predicted_" + role + "_x"]
        row[role + "_predicted_y"] = forward["predicted_" + role + "_y"]
        row[role + "_actual_x"] = forward["actual_" + role + "_x"]
        row[role + "_actual_y"] = forward["actual_" + role + "_y"]
        row[role + "_predicted_phase"] = forward["predicted_" + role + "_phase"]
        row[role + "_actual_phase"] = forward["actual_" + role + "_phase"]
        row[role + "_angular_error_deg"] = forward.get(role + "_angular_error", "")
        row[role + "_xy_error"] = forward.get(role + "_xy_distance", "")
        row[role + "_component_same_k"] = forward.get(role + "_component_same_k", "")
    return row


def _phase_from_xy(point: tuple[float, float]) -> float:
    return math.degrees(math.atan2(point[0] - PHASE_SPACE_CENTER_X, point[1] - PHASE_SPACE_CENTER_Y)) % 360.0


def _geometry_scores(points: list[tuple[float, float]]) -> dict:
    phases = [_phase_from_xy(point) for point in points]
    resultant = abs(sum(complex(math.cos(math.radians(phase)), math.sin(math.radians(phase))) for phase in phases) / 3.0)
    max_pair = max(math.dist(points[i], points[j]) for i in range(3) for j in range(i + 1, 3))
    return {"phases": phases, "phase_alignment_score": clip01(resultant), "convergence_score": clip01(1.0 - max_pair / (2.0 * (PHASE_SPACE_BASE_RADIUS + PHASE_SPACE_WAVE_RADIUS)))}


def _candidate_similarity(candidate: dict, source: dict) -> float:
    score = 0.0
    if candidate.get("source_regime") == source.get("source_regime"):
        score += 4.0
    if str(candidate.get("source_n_fft")) == str(source.get("source_n_fft")):
        score += 3.0
    if str(candidate.get("source_top_wave_count")) == str(source.get("source_top_wave_count")):
        score += 2.0
    if candidate.get("source_top_wave_pattern") and candidate.get("source_top_wave_pattern") == source.get("source_top_wave_pattern"):
        score += 2.0
    if candidate.get("source_centroid_region") == source.get("source_centroid_region"):
        score += 1.5
    if candidate.get("source_long_k") == source.get("source_long_k"):
        score += 0.75
    if candidate.get("source_mid_k") == source.get("source_mid_k"):
        score += 0.75
    if candidate.get("source_short_k") == source.get("source_short_k"):
        score += 0.75
    for key in ("source_phase_alignment_score", "source_convergence_score"):
        if candidate.get(key, "") != "" and source.get(key, "") != "" and abs(float(candidate[key]) - float(source[key])) <= 0.15:
            score += 1.0
    if candidate.get("source_area", "") != "" and source.get("source_area", "") != "":
        score += 1.0 if abs(math.log((float(candidate["source_area"]) + 1.0) / (float(source["source_area"]) + 1.0))) <= 0.35 else 0.0
    return score


def _permutation_tuple(name: str) -> tuple[int, int, int]:
    return {"IDENTITY": (0, 1, 2), "LONG_MID_SWAP": (1, 0, 2), "LONG_SHORT_SWAP": (2, 1, 0), "MID_SHORT_SWAP": (0, 2, 1), "3_CYCLE_A": (1, 2, 0), "3_CYCLE_B": (2, 0, 1)}.get(name, (0, 1, 2))


def _apply_transformation(points: list[tuple[float, float]], candidate: dict) -> list[tuple[float, float]]:
    transformation = candidate.get("transformation_type", "NO_CLEAR_TRANSFORM")
    if transformation == "ROLE_SWAP":
        return _permuted_points(points, _permutation_tuple(candidate.get("best_permutation", "IDENTITY")))
    if transformation == "ROTATION":
        return _rotate_points(points, float(candidate.get("best_rotation_deg", 0.0)))
    if transformation == "INVERSION_180":
        return _rotate_points(points, 180.0)
    if transformation in ("MIRROR_VERTICAL", "MIRROR_HORIZONTAL"):
        return _mirror_points(points, transformation.removeprefix("MIRROR_"))
    if transformation == "ROTATION_PLUS_ROLE_SWAP":
        permuted = _permuted_points(points, _permutation_tuple(candidate.get("best_rotation_permutation", "IDENTITY")))
        return _rotate_points(permuted, float(candidate.get("best_rotation_plus_swap_deg", 0.0)))
    return points[:]


def select_transformation(source_prediction: dict, source_asof: dict, history: list[dict]) -> dict:
    source_points = [(float(source_asof[role + "_x"]), float(source_asof[role + "_y"])) for role in ("long", "mid", "short")]
    source_shape = _shape_metrics(source_points)
    source_scores = _geometry_scores(source_points)
    source = {"source_regime": source_prediction.get("source_regime", ""), "source_n_fft": source_prediction.get("source_n_fft", ""), "source_top_wave_count": source_asof.get("top_wave_count", ""), "source_top_wave_pattern": source_asof.get("top_wave_pattern", ""), "source_centroid_region": source_asof.get("centroid_region", ""), "source_phase_alignment_score": source_scores["phase_alignment_score"], "source_convergence_score": source_scores["convergence_score"], "source_long_k": source_asof.get("long_k", ""), "source_mid_k": source_asof.get("mid_k", ""), "source_short_k": source_asof.get("short_k", ""), "source_area": source_shape["area"]}
    scoped = [row for row in history if row.get("source_regime") == source["source_regime"] and str(row.get("source_n_fft")) == str(source["source_n_fft"])]
    basis = "same machine + same source regime + same n_fft"
    if len(scoped) < 3:
        scoped = [row for row in history if str(row.get("source_n_fft")) == str(source["source_n_fft"])]
        basis = "same machine + same n_fft"
    if len(scoped) < 3:
        scoped = history[:]
        basis = "same machine + all historical transformation cases"
    ranked = sorted(((round(_candidate_similarity(row, source), 6), row) for row in scoped), key=lambda item: (item[0], item[1].get("source_date", "")), reverse=True)
    selected_cases = [row for _score, row in ranked[: min(5, len(ranked))]]
    counts = {}
    for row in selected_cases:
        counts[row["transformation_type"]] = counts.get(row["transformation_type"], 0) + 1
    selected = max(counts, key=counts.get) if counts else "NO_CLEAR_TRANSFORM"
    return {"selected_transformation": selected, "selection_basis": basis + f"; nearest_cases={len(selected_cases)}", "support_samples": len(selected_cases), "transformation_probability": counts.get(selected, 0) / len(selected_cases) if selected_cases else 0.0, "selected_case": next((row for row in selected_cases if row["transformation_type"] == selected), {})}


def frozen_next_phase_predictions(machine: str, source_rows: list[dict], historical_transformations: list[dict], forward_transformations: list[dict]) -> list[dict]:
    raw_rows = source_rows
    source_rows = [{**row, "bullish": row["close"] > row["open"], "next_day_bullish": (raw_rows[index + 1]["close"] > raw_rows[index + 1]["open"]) if index + 1 < len(raw_rows) else None} for index, row in enumerate(raw_rows)]
    components, daily, _centered, _comparison = analyze(source_rows)
    convergence, _ = phase_convergence_analysis(daily, components)
    alignment, _ = phase_alignment_analysis(convergence)
    regime, _events = period_regime_history(source_rows, convergence, alignment)
    asof = asof_phase_space_history(source_rows, regime)
    asof_by_date = {row["date"]: {**next(item for item in regime if item["date"] == row["date"]), **row} for row in asof}
    predictions = asof_next_phase_predictions(source_rows, asof, regime)
    source_prediction = next(row for row in predictions if row["source_date"] == "2026-08-16")
    source_asof = asof_by_date["2026-08-16"]
    baseline_points = [(float(source_prediction[role + "_predicted_x"]), float(source_prediction[role + "_predicted_y"])) for role in ("long", "mid", "short")]
    history = historical_transformations + forward_transformations
    selection = select_transformation(source_prediction, source_asof, history)
    transformed_points = _apply_transformation(baseline_points, selection["selected_case"])
    baseline_geometry = _prediction_geometry(baseline_points)
    transformed_geometry = _prediction_geometry(transformed_points)
    baseline_scores = _geometry_scores(baseline_points)
    transformed_scores = _geometry_scores(transformed_points)
    generated_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for prediction_type, points, geometry, scores in (("BASELINE", baseline_points, baseline_geometry, baseline_scores), ("TRANSFORMATION_AWARE", transformed_points, transformed_geometry, transformed_scores)):
        row = {"source_date": "2026-08-16", "target_date": "2026-08-17", "machine": machine, "prediction_version": "NEXT_PHASE_TRANSFORMATION_V1", "prediction_type": prediction_type, "source_regime": source_prediction.get("source_regime", ""), "source_n_fft": source_prediction.get("source_n_fft", ""), "selected_transformation": selection["selected_transformation"] if prediction_type == "TRANSFORMATION_AWARE" else "NO_TRANSFORM", "selection_basis": selection["selection_basis"] if prediction_type == "TRANSFORMATION_AWARE" else "BASELINE: existing as-of phase extrapolation", "support_samples": selection["support_samples"] if prediction_type == "TRANSFORMATION_AWARE" else "", "transformation_probability": selection["transformation_probability"] if prediction_type == "TRANSFORMATION_AWARE" else "", "support_status": "LOW_SUPPORT" if prediction_type == "TRANSFORMATION_AWARE" and selection["support_samples"] < 3 else "", "generated_at": generated_at, "source_cutoff": "2026-08-16", "logic_version": "asof_phase_extrapolation_plus_historical_transformation_v1", "source_commit": "", "prediction_status": "FROZEN_BEFORE_ACTUAL", "predicted_centroid_x": geometry["centroid_x"], "predicted_centroid_y": geometry["centroid_y"], "predicted_centroid_y_offset": geometry["centroid_y_offset"], "predicted_centroid_region": geometry["centroid_region"], "predicted_top_wave_count": geometry["top_wave_count"], "predicted_top_wave_pattern": geometry["top_wave_pattern"], "predicted_phase_alignment_score": scores["phase_alignment_score"], "predicted_convergence_score": scores["convergence_score"]}
        for role, point in zip(("long", "mid", "short"), points):
            row[role + "_phase"] = _phase_from_xy(point)
            row[role + "_x"], row[role + "_y"] = point
        rows.append(row)
    return rows


def _prediction_row_base(source: dict, target: dict | None, source_asof: dict, target_asof: dict | None) -> dict:
    target_date = target["date"] if target else ""
    status = "VALID_PREDICTION" if source_asof.get("status") == "VALID" else "INSUFFICIENT_HISTORY"
    if status == "VALID_PREDICTION" and (target is None or target_asof is None or target_asof.get("status") != "VALID"):
        status = "PENDING_ACTUAL"
    source_regime = source_asof.get("regime", "INSUFFICIENT_HISTORY")
    regime_row = source_asof
    return {
        "source_date": source["date"], "target_date": target_date, "machine": source["machine"], "status": status,
        "source_n_observations": source_asof.get("n_observations", ""), "source_n_fft": source_asof.get("n_fft", ""),
        "source_regime": source_regime, "source_period_stability_score": regime_row.get("period_stability_score", ""),
        "source_dominant_rank_signature": regime_row.get("dominant_rank_signature", ""),
        "source_component_reorder": regime_row.get("component_reorder", ""),
        "target_n_observations": target_asof.get("n_observations", "") if target_asof else "",
        "target_n_fft": target_asof.get("n_fft", "") if target_asof else "",
        "n_fft_changed": bool(target_asof and source_asof.get("n_fft") != target_asof.get("n_fft")),
        "target_regime": target_asof.get("regime", "") if target_asof else "",
        "target_bullish": target["bullish"] if target else "",
        "target_next_day_bullish": target["next_day_bullish"] if target else "",
    }


def asof_next_phase_predictions(rows: list[dict], asof_rows: list[dict], regime_rows: list[dict]) -> list[dict]:
    """Predict the next observation from each point-in-time FFT prefix only."""
    asof_by_date = {row["date"]: row for row in asof_rows}
    regime_by_date = {row["date"]: row for row in regime_rows}
    predictions = []
    for index, source in enumerate(rows):
        target = rows[index + 1] if index + 1 < len(rows) else None
        source_asof = asof_by_date[source["date"]]
        source_context = {**source_asof, **{key: value for key, value in regime_by_date.get(source["date"], {}).items() if key != "status"}}
        target_asof = asof_by_date.get(target["date"]) if target else None
        result = _prediction_row_base(source, target, source_context, target_asof)
        if source_asof["status"] != "VALID":
            predictions.append(result)
            continue

        prefix = rows[: index + 1]
        prefix_components, _prefix_daily, _centered, _comparison = analyze(prefix)
        role_components = {component["role"]: component for component in prefix_components}
        role_wave = {"LONG": 1, "MID": 2, "SHORT": 3}
        points = []
        for role in ("LONG", "MID", "SHORT"):
            component = role_components[role]
            wave = role_wave[role]
            coefficient = component["_coefficient"]
            current_phase = phase_position(index, component["frequency"], coefficient, component["n_fft"])
            predicted_phase = (current_phase + 360.0 * component["frequency"]) % 360.0
            theta_next = 2.0 * math.pi * component["frequency"] * (index + 1) + math.atan2(coefficient.imag, coefficient.real)
            predicted_value = component["amplitude"] * math.cos(theta_next)
            predicted_radius = PHASE_SPACE_BASE_RADIUS
            if abs(float(component["amplitude"])) > 0.0:
                predicted_radius += PHASE_SPACE_WAVE_RADIUS * predicted_value / abs(float(component["amplitude"]))
            predicted_x, predicted_y = phase_space_xy(predicted_phase, predicted_value, component["amplitude"])
            prefix_name = role.lower()
            actual = target_asof or {}
            result.update({
                prefix_name + "_source_k": round(float(component["frequency"]) * int(component["n_fft"])),
                prefix_name + "_source_frequency": component["frequency"],
                prefix_name + "_source_period": component["period_days"],
                prefix_name + "_source_rank": component["rank"],
                prefix_name + "_predicted_phase": predicted_phase,
                prefix_name + "_predicted_wave_value": predicted_value,
                prefix_name + "_predicted_amplitude": component["amplitude"],
                prefix_name + "_predicted_radius": predicted_radius,
                prefix_name + "_predicted_x": predicted_x,
                prefix_name + "_predicted_y": predicted_y,
                prefix_name + "_predicted_top_side": predicted_y < PHASE_SPACE_CENTER_Y,
                prefix_name + "_actual_k": actual.get(prefix_name + "_k", ""),
                prefix_name + "_actual_frequency": actual.get(prefix_name + "_frequency", ""),
                prefix_name + "_actual_period": actual.get(prefix_name + "_period", ""),
                prefix_name + "_actual_rank": actual.get(prefix_name + "_rank", ""),
                prefix_name + "_component_same_k": (round(float(component["frequency"]) * int(component["n_fft"])) == int(actual[prefix_name + "_k"])) if target_asof and actual.get(prefix_name + "_k", "") != "" else "",
            })
            if target_asof and target_asof.get("status") == "VALID":
                result.update({
                    prefix_name + "_actual_phase": actual[prefix_name + "_phase"],
                    prefix_name + "_actual_wave_value": actual[prefix_name + "_wave_value"],
                    prefix_name + "_actual_amplitude": actual[prefix_name + "_amplitude"],
                    prefix_name + "_actual_x": actual[prefix_name + "_x"],
                    prefix_name + "_actual_y": actual[prefix_name + "_y"],
                    prefix_name + "_angular_error_deg": _angular_error(predicted_phase, actual[prefix_name + "_phase"]),
                    prefix_name + "_xy_error": math.dist((predicted_x, predicted_y), (float(actual[prefix_name + "_x"]), float(actual[prefix_name + "_y"]))),
                    prefix_name + "_top_side_match": (predicted_y < PHASE_SPACE_CENTER_Y) == bool(actual[prefix_name + "_top_side"]),
                })
        predicted_points = [(result[role.lower() + "_predicted_x"], result[role.lower() + "_predicted_y"]) for role in ("LONG", "MID", "SHORT")]
        result.update({"predicted_" + key: value for key, value in _prediction_geometry(predicted_points).items()})
        if target_asof and target_asof.get("status") == "VALID":
            actual_points = [(float(target_asof[role.lower() + "_x"]), float(target_asof[role.lower() + "_y"])) for role in ("LONG", "MID", "SHORT")]
            actual_geometry = _prediction_geometry(actual_points)
            result.update({"actual_" + key: value for key, value in actual_geometry.items()})
            result.update({
                "centroid_distance_error": math.dist(predicted_points and (result["predicted_centroid_x"], result["predicted_centroid_y"]), (actual_geometry["centroid_x"], actual_geometry["centroid_y"])),
                "centroid_region_match": result["predicted_centroid_region"] == actual_geometry["centroid_region"],
                "top_wave_count_error": abs(result["predicted_top_wave_count"] - actual_geometry["top_wave_count"]),
                "top_wave_count_exact": result["predicted_top_wave_count"] == actual_geometry["top_wave_count"],
                "top_wave_pattern_match": result["predicted_top_wave_pattern"] == actual_geometry["top_wave_pattern"],
            })
        predictions.append(result)
    return predictions


FORWARD_VALIDATION_COMMIT = "9f948df"
FORWARD_SOURCE_DATE = "2026-08-15"
FORWARD_TARGET_DATE = "2026-08-16"
FROZEN_TWO_WAY_COMMIT = "920cb3b"
FROZEN_TWO_WAY_SOURCE_DATE = "2026-08-16"
FROZEN_TWO_WAY_TARGET_DATE = "2026-08-17"


def read_csv_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _csv_float(row: dict, key: str) -> float:
    return float(row[key])


def _csv_bool(row: dict, key: str) -> bool:
    return str(row.get(key, "")).strip().lower() == "true"


def _frozen_prediction_row(path: Path, source_date: str) -> dict:
    rows = [row for row in read_csv_rows(path) if row.get("source_date") == source_date]
    if not rows:
        raise FileNotFoundError(f"凍結予測が見つかりません: {path} / {source_date}")
    return rows[0]


def forward_validation_row(
    frozen: dict,
    target: dict,
    target_asof: dict,
) -> dict:
    """Compare the already-frozen D prediction with the D+1 as-of observation."""
    result = {
        "source_date": frozen["source_date"], "target_date": target["date"],
        "machine": frozen.get("machine", target["machine"]),
        "prediction_commit": FORWARD_VALIDATION_COMMIT,
        "prediction_status": "FROZEN_BEFORE_ACTUAL",
        "source_regime": frozen.get("source_regime", ""),
        "source_n_fft": frozen.get("source_n_fft", ""),
        "target_n_observations": target_asof.get("n_observations", ""),
        "target_n_fft": target_asof.get("n_fft", ""),
        "target_regime": target_asof.get("regime", ""),
        "n_fft_changed": frozen.get("target_n_fft", "") not in ("", None) and frozen.get("source_n_fft", "") != frozen.get("target_n_fft", ""),
        "actual_open": target["open"], "actual_high": target["high"], "actual_low": target["low"], "actual_close": target["close"],
        "actual_bullish": target["bullish"],
    }
    for role in ("long", "mid", "short"):
        predicted_phase = _csv_float(frozen, role + "_predicted_phase")
        actual_phase = float(target_asof[role + "_phase"])
        predicted_x = _csv_float(frozen, role + "_predicted_x")
        predicted_y = _csv_float(frozen, role + "_predicted_y")
        actual_x = float(target_asof[role + "_x"])
        actual_y = float(target_asof[role + "_y"])
        result.update({
            "predicted_" + role + "_phase": predicted_phase,
            "actual_" + role + "_phase": actual_phase,
            role + "_angular_error": _angular_error(predicted_phase, actual_phase),
            "predicted_" + role + "_x": predicted_x, "predicted_" + role + "_y": predicted_y,
            "actual_" + role + "_x": actual_x, "actual_" + role + "_y": actual_y,
            role + "_xy_distance": math.dist((predicted_x, predicted_y), (actual_x, actual_y)),
            "predicted_" + role + "_top_side": _csv_bool(frozen, role + "_predicted_top_side"),
            "actual_" + role + "_top_side": bool(target_asof[role + "_top_side"]),
            role + "_top_side_match": _csv_bool(frozen, role + "_predicted_top_side") == bool(target_asof[role + "_top_side"]),
            "predicted_" + role + "_k": frozen.get(role + "_source_k", ""),
            "predicted_" + role + "_frequency": frozen.get(role + "_source_frequency", ""),
            "predicted_" + role + "_period": frozen.get(role + "_source_period", ""),
            "predicted_" + role + "_rank": frozen.get(role + "_source_rank", ""),
            "actual_" + role + "_k": target_asof.get(role + "_k", ""),
            "actual_" + role + "_frequency": target_asof.get(role + "_frequency", ""),
            "actual_" + role + "_period": target_asof.get(role + "_period", ""),
            "actual_" + role + "_rank": target_asof.get(role + "_rank", ""),
            role + "_component_same_k": str(frozen.get(role + "_source_k", "")) == str(target_asof.get(role + "_k", "")),
        })
    predicted_points = [(result["predicted_" + role + "_x"], result["predicted_" + role + "_y"]) for role in ("long", "mid", "short")]
    actual_points = [(result["actual_" + role + "_x"], result["actual_" + role + "_y"]) for role in ("long", "mid", "short")]
    predicted_geometry = {
        "top_wave_count": int(frozen["predicted_top_wave_count"]),
        "top_wave_pattern": frozen["predicted_top_wave_pattern"],
        "centroid_x": _csv_float(frozen, "predicted_centroid_x"),
        "centroid_y": _csv_float(frozen, "predicted_centroid_y"),
        "centroid_region": frozen["predicted_centroid_region"],
    }
    actual_geometry = _prediction_geometry(actual_points)
    result.update({
        "predicted_top_wave_count": predicted_geometry["top_wave_count"], "actual_top_wave_count": actual_geometry["top_wave_count"],
        "top_count_match": predicted_geometry["top_wave_count"] == actual_geometry["top_wave_count"],
        "predicted_top_wave_pattern": predicted_geometry["top_wave_pattern"], "actual_top_wave_pattern": actual_geometry["top_wave_pattern"],
        "pattern_match": predicted_geometry["top_wave_pattern"] == actual_geometry["top_wave_pattern"],
        "predicted_centroid_x": predicted_geometry["centroid_x"], "predicted_centroid_y": predicted_geometry["centroid_y"],
        "actual_centroid_x": actual_geometry["centroid_x"], "actual_centroid_y": actual_geometry["centroid_y"],
        "centroid_distance": math.dist((predicted_geometry["centroid_x"], predicted_geometry["centroid_y"]), (actual_geometry["centroid_x"], actual_geometry["centroid_y"])),
        "predicted_centroid_region": predicted_geometry["centroid_region"], "actual_centroid_region": actual_geometry["centroid_region"],
        "centroid_region_match": predicted_geometry["centroid_region"] == actual_geometry["centroid_region"],
        "actual_phase_alignment_score": target_asof.get("phase_alignment_score", ""),
        "actual_convergence_score": target_asof.get("convergence_score", ""),
        "actual_wave_direction_pattern": target_asof.get("top_wave_pattern", ""),
        "actual_dominant_rank_signature": target_asof.get("dominant_rank_signature", ""),
        "actual_joint_repeat_period": target_asof.get("joint_repeat_period", ""),
        "actual_period_stability_score": target_asof.get("period_stability_score", ""),
    })
    return result


def build_forward_validation(
    machine: str,
    frozen_path: Path,
    all_rows: list[dict],
) -> dict:
    """Use 8/16 only for the answer check; never recompute the frozen 8/15 prediction."""
    frozen = _frozen_prediction_row(frozen_path, FORWARD_SOURCE_DATE)
    target_rows = [row for row in all_rows if row["date"] <= FORWARD_TARGET_DATE]
    target = next((row for row in target_rows if row["date"] == FORWARD_TARGET_DATE), None)
    if target is None:
        raise FileNotFoundError(f"台{machine}の{FORWARD_TARGET_DATE} OHLCが見つかりません")
    target = {**target, "bullish": target["close"] > target["open"], "next_day_bullish": None}
    target_rows = [target if row["date"] == FORWARD_TARGET_DATE else {**row, "bullish": row["close"] > row["open"], "next_day_bullish": None} for row in target_rows]
    full_components, full_daily, _centered, _comparison = analyze(target_rows)
    full_convergence, _ = phase_convergence_analysis(full_daily, full_components)
    full_alignment, _ = phase_alignment_analysis(full_convergence)
    extended_regime, _ = period_regime_history(target_rows, full_convergence, full_alignment)
    extended_asof = asof_phase_space_history(target_rows, extended_regime)
    target_asof = next(row for row in extended_asof if row["date"] == FORWARD_TARGET_DATE)
    target_regime = next(row for row in extended_regime if row["date"] == FORWARD_TARGET_DATE)
    target_asof = {**target_regime, **target_asof}
    assert target_asof["status"] == "VALID"
    result = forward_validation_row(frozen, target, target_asof)
    return result


def _frozen_prediction_points(row: dict) -> list[tuple[float, float]]:
    return [(float(row[role + "_x"]), float(row[role + "_y"])) for role in ("long", "mid", "short")]


def frozen_two_way_validation(
    machine: str,
    frozen_path: Path,
    all_rows: list[dict],
    historical_transformations: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Answer-check the already-frozen 8/16 predictions against 8/17 only.

    The frozen rows are read from next_phase_prediction_frozen.csv.  The
    target prefix is used only to calculate the actual 8/17 as-of state;
    it is never used to select or alter the transformation-aware prediction.
    """
    frozen_rows = [
        row for row in read_csv_rows(frozen_path)
        if row.get("source_date") == FROZEN_TWO_WAY_SOURCE_DATE
        and row.get("target_date") == FROZEN_TWO_WAY_TARGET_DATE
        and row.get("prediction_status") == "FROZEN_BEFORE_ACTUAL"
    ]
    if not frozen_rows:
        raise FileNotFoundError(f"8/16 -> 8/17凍結予測が見つかりません: {frozen_path}")
    target = next((row for row in all_rows if row["date"] == FROZEN_TWO_WAY_TARGET_DATE), None)
    if target is None:
        raise FileNotFoundError(f"台{machine}の{FROZEN_TWO_WAY_TARGET_DATE} OHLCが見つかりません")
    target = {**target, "bullish": target["close"] > target["open"], "next_day_bullish": None}
    target_rows = [
        {**row, "bullish": row["close"] > row["open"], "next_day_bullish": None}
        for row in all_rows if row["date"] <= FROZEN_TWO_WAY_TARGET_DATE
    ]
    _components, _daily, _centered, _comparison = analyze(target_rows)
    target_convergence, _ = phase_convergence_analysis(_daily, _components)
    target_alignment, _ = phase_alignment_analysis(target_convergence)
    target_regime_rows, _ = period_regime_history(target_rows, target_convergence, target_alignment)
    target_asof_rows = asof_phase_space_history(target_rows, target_regime_rows)
    target_asof = next(row for row in target_asof_rows if row["date"] == FROZEN_TWO_WAY_TARGET_DATE)
    target_regime = next(row for row in target_regime_rows if row["date"] == FROZEN_TWO_WAY_TARGET_DATE)
    target_asof = {**target_regime, **target_asof}
    if target_asof.get("status") != "VALID":
        raise ValueError(f"台{machine}の8/17 As-of Phase SpaceがVALIDではありません")
    source_asof = next(row for row in target_asof_rows if row["date"] == FROZEN_TWO_WAY_SOURCE_DATE)
    actual_points = [(float(target_asof[role + "_x"]), float(target_asof[role + "_y"])) for role in ("long", "mid", "short")]
    actual_geometry = _prediction_geometry(actual_points)
    results = []
    baseline_adapter = None
    for frozen in frozen_rows:
        predicted_points = _frozen_prediction_points(frozen)
        predicted_geometry = {
            "centroid_x": float(frozen["predicted_centroid_x"]),
            "centroid_y": float(frozen["predicted_centroid_y"]),
            "centroid_region": frozen["predicted_centroid_region"],
            "top_wave_count": int(frozen["predicted_top_wave_count"]),
            "top_wave_pattern": frozen["predicted_top_wave_pattern"],
        }
        result = {
            "source_date": frozen["source_date"], "target_date": frozen["target_date"], "machine": machine,
            "prediction_commit": FROZEN_TWO_WAY_COMMIT, "prediction_status": "FROZEN_BEFORE_ACTUAL",
            "prediction_type": frozen.get("prediction_type", ""), "source_regime": frozen.get("source_regime", ""),
            "source_n_fft": frozen.get("source_n_fft", ""), "source_cutoff": frozen.get("source_cutoff", ""),
            "selected_transformation": frozen.get("selected_transformation", ""),
            "selection_basis": frozen.get("selection_basis", ""), "support_samples": frozen.get("support_samples", ""),
            "transformation_probability": frozen.get("transformation_probability", ""),
            "support_status": frozen.get("support_status", ""),
            "target_n_observations": target_asof.get("n_observations", ""), "target_n_fft": target_asof.get("n_fft", ""),
            "target_regime": target_asof.get("regime", ""),
            "n_fft_changed": str(frozen.get("source_n_fft", "")) != str(target_asof.get("n_fft", "")),
            "actual_open": target["open"], "actual_high": target["high"], "actual_low": target["low"],
            "actual_close": target["close"], "actual_bullish": target["bullish"],
            "predicted_centroid_x": predicted_geometry["centroid_x"], "predicted_centroid_y": predicted_geometry["centroid_y"],
            "predicted_centroid_region": predicted_geometry["centroid_region"], "predicted_top_wave_count": predicted_geometry["top_wave_count"],
            "predicted_top_wave_pattern": predicted_geometry["top_wave_pattern"],
            "actual_centroid_x": actual_geometry["centroid_x"], "actual_centroid_y": actual_geometry["centroid_y"],
            "actual_centroid_region": actual_geometry["centroid_region"], "actual_top_wave_count": actual_geometry["top_wave_count"],
            "actual_top_wave_pattern": actual_geometry["top_wave_pattern"],
            "top_count_match": predicted_geometry["top_wave_count"] == actual_geometry["top_wave_count"],
            "pattern_match": predicted_geometry["top_wave_pattern"] == actual_geometry["top_wave_pattern"],
            "centroid_distance": math.dist((predicted_geometry["centroid_x"], predicted_geometry["centroid_y"]), (actual_geometry["centroid_x"], actual_geometry["centroid_y"])),
            "centroid_region_match": predicted_geometry["centroid_region"] == actual_geometry["centroid_region"],
            "actual_phase_alignment_score": target_asof.get("phase_alignment_score", ""),
            "actual_convergence_score": target_asof.get("convergence_score", ""),
            "actual_dominant_rank_signature": target_asof.get("dominant_rank_signature", ""),
            "actual_joint_repeat_period": target_asof.get("joint_repeat_period", ""),
            "actual_period_stability_score": target_asof.get("period_stability_score", ""),
        }
        for role in ("long", "mid", "short"):
            source_role = source_asof.get(role, {}) if isinstance(source_asof.get(role), dict) else source_asof
            predicted_phase = float(frozen[role + "_phase"])
            actual_phase = float(target_asof[role + "_phase"])
            predicted_x, predicted_y = predicted_points[("long", "mid", "short").index(role)]
            actual_x, actual_y = float(target_asof[role + "_x"]), float(target_asof[role + "_y"])
            result.update({
                "predicted_" + role + "_phase": predicted_phase, "actual_" + role + "_phase": actual_phase,
                role + "_angular_error": _angular_error(predicted_phase, actual_phase),
                "predicted_" + role + "_x": predicted_x, "predicted_" + role + "_y": predicted_y,
                "actual_" + role + "_x": actual_x, "actual_" + role + "_y": actual_y,
                role + "_xy_distance": math.dist((predicted_x, predicted_y), (actual_x, actual_y)),
                "predicted_" + role + "_k": source_asof.get(role + "_k", ""), "actual_" + role + "_k": target_asof.get(role + "_k", ""),
                "predicted_" + role + "_frequency": source_asof.get(role + "_frequency", ""), "actual_" + role + "_frequency": target_asof.get(role + "_frequency", ""),
                "predicted_" + role + "_period": source_asof.get(role + "_period", ""), "actual_" + role + "_period": target_asof.get(role + "_period", ""),
                "predicted_" + role + "_rank": source_asof.get(role + "_rank", ""), "actual_" + role + "_rank": target_asof.get(role + "_rank", ""),
                role + "_component_same_k": str(source_asof.get(role + "_k", "")) == str(target_asof.get(role + "_k", "")),
            })
        results.append(result)
        if frozen.get("prediction_type") == "BASELINE":
            baseline_adapter = {
                "source_date": frozen["source_date"], "target_date": frozen["target_date"], "machine": machine,
                "status": "VALID_PREDICTION", "source_regime": frozen.get("source_regime", ""), "source_n_fft": frozen.get("source_n_fft", ""),
                "comparison_scope": "FORWARD_FROZEN_20260816_20260817", "target_bullish": target["bullish"],
                "long_angular_error_deg": result["long_angular_error"], "mid_angular_error_deg": result["mid_angular_error"], "short_angular_error_deg": result["short_angular_error"],
            }
            for role in ("long", "mid", "short"):
                baseline_adapter[role + "_predicted_x"] = result["predicted_" + role + "_x"]
                baseline_adapter[role + "_predicted_y"] = result["predicted_" + role + "_y"]
                baseline_adapter[role + "_actual_x"] = result["actual_" + role + "_x"]
                baseline_adapter[role + "_actual_y"] = result["actual_" + role + "_y"]
                baseline_adapter[role + "_predicted_phase"] = result["predicted_" + role + "_phase"]
                baseline_adapter[role + "_actual_phase"] = result["actual_" + role + "_phase"]
                baseline_adapter[role + "_xy_error"] = result[role + "_xy_distance"]
    actual_transformations = phase_transformation_rows([baseline_adapter], target_rows, {FROZEN_TWO_WAY_SOURCE_DATE: source_asof}) if baseline_adapter else []
    if actual_transformations:
        refine_identity_classification(actual_transformations, historical_transformations)
        actual_transformation = actual_transformations[0]
        for result in results:
            for key, value in actual_transformation.items():
                result["actual_transformation_" + key] = value
    return results, actual_transformations


def _prediction_comparison_rows(rows: list[dict]) -> list[dict]:
    return [row for row in rows if row["status"] == "PENDING_ACTUAL" or row["status"] == "VALID_PREDICTION" and row.get("target_n_observations", "") != ""]


def _safe_mean(values: list[float]) -> float | str:
    return sum(values) / len(values) if values else ""


def _safe_median(values: list[float]) -> float | str:
    return quantile(values, 0.5) if values else ""


def next_phase_prediction_stats(rows: list[dict]) -> list[dict]:
    valid = [row for row in rows if row["status"] == "VALID_PREDICTION" and row.get("long_angular_error_deg", "") != ""]
    result = []
    for role in ("long", "mid", "short"):
        angular = [float(row[role + "_angular_error_deg"]) for row in valid]
        xy = [float(row[role + "_xy_error"]) for row in valid]
        top = [bool(row[role + "_top_side_match"]) for row in valid]
        same = [bool(row[role + "_component_same_k"]) for row in valid]
        result.append({"scope": role.upper(), "samples": len(valid), "mean_angular_error_deg": _safe_mean(angular), "median_angular_error_deg": _safe_median(angular), "mean_xy_error": _safe_mean(xy), "top_side_accuracy": sum(top) / len(top) * 100.0 if top else "", "component_same_k_rate": sum(same) / len(same) * 100.0 if same else ""})
    centroid = [row for row in valid if row.get("centroid_distance_error", "") != ""]
    result.append({"scope": "GEOMETRY", "samples": len(centroid), "centroid_mean_distance_error": _safe_mean([float(row["centroid_distance_error"]) for row in centroid]), "centroid_region_accuracy": sum(bool(row["centroid_region_match"]) for row in centroid) / len(centroid) * 100.0 if centroid else "", "top_wave_count_exact_accuracy": sum(bool(row["top_wave_count_exact"]) for row in centroid) / len(centroid) * 100.0 if centroid else "", "top_wave_pattern_accuracy": sum(bool(row["top_wave_pattern_match"]) for row in centroid) / len(centroid) * 100.0 if centroid else ""})
    return result


def next_phase_prediction_group_stats(rows: list[dict], key: str, values: tuple) -> list[dict]:
    valid = [row for row in rows if row["status"] == "VALID_PREDICTION" and row.get("long_angular_error_deg", "") != ""]
    result = []
    for value in values:
        samples = [row for row in valid if row.get(key) == value]
        entry = {key: value, "samples": len(samples)}
        for role in ("long", "mid", "short"):
            entry[role + "_mean_angular_error_deg"] = _safe_mean([float(row[role + "_angular_error_deg"]) for row in samples])
            entry[role + "_mean_xy_error"] = _safe_mean([float(row[role + "_xy_error"]) for row in samples])
            entry[role + "_top_side_accuracy"] = sum(bool(row[role + "_top_side_match"]) for row in samples) / len(samples) * 100.0 if samples else ""
        entry["centroid_mean_distance_error"] = _safe_mean([float(row["centroid_distance_error"]) for row in samples])
        entry["centroid_region_accuracy"] = sum(bool(row["centroid_region_match"]) for row in samples) / len(samples) * 100.0 if samples else ""
        entry["top_wave_count_exact_accuracy"] = sum(bool(row["top_wave_count_exact"]) for row in samples) / len(samples) * 100.0 if samples else ""
        entry["top_wave_pattern_accuracy"] = sum(bool(row["top_wave_pattern_match"]) for row in samples) / len(samples) * 100.0 if samples else ""
        result.append(entry)
    return result


def asof_phase_regime_stats(rows: list[dict]) -> list[dict]:
    valid = [row for row in rows if row["status"] == "VALID"]
    result = []
    for regime in ("STABLE", "TRANSITION", "UNSTABLE"):
        for count in (0, 1, 2, 3):
            samples = [row for row in valid if row["regime"] == regime and row["top_wave_count"] == count]
            result.append(_position_rate_row(regime, str(count), samples))
        samples = [row for row in valid if row["regime"] == regime and row["top_wave_count"] >= 2]
        result.append(_position_rate_row(regime, "2+", samples))
    return result


def asof_phase_region_stats(rows: list[dict]) -> list[dict]:
    valid = [row for row in rows if row["status"] == "VALID"]
    result = []
    for region in ("TOP", "BOTTOM"):
        for metric, flag in (("HIGH_ALIGNMENT", "high_alignment_asof"), ("CONVERGENCE", "phase_convergence_asof")):
            samples = [row for row in valid if row["centroid_region"] == region and row[flag] is True]
            result.append(_position_rate_row(region + "_" + metric, "TRUE", samples))
    return result


def asof_nfft_stats(rows: list[dict]) -> list[dict]:
    valid = [row for row in rows if row["status"] == "VALID"]
    result = []
    for n_fft in (32, 64):
        group = [row for row in valid if int(row["n_fft"]) == n_fft]
        for count in (0, 1, 2, 3):
            samples = [row for row in group if row["top_wave_count"] == count]
            rate = _position_rate_row("N_FFT", str(count), samples)
            result.append({"n_fft": n_fft, "top_wave_count": count, **{key: rate[key] for key in rate if key not in {"category", "value"}}})
        samples = [row for row in group if row["top_wave_count"] >= 2]
        rate = _position_rate_row("N_FFT", "2+", samples)
        result.append({"n_fft": n_fft, "top_wave_count": "2+", **{key: rate[key] for key in rate if key not in {"category", "value"}}})
    return result


def asof_nfft_pattern_stats(rows: list[dict]) -> list[dict]:
    valid = [row for row in rows if row["status"] == "VALID"]
    result = []
    patterns = ("NONE", "LONG", "MID", "SHORT", "LONG+MID", "LONG+SHORT", "MID+SHORT", "LONG+MID+SHORT")
    for n_fft in (32, 64):
        group = [row for row in valid if int(row["n_fft"]) == n_fft]
        for pattern in patterns:
            rate = _position_rate_row("N_FFT", pattern, [row for row in group if row["top_wave_pattern"] == pattern])
            result.append({"n_fft": n_fft, "top_wave_pattern": pattern, **{key: rate[key] for key in rate if key not in {"category", "value", "bullish_count", "next_day_bullish_count"}}})
    return result


def asof_nfft_regime_stats(rows: list[dict]) -> list[dict]:
    valid = [row for row in rows if row["status"] == "VALID"]
    result = []
    for n_fft in (32, 64):
        for regime in ("STABLE", "TRANSITION", "UNSTABLE"):
            samples = [row for row in valid if int(row["n_fft"]) == n_fft and row["regime"] == regime and row["top_wave_count"] >= 2]
            rate = _position_rate_row("N_FFT_REGIME", regime, samples)
            result.append({"n_fft": n_fft, "regime": regime, "top_wave_count": "2+", **{key: rate[key] for key in rate if key not in {"category", "value"}}})
    return result


def asof_nfft_transition_detail(asof_rows: list[dict], regime_rows: list[dict]) -> list[dict]:
    valid = [row for row in regime_rows if row.get("n_fft") not in ("", None)]
    if not valid:
        return []
    boundary = next((index for index, row in enumerate(valid) if int(row["n_fft"]) == 64 and (index == 0 or int(valid[index - 1]["n_fft"]) != 64)), None)
    if boundary is None:
        return []
    by_date = {row["date"]: row for row in asof_rows}
    result = []
    for regime_row in valid[max(0, boundary - 3): boundary + 4]:
        asof = by_date[regime_row["date"]]
        result.append({
            "date": regime_row["date"], "n_observations": regime_row["n_observations"], "n_fft": regime_row["n_fft"],
            "long_k": regime_row.get("long_k", ""), "mid_k": regime_row.get("mid_k", ""), "short_k": regime_row.get("short_k", ""),
            "long_frequency": regime_row.get("long_frequency", ""), "mid_frequency": regime_row.get("mid_frequency", ""), "short_frequency": regime_row.get("short_frequency", ""),
            "long_period": regime_row.get("long_period", ""), "mid_period": regime_row.get("mid_period", ""), "short_period": regime_row.get("short_period", ""),
            "dominant_rank_signature": regime_row.get("dominant_rank_signature", ""), "regime": regime_row.get("regime", ""),
            "period_stability_score": regime_row.get("period_stability_score", ""), "joint_repeat_period": regime_row.get("joint_repeat_period", ""),
            "long_phase": asof.get("long_phase", ""), "mid_phase": asof.get("mid_phase", ""), "short_phase": asof.get("short_phase", ""),
            "top_wave_count": asof.get("top_wave_count", ""), "top_wave_pattern": asof.get("top_wave_pattern", ""),
            "centroid_region": asof.get("centroid_region", ""), "centroid_y_offset": asof.get("centroid_y_offset", ""),
            "alignment_score": asof.get("phase_alignment_score", ""), "convergence_score": asof.get("convergence_score", ""),
            "bullish": asof.get("bullish", ""), "next_day_bullish": asof.get("next_day_bullish", ""),
            "n_fft_changed": regime_row.get("n_fft_changed", False),
        })
    return result


def phase_alignment_score(row: dict) -> float:
    angles = [math.radians(row[f"wave{wave}_phase"]) for wave in (1, 2, 3)]
    resultant = sum(complex(math.cos(angle), math.sin(angle)) for angle in angles) / 3.0
    return clip01(abs(resultant))


def phase_alignment_analysis(convergence_rows: list[dict]) -> tuple[list[dict], float]:
    result = []
    for row in convergence_rows:
        alignment = phase_alignment_score({
            "wave1_phase": row["long_phase"], "wave2_phase": row["mid_phase"], "wave3_phase": row["short_phase"],
        })
        result.append({**row, "phase_alignment_score": alignment})
    threshold = quantile([row["phase_alignment_score"] for row in result], PHASE_CONVERGENCE_QUANTILE)
    for row in result:
        row["high_alignment"] = row["phase_alignment_score"] >= threshold
    return result, threshold


def phase_alignment_stats(rows: list[dict]) -> list[dict]:
    result = []
    for index in range(5):
        start = index * CONVERGENCE_SCORE_BIN_WIDTH
        end = start + CONVERGENCE_SCORE_BIN_WIDTH
        samples = [row for row in rows if (start <= row["phase_alignment_score"] <= end if index == 4 else start <= row["phase_alignment_score"] < end)]
        next_samples = [row for row in samples if row["next_day_bullish"] is not None]
        bullish = sum(row["bullish"] for row in samples)
        next_bullish = sum(row["next_day_bullish"] for row in next_samples)
        result.append({
            "score_bin": f"{start:.1f}-{end:.1f}", "samples": len(samples), "bullish_count": bullish,
            "bullish_rate": bullish / len(samples) * 100.0 if samples else "",
            "next_day_samples": len(next_samples), "next_day_bullish_count": next_bullish,
            "next_day_bullish_rate": next_bullish / len(next_samples) * 100.0 if next_samples else "",
        })
    return result


def phase_alignment_region_stats(rows: list[dict]) -> list[dict]:
    result = []
    for region in ("TOP", "RIGHT", "BOTTOM", "LEFT"):
        for high_alignment in (False, True):
            samples = [row for row in rows if row["centroid_region"] == region and row["high_alignment"] is high_alignment]
            next_samples = [row for row in samples if row["next_day_bullish"] is not None]
            bullish = sum(row["bullish"] for row in samples)
            next_bullish = sum(row["next_day_bullish"] for row in next_samples)
            result.append({
                "centroid_region": region, "high_alignment": high_alignment, "samples": len(samples),
                "bullish_count": bullish, "bullish_rate": bullish / len(samples) * 100.0 if samples else "",
                "next_day_samples": len(next_samples), "next_day_bullish_count": next_bullish,
                "next_day_bullish_rate": next_bullish / len(next_samples) * 100.0 if next_samples else "",
            })
    return result


def lcm(left: int, right: int) -> int:
    return abs(left * right) // math.gcd(left, right)


def repeat_periods(components: list[dict]) -> tuple[dict[str, int], int]:
    periods = {}
    for component in components:
        n_fft = int(component["n_fft"])
        fft_bin = round(float(component["frequency"]) * n_fft)
        periods[component["role"]] = n_fft // math.gcd(fft_bin, n_fft)
    joint = 1
    for period in periods.values():
        joint = lcm(joint, period)
    return periods, joint


def add_repeat_metadata(rows: list[dict], joint_repeat_period: int) -> list[dict]:
    result = []
    for index, row in enumerate(rows):
        result.append({
            **row,
            "repeat_position": index % joint_repeat_period,
            "repeat_cycle_index": index // joint_repeat_period,
        })
    return result


def period_regime_history(
    rows: list[dict],
    full_convergence_rows: list[dict],
    full_alignment_rows: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Build expanding/as-of FFT history. Each prefix ends exactly on its row date."""
    full_convergence = {row["date"]: row for row in full_convergence_rows}
    full_alignment = {row["date"]: row for row in full_alignment_rows}
    history = []
    events = []
    previous = None

    for index, row in enumerate(rows):
        as_of = row["date"]
        n_observations = index + 1
        base = {
            "date": as_of, "machine": row["machine"], "n_observations": n_observations,
            "open": row["open"], "high": row["high"], "low": row["low"], "close": row["close"],
            "bullish": row["bullish"], "next_day_bullish": row["next_day_bullish"],
            "status": "INSUFFICIENT_HISTORY", "regime": "INSUFFICIENT_HISTORY",
            "n_fft": "", "component_reorder": False,
            "full_fft_alignment_score": full_alignment.get(as_of, {}).get("phase_alignment_score", ""),
            "full_fft_convergence_score": full_convergence.get(as_of, {}).get("convergence_score", ""),
            "full_fft_centroid_region": full_convergence.get(as_of, {}).get("centroid_region", ""),
        }
        if n_observations < MIN_REGIME_OBSERVATIONS:
            history.append(base)
            continue

        prefix_rows = rows[: index + 1]
        assert prefix_rows[-1]["date"] == as_of
        assert max(prefix_row["date"] for prefix_row in prefix_rows) <= as_of
        prefix_components, prefix_daily, _centered, _comparison = analyze(prefix_rows)
        prefix_convergence, _ = phase_convergence_analysis(prefix_daily, prefix_components)
        prefix_last = prefix_daily[-1]
        prefix_last_convergence = prefix_convergence[-1]
        role_components = {component["role"]: component for component in prefix_components}
        role_order = ("LONG", "MID", "SHORT")
        n_fft = int(prefix_components[0]["n_fft"])
        periods = {role: role_components[role] for role in role_order}
        repeat_by_role, joint_repeat = repeat_periods(prefix_components)
        signature = "-".join(str(periods[role]["rank"]) for role in role_order)
        current = {
            **base, "status": "VALID", "n_fft": n_fft,
            "dominant_rank_signature": signature,
            "long_k": round(periods["LONG"]["frequency"] * n_fft),
            "mid_k": round(periods["MID"]["frequency"] * n_fft),
            "short_k": round(periods["SHORT"]["frequency"] * n_fft),
            "long_frequency": periods["LONG"]["frequency"], "mid_frequency": periods["MID"]["frequency"], "short_frequency": periods["SHORT"]["frequency"],
            "long_period": periods["LONG"]["period_days"], "mid_period": periods["MID"]["period_days"], "short_period": periods["SHORT"]["period_days"],
            "long_amplitude": periods["LONG"]["amplitude"], "mid_amplitude": periods["MID"]["amplitude"], "short_amplitude": periods["SHORT"]["amplitude"],
            "long_power": periods["LONG"]["relative_power"], "mid_power": periods["MID"]["relative_power"], "short_power": periods["SHORT"]["relative_power"],
            "long_rank": periods["LONG"]["rank"], "mid_rank": periods["MID"]["rank"], "short_rank": periods["SHORT"]["rank"],
            "long_repeat_period": repeat_by_role["LONG"], "mid_repeat_period": repeat_by_role["MID"], "short_repeat_period": repeat_by_role["SHORT"],
            "joint_repeat_period": joint_repeat,
            "asof_long_phase": prefix_last["wave1_phase"], "asof_mid_phase": prefix_last["wave2_phase"], "asof_short_phase": prefix_last["wave3_phase"],
            "asof_phase_alignment_score": phase_alignment_score(prefix_last),
            "asof_phase_convergence_score": prefix_last_convergence["convergence_score"],
            "asof_centroid_region": prefix_last_convergence["centroid_region"],
            "full_fft_alignment_score": full_alignment.get(as_of, {}).get("phase_alignment_score", ""),
            "full_fft_convergence_score": full_convergence.get(as_of, {}).get("convergence_score", ""),
            "full_fft_centroid_region": full_convergence.get(as_of, {}).get("centroid_region", ""),
        }
        if previous is not None:
            nfft_changed = previous["n_fft"] != n_fft
            current["n_fft_changed"] = nfft_changed
            current["component_reorder"] = previous.get("dominant_rank_signature") != signature
            if not nfft_changed:
                for role in role_order:
                    previous_period = float(previous[f"{role.lower()}_period"])
                    current_period = float(current[f"{role.lower()}_period"])
                    change = abs(current_period - previous_period)
                    current[f"{role.lower()}_period_change"] = change
                    current[f"{role.lower()}_period_change_pct"] = change / previous_period if previous_period else 0.0
                mean_change = sum(current[f"{role.lower()}_period_change_pct"] for role in role_order) / 3.0
                current["period_stability_score"] = clip01(1.0 - mean_change / REGIME_REFERENCE_CHANGE)
            else:
                current["period_stability_score"] = ""
                current["long_period_change"] = current["mid_period_change"] = current["short_period_change"] = ""
                current["long_period_change_pct"] = current["mid_period_change_pct"] = current["short_period_change_pct"] = ""
            shift_roles = [role for role in role_order if isinstance(current.get(f"{role.lower()}_period_change_pct"), (int, float)) and current[f"{role.lower()}_period_change_pct"] >= REGIME_SHIFT_PCT]
            if nfft_changed or len(shift_roles) >= 2 or (current["period_stability_score"] != "" and current["period_stability_score"] < REGIME_UNSTABLE_SCORE_THRESHOLD):
                current["regime"] = "UNSTABLE"
            elif current["component_reorder"] or shift_roles or current["period_stability_score"] == "" or current["period_stability_score"] < REGIME_STABLE_SCORE_THRESHOLD:
                current["regime"] = "TRANSITION"
            else:
                current["regime"] = "STABLE"
            current["status"] = current["regime"]
        else:
            current["n_fft_changed"] = False
            current["component_reorder"] = False
            current["period_stability_score"] = ""
            current["regime"] = "TRANSITION"
            current["status"] = "TRANSITION"
        if previous is not None and previous.get("joint_repeat_period") == joint_repeat:
            current["joint_repeat_stable_count"] = int(previous.get("joint_repeat_stable_count", 0)) + 1
        else:
            current["joint_repeat_stable_count"] = 1
        history.append(current)

        if previous is not None:
            if previous.get("n_fft") != n_fft:
                events.append({"date": as_of, "event_type": "FFT_SIZE_CHANGE", "previous_value": previous.get("n_fft"), "current_value": n_fft, "details": "frequency-bin resolution changed"})
            for role in role_order:
                pct = current.get(f"{role.lower()}_period_change_pct")
                if isinstance(pct, (int, float)) and pct >= REGIME_SHIFT_PCT:
                    events.append({"date": as_of, "event_type": f"{role}_PERIOD_SHIFT", "previous_value": previous.get(f"{role.lower()}_period"), "current_value": current[f"{role.lower()}_period"], "details": f"absolute pct change={pct:.3f}"})
            if current["component_reorder"]:
                events.append({"date": as_of, "event_type": "COMPONENT_REORDER", "previous_value": previous.get("dominant_rank_signature"), "current_value": signature, "details": "LONG-MID-SHORT rank signature changed"})
            if previous.get("joint_repeat_period") != joint_repeat:
                events.append({"date": as_of, "event_type": "JOINT_REPEAT_CHANGE", "previous_value": previous.get("joint_repeat_period"), "current_value": joint_repeat, "details": "LCM of discrete component repeat periods changed"})
            if previous.get("regime") != current["regime"]:
                events.append({"date": as_of, "event_type": f"REGIME_TO_{current['regime']}", "previous_value": previous.get("regime"), "current_value": current["regime"], "details": "period-regime classification changed"})
        previous = current
    return history, events


def period_regime_stats(rows: list[dict]) -> list[dict]:
    result = []
    for regime in ("INSUFFICIENT_HISTORY", "STABLE", "TRANSITION", "UNSTABLE"):
        samples = [row for row in rows if row.get("regime") == regime]
        next_samples = [row for row in samples if row.get("next_day_bullish") is not None]
        valid = [row for row in samples if row.get("long_period") not in (None, "")]
        alignment = [row for row in samples if row.get("asof_phase_alignment_score") not in (None, "")]
        convergence = [row for row in samples if row.get("asof_phase_convergence_score") not in (None, "")]
        bullish = sum(row["bullish"] for row in samples)
        next_bullish = sum(row["next_day_bullish"] for row in next_samples)
        result.append({
            "regime": regime, "samples": len(samples), "bullish_count": bullish,
            "bullish_rate": bullish / len(samples) * 100.0 if samples else "",
            "next_day_samples": len(next_samples), "next_day_bullish_count": next_bullish,
            "next_day_bullish_rate": next_bullish / len(next_samples) * 100.0 if next_samples else "",
            "avg_long_period": sum(float(row["long_period"]) for row in valid) / len(valid) if valid else "",
            "avg_mid_period": sum(float(row["mid_period"]) for row in valid) / len(valid) if valid else "",
            "avg_short_period": sum(float(row["short_period"]) for row in valid) / len(valid) if valid else "",
            "avg_alignment": sum(float(row["asof_phase_alignment_score"]) for row in alignment) / len(alignment) if alignment else "",
            "avg_convergence": sum(float(row["asof_phase_convergence_score"]) for row in convergence) / len(convergence) if convergence else "",
        })
    return result


def validate_period_regime_history(history: list[dict], cutoff_date: str) -> None:
    assert history
    assert all(row["date"] <= cutoff_date for row in history)
    assert all(row["n_observations"] < MIN_REGIME_OBSERVATIONS and row["regime"] == "INSUFFICIENT_HISTORY" or row["n_observations"] >= MIN_REGIME_OBSERVATIONS for row in history)
    assert all(row["n_observations"] <= index + 1 for index, row in enumerate(history))


def svg_path(values: list[float], x0: float, y0: float, width: float, height: float, lo: float, hi: float) -> str:
    span = hi - lo or 1.0
    points = []
    for i, value in enumerate(values):
        x = x0 + width * i / max(1, len(values) - 1)
        y = y0 + height * (hi - value) / span
        points.append(f"{x:.2f},{y:.2f}")
    return "M " + " L ".join(points)


def build_html(machine: str, rows: list[dict], components: list[dict], daily: list[dict]) -> str:
    width, left, right = 1200, 70, 30
    plot_width = width - left - right
    top_height, wave_height, gap = 260, 300, 34
    chart_height = 18 + top_height + wave_height + gap + 24
    dates = [row["date"] for row in rows]
    prices = [row["low"] for row in rows] + [row["high"] for row in rows]
    p_lo, p_hi = min(prices), max(prices)
    wave_values = [[row[f"wave{i}_value"] for row in daily] for i in (1, 2, 3)]
    wave_values.append([row["combined_wave"] for row in daily])
    w_lo, w_hi = min(value for series in wave_values for value in series), max(value for series in wave_values for value in series)
    if w_lo == w_hi:
        w_lo -= 1
        w_hi += 1
    def x(i: int) -> float:
        return left + plot_width * i / max(1, len(rows) - 1)
    def price_y(v: float) -> float:
        return 18 + top_height * (p_hi - v) / (p_hi - p_lo or 1)
    def wave_y(v: float) -> float:
        return 18 + top_height + gap + wave_height * (w_hi - v) / (w_hi - w_lo)
    lines = []
    lines.append(f'<svg viewBox="0 0 {width} {chart_height}" role="img" aria-label="台{machine}のローソク足とFFT再構成波">')
    lines.append('<style>text{font-family:system-ui,sans-serif;fill:#263238;font-size:12px}.axis{stroke:#9aa7ad;stroke-width:1}.grid{stroke:#dfe5e8;stroke-width:1}.candle{stroke:#455a64;stroke-width:1}.bull{fill:#d32f2f}.bear{fill:#1976d2}.w1{stroke:#ef6c00}.w2{stroke:#2e7d32}.w3{stroke:#6a1b9a}.combined{stroke:#111827;stroke-width:2.5}.wave{fill:none;stroke-width:2}.bullmark{fill:#d32f2f;stroke:#fff;stroke-width:1}</style>')
    for frac in (0.0, 0.5, 1.0):
        yy = 18 + top_height * frac
        lines.append(f'<line class="grid" x1="{left}" y1="{yy:.1f}" x2="{width-right}" y2="{yy:.1f}"/>')
        lines.append(f'<line class="grid" x1="{left}" y1="{18+top_height+gap+wave_height*frac:.1f}" x2="{width-right}" y2="{18+top_height+gap+wave_height*frac:.1f}"/>')
    for i, row in enumerate(rows):
        xx = x(i)
        lines.append(f'<line class="axis" x1="{xx:.1f}" y1="18" x2="{xx:.1f}" y2="{chart_height}" opacity=".12"/>')
        candle_top, candle_bottom = price_y(max(row["open"], row["close"])), price_y(min(row["open"], row["close"]))
        klass = "bull" if row["close"] > row["open"] else "bear"
        body_height = max(1.5, candle_bottom - candle_top)
        lines.append(f'<line class="candle" x1="{xx:.1f}" y1="{price_y(row["high"]):.1f}" x2="{xx:.1f}" y2="{price_y(row["low"]):.1f}"/>')
        lines.append(f'<rect class="{klass}" x="{xx-5:.1f}" y="{candle_top:.1f}" width="10" height="{body_height:.1f}"/>')
    for i, series in enumerate(wave_values[:3], 1):
        lines.append(f'<path class="wave w{i}" d="{svg_path(series, left, 18+top_height+gap, plot_width, wave_height, w_lo, w_hi)}"/>')
    lines.append(f'<path class="wave combined" d="{svg_path(wave_values[3], left, 18+top_height+gap, plot_width, wave_height, w_lo, w_hi)}"/>')
    for i, row in enumerate(daily):
        if row["bullish"]:
            lines.append(f'<circle class="bullmark" cx="{x(i):.1f}" cy="{wave_y(row["combined_wave"]):.1f}" r="4"/>')
    for i in (0, len(rows)//2, len(rows)-1):
        xx = x(i)
        lines.append(f'<text x="{xx:.1f}" y="{chart_height-4}" text-anchor="middle">{html.escape(dates[i])}</text>')
    lines.extend([f'<text x="8" y="28">Close / OHLC</text>', f'<text x="8" y="{18+top_height+gap+12}">reconstructed wave</text>'])
    lines.append('</svg>')
    component_rows = "".join(
        f"<tr><td>{c['rank']}</td><td>{c['period_days']:.2f}</td><td>{c['frequency']:.5f}</td><td>{c['amplitude']:.2f}</td><td>{c['phase']:.2f}°</td><td>{c['relative_power']*100:.2f}%</td></tr>"
        for c in components
    )
    return f'''<!doctype html><html lang="ja"><meta charset="utf-8"><title>Wave Lab {machine}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1250px;margin:24px auto;padding:0 16px;color:#263238}}table{{border-collapse:collapse;margin:12px 0 22px}}th,td{{border-bottom:1px solid #ddd;padding:5px 10px;text-align:right}}th{{text-align:left}}.note{{color:#546e7a}}svg{{width:100%;height:auto}}</style>
<h1>Wave Lab: 台{machine} FFT再構成</h1><p>対象期間: {dates[0]}〜{dates[-1]} / データ件数: {len(rows)} / Closeベース</p>
<p class="note">全履歴でFFTを計算したretrospective / exploratory analysisです。赤丸は陽線（close &gt; open）。位相は 0°=谷、90°=上昇、180°=山、270°=下降の定義です。</p>
<h2>主要周期</h2><table><thead><tr><th>rank</th><th>period_days</th><th>frequency</th><th>amplitude</th><th>FFT phase</th><th>relative power</th></tr></thead><tbody>{component_rows}</tbody></table>
{''.join(lines)}
<p class="note">橙/緑/紫=dominant wave 1〜3、黒=combined wave。上段と下段は同じ日付軸です。</p></html>'''


def build_html_v2(machine: str, rows: list[dict], components: list[dict], daily: list[dict]) -> str:
    """Three-band view: OHLC, LONG, then MID/SHORT/COMBINED."""
    width, left, right = 1200, 90, 30
    plot_width = width - left - right
    top_height, long_height, lower_height, gap = 220, 180, 220, 32
    chart_height = 18 + top_height + gap + long_height + gap + lower_height + 24
    dates = [row["date"] for row in rows]
    prices = [row["low"] for row in rows] + [row["high"] for row in rows]
    p_lo, p_hi = min(prices), max(prices)
    role_index = {component["role"]: index + 1 for index, component in enumerate(components)}
    role_values = {role: [row[f"wave{role_index[role]}_value"] for row in daily] for role in ("LONG", "MID", "SHORT")}
    lower_values = role_values["MID"] + role_values["SHORT"] + [row["combined_wave"] for row in daily]
    lower_lo, lower_hi = min(lower_values), max(lower_values)
    long_lo, long_hi = min(role_values["LONG"]), max(role_values["LONG"])
    if lower_lo == lower_hi:
        lower_lo -= 1
        lower_hi += 1
    if long_lo == long_hi:
        long_lo -= 1
        long_hi += 1
    long_top = 18 + top_height + gap
    lower_top = long_top + long_height + gap

    def x(index: int) -> float:
        return left + plot_width * index / max(1, len(rows) - 1)

    def y(value: float, top: float, height: float, lo: float, hi: float) -> float:
        return top + height * (hi - value) / (hi - lo or 1)

    lines = [f'<svg viewBox="0 0 {width} {chart_height}" role="img" aria-label="台{machine} OHLCとLONG MID SHORT波形">']
    lines.append('<style>text{font-family:system-ui,sans-serif;fill:#263238;font-size:12px}.axis{stroke:#9aa7ad;stroke-width:1}.grid{stroke:#dfe5e8;stroke-width:1}.candle{stroke:#455a64;stroke-width:1}.bull{fill:#d32f2f}.bear{fill:#1976d2}.long{stroke:#c62828;stroke-width:4}.mid{stroke:#2e7d32;stroke-width:2}.short{stroke:#6a1b9a;stroke-width:2}.combined{stroke:#111827;stroke-width:2.5}.wave{fill:none}.bullmark{fill:#d32f2f;stroke:#fff;stroke-width:1}.nextmark{fill:#6a1b9a;font-size:16px;font-weight:600}</style>')
    for top, height, lo, hi in ((18, top_height, p_lo, p_hi), (long_top, long_height, long_lo, long_hi), (lower_top, lower_height, lower_lo, lower_hi)):
        for fraction in (0.0, 0.5, 1.0):
            yy = top + height * fraction
            lines.append(f'<line class="grid" x1="{left}" y1="{yy:.1f}" x2="{width-right}" y2="{yy:.1f}"/>')
    for index, row in enumerate(rows):
        xx = x(index)
        lines.append(f'<line class="axis" x1="{xx:.1f}" y1="18" x2="{xx:.1f}" y2="{18+chart_height}" opacity=".12"/>')
        price = lambda value: y(value, 18, top_height, p_lo, p_hi)
        candle_top, candle_bottom = price(max(row["open"], row["close"])), price(min(row["open"], row["close"]))
        lines.append(f'<line class="candle" x1="{xx:.1f}" y1="{price(row["high"]):.1f}" x2="{xx:.1f}" y2="{price(row["low"]):.1f}"/>')
        lines.append(f'<rect class="{"bull" if row["close"] > row["open"] else "bear"}" x="{xx-5:.1f}" y="{candle_top:.1f}" width="10" height="{max(1.5, candle_bottom-candle_top):.1f}"/>')
    lines.append(f'<path class="wave long" d="{svg_path(role_values["LONG"], left, long_top, plot_width, long_height, long_lo, long_hi)}"/>')
    lines.append(f'<path class="wave mid" d="{svg_path(role_values["MID"], left, lower_top, plot_width, lower_height, lower_lo, lower_hi)}"/>')
    lines.append(f'<path class="wave short" d="{svg_path(role_values["SHORT"], left, lower_top, plot_width, lower_height, lower_lo, lower_hi)}"/>')
    combined = [row["combined_wave"] for row in daily]
    lines.append(f'<path class="wave combined" d="{svg_path(combined, left, lower_top, plot_width, lower_height, lower_lo, lower_hi)}"/>')
    for index, row in enumerate(daily):
        yy = y(row["combined_wave"], lower_top, lower_height, lower_lo, lower_hi)
        if row["bullish"]:
            lines.append(f'<circle class="bullmark" cx="{x(index):.1f}" cy="{yy:.1f}" r="4"/>')
        if row["next_day_bullish"] is True:
            lines.append(f'<text class="nextmark" x="{x(index):.1f}" y="{yy-8:.1f}" text-anchor="middle">★</text>')
    for index in (0, len(rows)//2, len(rows)-1):
        lines.append(f'<text x="{x(index):.1f}" y="{chart_height-4}" text-anchor="middle">{html.escape(dates[index])}</text>')
    lines.extend([f'<text x="8" y="28">OHLC</text>', f'<text x="8" y="{long_top+14}">LONG</text>', f'<text x="8" y="{lower_top+14}">MID / SHORT / COMBINED</text>', '</svg>'])

    def rate(value: object) -> str:
        return "" if value == "" else f"{float(value):.1f}%"

    component_rows = "".join(
        f"<tr><td>{component['rank']}</td><td>{component['role']}</td><td>{component['period_days']:.2f}</td><td>{component['frequency']:.5f}</td><td>{component['amplitude']:.2f}</td><td>{component['phase']:.2f}°</td><td>{component['relative_power']*100:.2f}%</td></tr>"
        for component in sorted(components, key=lambda item: item["period_days"], reverse=True)
    )
    phase_rows = "".join(
        f"<tr><td>{item['role']}</td><td>{item['phase_bin']}°</td><td>{item['samples']}</td><td>{rate(item['next_day_bullish_rate'])}</td></tr>"
        for item in phase_nextday_stats(daily, components)
    )
    pattern_rows = "".join(
        f"<tr><td>{item['pattern']}</td><td>{item['samples']}</td><td>{item['next_day_bullish_count']}</td><td>{rate(item['next_day_bullish_rate'])}</td></tr>"
        for item in pattern_nextday_stats(daily)
    )
    legend = " / ".join(f"{component['role']}: {component['period_days']:.2f} days" for component in sorted(components, key=lambda item: item["period_days"], reverse=True))
    return f'''<!doctype html><html lang="ja"><meta charset="utf-8"><title>Wave Lab {machine}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:1250px;margin:24px auto;padding:0 16px;color:#263238}}table{{border-collapse:collapse;margin:12px 0 22px}}th,td{{border-bottom:1px solid #ddd;padding:5px 10px;text-align:right}}th{{text-align:left}}.note,.small{{color:#546e7a}}svg{{width:100%;height:auto}}.legend{{font-weight:600}}</style>
<h1>Wave Lab: 台{machine} FFT再構成</h1><p>対象期間: {dates[0]}〜{dates[-1]} / データ件数: {len(rows)} / Closeベース</p>
<p class="note">全期間FFTによる retrospective / exploratory analysis です。未来データを含むため予測性能ではありません。●=当日陽線、★=次回観測日の陽線。位相は0°=谷、90°=上昇、180°=山、270°=下降です。</p>
<p class="note">方向判定: 前後差分の符号が反転し、振幅の{DIRECTION_SLOPE_EPSILON_FRACTION:.0%}以上の傾きが両側にある場合のみ turning_up / turning_down とし、それ以外は rising / falling。統計用UP/DOWNでは turning_upをUP、turning_downをDOWNに含めます。</p>
<h2>周期対応</h2><p class="legend">{legend}</p><table><thead><tr><th>FFT rank</th><th>role</th><th>period days</th><th>frequency</th><th>amplitude</th><th>FFT phase</th><th>relative power</th></tr></thead><tbody>{component_rows}</tbody></table>
{''.join(lines)}
<p class="small">LONGは値や振幅を変更せず線幅のみ太く表示。MID/SHORT/COMBINEDは下段で同じ日付軸に重ねています。</p>
<h2>位相別 翌日陽線率</h2><p class="small">最終観測日は翌日データがないため除外。samplesを必ず併記しています。</p><table><thead><tr><th>role</th><th>phase</th><th>samples</th><th>next-day bullish rate</th></tr></thead><tbody>{phase_rows}</tbody></table>
<h2>3波方向パターン別 翌日陽線率</h2><table><thead><tr><th>pattern</th><th>samples</th><th>next-day bullish count</th><th>next-day bullish rate</th></tr></thead><tbody>{pattern_rows}</tbody></table>
</html>'''


def build_html_v3(machine: str, rows: list[dict], components: list[dict], daily: list[dict]) -> str:
    """Interactive research dashboard with a shared date cursor and phase space."""
    public_components = [
        {key: component[key] for key in (
            "rank", "role", "frequency", "period_days", "amplitude", "phase",
            "relative_power", "n_observations", "n_fft", "sampling_interval",
            "frequency_unit", "period_basis",
        )}
        for component in components
    ]
    payload = {
        "machine": machine,
        "rows": daily,
        "components": public_components,
        "phase_stats": phase_nextday_stats(daily, components),
        "pattern_stats": pattern_nextday_stats(daily),
    }
    page = r'''<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wave Lab __MACHINE__</title>
<style>
:root{color-scheme:dark;--bg:#07111d;--panel:#0e1d2d;--panel2:#12263a;--line:#29435c;--text:#e6edf5;--muted:#9db0c2;--long:#ff7b72;--mid:#55d187;--short:#c084fc;--combined:#f2f5f7;--cursor:#ffd166;--bull:#ff6b6b;--next:#67e8f9}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 20% 0,#102940 0,#07111d 48%);color:var(--text);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}main{max-width:1440px;margin:0 auto;padding:20px}.header{display:flex;justify-content:space-between;gap:20px;align-items:end;margin-bottom:14px}.header h1{margin:0;font-size:24px}.muted,.note{color:var(--muted)}.note{font-size:12px}.controls,.panel{background:linear-gradient(145deg,var(--panel),#0a1725);border:1px solid var(--line);border-radius:12px;box-shadow:0 12px 28px #0004}.controls{display:flex;align-items:center;gap:10px;padding:12px;margin-bottom:14px;flex-wrap:wrap}.controls input[type=range]{flex:1;min-width:240px;accent-color:var(--cursor)}button{background:var(--panel2);color:var(--text);border:1px solid var(--line);border-radius:7px;padding:6px 12px;cursor:pointer}button:hover,button:focus-visible{border-color:var(--cursor);outline:none}.selected{color:var(--cursor);font-weight:700}.summary{display:grid;grid-template-columns:repeat(7,minmax(110px,1fr));gap:8px;margin-bottom:14px}.metric{padding:9px;background:#0b1a2a;border:1px solid var(--line);border-radius:8px}.metric .label{display:block;color:var(--muted);font-size:11px}.metric .value{font-weight:600}.panel{padding:12px}.panel h2{font-size:16px;margin:0 0 8px}.chart-panel{margin-bottom:14px}.chart-panel svg{width:100%;height:auto;display:block}.bottom{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(380px,.85fr);gap:14px}.phase-panel svg{width:100%;height:auto;display:block}.info-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.info-grid section{min-width:0}.info-grid h3{font-size:13px;color:var(--muted);margin:4px 0}.table-wrap{overflow:auto;max-height:270px}table{border-collapse:collapse;width:100%;font-size:12px}th,td{border-bottom:1px solid var(--line);padding:5px 6px;text-align:right;white-space:nowrap}th{text-align:left;color:var(--muted)}td:first-child,th:first-child{text-align:left}.role-long{color:var(--long)}.role-mid{color:var(--mid)}.role-short{color:var(--short)}.legend{display:flex;gap:12px;flex-wrap:wrap;font-size:12px}.swatch{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:4px}.sw-long{background:var(--long)}.sw-mid{background:var(--mid)}.sw-short{background:var(--short)}.sw-combined{background:var(--combined)}.sw-cursor{background:var(--cursor)}.bull{color:var(--bull)}.next{color:var(--next)}.svg-label{fill:var(--muted);font-size:12px}.svg-axis{stroke:var(--line);stroke-width:1}.svg-cursor{stroke:var(--cursor);stroke-width:2;stroke-dasharray:5 4}.svg-candle{stroke:#b8c7d5;stroke-width:1}.svg-wave{fill:none}.svg-point{stroke:var(--cursor);stroke-width:2}.tiny{font-size:11px;color:var(--muted)}@media(max-width:900px){.summary{grid-template-columns:repeat(3,1fr)}.bottom{grid-template-columns:1fr}.info-grid{grid-template-columns:1fr}}@media(max-width:520px){main{padding:10px}.header{display:block}.summary{grid-template-columns:repeat(2,1fr)}.controls input[type=range]{min-width:160px}.info-grid{display:block}}
</style></head><body><main>
<div class="header"><div><h1>Wave Lab / 台__MACHINE__</h1><div class="muted">全42観測・Closeベース・観測間隔ベースのFFT研究画面</div></div><div class="note">retrospective / exploratory analysis<br>未来予測性能ではありません</div></div>
<div class="controls"><button id="prev" type="button">← 前へ</button><button id="next" type="button">次へ →</button><label for="dateSlider">観測日</label><input id="dateSlider" type="range" min="0" max="41" value="41"><span id="sliderLabel" class="selected"></span></div>
<div id="summary" class="summary"></div>
<section class="panel chart-panel"><h2>上段: OHLC / 中段: Fourier reconstructed waves</h2><div class="legend"><span><i class="swatch sw-long"></i>LONG</span><span><i class="swatch sw-mid"></i>MID</span><span><i class="swatch sw-short"></i>SHORT</span><span><i class="swatch sw-combined"></i>COMBINED</span><span><i class="swatch sw-cursor"></i>選択日カーソル</span><span class="bull">● 当日陽線</span><span class="next">★ 次回観測日陽線</span></div><svg id="mainChart" viewBox="0 0 1200 650" role="img" aria-label="OHLCとFFT再構成波"></svg></section>
<div class="bottom"><section class="panel phase-panel"><h2>下段左: 位相空間</h2><div class="tiny">0°=谷 / 90°=上昇 / 180°=山 / 270°=下降。薄い点は全観測日、明るい点は選択日。</div><svg id="phaseSpace" viewBox="0 0 600 390" role="img" aria-label="LONG MID SHORTの位相空間"></svg></section>
<section class="panel"><h2>下段右: 選択日と統計</h2><div id="info"></div></section></div>
<p class="note">周波数は cycles / observation、サンプリング間隔 d=1 observation。period = 1 / frequency。42観測をすべて使用し、n_fft=64へゼロパディングしています。休日・欠測がある場合、周期単位はcalendar daysではなくobservationsです。</p>
</main><script>
const DATA=__DATA__;
const rows=DATA.rows, components=DATA.components;
const roleIndex={}; components.forEach((c,i)=>roleIndex[c.role]=i+1);
const roles=['LONG','MID','SHORT'];
const colors={LONG:'var(--long)',MID:'var(--mid)',SHORT:'var(--short)',COMBINED:'var(--combined)'};
const slider=document.getElementById('dateSlider'), sliderLabel=document.getElementById('sliderLabel');
slider.max=String(rows.length-1); slider.value=String(rows.length-1);
function esc(value){return String(value??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));}
function extent(values){return [Math.min(...values),Math.max(...values)];}
function xAt(i,left=70,width=1080){return left+width*i/Math.max(1,rows.length-1);}
function pathFor(values,top,height,left,width,lo,hi){const span=hi-lo||1;return 'M '+values.map((v,i)=>`${xAt(i,left,width).toFixed(2)},${(top+height*(hi-v)/span).toFixed(2)}`).join(' L ');}
function series(role){const wave=roleIndex[role];return rows.map(r=>Number(r[`wave${wave}_value`]));}
function phase(role,index){const wave=roleIndex[role];return Number(rows[index][`wave${wave}_phase`]);}
function direction(role,index){const wave=roleIndex[role];return rows[index][`wave${wave}_direction`];}
function drawMain(index){
 const svg=document.getElementById('mainChart'), row=rows[index], left=70,width=1080, top=18,ohlcH=235,gap=28,waveTop=281,waveH=325;
 const prices=rows.flatMap(r=>[Number(r.low),Number(r.high)]), [plo,phi]=extent(prices);
 const waveSeries={LONG:series('LONG'),MID:series('MID'),SHORT:series('SHORT'),COMBINED:rows.map(r=>Number(r.combined_wave))};
 const waveValues=Object.values(waveSeries).flat(), [wlo,whi]=extent(waveValues); const sy=(v,t,h,lo,hi)=>t+h*(hi-v)/(hi-lo||1);
 let out='';
 for(const [t,h,lo,hi] of [[top,ohlcH,plo,phi],[waveTop,waveH,wlo,whi]]) for(const f of [0,.5,1]) out+=`<line class="svg-axis" x1="${left}" y1="${t+h*f}" x2="${left+width}" y2="${t+h*f}"/>`;
 for(let i=0;i<rows.length;i++){const xx=xAt(i),r=rows[i],py=v=>sy(Number(v),top,ohlcH,plo,phi);out+=`<line class="svg-axis" opacity=".22" x1="${xx}" y1="${top}" x2="${xx}" y2="${waveTop+waveH}"/>`;const y1=py(Math.max(r.open,r.close)),y2=py(Math.min(r.open,r.close));out+=`<line class="svg-candle" x1="${xx}" y1="${py(r.high)}" x2="${xx}" y2="${py(r.low)}"/><rect x="${xx-6}" y="${y1}" width="12" height="${Math.max(2,y2-y1)}" fill="${r.bullish?'var(--bull)':'#3478b9'}"/>`;}
 for(const role of roles){const vals=waveSeries[role];out+=`<path class="svg-wave" stroke="${colors[role]}" stroke-width="${role==='LONG'?4:2.2}" d="${pathFor(vals,waveTop,waveH,left,width,wlo,whi)}"/>`;}
 out+=`<path class="svg-wave" stroke="${colors.COMBINED}" stroke-width="3" d="${pathFor(waveSeries.COMBINED,waveTop,waveH,left,width,wlo,whi)}"/>`;
 const cursor=xAt(index);out+=`<line class="svg-cursor" x1="${cursor}" y1="${top}" x2="${cursor}" y2="${waveTop+waveH}"/>`;
 for(const role of [...roles,'COMBINED']){const val=waveSeries[role][index],yy=sy(val,waveTop,waveH,wlo,whi);out+=`<circle class="svg-point" cx="${cursor}" cy="${yy}" r="${role==='LONG'?6:5}" fill="${colors[role]}"/>`;}
 out+=`<text class="svg-label" x="8" y="30">OHLC</text><text class="svg-label" x="8" y="${waveTop+16}">waves / observation value</text>`;
 for(const i of [0,Math.floor((rows.length-1)/2),rows.length-1]) out+=`<text class="svg-label" text-anchor="middle" x="${xAt(i)}" y="${waveTop+waveH+22}">${esc(rows[i].date)}</text>`;
 svg.innerHTML=out;
}
function drawPhase(index){
 const svg=document.getElementById('phaseSpace'),cx=300,cy=190,r=112;let out=`<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--line)"/><circle cx="${cx}" cy="${cy}" r="${r*.5}" fill="none" stroke="var(--line)" opacity=".5"/><line class="svg-axis" x1="${cx-r-20}" y1="${cy}" x2="${cx+r+20}" y2="${cy}"/><line class="svg-axis" x1="${cx}" y1="${cy-r-20}" x2="${cx}" y2="${cy+r+20}"/>`;
 out+=`<text class="svg-label" x="${cx-10}" y="${cy-r-28}">180° 山</text><text class="svg-label" x="${cx-10}" y="${cy+r+35}">0° 谷</text><text class="svg-label" x="${cx+r+25}" y="${cy+4}">90° 上昇</text><text class="svg-label" x="${cx-r-76}" y="${cy+4}">270° 下降</text>`;
 for(const role of roles){const col=colors[role];for(let i=0;i<rows.length;i++){const a=phase(role,i)*Math.PI/180;const rr=i===index?r:r*.86;const px=cx+rr*Math.sin(a),py=cy+rr*Math.cos(a);out+=`<circle cx="${px}" cy="${py}" r="${i===index?8:3}" fill="${col}" opacity="${i===index?1:.28}"/>`; } const a=phase(role,index)*Math.PI/180;out+=`<text class="svg-label" fill="${col}" x="${cx+r*.65*Math.sin(a)}" y="${cy+r*.65*Math.cos(a)-8}">${role}</text>`;}
 svg.innerHTML=out;
}
function detail(index){
 const r=rows[index], bullish=r.bullish?'陽線':'陰線', next=r.next_day_bullish===null?'なし':(r.next_day_bullish?'陽線':'陰線');
 const cell=(label,value,cls='')=>`<div class="metric"><span class="label">${label}</span><span class="value ${cls}">${esc(value)}</span></div>`;
 document.getElementById('summary').innerHTML=cell('date',r.date,'selected')+cell('Open',r.open)+cell('High',r.high)+cell('Low',r.low)+cell('Close',r.close)+cell('当日',bullish,bullish==='陽線'?'bull':'')+cell('次回観測日',next,next==='陽線'?'next':'');
 sliderLabel.textContent=`${r.date} (${index+1}/${rows.length})`;
 const dirRows=roles.map(role=>{const w=roleIndex[role];return `<tr><td class="role-${role.toLowerCase()}">${role}</td><td>${Number(r[`wave${w}_phase`]).toFixed(1)}°</td><td>${esc(r[`wave${w}_direction`])}</td><td>${r[`wave${w}_up`]?'UP':'DOWN'}</td><td>${Number(r[`wave${w}_value`]).toFixed(1)}</td></tr>`}).join('');
 const compRows=components.map(c=>`<tr><td class="role-${c.role.toLowerCase()}">${c.role}</td><td>${c.rank}</td><td>${Number(c.frequency).toFixed(5)}</td><td>${Number(c.period_days).toFixed(3)}</td><td>${Number(c.amplitude).toFixed(1)}</td><td>${(Number(c.relative_power)*100).toFixed(1)}%</td></tr>`).join('');
 const phaseRows=DATA.phase_stats.map(s=>`<tr><td class="role-${s.role.toLowerCase()}">${s.role}</td><td>${s.phase_bin}°</td><td>${s.samples}</td><td>${s.next_day_bullish_rate===''?'—':Number(s.next_day_bullish_rate).toFixed(1)+'%'}</td></tr>`).join('');
 const patternRows=DATA.pattern_stats.map(s=>`<tr><td>${s.pattern}</td><td>${s.samples}</td><td>${s.next_day_bullish_count}</td><td>${s.next_day_bullish_rate===''?'—':Number(s.next_day_bullish_rate).toFixed(1)+'%'}</td></tr>`).join('');
 document.getElementById('info').innerHTML=`<div class="info-grid"><section><h3>現在選択日</h3><table><tbody>${dirRows}</tbody></table><p><b>pattern:</b> ${esc(r.wave_direction_pattern)}</p><p><b>combined:</b> ${Number(r.combined_wave).toFixed(1)}</p></section><section><h3>主要周期 / FFT</h3><div class="table-wrap"><table><thead><tr><th>role</th><th>rank</th><th>freq</th><th>period</th><th>amp</th><th>power</th></tr></thead><tbody>${compRows}</tbody></table></div></section><section><h3>位相別 翌日陽線率</h3><div class="table-wrap"><table><thead><tr><th>role</th><th>phase</th><th>n</th><th>rate</th></tr></thead><tbody>${phaseRows}</tbody></table></div></section><section><h3>方向パターン別 翌日陽線率</h3><div class="table-wrap"><table><thead><tr><th>pattern</th><th>n</th><th>bull</th><th>rate</th></tr></thead><tbody>${patternRows}</tbody></table></div></section></div>`;
}
function render(index){drawMain(index);drawPhase(index);detail(index);}
slider.addEventListener('input',()=>render(Number(slider.value)));document.getElementById('uiPrev').addEventListener('click',()=>{slider.value=String(Math.max(0,Number(slider.value)-1));render(Number(slider.value));});document.getElementById('uiNext').addEventListener('click',()=>{slider.value=String(Math.min(rows.length-1,Number(slider.value)+1));render(Number(slider.value));});render(Number(slider.value));
</script></body></html>'''
    extra_script = r'''<script>document.addEventListener('DOMContentLoaded',()=>{
const uiControls=document.querySelector('.controls');
document.querySelector('main').insertAdjacentHTML('beforeend','<p class="note">Faint future trails are display-only research context; this is not an as-of replay.</p>');
uiControls.innerHTML='<button id="uiPrev" type="button">Prev</button><button id="uiPlay" type="button">Play</button><button id="uiStop" type="button">Stop</button><button id="uiNext" type="button">Next</button><label><input id="uiLoop" type="checkbox"> Loop</label><label>Speed <select id="uiSpeed"><option value="500">Fast</option><option value="1000" selected>Normal (1 obs/sec)</option><option value="2000">Slow</option></select></label><label>OHLC <select id="uiOhlcMode"><option value="zero">0 BASE</option><option value="connect">CLOSE CONNECT</option></select></label><input id="uiSlider" type="range" min="0" max="41" value="41"><span id="uiSliderLabel" class="selected"></span>';
const uiSlider=document.getElementById('uiSlider'),uiSliderLabel=document.getElementById('uiSliderLabel'),uiSpeed=document.getElementById('uiSpeed'),uiMode=document.getElementById('uiOhlcMode'),uiLoop=document.getElementById('uiLoop');let uiIndex=rows.length-1,uiTimer=null;
// CLOSE CONNECT is display-only: each candle is rebased to the prior display close using raw OHLC deltas.
function uiDisplayRows(){let previousClose=0;return rows.map((r,i)=>{const raw={open:Number(r.open),high:Number(r.high),low:Number(r.low),close:Number(r.close)};if(uiMode.value==='zero'){previousClose=raw.close;return raw;}const base=i===0?0:previousClose;const display={open:base,high:base+raw.high-raw.open,low:base+raw.low-raw.open,close:base+raw.close-raw.open};previousClose=display.close;return display;});}
function uiDrawMain(){const svg=document.getElementById('mainChart'),display=uiDisplayRows(),left=70,width=1080,top=18,ohlcH=235,waveTop=281,waveH=325;const prices=display.flatMap(r=>[r.low,r.high]),[plo,phi]=ext(prices),series={LONG:vals('LONG'),MID:vals('MID'),SHORT:vals('SHORT'),COMBINED:rows.map(r=>Number(r.combined_wave))},[wlo,whi]=ext(Object.values(series).flat());let out='';for(const [t,h] of [[top,ohlcH],[waveTop,waveH]])for(const f of [0,.5,1])out+=`<line class="svg-axis" x1="${left}" y1="${t+h*f}" x2="${left+width}" y2="${t+h*f}"/>`;for(let i=0;i<display.length;i++){const xx=xAt(i),r=display[i],source=rows[i],py=v=>yScale(v,top,ohlcH,plo,phi),y1=py(Math.max(r.open,r.close)),y2=py(Math.min(r.open,r.close));out+=`<line class="svg-axis" opacity=".22" x1="${xx}" y1="${top}" x2="${xx}" y2="${waveTop+waveH}"/><line class="svg-candle" x1="${xx}" y1="${py(r.high)}" x2="${xx}" y2="${py(r.low)}"/><rect x="${xx-6}" y="${y1}" width="12" height="${Math.max(2,y2-y1)}" fill="${source.bullish?'var(--bull)':'#3478b9'}"/>`;}for(const role of roles)out+=`<path class="svg-wave" stroke="${colors[role]}" stroke-width="${role==='LONG'?4:2.2}" d="${pathFor(series[role],waveTop,waveH,left,width,wlo,whi)}"/>`;out+=`<path class="svg-wave" stroke="${colors.COMBINED}" stroke-width="3" d="${pathFor(series.COMBINED,waveTop,waveH,left,width,wlo,whi)}"/>`;const cursor=xAt(uiIndex);out+=`<line class="svg-cursor" x1="${cursor}" y1="${top}" x2="${cursor}" y2="${waveTop+waveH}"/>`;for(const role of [...roles,'COMBINED']){const yy=yScale(series[role][uiIndex],waveTop,waveH,wlo,whi);out+=`<circle class="svg-point" cx="${cursor}" cy="${yy}" r="${role==='LONG'?6:5}" fill="${colors[role]}"/>`;}out+=`<text class="svg-label" x="8" y="30">OHLC (${uiMode.value==='zero'?'0 BASE':'CLOSE CONNECT'})</text><text class="svg-label" x="8" y="${waveTop+16}">wave value / observation</text>`;for(const i of [0,Math.floor((rows.length-1)/2),rows.length-1])out+=`<text class="svg-label" text-anchor="middle" x="${xAt(i)}" y="${waveTop+waveH+22}">${esc(rows[i].date)}</text>`;svg.innerHTML=out;}
function uiPhasePoint(role,i){const component=components.find(c=>c.role===role),wave=roleIndex[role],amplitude=Number(component.amplitude)||1,base=78,scale=32,radius=base+scale*Number(rows[i][`wave${wave}_value`])/amplitude,a=Number(rows[i][`wave${wave}_phase`])*Math.PI/180;return [300+radius*Math.sin(a),190+radius*Math.cos(a)];}
function uiPhasePath(role,end){const points=[];for(let i=0;i<=end;i++){const p=uiPhasePoint(role,i);points.push(`${p[0].toFixed(2)},${p[1].toFixed(2)}`);}return points.length?'M '+points.join(' L '):'';}
function uiDrawPhase(){const svg=document.getElementById('phaseSpace'),cx=300,cy=190,r=116;let out=`<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--line)"/><circle cx="${cx}" cy="${cy}" r="78" fill="none" stroke="var(--line)" opacity=".5"/><line class="svg-axis" x1="${cx-r-20}" y1="${cy}" x2="${cx+r+20}" y2="${cy}"/><line class="svg-axis" x1="${cx}" y1="${cy-r-20}" x2="${cx}" y2="${cy+r+20}"/><text class="svg-label" x="${cx-22}" y="${cy-r-28}">180 crest</text><text class="svg-label" x="${cx-22}" y="${cy+r+35}">0 trough</text><text class="svg-label" x="${cx+r+25}" y="${cy+4}">90 rising</text><text class="svg-label" x="${cx-r-85}" y="${cy+4}">270 falling</text>`;for(const role of roles){const col=colors[role],full=uiPhasePath(role,rows.length-1),past=uiPhasePath(role,uiIndex);out+=`<path d="${full}" fill="none" stroke="${col}" stroke-width="2" opacity=".18"/><path d="${past}" fill="none" stroke="${col}" stroke-width="3" opacity=".9"/>`;const current=uiPhasePoint(role,uiIndex);out+=`<circle cx="${current[0]}" cy="${current[1]}" r="8" fill="${col}" stroke="var(--cursor)" stroke-width="2"/><text class="svg-label" x="${current[0]+8}" y="${current[1]-8}">${role}</text>`;}svg.innerHTML=out;}
function drawAsOfPhase(){const svg=document.getElementById('asofPhaseSpace');if(!svg)return;const row=asofRows[uiIndex],cx=300,cy=190,r=116;let out='<circle cx="'+cx+'" cy="'+cy+'" r="'+r+'" fill="none" stroke="var(--line)"/><line class="svg-axis" x1="'+(cx-r-20)+'" y1="'+cy+'" x2="'+(cx+r+20)+'" y2="'+cy+'"/><line class="svg-axis" x1="'+cx+'" y1="'+(cy-r-20)+'" x2="'+cx+'" y2="'+(cy+r+20)+'"/><text class="svg-label" x="278" y="45">180 crest</text><text class="svg-label" x="278" y="350">0 trough</text><text class="svg-label" x="438" y="194">90 rising</text><text class="svg-label" x="55" y="194">270 falling</text>';if(!row||row.status!=='VALID'){out+='<text class="svg-label" x="190" y="190">INSUFFICIENT_HISTORY</text>';svg.innerHTML=out;const summary=document.getElementById('asofPhaseSummary');if(summary)summary.textContent=row?row.date+' / '+row.status:'-';return;}const points=roles.map(role=>[Number(row[role.toLowerCase()+'_x']),Number(row[role.toLowerCase()+'_y'])]);out+='<polygon points="'+points.map(p=>p[0]+','+p[1]).join(' ')+'" fill="none" stroke="var(--cursor)" stroke-width="1.5" opacity=".85"/>';roles.forEach((role,i)=>{const p=points[i];out+='<circle cx="'+p[0]+'" cy="'+p[1]+'" r="8" fill="'+colors[role]+'" stroke="var(--cursor)" stroke-width="2"/><text class="svg-label" x="'+(p[0]+8)+'" y="'+(p[1]-8)+'">'+role+'</text>';});out+='<circle cx="'+Number(row.centroid_x)+'" cy="'+Number(row.centroid_y)+'" r="4" fill="var(--cursor)" stroke="var(--text)" stroke-width="1"/><text class="svg-label" x="18" y="24">AS-OF '+esc(row.date)+' / regime '+esc(row.regime)+' / TOP waves '+row.top_wave_count+' / '+esc(row.top_wave_pattern)+'</text>';svg.innerHTML=out;const summary=document.getElementById('asofPhaseSummary');if(summary)summary.textContent='score alignment='+(row.phase_alignment_score===''?'-':Number(row.phase_alignment_score).toFixed(3))+' / convergence='+(row.convergence_score===''?'-':Number(row.convergence_score).toFixed(3))+' / centroid='+row.centroid_region+' / y offset='+Number(row.centroid_y_offset).toFixed(2);}
function drawNfftComparison(){const box=document.getElementById('nfftComparison');if(!box)return;const stats=DATA.asof_nfft_stats||[],regime=DATA.asof_nfft_regime_stats||[],fmt=v=>v===''||v===undefined?'-':Number(v).toFixed(1);let html='<div class="table-wrap"><table><thead><tr><th>n_fft</th><th>TOP count</th><th>samples</th><th>bullish rate</th><th>next bullish rate</th></tr></thead><tbody>';for(const r of stats)html+='<tr><td>'+r.n_fft+'</td><td>'+r.top_wave_count+'</td><td>'+r.samples+'</td><td>'+fmt(r.bullish_rate)+'%</td><td>'+fmt(r.next_day_bullish_rate)+'%</td></tr>';html+='</tbody></table></div><h3>n_fft × regime × 2+ TOP</h3><div class="table-wrap"><table><thead><tr><th>n_fft</th><th>regime</th><th>samples</th><th>bullish rate</th><th>next bullish rate</th></tr></thead><tbody>';for(const r of regime)html+='<tr><td>'+r.n_fft+'</td><td>'+r.regime+'</td><td>'+r.samples+'</td><td>'+fmt(r.bullish_rate)+'%</td><td>'+fmt(r.next_day_bullish_rate)+'%</td></tr>';html+='</tbody></table></div><p class="tiny">The n_fft frame is a shared resolution setting. A boundary change can mix resolution change, component reselection, period/phase movement, and regime movement; it is not by itself a machine-period change.</p>';box.innerHTML=html;}
function drawRegimeHistory(){const svg=document.getElementById('periodRegimeChart');if(!svg||!regimeRows.length)return;const left=70,width=1080,top=18,periodH=155,jointTop=188,jointH=62,bandTop=278,bandH=28,xAtRegime=i=>left+width*i/Math.max(1,regimeRows.length-1);const valid=regimeRows.filter(r=>Number.isFinite(Number(r.long_period)));const allPeriods=valid.flatMap(r=>[Number(r.long_period),Number(r.mid_period),Number(r.short_period)]),lo=Math.min(...allPeriods),hi=Math.max(...allPeriods),sy=v=>top+periodH*(hi-v)/(hi-lo||1);const path=key=>{const pts=regimeRows.map((r,i)=>Number.isFinite(Number(r[key]))?`${xAtRegime(i).toFixed(2)},${sy(Number(r[key])).toFixed(2)}`:null).filter(Boolean);return pts.length?'M '+pts.join(' L '):'';};let out='<line class="svg-axis" x1="'+left+'" y1="'+top+'" x2="'+(left+width)+'" y2="'+top+'"/><line class="svg-axis" x1="'+left+'" y1="'+(top+periodH)+'" x2="'+(left+width)+'" y2="'+(top+periodH)+'"/>';for(const [key,label,color] of [['long_period','LONG','var(--long)'],['mid_period','MID','var(--mid)'],['short_period','SHORT','var(--short)']]){const d=path(key);if(d)out+='<path d="'+d+'" fill="none" stroke="'+color+'" stroke-width="'+(label==='LONG'?3:2)+'"/>';out+='<text class="svg-label" x="8" y="'+(top+20+(['LONG','MID','SHORT'].indexOf(label)*18))+'">'+label+'</text>';}out+='<text class="svg-label" x="8" y="'+(jointTop+18)+'">joint repeat</text>';const jointValues=regimeRows.filter(r=>Number.isFinite(Number(r.joint_repeat_period))).map(r=>Number(r.joint_repeat_period)),jlo=Math.min(...jointValues),jhi=Math.max(...jointValues),jy=v=>jointTop+jointH*(jhi-v)/(jhi-jlo||1);const jointPts=regimeRows.map((r,i)=>Number.isFinite(Number(r.joint_repeat_period))?`${xAtRegime(i).toFixed(2)},${jy(Number(r.joint_repeat_period)).toFixed(2)}`:null).filter(Boolean);if(jointPts.length)out+='<path d="M '+jointPts.join(' L ')+'" fill="none" stroke="var(--cursor)" stroke-width="3"/>';for(const r of regimeRows){const i=regimeRows.indexOf(r),fill=r.regime==='STABLE'?'var(--mid)':r.regime==='TRANSITION'?'var(--cursor)':r.regime==='UNSTABLE'?'var(--bull)':'var(--line)';out+='<rect x="'+(xAtRegime(i)-width/regimeRows.length/2)+'" y="'+bandTop+'" width="'+(width/regimeRows.length+1)+'" height="'+bandH+'" fill="'+fill+'" opacity=".6"/><text class="svg-label" x="'+xAtRegime(i)+'" y="'+(bandTop+19)+'" text-anchor="middle" font-size="10">'+(i===0||i===Math.floor((regimeRows.length-1)/2)||i===regimeRows.length-1?r.regime:'')+'</text>';if(r.n_fft_changed)out+='<line class="svg-cursor" x1="'+xAtRegime(i)+'" y1="'+top+'" x2="'+xAtRegime(i)+'" y2="'+(bandTop+bandH)+'"/>';}const cursor=xAtRegime(uiIndex);out+='<line class="svg-cursor" x1="'+cursor+'" y1="'+top+'" x2="'+cursor+'" y2="'+(bandTop+bandH)+'"/>';for(const i of [0,Math.floor((regimeRows.length-1)/2),regimeRows.length-1])out+='<text class="svg-label" text-anchor="middle" x="'+xAtRegime(i)+'" y="'+(bandTop+bandH+22)+'">'+esc(regimeRows[i].date)+'</text>';svg.innerHTML=out;const selected=regimeRows[uiIndex];document.getElementById('periodRegimeSummary').textContent=selected?'selected '+selected.date+' / regime='+selected.regime+' / n_fft='+(selected.n_fft||'-')+' / joint_repeat='+(selected.joint_repeat_period||'-')+' observations':'-';}
function uiRate(v){return v===''?'-':Number(v).toFixed(1)+'%';}
function uiDetail(){const r=rows[uiIndex],same=r.bullish?'BULLISH':'BEARISH',next=r.next_day_bullish===null?'N/A':(r.next_day_bullish?'BULLISH':'BEARISH'),cell=(l,v,c='')=>`<div class="metric"><span class="label">${l}</span><span class="value ${c}">${esc(v)}</span></div>`;document.getElementById('summary').innerHTML=cell('date',r.date,'selected')+cell('Open',r.open)+cell('High',r.high)+cell('Low',r.low)+cell('Close',r.close)+cell('same-day',same,same==='BULLISH'?'bull':'')+cell('next observation',next,next==='BULLISH'?'next':'');uiSliderLabel.textContent=`${r.date} (${uiIndex+1}/${rows.length})`;const dirs=roles.map(role=>{const w=roleIndex[role];return `<tr><td class="role-${role.toLowerCase()}">${role}</td><td>${Number(r[`wave${w}_phase`]).toFixed(1)} deg</td><td>${esc(r[`wave${w}_direction`])}</td><td>${r[`wave${w}_up`]?'UP':'DOWN'}</td><td>${Number(r[`wave${w}_value`]).toFixed(1)}</td></tr>`}).join('');const comps=components.map(c=>`<tr><td class="role-${c.role.toLowerCase()}">${c.role}</td><td>${c.rank}</td><td>${Number(c.frequency).toFixed(5)}</td><td>${Number(c.period_days).toFixed(3)}</td><td>${Number(c.amplitude).toFixed(1)}</td><td>${(Number(c.relative_power)*100).toFixed(1)}%</td></tr>`).join('');document.getElementById('info').innerHTML=`<div class="info-grid"><section><h3>Selected wave state</h3><table><thead><tr><th>role</th><th>phase</th><th>direction</th><th>UP/DOWN</th><th>value</th></tr></thead><tbody>${dirs}</tbody></table><p><b>pattern:</b> ${esc(r.wave_direction_pattern)}</p><p><b>combined:</b> ${Number(r.combined_wave).toFixed(1)}</p></section><section><h3>Components</h3><div class="table-wrap"><table><thead><tr><th>role</th><th>rank</th><th>freq</th><th>period</th><th>amp</th><th>power</th></tr></thead><tbody>${comps}</tbody></table></div></section></div>`;}
function uiDetail(){const r=rows[uiIndex],regime=regimeRows[uiIndex]||{},asof=asofRows[uiIndex]||{},same=r.bullish?'BULLISH':'BEARISH',next=r.next_day_bullish===null?'N/A':(r.next_day_bullish?'BULLISH':'BEARISH');uiSliderLabel.textContent=`${r.date} (${uiIndex+1}/${rows.length})`;document.getElementById('summary').innerHTML='<div class="metric"><span class="label">date</span><span class="value selected">'+esc(r.date)+'</span></div><div class="metric"><span class="label">OHLC</span><span class="value">'+[r.open,r.high,r.low,r.close].map(esc).join(' / ')+'</span></div><div class="metric"><span class="label">same-day</span><span class="value">'+same+'</span></div><div class="metric"><span class="label">next observation</span><span class="value">'+next+'</span></div>';const dirs=roles.map(role=>{const w=roleIndex[role];return '<tr><td>'+role+'</td><td>'+Number(r['wave'+w+'_phase']).toFixed(1)+' deg</td><td>'+esc(r['wave'+w+'_direction'])+'</td><td>'+((r['wave'+w+'_up'])?'UP':'DOWN')+'</td><td>'+Number(r['wave'+w+'_value']).toFixed(1)+'</td></tr>';}).join('');document.getElementById('info').innerHTML='<h3>Selected wave state</h3><table><thead><tr><th>role</th><th>phase</th><th>direction</th><th>UP/DOWN</th><th>value</th></tr></thead><tbody>'+dirs+'</tbody></table><p><b>pattern:</b> '+esc(r.wave_direction_pattern)+'</p><p><b>combined:</b> '+Number(r.combined_wave).toFixed(1)+'</p><h3>PERIOD REGIME HISTORY</h3><p>regime='+esc(regime.regime||'-')+' / n_fft='+esc(regime.n_fft||'-')+' / stability='+(regime.period_stability_score===''?'-':Number(regime.period_stability_score).toFixed(3))+'</p><p>LONG='+(regime.long_period?Number(regime.long_period).toFixed(3):'-')+' / MID='+(regime.mid_period?Number(regime.mid_period).toFixed(3):'-')+' / SHORT='+(regime.short_period?Number(regime.short_period).toFixed(3):'-')+'</p><p>component reorder='+(regime.component_reorder?'YES':'NO')+' / joint repeat='+(regime.joint_repeat_period||'-')+' obs / stable count='+(regime.joint_repeat_stable_count||'-')+'</p><p class="tiny">MIN history='+DATA.min_regime_observations+'; shift threshold='+Number(DATA.regime_shift_pct*100).toFixed(0)+'%.</p>'+(asof.status==='VALID'?'<h3>AS-OF PHASE SPACE</h3><p>regime='+esc(asof.regime)+' / n_fft='+esc(asof.n_fft)+' / TOP waves='+asof.top_wave_count+' / pattern='+esc(asof.top_wave_pattern)+'</p><p>alignment=' + Number(asof.phase_alignment_score).toFixed(3)+' / convergence='+Number(asof.convergence_score).toFixed(3)+' / centroid='+esc(asof.centroid_region)+' / y offset='+Number(asof.centroid_y_offset).toFixed(2)+'</p>':'<h3>AS-OF PHASE SPACE</h3><p>INSUFFICIENT_HISTORY</p>');}
function drawNextPrediction(){const svg=document.getElementById('nextPredictionSpace'),summary=document.getElementById('nextPredictionSummary');if(!svg)return;const row=nextPhaseRows[uiIndex],cx=300,cy=190,r=116;let out='<circle cx="'+cx+'" cy="'+cy+'" r="'+r+'" fill="none" stroke="var(--line)"/><line class="svg-axis" x1="'+(cx-r-20)+'" y1="'+cy+'" x2="'+(cx+r+20)+'" y2="'+cy+'"/><line class="svg-axis" x1="'+cx+'" y1="'+(cy-r-20)+'" x2="'+cx+'" y2="'+(cy+r+20)+'"/><text class="svg-label" x="278" y="45">180 crest</text><text class="svg-label" x="278" y="350">0 trough</text><text class="svg-label" x="438" y="194">90 rising</text><text class="svg-label" x="55" y="194">270 falling</text>';if(!row||row.status==='INSUFFICIENT_HISTORY'){out+='<text class="svg-label" x="170" y="190">INSUFFICIENT_HISTORY</text>';svg.innerHTML=out;if(summary)summary.textContent=row?row.source_date+' / '+row.status:'-';return;}const points=roles.map(role=>{const p=role.toLowerCase();return [Number(row[p+'_predicted_x']),Number(row[p+'_predicted_y'])];});out+='<polygon points="'+points.map(p=>p.join(',')).join(' ')+'" fill="none" stroke="var(--cursor)" stroke-width="2" stroke-dasharray="5 4"/>';roles.forEach((role,i)=>{const p=points[i];out+='<circle cx="'+p[0]+'" cy="'+p[1]+'" r="8" fill="none" stroke="'+colors[role]+'" stroke-width="3" stroke-dasharray="4 3"/><text class="svg-label" x="'+(p[0]+8)+'" y="'+(p[1]-8)+'">P '+role+'</text>';});const hasActual=row.long_actual_x!==undefined&&row.long_actual_x!=='';if(hasActual){const actual=roles.map(role=>{const p=role.toLowerCase();return [Number(row[p+'_actual_x']),Number(row[p+'_actual_y'])];});out+='<polygon points="'+actual.map(p=>p.join(',')).join(' ')+'" fill="none" stroke="var(--text)" stroke-width="1.5"/>';roles.forEach((role,i)=>{const p=actual[i];out+='<circle cx="'+p[0]+'" cy="'+p[1]+'" r="5" fill="'+colors[role]+'" stroke="var(--text)" stroke-width="1"/><text class="svg-label" x="'+(p[0]+6)+'" y="'+(p[1]+14)+'">A '+role+'</text>';});}out+='<text class="svg-label" x="18" y="24">PREDICTED D+1 (dashed) / ACTUAL AS-OF D+1 (solid)</text>';svg.innerHTML=out;if(summary){const fmt=v=>v===''||v===undefined?'-':Number(v).toFixed(1);summary.innerHTML='source='+esc(row.source_date)+' → target='+esc(row.target_date||'next observation')+' / regime='+esc(row.source_regime)+' / n_fft='+esc(row.source_n_fft)+' → '+esc(row.target_n_fft||'PENDING')+'<br>predicted TOP='+row.predicted_top_wave_count+' / '+esc(row.predicted_top_wave_pattern)+' / centroid='+esc(row.predicted_centroid_region)+(hasActual?' | actual TOP='+row.actual_top_wave_count+' / '+esc(row.actual_top_wave_pattern)+' / centroid='+esc(row.actual_centroid_region)+' / centroid error='+fmt(row.centroid_distance_error):' | actual comparison=PENDING')+'<br>LONG angle error='+fmt(row.long_angular_error_deg)+'° / MID='+fmt(row.mid_angular_error_deg)+'° / SHORT='+fmt(row.short_angular_error_deg)+'°';}}
const debugOutput=document.getElementById('debugOutput');
const pathPoints=id=>{const node=document.getElementById(id);const d=node?node.getAttribute('d')||'':'';return d?d.split(' L ').length:0;};
function assignPhaseIds(){const svg=document.getElementById('phaseSpace');if(!svg)return;const paths=Array.from(svg.querySelectorAll('path'));['long','mid','short'].forEach((role,i)=>{if(paths[i*2])paths[i*2].id=role+'-full-path';if(paths[i*2+1])paths[i*2+1].id=role+'-past-path';});const circles=Array.from(svg.querySelectorAll('circle')).slice(2,5);['long','mid','short'].forEach((role,i)=>{if(circles[i])circles[i].id=role+'-current';});const row=convergenceRows[uiIndex];if(row){const ns='http://www.w3.org/2000/svg';const triangle=document.createElementNS(ns,'polygon');triangle.id='full-phase-triangle';triangle.setAttribute('points',[[row.long_x,row.long_y],[row.mid_x,row.mid_y],[row.short_x,row.short_y]].map(p=>p.join(',')).join(' '));triangle.setAttribute('fill','none');triangle.setAttribute('stroke','var(--cursor)');triangle.setAttribute('stroke-width','1.5');svg.appendChild(triangle);const centroid=document.createElementNS(ns,'circle');centroid.id='full-phase-centroid';centroid.setAttribute('cx',row.centroid_x);centroid.setAttribute('cy',row.centroid_y);centroid.setAttribute('r','4');centroid.setAttribute('fill','var(--cursor)');svg.appendChild(centroid);}}
function assignAsOfIds(){const svg=document.getElementById('asofPhaseSpace');if(!svg)return;const row=asofRows[uiIndex];if(!row||row.status!=='VALID')return;const circles=Array.from(svg.querySelectorAll('circle'));const current=circles.slice(1,4),ids=['asof-long-current','asof-mid-current','asof-short-current'];ids.forEach((id,i)=>{if(current[i])current[i].id=id;});const polygon=svg.querySelector('polygon');if(polygon)polygon.id='asof-triangle';if(circles[4])circles[4].id='asof-centroid';}
function updateDebug(lastError){if(!debugOutput)return;const asof=asofRows[uiIndex]||{};const pass=rows.length>0&&pathPoints('long-full-path')>1&&pathPoints('mid-full-path')>1&&pathPoints('short-full-path')>1;debugOutput.textContent='JS initialized: YES | SELF TEST: '+(pass?'PASS':'FAIL')+' | rows='+rows.length+' | selectedIndex='+uiIndex+' | timer='+(uiTimer===null?'stopped':'running')+' | mode='+uiMode.value+' | LONG points='+pathPoints('long-full-path')+' | MID points='+pathPoints('mid-full-path')+' | SHORT points='+pathPoints('short-full-path')+' | As-of='+((asof.status)||'-')+' | last error='+(lastError||'none');}
function panelSafe(name,fn){try{fn();return '';}catch(error){console.error('Wave Lab '+name+' failed',error);return name+': '+(error&&error.message?error.message:String(error));}}
function updateView(index){uiIndex=Math.max(0,Math.min(rows.length-1,index));uiSlider.value=String(uiIndex);const errors=[panelSafe('OHLC/Fourier',uiDrawMain),panelSafe('Full Phase Space',()=>{uiDrawPhase();assignPhaseIds()}),panelSafe('Period Regime',drawRegimeHistory),panelSafe('As-of Phase Space',()=>{drawAsOfPhase();assignAsOfIds()}),panelSafe('Next Phase Prediction',drawNextPrediction),panelSafe('Info',uiDetail)].filter(Boolean);updateDebug(errors.join('; ')||'');}
function stopPlayback(){if(uiTimer!==null){clearInterval(uiTimer);uiTimer=null;}document.getElementById('uiPlay').textContent='Play';}
function advance(){if(uiIndex<rows.length-1){updateView(uiIndex+1);return true;}if(uiLoop.checked){updateView(0);return true;}stopPlayback();return false;}
function startPlayback(){if(uiTimer!==null)return;if(uiIndex>=rows.length-1&&!uiLoop.checked)return;document.getElementById('uiPlay').textContent='Playing';uiTimer=setInterval(advance,Number(uiSpeed.value));}
document.getElementById('uiPlay').addEventListener('click',startPlayback);document.getElementById('uiStop').addEventListener('click',stopPlayback);document.getElementById('uiPrev').addEventListener('click',()=>{stopPlayback();updateView(uiIndex-1);});document.getElementById('uiNext').addEventListener('click',()=>{stopPlayback();updateView(uiIndex+1);});uiSlider.addEventListener('input',()=>updateView(Number(uiSlider.value)));uiMode.addEventListener('change',()=>updateView(uiIndex));uiSpeed.addEventListener('change',()=>{if(uiTimer!==null){stopPlayback();startPlayback();}});updateView(uiIndex);
});
</script>'''
    return page.replace("__MACHINE__", machine).replace("__DATA__", json.dumps(payload, ensure_ascii=False, separators=(",", ":"))).replace("</script></body>", "</script>" + extra_script + "</body>")


def forward_validation_script() -> str:
    return """function drawForwardValidation(){
  const box=document.getElementById('forwardValidationSummary');
  const svg=document.getElementById('forwardValidationSpace');
  if(!box)return;
  const related=(Array.isArray(DATA.forward_validation)?DATA.forward_validation:[]).filter(item=>item.source_date===rows[uiIndex].date);
  const row=related.find(item=>!item.prediction_type);
  const dual=related.filter(item=>item.prediction_type);
  const cx=300,cy=190,r=116;
  const frame='<circle cx="'+cx+'" cy="'+cy+'" r="'+r+'" fill="none" stroke="var(--line)"/><line class="svg-axis" x1="'+(cx-r-20)+'" y1="'+cy+'" x2="'+(cx+r+20)+'" y2="'+cy+'"/><line class="svg-axis" x1="'+cx+'" y1="'+(cy-r-20)+'" x2="'+cx+'" y2="'+(cy+r+20)+'"/><text class="svg-label" x="278" y="45">180 crest</text><text class="svg-label" x="278" y="350">0 trough</text><text class="svg-label" x="438" y="194">90 rising</text><text class="svg-label" x="55" y="194">270 falling</text>';
  if(dual.length){
    const baseline=dual.find(item=>item.prediction_type==='BASELINE'),aware=dual.find(item=>item.prediction_type==='TRANSFORMATION_AWARE'),fmt=value=>value===''||value===undefined||value===null?'—':Number(value).toFixed(1),points=item=>roles.map(role=>[Number(item['predicted_'+role.toLowerCase()+'_x']),Number(item['predicted_'+role.toLowerCase()+'_y'])]),draw=(item,stroke,dash,label)=>{const pts=points(item);let s='<polygon points="'+pts.map(p=>p.join(',')).join(' ')+'" fill="none" stroke="'+stroke+'" stroke-width="2" stroke-dasharray="'+dash+'"/>';roles.forEach((role,i)=>{const p=pts[i];s+='<circle cx="'+p[0]+'" cy="'+p[1]+'" r="7" fill="none" stroke="'+colors[role]+'" stroke-width="3" stroke-dasharray="'+dash+'"/><text class="svg-label" x="'+(p[0]+7)+'" y="'+(p[1]-7)+'">'+label+' '+role+'</text>';});return s;};
    const actual=dual[0],actualPts=roles.map(role=>[Number(actual['actual_'+role.toLowerCase()+'_x']),Number(actual['actual_'+role.toLowerCase()+'_y'])]);let graphic=frame;graphic+=draw(baseline,'var(--cursor)','7 4','B');graphic+=draw(aware,'var(--next)','3 3','T');graphic+='<polygon points="'+actualPts.map(p=>p.join(',')).join(' ')+'" fill="none" stroke="var(--text)" stroke-width="2"/>';roles.forEach((role,i)=>{const p=actualPts[i];graphic+='<circle cx="'+p[0]+'" cy="'+p[1]+'" r="6" fill="'+colors[role]+'" stroke="var(--text)"/><text class="svg-label" x="'+(p[0]+7)+'" y="'+(p[1]+13)+'">A '+role+'</text>';});if(svg)svg.innerHTML=graphic;
    const actualType=actual.actual_transformation_transformation_type||'—',roleDetail=item=>roles.map(role=>{const k=role.toLowerCase();return role+' '+fmt(item[k+'_angular_error'])+'° / XY '+fmt(item[k+'_xy_distance'])+' / k '+esc(item['predicted_'+k+'_k'])+'→'+esc(item['actual_'+k+'_k'])+' '+(item[k+'_component_same_k']?'MATCH':'CHANGE');}).join(' ; ');box.innerHTML='<p><b>Source:</b> 2026-08-16 → <b>Target:</b> 2026-08-17 / <b>Commit:</b> 920cb3b / <b>Status:</b> FROZEN_BEFORE_ACTUAL</p><p><b>BASELINE:</b> phases '+roles.map(role=>fmt(baseline['predicted_'+role.toLowerCase()+'_phase'])+'°').join(' / ')+' / '+roleDetail(baseline)+' / TOP '+baseline.predicted_top_wave_count+'→'+baseline.actual_top_wave_count+' / '+esc(baseline.actual_top_wave_pattern)+' / centroid '+esc(baseline.actual_centroid_region)+' / centroid distance '+fmt(baseline.centroid_distance)+'</p><p><b>TRANSFORMATION-AWARE:</b> '+esc(aware.selected_transformation)+' / support '+esc(aware.support_samples)+' / probability '+fmt(Number(aware.transformation_probability)*100)+'% / phases '+roles.map(role=>fmt(aware['predicted_'+role.toLowerCase()+'_phase'])+'°').join(' / ')+' / '+roleDetail(aware)+' / TOP '+aware.predicted_top_wave_count+'→'+aware.actual_top_wave_count+' / '+esc(aware.actual_top_wave_pattern)+' / centroid '+esc(aware.actual_centroid_region)+' / centroid distance '+fmt(aware.centroid_distance)+'</p><p><b>Actual transformation:</b> '+esc(actualType)+' / best permutation '+esc(actual.actual_transformation_best_permutation||'—')+' / improvement '+fmt(Number(actual.actual_transformation_transformation_improvement)*100)+'% / <b>Target OHLC:</b> '+esc(actual.actual_open)+' / '+esc(actual.actual_high)+' / '+esc(actual.actual_low)+' / '+esc(actual.actual_close)+' / '+(actual.actual_bullish?'BULLISH':'BEARISH')+'</p>';return;
  }
  if(!row){if(svg)svg.innerHTML=frame+'<text class="svg-label" x="175" y="190">NO FROZEN FORWARD VALIDATION</text>';box.innerHTML='<span>選択日に登録されたFROZEN FORWARD VALIDATIONはありません。</span>';return;}
  const format=value=>value===''||value===undefined||value===null?'—':Number(value).toFixed(1);
  const yesNo=value=>value?'YES':'NO';
  const predicted=roles.map(role=>{const key=role.toLowerCase();return [Number(row['predicted_'+key+'_x']),Number(row['predicted_'+key+'_y'])];});
  const actual=roles.map(role=>{const key=role.toLowerCase();return [Number(row['actual_'+key+'_x']),Number(row['actual_'+key+'_y'])];});
  if(svg){let graphic=frame;
    graphic+='<polygon points="'+predicted.map(point=>point.join(',')).join(' ')+'" fill="none" stroke="var(--cursor)" stroke-width="2" stroke-dasharray="6 4"/><polygon points="'+actual.map(point=>point.join(',')).join(' ')+'" fill="none" stroke="var(--text)" stroke-width="2"/>';
    roles.forEach((role,i)=>{const color=colors[role],pp=predicted[i],ap=actual[i];graphic+='<circle cx="'+pp[0]+'" cy="'+pp[1]+'" r="8" fill="none" stroke="'+color+'" stroke-width="3" stroke-dasharray="4 3"/><text class="svg-label" x="'+(pp[0]+8)+'" y="'+(pp[1]-8)+'">P '+role+'</text><circle cx="'+ap[0]+'" cy="'+ap[1]+'" r="6" fill="'+color+'" stroke="var(--text)" stroke-width="1.5"/><text class="svg-label" x="'+(ap[0]+8)+'" y="'+(ap[1]+14)+'">A '+role+'</text>';});
    graphic+='<circle cx="'+Number(row.predicted_centroid_x)+'" cy="'+Number(row.predicted_centroid_y)+'" r="5" fill="none" stroke="var(--cursor)" stroke-width="2" stroke-dasharray="3 3"/><text class="svg-label" x="'+(Number(row.predicted_centroid_x)+7)+'" y="'+(Number(row.predicted_centroid_y)-7)+'">P centroid</text><circle cx="'+Number(row.actual_centroid_x)+'" cy="'+Number(row.actual_centroid_y)+'" r="4" fill="var(--text)" stroke="var(--text)"/><text class="svg-label" x="'+(Number(row.actual_centroid_x)+7)+'" y="'+(Number(row.actual_centroid_y)+14)+'">A centroid</text><text class="svg-label" x="18" y="24">PREDICTED D+1 (dashed) / ACTUAL AS-OF D+1 (solid)</text>';
    svg.innerHTML=graphic;}
  const roleRows=roles.map(role=>{
    const key=role.toLowerCase();
    return '<tr><td>'+role+'</td><td>'+format(row['predicted_'+key+'_phase'])+'°</td><td>'+format(row['actual_'+key+'_phase'])+'°</td><td>'+format(row[key+'_angular_error'])+'°</td><td>'+format(row[key+'_xy_distance'])+'</td><td>'+row['predicted_'+key+'_k']+' → '+row['actual_'+key+'_k']+' / '+yesNo(row[key+'_component_same_k'])+'</td></tr>';
  }).join('');
  box.innerHTML='<p><b>Source:</b> '+esc(row.source_date)+' → <b>Target:</b> '+esc(row.target_date)+' / <b>Prediction commit:</b> '+esc(row.prediction_commit)+' / <b>Status:</b> '+esc(row.prediction_status)+'</p><table><thead><tr><th>role</th><th>predicted phase</th><th>actual As-of phase</th><th>angular error</th><th>XY error</th><th>component k</th></tr></thead><tbody>'+roleRows+'</tbody></table><p><b>Geometry:</b> TOP count '+row.predicted_top_wave_count+' → '+row.actual_top_wave_count+' / MATCH='+yesNo(row.top_count_match)+'; pattern '+esc(row.predicted_top_wave_pattern)+' → '+esc(row.actual_top_wave_pattern)+' / MATCH='+yesNo(row.pattern_match)+'; centroid '+esc(row.predicted_centroid_region)+' → '+esc(row.actual_centroid_region)+' / MATCH='+yesNo(row.centroid_region_match)+'; distance='+format(row.centroid_distance)+'</p><p><b>Target OHLC (実測答え合わせラベル):</b> Open '+format(row.actual_open)+', High '+format(row.actual_high)+', Low '+format(row.actual_low)+', Close '+format(row.actual_close)+' / '+(row.actual_bullish?'BULLISH':'BEARISH')+'</p>';
}
"""


def transformation_script() -> str:
    return """function drawTransformation(){
  const box=document.getElementById('transformationSummary'),svg=document.getElementById('transformationSpace');
  if(!box||!svg)return;
  const row=(Array.isArray(transformationRows)?transformationRows:[]).find(item=>item.source_date===rows[uiIndex].date);
  const cx=300,cy=190,r=116;
  const frame='<circle cx="'+cx+'" cy="'+cy+'" r="'+r+'" fill="none" stroke="var(--line)"/><line class="svg-axis" x1="'+(cx-r-20)+'" y1="'+cy+'" x2="'+(cx+r+20)+'" y2="'+cy+'"/><line class="svg-axis" x1="'+cx+'" y1="'+(cy-r-20)+'" x2="'+cx+'" y2="'+(cy+r+20)+'"/><text class="svg-label" x="278" y="45">180 crest</text><text class="svg-label" x="278" y="350">0 trough</text><text class="svg-label" x="438" y="194">90 rising</text><text class="svg-label" x="55" y="194">270 falling</text>';
  if(!row){svg.innerHTML=frame+'<text class="svg-label" x="170" y="190">NO HISTORICAL TRANSFORMATION</text>';box.textContent='選択日には有効なhistorical 1-observation comparisonがありません。';return;}
  const points=(prefix,suffix='')=>roles.map(role=>{const key=role.toLowerCase();return [Number(row[prefix+key+suffix+'_x']),Number(row[prefix+key+suffix+'_y'])];});
  const original=points('predicted_'),transformed=points('transformed_'),actual=points('actual_');
  let out=frame;
  out+='<polygon points="'+original.map(p=>p.join(',')).join(' ')+'" fill="none" stroke="var(--cursor)" stroke-width="1.8" stroke-dasharray="6 4"/>';
  out+='<polygon points="'+transformed.map(p=>p.join(',')).join(' ')+'" fill="none" stroke="#f0b35b" stroke-width="2" stroke-dasharray="2 3"/>';
  out+='<polygon points="'+actual.map(p=>p.join(',')).join(' ')+'" fill="none" stroke="var(--text)" stroke-width="2"/>';
  roles.forEach((role,i)=>{const color=colors[role],p=original[i],t=transformed[i],a=actual[i];out+='<circle cx="'+p[0]+'" cy="'+p[1]+'" r="7" fill="none" stroke="'+color+'" stroke-dasharray="4 3"/><text class="svg-label" x="'+(p[0]+7)+'" y="'+(p[1]-7)+'">P '+role+'</text><circle cx="'+t[0]+'" cy="'+t[1]+'" r="7" fill="none" stroke="#f0b35b" stroke-dasharray="2 3"/><text class="svg-label" x="'+(t[0]+7)+'" y="'+(t[1]+9)+'">T '+role+'</text><circle cx="'+a[0]+'" cy="'+a[1]+'" r="5" fill="'+color+'" stroke="var(--text)"/><text class="svg-label" x="'+(a[0]+7)+'" y="'+(a[1]+15)+'">A '+role+'</text>';});
  out+='<text class="svg-label" x="18" y="24">P original / T best transformation / A actual</text>';svg.innerHTML=out;
  const f=v=>v===''||v===undefined||v===null?'—':Number(v).toFixed(2);
  box.innerHTML='<p><b>Source:</b> '+esc(row.source_date)+' → <b>Target:</b> '+esc(row.target_date)+' / <b>type:</b> '+esc(row.transformation_type)+' / <b>best permutation:</b> '+esc(row.best_permutation)+'</p><p><b>rotation:</b> '+f(row.best_rotation_deg)+'° / <b>identity distance:</b> '+f(row.identity_distance)+' / <b>transformed distance:</b> '+f(row.best_transformation_distance)+' / <b>improvement:</b> '+f(Number(row.transformation_improvement)*100)+'%</p><p><b>centroid distance:</b> '+f(row.centroid_distance)+' / <b>shape similarity:</b> '+f(Number(row.shape_similarity)*100)+'% / <b>confidence:</b> '+f(Number(row.classification_confidence)*100)+'%</p>';
}
"""


def frozen_prediction_script() -> str:
    return """function drawFrozenPrediction(){
  const box=document.getElementById('frozenPredictionSummary'),svg=document.getElementById('frozenPredictionSpace');
  if(!box||!svg)return;
  const rows2=Array.isArray(frozenPredictionRows)?frozenPredictionRows:[],baseline=rows2.find(row=>row.prediction_type==='BASELINE'),aware=rows2.find(row=>row.prediction_type==='TRANSFORMATION_AWARE');
  if(!baseline||!aware){box.textContent='FROZEN prediction payload unavailable';return;}
  const cx=300,cy=190,r=116,frame='<circle cx="'+cx+'" cy="'+cy+'" r="'+r+'" fill="none" stroke="var(--line)"/><line class="svg-axis" x1="'+(cx-r-20)+'" y1="'+cy+'" x2="'+(cx+r+20)+'" y2="'+cy+'"/><line class="svg-axis" x1="'+cx+'" y1="'+(cy-r-20)+'" x2="'+cx+'" y2="'+(cy+r+20)+'"/><text class="svg-label" x="278" y="45">180 crest</text><text class="svg-label" x="278" y="350">0 trough</text><text class="svg-label" x="438" y="194">90 rising</text><text class="svg-label" x="55" y="194">270 falling</text>';
  const pts=row=>roles.map(role=>[Number(row[role.toLowerCase()+'_x']),Number(row[role.toLowerCase()+'_y'])]);const bp=pts(baseline),tp=pts(aware);let out=frame+'<polygon points="'+bp.map(p=>p.join(',')).join(' ')+'" fill="none" stroke="var(--cursor)" stroke-width="2" stroke-dasharray="6 4"/><polygon points="'+tp.map(p=>p.join(',')).join(' ')+'" fill="none" stroke="#f0b35b" stroke-width="2" stroke-dasharray="2 3"/>';
  roles.forEach((role,i)=>{const color=colors[role];out+='<circle cx="'+bp[i][0]+'" cy="'+bp[i][1]+'" r="7" fill="none" stroke="'+color+'" stroke-dasharray="4 3"/><text class="svg-label" x="'+(bp[i][0]+7)+'" y="'+(bp[i][1]-7)+'">B '+role+'</text><circle cx="'+tp[i][0]+'" cy="'+tp[i][1]+'" r="7" fill="none" stroke="#f0b35b" stroke-dasharray="2 3"/><text class="svg-label" x="'+(tp[i][0]+7)+'" y="'+(tp[i][1]+12)+'">T '+role+'</text>';});out+='<text class="svg-label" x="18" y="24">B BASELINE / T TRANSFORMATION-AWARE</text>';svg.innerHTML=out;
  box.innerHTML='<p><b>Source:</b> '+esc(baseline.source_date)+' → <b>Target:</b> '+esc(baseline.target_date)+' / <b>Status:</b> '+esc(baseline.prediction_status)+'</p><p><b>BASELINE:</b> phases '+roles.map(role=>Number(baseline[role.toLowerCase()+'_phase']).toFixed(1)+'°').join(' / ')+' / TOP '+baseline.predicted_top_wave_count+' / '+esc(baseline.predicted_top_wave_pattern)+' / '+esc(baseline.predicted_centroid_region)+'</p><p><b>TRANSFORMATION-AWARE:</b> '+esc(aware.selected_transformation)+' / support '+esc(aware.support_samples)+' / probability '+(Number(aware.transformation_probability)*100).toFixed(1)+'% / '+esc(aware.support_status||'OK')+' / phases '+roles.map(role=>Number(aware[role.toLowerCase()+'_phase']).toFixed(1)+'°').join(' / ')+' / TOP '+aware.predicted_top_wave_count+' / '+esc(aware.predicted_top_wave_pattern)+' / '+esc(aware.predicted_centroid_region)+'</p>';
}
"""


def transformation_probability_script() -> str:
    return """function drawNextTransformationProbability(){
  const box=document.getElementById('nextTransformationProbabilitySummary');
  if(!box)return;
  const row=DATA.next_transformation_probability_frozen;
  if(!row){box.textContent='Transformation probability payload unavailable';return;}
  const pct=row.transform_probability===''?'—':(Number(row.transform_probability)*100).toFixed(1)+'%';
  const conditional=row.conditional_type_probability===''?'—':(Number(row.conditional_type_probability)*100).toFixed(1)+'%';
  const width=row.transform_probability===''?0:Math.max(0,Math.min(100,Number(row.transform_probability)*100));
  box.innerHTML='<p><b>Source:</b> '+esc(row.source_date)+' → <b>Target:</b> '+esc(row.target_date)+' / <b>Status:</b> '+esc(row.status)+'</p><div style="height:10px;background:var(--line);border-radius:6px;overflow:hidden"><div style="height:100%;width:'+width+'%;background:var(--next)"></div></div><p><b>Transformation probability:</b> '+pct+' / <b>Confidence:</b> '+esc(row.confidence)+'</p><p><b>Support:</b> '+esc(row.support_samples)+' samples / transform '+esc(row.transform_samples)+' / non-transform '+esc(row.non_transform_samples)+' / <b>Level:</b> '+esc(row.selected_support_level)+'</p><p><b>Most likely type:</b> '+esc(row.most_likely_transform_type||'—')+' / <b>conditional probability:</b> '+conditional+'</p><p class="tiny">'+esc(row.selection_basis)+'; geometry threshold='+Number(row.geometry_distance_threshold).toFixed(2)+'. This is P(Transformation on next observation), not a phase or bullish/bearish prediction.</p>';
}
"""


def frozen_0817_prediction_script() -> str:
    return """function drawFrozenPrediction1818(){
  const box=document.getElementById('frozenPrediction1818Summary'),svg=document.getElementById('frozenPrediction1818Space');
  if(!box||!svg)return;
  const records=Array.isArray(DATA.frozen_prediction_0817_rows)?DATA.frozen_prediction_0817_rows:[],baseline=records.find(row=>row.prediction_type==='BASELINE'),aware=records.find(row=>row.prediction_type==='TRANSFORMATION_AWARE');
  if(!baseline||!aware){box.textContent='2026-08-17 → 2026-08-18 frozen prediction payload unavailable';return;}
  const cx=300,cy=190,r=116,frame='<circle cx="'+cx+'" cy="'+cy+'" r="'+r+'" fill="none" stroke="var(--line)"/><line class="svg-axis" x1="'+(cx-r-20)+'" y1="'+cy+'" x2="'+(cx+r+20)+'" y2="'+cy+'"/><line class="svg-axis" x1="'+cx+'" y1="'+(cy-r-20)+'" x2="'+cx+'" y2="'+(cy+r+20)+'"/><text class="svg-label" x="278" y="45">180 crest</text><text class="svg-label" x="278" y="350">0 trough</text><text class="svg-label" x="438" y="194">90 rising</text><text class="svg-label" x="55" y="194">270 falling</text>';
  const points=(row,prefix)=>roles.map(role=>[Number(row[prefix+'_'+role.toLowerCase()+'_x']),Number(row[prefix+'_'+role.toLowerCase()+'_y'])]);
  const bp=points(baseline,'baseline'),ap=points(aware,'aware');let out=frame+'<polygon points="'+bp.map(p=>p.join(',')).join(' ')+'" fill="none" stroke="var(--cursor)" stroke-width="2" stroke-dasharray="6 4"/><polygon points="'+ap.map(p=>p.join(',')).join(' ')+'" fill="none" stroke="var(--next)" stroke-width="2" stroke-dasharray="2 3"/>';
  roles.forEach((role,i)=>{const color=colors[role],b=bp[i],a=ap[i];out+='<circle cx="'+b[0]+'" cy="'+b[1]+'" r="7" fill="none" stroke="'+color+'" stroke-width="3" stroke-dasharray="4 3"/><text class="svg-label" x="'+(b[0]+7)+'" y="'+(b[1]-7)+'">B '+role+'</text><circle cx="'+a[0]+'" cy="'+a[1]+'" r="7" fill="none" stroke="'+color+'" stroke-width="2" stroke-dasharray="2 3"/><text class="svg-label" x="'+(a[0]+7)+'" y="'+(a[1]+13)+'">T '+role+'</text>';});out+='<text class="svg-label" x="18" y="24">B BASELINE / T TRANSFORMATION-AWARE / NO ACTUAL</text>';svg.innerHTML=out;
  const pct=Number(baseline.transform_probability)*100, f=v=>v===''||v===undefined||v===null?'—':Number(v).toFixed(1), summary=row=>roles.map(role=>f(row['aware_'+role.toLowerCase()+'_phase'])+'°').join(' / ');
  box.innerHTML='<p><b>Source:</b> 2026-08-17 → <b>Target:</b> 2026-08-18 / <b>Status:</b> '+esc(baseline.prediction_status)+'</p><p><b>BASELINE:</b> phases '+roles.map(role=>f(baseline['baseline_'+role.toLowerCase()+'_phase'])+'°').join(' / ')+' / TOP '+baseline.baseline_top_wave_count+' / '+esc(baseline.baseline_top_wave_pattern)+' / '+esc(baseline.baseline_centroid_region)+'</p><p><b>TRANSFORMATION PROBABILITY:</b> '+f(pct)+'% / '+esc(baseline.probability_confidence)+' / '+esc(baseline.belt_state)+' / support '+esc(baseline.probability_support_samples)+' / most likely '+esc(baseline.most_likely_type||'—')+' / conditional '+f(Number(baseline.conditional_probability)*100)+'%</p><p><b>TRANSFORMATION-AWARE:</b> '+esc(aware.selected_transformation_type)+' / '+esc(aware.selected_permutation||'—')+' / applied '+esc(String(aware.transform_applied))+' / support '+esc(aware.selection_support_samples)+' / phases '+summary(aware)+' / TOP '+aware.aware_top_wave_count+' / '+esc(aware.aware_top_wave_pattern)+' / '+esc(aware.aware_centroid_region)+'</p><p class="tiny">No 2026-08-18 actual data is displayed or used. Transformation probability is not bullish/bearish probability.</p>';
}
"""


def build_html_v4(machine: str, rows: list[dict], components: list[dict], daily: list[dict], forward_validation: dict | None = None, frozen_next_phase_rows: list[dict] | None = None, transformation_rows: list[dict] | None = None, frozen_prediction_rows: list[dict] | None = None, forward_validation_extra: list[dict] | None = None, next_transformation_probability: dict | None = None, frozen_0817_rows: list[dict] | None = None) -> str:
    """ASCII-safe interactive dashboard; data labels remain unambiguous in any locale."""
    public_components = [
        {key: component[key] for key in (
            "rank", "role", "frequency", "period_days", "amplitude", "phase",
            "relative_power", "n_observations", "n_fft", "sampling_interval",
            "frequency_unit", "period_basis",
        )}
        for component in components
    ]
    convergence_rows, convergence_threshold = phase_convergence_analysis(daily, components)
    alignment_rows, alignment_threshold = phase_alignment_analysis(convergence_rows)
    single_repeat_periods, joint_repeat_period = repeat_periods(components)
    alignment_rows = add_repeat_metadata(alignment_rows, joint_repeat_period)
    regime_rows, regime_events = period_regime_history(daily, convergence_rows, alignment_rows)
    validate_period_regime_history(regime_rows, REGIME_CUTOFF_DATE)
    position_rows = phase_position_rows(daily, convergence_rows)
    validate_phase_position_rows(position_rows, convergence_rows)
    asof_rows = asof_phase_space_history(daily, regime_rows)
    validate_asof_phase_space(asof_rows, REGIME_CUTOFF_DATE)
    next_phase_rows = frozen_next_phase_rows if frozen_next_phase_rows is not None else asof_next_phase_predictions(daily, asof_rows, regime_rows)
    payload = {"machine": machine, "rows": daily, "components": public_components,
               "phase_stats": phase_nextday_stats(daily, components),
               "pattern_stats": pattern_nextday_stats(daily),
               "convergence_rows": convergence_rows,
               "convergence_threshold": convergence_threshold,
               "convergence_stats": phase_convergence_stats(convergence_rows),
               "convergence_region_stats": phase_convergence_region_stats(convergence_rows),
               "alignment_rows": alignment_rows,
               "alignment_threshold": alignment_threshold,
               "alignment_stats": phase_alignment_stats(alignment_rows),
               "alignment_region_stats": phase_alignment_region_stats(alignment_rows),
               "single_repeat_periods": single_repeat_periods,
               "joint_repeat_period": joint_repeat_period,
               "regime_rows": regime_rows,
               "regime_events": regime_events,
               "regime_stats": period_regime_stats(regime_rows),
               "phase_position_rows": position_rows,
               "phase_position_stats": phase_position_stats(position_rows),
               "phase_position_pattern_stats": phase_position_pattern_stats(position_rows),
               "asof_phase_rows": asof_rows,
               "asof_phase_stats": phase_position_stats([row for row in asof_rows if row["status"] == "VALID"]),
               "asof_phase_pattern_stats": phase_position_pattern_stats([row for row in asof_rows if row["status"] == "VALID"]),
               "asof_phase_regime_stats": asof_phase_regime_stats(asof_rows),
               "asof_phase_region_stats": asof_phase_region_stats(asof_rows),
               "asof_nfft_stats": asof_nfft_stats(asof_rows),
               "asof_nfft_pattern_stats": asof_nfft_pattern_stats(asof_rows),
               "asof_nfft_regime_stats": asof_nfft_regime_stats(asof_rows),
               "asof_nfft_transition_detail": asof_nfft_transition_detail(asof_rows, regime_rows),
               "next_phase_rows": next_phase_rows,
               "forward_validation": ([forward_validation] if isinstance(forward_validation, dict) else (forward_validation or [])) + (forward_validation_extra or []),
               "transformation_rows": transformation_rows or [],
               "frozen_prediction_rows": frozen_prediction_rows or [],
               "next_phase_stats": next_phase_prediction_stats(next_phase_rows),
               "next_phase_regime_stats": next_phase_prediction_group_stats(next_phase_rows, "source_regime", ("STABLE", "TRANSITION", "UNSTABLE")),
               "next_phase_nfft_stats": next_phase_prediction_group_stats(next_phase_rows, "source_n_fft", (32, 64)),
               "next_transformation_probability_frozen": next_transformation_probability,
               "frozen_prediction_0817_rows": frozen_0817_rows or [],
               "asof_threshold_min_samples": ASOF_THRESHOLD_MIN_SAMPLES,
               "regime_cutoff_date": REGIME_CUTOFF_DATE,
               "min_regime_observations": MIN_REGIME_OBSERVATIONS,
               "regime_reference_change": REGIME_REFERENCE_CHANGE,
               "regime_shift_pct": REGIME_SHIFT_PCT}
    page = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wave Lab __MACHINE__</title>
<style>
:root{color-scheme:dark;--bg:#07111d;--panel:#0e1d2d;--panel2:#12263a;--line:#29435c;--text:#e6edf5;--muted:#9db0c2;--long:#ff7b72;--mid:#55d187;--short:#c084fc;--combined:#f2f5f7;--cursor:#ffd166;--bull:#ff6b6b;--next:#67e8f9}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 20% 0,#102940 0,#07111d 48%);color:var(--text);font:14px/1.45 system-ui,sans-serif}main{max-width:1440px;margin:auto;padding:20px}.header{display:flex;justify-content:space-between;gap:20px;align-items:end;margin-bottom:14px}.header h1{margin:0;font-size:24px}.muted,.note{color:var(--muted)}.note{font-size:12px}.controls,.panel{background:linear-gradient(145deg,var(--panel),#0a1725);border:1px solid var(--line);border-radius:12px;box-shadow:0 12px 28px #0004}.controls{display:flex;align-items:center;gap:10px;padding:12px;margin-bottom:14px;flex-wrap:wrap}.controls input[type=range]{flex:1;min-width:240px;accent-color:var(--cursor)}button{background:var(--panel2);color:var(--text);border:1px solid var(--line);border-radius:7px;padding:6px 12px;cursor:pointer}button:hover,button:focus-visible{border-color:var(--cursor);outline:none}.selected{color:var(--cursor);font-weight:700}.summary{display:grid;grid-template-columns:repeat(7,minmax(110px,1fr));gap:8px;margin-bottom:14px}.metric{padding:9px;background:#0b1a2a;border:1px solid var(--line);border-radius:8px}.metric .label{display:block;color:var(--muted);font-size:11px}.metric .value{font-weight:600}.panel{padding:12px}.panel h2{font-size:16px;margin:0 0 8px}.chart-panel{margin-bottom:14px}.chart-panel svg,.phase-panel svg{width:100%;height:auto;display:block}.bottom{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(380px,.85fr);gap:14px}.info-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.info-grid h3{font-size:13px;color:var(--muted);margin:4px 0}.table-wrap{overflow:auto;max-height:270px}table{border-collapse:collapse;width:100%;font-size:12px}th,td{border-bottom:1px solid var(--line);padding:5px 6px;text-align:right;white-space:nowrap}th,td:first-child{text-align:left}th{color:var(--muted)}.role-long{color:var(--long)}.role-mid{color:var(--mid)}.role-short{color:var(--short)}.legend{display:flex;gap:12px;flex-wrap:wrap;font-size:12px}.swatch{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:4px}.sw-long{background:var(--long)}.sw-mid{background:var(--mid)}.sw-short{background:var(--short)}.sw-combined{background:var(--combined)}.sw-cursor{background:var(--cursor)}.bull{color:var(--bull)}.next{color:var(--next)}.tiny{font-size:11px;color:var(--muted)}.svg-label{fill:var(--muted);font-size:12px}.svg-axis{stroke:var(--line);stroke-width:1}.svg-cursor{stroke:var(--cursor);stroke-width:2;stroke-dasharray:5 4}.svg-candle{stroke:#b8c7d5;stroke-width:1}.svg-wave{fill:none}.svg-point{stroke:var(--cursor);stroke-width:2}@media(max-width:900px){.summary{grid-template-columns:repeat(3,1fr)}.bottom{grid-template-columns:1fr}.info-grid{grid-template-columns:1fr}}@media(max-width:520px){main{padding:10px}.header{display:block}.summary{grid-template-columns:repeat(2,1fr)}.controls input[type=range]{min-width:160px}.info-grid{display:block}}
</style></head><body><main>
<div class="header"><div><h1>Wave Lab / Machine __MACHINE__</h1><div class="muted">as-of cutoff __CUTOFF__ / Close / observation-based Fourier research</div></div><div class="note">retrospective / exploratory analysis<br>not predictive performance</div></div>
<div class="controls" id="waveLabControls"><button id="uiPrev" type="button">Prev</button><button id="uiPlay" type="button">Play</button><button id="uiStop" type="button">Stop</button><button id="uiNext" type="button">Next</button><label><input id="uiLoop" type="checkbox"> Loop</label><label>Speed <select id="uiSpeed"><option value="500">Fast</option><option value="1000" selected>Normal</option><option value="2000">Slow</option></select></label><label>OHLC <select id="uiOhlcMode"><option value="zero">0 BASE</option><option value="connect">CLOSE CONNECT</option></select></label><label for="uiSlider">Observation</label><input id="uiSlider" type="range" min="0" max="__ROW_MAX__" value="__ROW_MAX__"><span id="uiSliderLabel" class="selected"></span></div>
<div id="summary" class="summary"></div>
<section class="panel chart-panel"><h2>OHLC and Fourier reconstructed waves</h2><div class="legend"><span><i class="swatch sw-long"></i>LONG</span><span><i class="swatch sw-mid"></i>MID</span><span><i class="swatch sw-short"></i>SHORT</span><span><i class="swatch sw-combined"></i>COMBINED</span><span><i class="swatch sw-cursor"></i>selected date</span><span class="bull">● same-day bullish</span><span class="next">&#9733; next-observation bullish</span></div><svg id="mainChart" viewBox="0 0 1200 650" role="img" aria-label="OHLC and reconstructed waves"></svg></section>
<div class="bottom"><section class="panel phase-panel"><h2>Phase space</h2><div class="tiny">0 deg = trough / 90 deg = rising / 180 deg = crest / 270 deg = falling. Faint points are all observations; bright points are selected.</div><svg id="phaseSpace" viewBox="0 0 600 390" role="img" aria-label="LONG MID SHORT phase space"></svg></section><section class="panel"><h2>Selected date and statistics</h2><div id="info"></div></section></div>
<p class="note">Frequency = cycles / observation, d = 1 observation, period = 1 / frequency. The full FFT uses all observations through the cutoff; n_fft is the next power of two and may change in as-of history. Units are observations, not calendar days. Faint future trails are display-only research context, not an as-of replay.</p><section class="panel" id="debugPanel"><h2>DEBUG</h2><pre id="debugOutput" class="tiny">JS initialized: NO</pre></section>
</main><script>
const DATA=__DATA__, rows=DATA.rows, components=DATA.components, roles=['LONG','MID','SHORT'];
const convergenceRows=Array.isArray(DATA.convergence_rows)?DATA.convergence_rows:[], alignmentRows=Array.isArray(DATA.alignment_rows)?DATA.alignment_rows:[], positionRows=Array.isArray(DATA.phase_position_rows)?DATA.phase_position_rows:[];
const asofRows=Array.isArray(DATA.asof_phase_rows)?DATA.asof_phase_rows:[], regimeRows=Array.isArray(DATA.regime_rows)?DATA.regime_rows:[];
const nextPhaseRows=Array.isArray(DATA.next_phase_rows)?DATA.next_phase_rows:[];
const transformationRows=Array.isArray(DATA.transformation_rows)?DATA.transformation_rows:[];
const frozenPredictionRows=Array.isArray(DATA.frozen_prediction_rows)?DATA.frozen_prediction_rows:[];
const roleIndex={}; components.forEach((c,i)=>roleIndex[c.role]=i+1);
const colors={LONG:'var(--long)',MID:'var(--mid)',SHORT:'var(--short)',COMBINED:'var(--combined)'};
const slider=document.getElementById('uiSlider'), sliderLabel=document.getElementById('uiSliderLabel'); slider.max=String(rows.length-1); slider.value=String(rows.length-1);
const esc=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const xAt=(i,left=70,width=1080)=>left+width*i/Math.max(1,rows.length-1);
const ext=a=>[Math.min(...a),Math.max(...a)];
const vals=role=>rows.map(r=>Number(r[`wave${roleIndex[role]}_value`]));
const phase=(role,i)=>Number(rows[i][`wave${roleIndex[role]}_phase`]);
const yScale=(v,t,h,lo,hi)=>t+h*(hi-v)/(hi-lo||1);
function pathFor(a,t,h,left,width,lo,hi){return 'M '+a.map((v,i)=>`${xAt(i,left,width).toFixed(2)},${yScale(v,t,h,lo,hi).toFixed(2)}`).join(' L ');}
function drawMain(index){const svg=document.getElementById('mainChart'), left=70,width=1080,top=18,ohlcH=235,waveTop=281,waveH=325;const prices=rows.flatMap(r=>[Number(r.low),Number(r.high)]),[plo,phi]=ext(prices);const series={LONG:vals('LONG'),MID:vals('MID'),SHORT:vals('SHORT'),COMBINED:rows.map(r=>Number(r.combined_wave))},[wlo,whi]=ext(Object.values(series).flat());let out='';for(const [t,h] of [[top,ohlcH],[waveTop,waveH]])for(const f of [0,.5,1])out+=`<line class="svg-axis" x1="${left}" y1="${t+h*f}" x2="${left+width}" y2="${t+h*f}"/>`;for(let i=0;i<rows.length;i++){const xx=xAt(i),r=rows[i],py=v=>yScale(Number(v),top,ohlcH,plo,phi),y1=py(Math.max(r.open,r.close)),y2=py(Math.min(r.open,r.close));out+=`<line class="svg-axis" opacity=".22" x1="${xx}" y1="${top}" x2="${xx}" y2="${waveTop+waveH}"/><line class="svg-candle" x1="${xx}" y1="${py(r.high)}" x2="${xx}" y2="${py(r.low)}"/><rect x="${xx-6}" y="${y1}" width="12" height="${Math.max(2,y2-y1)}" fill="${r.bullish?'var(--bull)':'#3478b9'}"/>`;}for(const role of roles)out+=`<path class="svg-wave" stroke="${colors[role]}" stroke-width="${role==='LONG'?4:2.2}" d="${pathFor(series[role],waveTop,waveH,left,width,wlo,whi)}"/>`;out+=`<path class="svg-wave" stroke="${colors.COMBINED}" stroke-width="3" d="${pathFor(series.COMBINED,waveTop,waveH,left,width,wlo,whi)}"/>`;const cursor=xAt(index);out+=`<line class="svg-cursor" x1="${cursor}" y1="${top}" x2="${cursor}" y2="${waveTop+waveH}"/>`;for(const role of [...roles,'COMBINED']){const yy=yScale(series[role][index],waveTop,waveH,wlo,whi);out+=`<circle class="svg-point" cx="${cursor}" cy="${yy}" r="${role==='LONG'?6:5}" fill="${colors[role]}"/>`;}out+=`<text class="svg-label" x="8" y="30">OHLC</text><text class="svg-label" x="8" y="${waveTop+16}">wave value / observation</text>`;for(const i of [0,Math.floor((rows.length-1)/2),rows.length-1])out+=`<text class="svg-label" text-anchor="middle" x="${xAt(i)}" y="${waveTop+waveH+22}">${esc(rows[i].date)}</text>`;svg.innerHTML=out;}
function drawPhase(index){const svg=document.getElementById('phaseSpace'),cx=300,cy=190,r=112;let out=`<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="var(--line)"/><circle cx="${cx}" cy="${cy}" r="${r*.5}" fill="none" stroke="var(--line)" opacity=".5"/><line class="svg-axis" x1="${cx-r-20}" y1="${cy}" x2="${cx+r+20}" y2="${cy}"/><line class="svg-axis" x1="${cx}" y1="${cy-r-20}" x2="${cx}" y2="${cy+r+20}"/><text class="svg-label" x="${cx-20}" y="${cy-r-28}">180 crest</text><text class="svg-label" x="${cx-20}" y="${cy+r+35}">0 trough</text><text class="svg-label" x="${cx+r+25}" y="${cy+4}">90 rising</text><text class="svg-label" x="${cx-r-85}" y="${cy+4}">270 falling</text>`;for(const role of roles){const col=colors[role];for(let i=0;i<rows.length;i++){const a=phase(role,i)*Math.PI/180,rr=i===index?r:r*.86,px=cx+rr*Math.sin(a),py=cy+rr*Math.cos(a);out+=`<circle cx="${px}" cy="${py}" r="${i===index?8:3}" fill="${col}" opacity="${i===index?1:.28}"/>`;}const a=phase(role,index)*Math.PI/180;out+=`<text class="svg-label" x="${cx+r*.65*Math.sin(a)}" y="${cy+r*.65*Math.cos(a)-8}">${role}</text>`;}svg.innerHTML=out;}
function rate(v){return v===''?'-':Number(v).toFixed(1)+'%';}
function detail(index){const r=rows[index], same=r.bullish?'BULLISH':'BEARISH', next=r.next_day_bullish===null?'N/A':(r.next_day_bullish?'BULLISH':'BEARISH'),cell=(l,v,c='')=>`<div class="metric"><span class="label">${l}</span><span class="value ${c}">${esc(v)}</span></div>`;document.getElementById('summary').innerHTML=cell('date',r.date,'selected')+cell('Open',r.open)+cell('High',r.high)+cell('Low',r.low)+cell('Close',r.close)+cell('same-day',same,same==='BULLISH'?'bull':'')+cell('next observation',next,next==='BULLISH'?'next':'');sliderLabel.textContent=`${r.date} (${index+1}/${rows.length})`;const dirRows=roles.map(role=>{const w=roleIndex[role];return `<tr><td class="role-${role.toLowerCase()}">${role}</td><td>${Number(r[`wave${w}_phase`]).toFixed(1)} deg</td><td>${esc(r[`wave${w}_direction`])}</td><td>${r[`wave${w}_up`]?'UP':'DOWN'}</td><td>${Number(r[`wave${w}_value`]).toFixed(1)}</td></tr>`}).join('');const compRows=components.map(c=>`<tr><td class="role-${c.role.toLowerCase()}">${c.role}</td><td>${c.rank}</td><td>${Number(c.frequency).toFixed(5)}</td><td>${Number(c.period_days).toFixed(3)}</td><td>${Number(c.amplitude).toFixed(1)}</td><td>${(Number(c.relative_power)*100).toFixed(1)}%</td></tr>`).join('');const phaseRows=DATA.phase_stats.map(s=>`<tr><td class="role-${s.role.toLowerCase()}">${s.role}</td><td>${s.phase_bin} deg</td><td>${s.samples}</td><td>${rate(s.next_day_bullish_rate)}</td></tr>`).join('');const patternRows=DATA.pattern_stats.map(s=>`<tr><td>${s.pattern}</td><td>${s.samples}</td><td>${s.next_day_bullish_count}</td><td>${rate(s.next_day_bullish_rate)}</td></tr>`).join('');document.getElementById('info').innerHTML=`<div class="info-grid"><section><h3>Selected wave state</h3><table><thead><tr><th>role</th><th>phase</th><th>direction</th><th>UP/DOWN</th><th>value</th></tr></thead><tbody>${dirRows}</tbody></table><p><b>pattern:</b> ${esc(r.wave_direction_pattern)}</p><p><b>combined:</b> ${Number(r.combined_wave).toFixed(1)}</p></section><section><h3>Components</h3><div class="table-wrap"><table><thead><tr><th>role</th><th>rank</th><th>freq</th><th>period</th><th>amp</th><th>power</th></tr></thead><tbody>${compRows}</tbody></table></div></section><section><h3>Phase / next bullish rate</h3><div class="table-wrap"><table><thead><tr><th>role</th><th>phase</th><th>n</th><th>rate</th></tr></thead><tbody>${phaseRows}</tbody></table></div></section><section><h3>Direction pattern / next bullish rate</h3><div class="table-wrap"><table><thead><tr><th>pattern</th><th>n</th><th>bull</th><th>rate</th></tr></thead><tbody>${patternRows}</tbody></table></div></section></div>`;}
function render(i){drawMain(i);drawPhase(i);detail(i);}slider.addEventListener('input',()=>render(Number(slider.value)));document.getElementById('uiPrev').addEventListener('click',()=>{slider.value=String(Math.max(0,Number(slider.value)-1));render(Number(slider.value));});document.getElementById('uiNext').addEventListener('click',()=>{slider.value=String(Math.min(rows.length-1,Number(slider.value)+1));render(Number(slider.value));});render(Number(slider.value));
</script></body></html>'''
    page = page.replace("__MACHINE__", machine).replace("__CUTOFF__", REGIME_CUTOFF_DATE).replace("__ROW_MAX__", str(max(0, len(rows) - 1))).replace("__DATA__", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    panels = '<section class="panel chart-panel" id="periodRegimePanel"><h2>PERIOD REGIME HISTORY</h2><svg id="periodRegimeChart" viewBox="0 0 1200 360" role="img" aria-label="Period Regime History"></svg><div id="periodRegimeSummary" class="tiny"></div></section><section class="panel chart-panel" id="asofPhasePanel"><h2>AS-OF PHASE SPACE</h2><div class="tiny">Each selected date uses only observations through that date. The first 20 observations are INSUFFICIENT_HISTORY.</div><svg id="asofPhaseSpace" viewBox="0 0 600 390" role="img" aria-label="As-of Phase Space"></svg><div id="asofPhaseSummary" class="tiny"></div></section><section class="panel chart-panel" id="nfftComparisonPanel"><h2>FFT FRAME COMPARISON</h2><div id="nfftComparison"></div></section>'
    panels += '<section class="panel chart-panel" id="nextPredictionPanel"><h2>NEXT PHASE PREDICTION</h2><div class="tiny">Prediction uses only the source date D prefix FFT. Target D+1 As-of coordinates are answer-check data only; no bullish/bearish model is used.</div><svg id="nextPredictionSpace" viewBox="0 0 600 390" role="img" aria-label="Predicted and actual next Phase Space"></svg><div id="nextPredictionSummary" class="tiny"></div></section>'
    panels += '<section class="panel chart-panel" id="forwardValidationPanel"><h2>FROZEN FORWARD VALIDATION</h2><div class="tiny">This is a forward answer-check of the prediction frozen before the target observation. It is separate from the historical 29-row backtest and is not a bullish/bearish prediction model. Dashed markers/triangle = predicted D+1; solid markers/triangle = actual D+1 As-of.</div><svg id="forwardValidationSpace" viewBox="0 0 600 390" role="img" aria-label="Frozen predicted and actual next Phase Space"></svg><div id="forwardValidationSummary" class="tiny"></div></section>'
    panels += '<section class="panel chart-panel" id="transformationPanel"><h2>PHASE SPACE TRANSFORMATION</h2><div class="tiny">Historical D→D+1 geometry only. P = original prediction, T = best-fit transformation, A = actual As-of. This exploration does not use 2026-08-17 data.</div><svg id="transformationSpace" viewBox="0 0 600 390" role="img" aria-label="Phase Space transformation comparison"></svg><div id="transformationSummary" class="tiny"></div></section>'
    panels += '<section class="panel chart-panel" id="frozenPredictionPanel"><h2>NEXT PHASE PREDICTION: BASELINE vs TRANSFORMATION-AWARE</h2><div class="tiny">Frozen from observations through 2026-08-16 only. No 2026-08-17 OHLC or As-of data is used. B = baseline, T = transformation-aware; actual target points are not shown.</div><svg id="frozenPredictionSpace" viewBox="0 0 600 390" role="img" aria-label="Baseline and transformation-aware frozen prediction"></svg><div id="frozenPredictionSummary" class="tiny"></div></section>'
    panels += '<section class="panel chart-panel" id="nextTransformationProbabilityPanel"><h2>NEXT TRANSFORMATION PROBABILITY</h2><div class="tiny">Walk-forward empirical probability of a clear Phase Space Transformation on the next observation. This is separate from selecting a transformation type and does not predict bullish/bearish.</div><div id="nextTransformationProbabilitySummary" class="tiny"></div></section>'
    panels += '<section class="panel chart-panel" id="frozenPrediction1818Panel"><h2>NEXT PHASE PREDICTION: 2026-08-17 → 2026-08-18</h2><div class="tiny">FROZEN_BEFORE_ACTUAL. B = BASELINE; T = TRANSFORMATION-AWARE. No 2026-08-18 actual data is present or used.</div><svg id="frozenPrediction1818Space" viewBox="0 0 600 390" role="img" aria-label="2026-08-17 to 2026-08-18 frozen phase prediction"></svg><div id="frozenPrediction1818Summary" class="tiny"></div></section>'
    page = page.replace('<p class="note">Frequency = cycles / observation', panels + '<p class="note">Frequency = cycles / observation', 1)
    legacy_page = build_html_v3(machine, rows, components, daily)
    first_end = legacy_page.find('</script>')
    legacy_start = legacy_page.find('<script>', first_end)
    legacy_end = legacy_page.find('</script>', legacy_start) + len('</script>')
    legacy_extra = legacy_page[legacy_start:legacy_end]
    # Keep the controls in the HTML body.  The legacy script used to replace
    # them at runtime, which made the whole control bar disappear when any
    # later initializer threw.  Only listeners/state are restored here.
    controls_start = legacy_extra.find("uiControls.innerHTML=")
    controls_end = legacy_extra.find(";", controls_start)
    if controls_start >= 0 and controls_end >= controls_start:
        legacy_extra = legacy_extra[:controls_start] + "/* static controls retained */" + legacy_extra[controls_end + 1:]
    legacy_extra = legacy_extra.replace("function updateView(index){", forward_validation_script() + transformation_script() + frozen_prediction_script() + transformation_probability_script() + frozen_0817_prediction_script() + "function updateView(index){")
    legacy_extra = legacy_extra.replace("panelSafe('Next Phase Prediction',drawNextPrediction)", "panelSafe('Next Phase Prediction',drawNextPrediction),panelSafe('Forward Validation',drawForwardValidation),panelSafe('Phase Space Transformation',drawTransformation),panelSafe('Frozen Next Prediction',drawFrozenPrediction),panelSafe('Next Transformation Probability',drawNextTransformationProbability),panelSafe('Frozen 8/17 to 8/18 Prediction',drawFrozenPrediction1818)")
    return page.replace('</script></body>', '</script>' + legacy_extra + '</body>')


def add_interactive_ui(html: str) -> str:
    """Add the browser-only controls without changing analysis data or CSV output."""
    script = r'''<script>
(() => {
  "use strict";
  const start = () => {
    const controls = document.getElementById("waveLabControls");
    const mainChart = document.getElementById("mainChart");
    const phaseSpace = document.getElementById("phaseSpace");
    if (!controls || !mainChart || !phaseSpace || !Array.isArray(rows) || !rows.length) {
      console.error("Wave Lab UI init failed: required element or dataset missing");
      return;
    }
    const convergenceRows = Array.isArray(DATA.convergence_rows) ? DATA.convergence_rows : [];
    const alignmentRows = Array.isArray(DATA.alignment_rows) ? DATA.alignment_rows : [];
    const regimeRows = Array.isArray(DATA.regime_rows) ? DATA.regime_rows : [];
    const asofRows = Array.isArray(DATA.asof_phase_rows) ? DATA.asof_phase_rows : [];
    const positionRows = Array.isArray(DATA.phase_position_rows) ? DATA.phase_position_rows : [];
    const requiredControls = ["uiPrev", "uiPlay", "uiStop", "uiNext", "uiLoop", "uiSpeed", "uiOhlcMode", "uiSlider", "uiSliderLabel"];
    if (requiredControls.some(id => !document.getElementById(id))) {
      console.error("Wave Lab UI init failed: static control missing");
      return;
    }
    document.querySelector('main').insertAdjacentHTML('beforeend', '<section class="panel chart-panel" id="periodRegimePanel"><h2>PERIOD REGIME HISTORY</h2><div class="tiny">Expanding/as-of FFT: each date uses only observations through that date. STABLE / TRANSITION / UNSTABLE and n_fft changes are shown on the same observation axis.</div><svg id="periodRegimeChart" viewBox="0 0 1200 360" role="img" aria-label="Period Regime History"></svg><div id="periodRegimeSummary" class="tiny"></div></section><section class="panel chart-panel" id="asofPhasePanel"><h2>AS-OF PHASE SPACE</h2><div class="tiny"><b>FULL PERIOD PHASE SPACE:</b> uses all observations through the cutoff, so future observations can affect past positions. <b>AS-OF PHASE SPACE:</b> each selected date uses only observations through that date; future observations are not used for that day's FFT, phase, or position. This remains exploratory and does not guarantee prediction performance. The first 20 observations are INSUFFICIENT_HISTORY.</div><svg id="asofPhaseSpace" viewBox="0 0 600 390" role="img" aria-label="As-of Phase Space"></svg><div id="asofPhaseSummary" class="tiny"></div></section><section class="panel chart-panel" id="nfftComparisonPanel"><h2>FFT FRAME COMPARISON</h2><div class="tiny">n_fft=32/64 is a common FFT calculation frame, not a machine-specific period. At the 32→64 boundary, frequency-bin resolution, component selection, period, phase, and regime can change together.</div><div id="nfftComparison"></div></section>');
    const slider = document.getElementById("uiSlider");
    const label = document.getElementById("uiSliderLabel");
    const mode = document.getElementById("uiOhlcMode");
    const loop = document.getElementById("uiLoop");
    const speed = document.getElementById("uiSpeed");
    const debug = document.getElementById("debugOutput");
    let selectedIndex = rows.length - 1;
    let timerId = null;

    const finite = value => Number.isFinite(Number(value));
    const safe = value => finite(value) ? Number(value) : 0;
    const waveValues = role => rows.map(row => safe(row["wave" + roleIndex[role] + "_value"]));
    const scaleY = (value, top, height, lo, hi) => top + height * (hi - value) / (hi - lo || 1);
    const extentSafe = values => {
      const valid = values.filter(finite);
      if (!valid.length) return [0, 1];
      const lo = Math.min(...valid), hi = Math.max(...valid);
      return [lo, hi === lo ? lo + 1 : hi];
    };
    const phasePoint = (role, index) => {
      const component = components.find(item => item.role === role);
      const wave = roleIndex[role];
      const amplitude = component && finite(component.amplitude) ? Math.abs(Number(component.amplitude)) : 0;
      const value = safe(rows[index]["wave" + wave + "_value"]);
      const phaseValue = safe(rows[index]["wave" + wave + "_phase"]);
      const angle = phaseValue * Math.PI / 180;
      const radius = 78 + (amplitude ? 32 * value / amplitude : 0);
      if (!finite(angle) || !finite(radius)) return null;
      const x = 300 + radius * Math.sin(angle), y = 190 + radius * Math.cos(angle);
      return finite(x) && finite(y) ? [x, y] : null;
    };
    const pathTo = (role, end) => {
      const points = [];
      for (let i = 0; i <= end; i++) {
        const point = phasePoint(role, i);
        if (point) points.push(point[0].toFixed(2) + "," + point[1].toFixed(2));
      }
      return points.length ? "M " + points.join(" L ") : "";
    };
    const displayRows = () => {
      let previousClose = 0;
      return rows.map((row, index) => {
        const raw = {open: safe(row.open), high: safe(row.high), low: safe(row.low), close: safe(row.close)};
        if (mode.value === "zero") { previousClose = raw.close; return raw; }
        const base = index ? previousClose : 0;
        const display = {
          open: base,
          high: base + raw.high - raw.open,
          low: base + raw.low - raw.open,
          close: base + raw.close - raw.open
        };
        previousClose = display.close;
        return display;
      });
    };
    const drawMain = () => {
      const display = displayRows();
      const left = 70, width = 1080, top = 18, ohlcHeight = 235, waveTop = 281, waveHeight = 325;
      const prices = display.flatMap(row => [row.low, row.high]);
      const [priceLo, priceHi] = extentSafe(prices);
      const series = {LONG: waveValues("LONG"), MID: waveValues("MID"), SHORT: waveValues("SHORT"),
        COMBINED: rows.map(row => safe(row.combined_wave))};
      const [waveLo, waveHi] = extentSafe(Object.values(series).flat());
      let out = "";
      for (const [topValue, height] of [[top, ohlcHeight], [waveTop, waveHeight]]) {
        for (const fraction of [0, .5, 1]) out += '<line class="svg-axis" x1="' + left + '" y1="' +
          (topValue + height * fraction) + '" x2="' + (left + width) + '" y2="' +
          (topValue + height * fraction) + '"/>';
      }
      for (let i = 0; i < display.length; i++) {
        const x = xAt(i), row = display[i], source = rows[i];
        const py = value => scaleY(value, top, ohlcHeight, priceLo, priceHi);
        const bodyTop = py(Math.max(row.open, row.close)), bodyBottom = py(Math.min(row.open, row.close));
        out += '<line class="svg-axis" opacity=".22" x1="' + x + '" y1="' + top + '" x2="' + x +
          '" y2="' + (waveTop + waveHeight) + '"/><line class="svg-candle" x1="' + x + '" y1="' +
          py(row.high) + '" x2="' + x + '" y2="' + py(row.low) + '"/><rect x="' + (x - 6) +
          '" y="' + bodyTop + '" width="12" height="' + Math.max(2, bodyBottom - bodyTop) +
          '" fill="' + (source.bullish ? 'var(--bull)' : '#3478b9') + '"/>';
      }
      for (const role of roles) {
        const values = series[role];
        out += '<path class="svg-wave" stroke="' + colors[role] + '" stroke-width="' +
          (role === "LONG" ? 4 : 2.2) + '" d="' + pathFor(values, waveTop, waveHeight, left, width, waveLo, waveHi) + '"/>';
      }
      out += '<path class="svg-wave" stroke="' + colors.COMBINED + '" stroke-width="3" d="' +
        pathFor(series.COMBINED, waveTop, waveHeight, left, width, waveLo, waveHi) + '"/>';
      const cursor = xAt(selectedIndex);
      out += '<line class="svg-cursor" x1="' + cursor + '" y1="' + top + '" x2="' + cursor +
        '" y2="' + (waveTop + waveHeight) + '"/>';
      for (const role of [...roles, "COMBINED"]) out += '<circle class="svg-point" cx="' + cursor +
        '" cy="' + scaleY(series[role][selectedIndex], waveTop, waveHeight, waveLo, waveHi) +
        '" r="' + (role === "LONG" ? 6 : 5) + '" fill="' + colors[role] + '"/>';
      out += '<text class="svg-label" x="8" y="30">OHLC (' + (mode.value === "zero" ? "0 BASE" : "CLOSE CONNECT") +
        ')</text><text class="svg-label" x="8" y="297">wave value / observation</text>';
      mainChart.innerHTML = out;
    };
    const drawPhase = () => {
      const cx = 300, cy = 190, r = 112;
      let out = '<circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="none" stroke="var(--line)"/>' +
        '<line class="svg-axis" x1="' + (cx-r-20) + '" y1="' + cy + '" x2="' + (cx+r+20) + '" y2="' + cy +
        '"/><line class="svg-axis" x1="' + cx + '" y1="' + (cy-r-20) + '" x2="' + cx + '" y2="' + (cy+r+20) + '"/>';
      for (const role of roles) {
        const full = pathTo(role, rows.length - 1), past = pathTo(role, selectedIndex);
        const prefix = role.toLowerCase();
        if (full) out += '<path id="' + prefix + '-full-path" class="phase-full" d="' + full + '" fill="none" stroke="' + colors[role] + '" stroke-width="2" opacity=".18"></path>';
        if (past) out += '<path id="' + prefix + '-past-path" class="phase-past" d="' + past + '" fill="none" stroke="' + colors[role] + '" stroke-width="3" opacity=".9"></path>';
        const point = phasePoint(role, selectedIndex);
        if (point) out += '<circle id="' + prefix + '-current" class="phase-current" cx="' + point[0] + '" cy="' + point[1] +
          '" r="8" fill="' + colors[role] + '" stroke="var(--cursor)" stroke-width="2"/><text class="svg-label" x="' +
          (point[0] + 8) + '" y="' + (point[1] - 8) + '">' + role + '</text>';
      }
      const convergence = convergenceRows[selectedIndex];
      const alignment = alignmentRows[selectedIndex];
      if (convergence) {
        const triangle = [[convergence.long_x, convergence.long_y], [convergence.mid_x, convergence.mid_y], [convergence.short_x, convergence.short_y]];
        out += '<polygon id="full-phase-triangle" points="' + triangle.map(point => point[0] + ',' + point[1]).join(' ') +
          '" fill="none" stroke="var(--cursor)" stroke-width="1.5" opacity=".75"/>';
        out += '<circle id="full-phase-centroid" cx="' + convergence.centroid_x + '" cy="' + convergence.centroid_y +
          '" r="4" fill="var(--cursor)" stroke="var(--text)" stroke-width="1"/>';
        if (convergence.phase_convergence || (alignment && alignment.high_alignment)) {
          const label = convergence.phase_convergence && alignment && alignment.high_alignment ? 'CONVERGENCE + ALIGNMENT' :
            (convergence.phase_convergence ? 'CONVERGENCE' : 'ALIGNMENT');
          out += '<text class="svg-label" x="' + (convergence.centroid_x + 8) + '" y="' +
            (convergence.centroid_y + 16) + '">' + label + '</text>';
        }
      }
      const position = positionRows[selectedIndex];
      if (position) {
        out += '<text class="svg-label" x="18" y="24">regions: LONG ' + position.long_region +
          ' / MID ' + position.mid_region + ' / SHORT ' + position.short_region +
          ' | TOP waves: ' + position.top_wave_count + '</text>';
      }
      out += '<text class="svg-label" x="278" y="45">180 crest</text><text class="svg-label" x="278" y="350">0 trough</text>';
      out += '<text class="svg-label" x="438" y="194">90 rising</text><text class="svg-label" x="55" y="194">270 falling</text>';
      phaseSpace.innerHTML = out;
    };
    const updateInfo = () => {
      const row = rows[selectedIndex];
      const convergence = convergenceRows[selectedIndex] || {};
      const alignment = alignmentRows[selectedIndex] || {};
      const position = positionRows[selectedIndex] || {};
      label.textContent = row.date + " (" + (selectedIndex + 1) + "/" + rows.length + ")";
      const same = row.bullish ? "BULLISH" : "BEARISH";
      const next = row.next_day_bullish === null ? "N/A" : (row.next_day_bullish ? "BULLISH" : "BEARISH");
      document.getElementById("summary").innerHTML =
        '<div class="metric"><span class="label">date</span><span class="value selected">' + esc(row.date) + '</span></div>' +
        '<div class="metric"><span class="label">Open / High / Low / Close</span><span class="value">' +
        [row.open,row.high,row.low,row.close].map(esc).join(" / ") + '</span></div>' +
        '<div class="metric"><span class="label">same-day</span><span class="value">' + same + '</span></div>' +
        '<div class="metric"><span class="label">next observation</span><span class="value">' + next + '</span></div>';
      const dir = roles.map(role => { const w = roleIndex[role]; return '<tr><td>' + role + '</td><td>' +
        safe(row["wave" + w + "_phase"]).toFixed(1) + ' deg</td><td>' + esc(row["wave" + w + "_direction"]) +
        '</td><td>' + (row["wave" + w + "_up"] ? 'UP' : 'DOWN') + '</td><td>' + safe(row["wave" + w + "_value"]).toFixed(1) + '</td></tr>'; }).join("");
      const convergenceText = convergence.phase_convergence ? 'YES' : 'NO';
      const alignmentText = alignment.high_alignment ? 'YES' : 'NO';
      document.getElementById("info").innerHTML = '<h3>Selected wave state</h3><table><thead><tr><th>role</th><th>phase</th><th>direction</th><th>UP/DOWN</th><th>value</th></tr></thead><tbody>' + dir +
        '</tbody></table><p><b>pattern:</b> ' + esc(row.wave_direction_pattern) + '</p><p><b>combined:</b> ' + safe(row.combined_wave).toFixed(1) + '</p>' +
        '<h3>PHASE CONVERGENCE</h3><p><b>score:</b> ' + safe(convergence.convergence_score).toFixed(3) +
        ' / <b>max distance:</b> ' + safe(convergence.max_pair_distance).toFixed(2) + '</p><p><b>centroid:</b> ' +
        esc(convergence.centroid_region || '-') + ' / <b>phase_convergence:</b> ' + convergenceText +
        '</p><p class="tiny">score = clip(1 - max_pair_distance / 220, 0, 1); threshold = ' +
        safe(DATA.convergence_threshold).toFixed(3) + ' (top 20% of all observations)</p>' +
        '<h3>PHASE ALIGNMENT</h3><p><b>score:</b> ' + safe(alignment.phase_alignment_score).toFixed(3) +
        ' / <b>high alignment:</b> ' + alignmentText + ' / <b>threshold:</b> ' + safe(DATA.alignment_threshold).toFixed(3) +
        '</p><p><b>joint repeat:</b> ' + safe(DATA.joint_repeat_period).toFixed(0) +
        ' observations / <b>position:</b> ' + safe(alignment.repeat_position).toFixed(0) +
        ' / <b>cycle:</b> ' + safe(alignment.repeat_cycle_index).toFixed(0) +
        '</p><p class="tiny">Convergence uses XY distance and wave radius. Alignment uses phase angles only. FFT bins k=6,16,24 with n_fft=64 return to the same joint phase configuration every ' +
        safe(DATA.joint_repeat_period).toFixed(0) + ' observations; this is a retrospective discrete-FFT property, not a calendar-day law.</p>';
      if (position.date) {
        document.getElementById("info").innerHTML += '<h3>PHASE POSITION / ACTIVITY REGION</h3><p><b>LONG:</b> ' +
          esc(position.long_region) + ' / ' + (position.long_top_side ? 'TOP SIDE' : 'BOTTOM SIDE') +
          ' &nbsp; <b>MID:</b> ' + esc(position.mid_region) + ' / ' + (position.mid_top_side ? 'TOP SIDE' : 'BOTTOM SIDE') +
          ' &nbsp; <b>SHORT:</b> ' + esc(position.short_region) + ' / ' + (position.short_top_side ? 'TOP SIDE' : 'BOTTOM SIDE') +
          '</p><p><b>top_wave_count:</b> ' + position.top_wave_count + ' / <b>top_wave_pattern:</b> ' +
          esc(position.top_wave_pattern) + '</p><p><b>centroid:</b> ' + esc(position.centroid_region) +
          ' / <b>y offset:</b> ' + safe(position.centroid_y_offset).toFixed(2) +
          ' (negative=TOP, positive=BOTTOM)</p>';
      }
      debug.textContent = 'debug index=' + selectedIndex + ' date=' + row.date + ' timer=' + (timerId !== null) +
        ' mode=' + mode.value + ' phase paths=' + document.querySelectorAll('#phaseSpace path').length;
    };
    const updateView = index => {
      selectedIndex = Math.max(0, Math.min(rows.length - 1, Number(index) || 0));
      slider.value = String(selectedIndex);
      try { drawMain(); drawPhase(); updateInfo(); } catch (error) {
        debug.textContent = 'UI error: ' + error.message;
        console.error('Wave Lab updateView failed', error);
      }
    };
    const drawNfftComparison = () => {
      const box = document.getElementById('nfftComparison');
      if (!box) return;
      const stats = Array.isArray(DATA.asof_nfft_stats) ? DATA.asof_nfft_stats : [];
      const regimeStats = Array.isArray(DATA.asof_nfft_regime_stats) ? DATA.asof_nfft_regime_stats : [];
      const formatRate = value => value === '' || value === undefined || value === null ? '-' : Number(value).toFixed(1) + '%';
      let output = '<div class="table-wrap"><table><thead><tr><th>n_fft</th><th>TOP count</th><th>samples</th><th>bullish rate</th><th>next bullish rate</th></tr></thead><tbody>';
      for (const row of stats) output += '<tr><td>' + row.n_fft + '</td><td>' + row.top_wave_count + '</td><td>' + row.samples + '</td><td>' + formatRate(row.bullish_rate) + '</td><td>' + formatRate(row.next_day_bullish_rate) + '</td></tr>';
      output += '</tbody></table></div><h3>n_fft × regime × 2+ TOP</h3><div class="table-wrap"><table><thead><tr><th>n_fft</th><th>regime</th><th>samples</th><th>bullish rate</th><th>next bullish rate</th></tr></thead><tbody>';
      for (const row of regimeStats) output += '<tr><td>' + row.n_fft + '</td><td>' + row.regime + '</td><td>' + row.samples + '</td><td>' + formatRate(row.bullish_rate) + '</td><td>' + formatRate(row.next_day_bullish_rate) + '</td></tr>';
      box.innerHTML = output + '</tbody></table></div><p class="tiny">n_fft is a shared FFT calculation frame, not a machine-specific period.</p>';
    };
    const stop = () => { if (timerId !== null) { clearInterval(timerId); timerId = null; } document.getElementById('uiPlay').textContent = 'Play'; updateInfo(); };
    const advance = () => { if (selectedIndex < rows.length - 1) updateView(selectedIndex + 1); else if (loop.checked) updateView(0); else stop(); };
    const play = () => { if (timerId !== null || (selectedIndex >= rows.length - 1 && !loop.checked)) return; document.getElementById('uiPlay').textContent = 'Playing'; timerId = setInterval(advance, Number(speed.value)); updateInfo(); };
    document.getElementById('uiPrev').addEventListener('click', () => { stop(); updateView(selectedIndex - 1); });
    document.getElementById('uiNext').addEventListener('click', () => { stop(); updateView(selectedIndex + 1); });
    document.getElementById('uiPlay').addEventListener('click', play);
    document.getElementById('uiStop').addEventListener('click', stop);
    slider.addEventListener('input', () => { stop(); updateView(slider.value); });
    mode.addEventListener('change', () => updateView(selectedIndex));
    speed.addEventListener('change', () => { if (timerId !== null) { stop(); play(); } });
    try {
      drawNfftComparison();
      updateView(selectedIndex);
    } catch (error) {
      debug.textContent = 'UI initialization error: ' + error.message;
      console.error('Wave Lab UI initialization failed', error);
    }
    console.info('Wave Lab UI ready', {rows: rows.length, phasePoints: document.querySelectorAll('#phaseSpace path').length});
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true}); else start();
})();
</script>'''
    return html.replace('</body></html>', script + '</body></html>')


def run(machine: str) -> Path:
    machine = parse_machine(machine)
    # Keep the main dashboard/past-analysis payload current through the latest
    # formal OHLC.  The separate 8/16 and 8/17 prefixes below are retained for
    # the frozen answer checks.
    all_rows = load_machine_rows(machine)
    validation_rows = load_machine_rows(machine, FROZEN_TWO_WAY_TARGET_DATE)
    rows = [row for row in all_rows if row["date"] <= REGIME_CUTOFF_DATE]
    if not rows:
        raise FileNotFoundError(f"台{machine}のdaily OHLCが見つかりません")
    components, daily, _centered, comparison_components = analyze(rows)
    convergence_rows, _convergence_threshold = phase_convergence_analysis(daily, components)
    alignment_rows, _alignment_threshold = phase_alignment_analysis(convergence_rows)
    single_repeat_periods, joint_repeat_period = repeat_periods(components)
    alignment_rows = add_repeat_metadata(alignment_rows, joint_repeat_period)
    regime_rows, regime_events = period_regime_history(daily, convergence_rows, alignment_rows)
    validate_period_regime_history(regime_rows, REGIME_CUTOFF_DATE)
    position_rows = phase_position_rows(daily, convergence_rows)
    validate_phase_position_rows(position_rows, convergence_rows)
    asof_rows = asof_phase_space_history(daily, regime_rows)
    validate_asof_phase_space(asof_rows, REGIME_CUTOFF_DATE)
    out_dir = OUTPUT_ROOT / machine
    out_dir.mkdir(parents=True, exist_ok=True)
    frozen_prediction_path = out_dir / "next_phase_prediction_daily.csv"
    frozen_two_way_path = out_dir / "next_phase_prediction_frozen.csv"
    frozen_next_phase_rows = read_csv_rows(frozen_prediction_path)
    next_phase_rows = frozen_next_phase_rows or asof_next_phase_predictions(daily, asof_rows, regime_rows)
    asof_by_date = {row["date"]: row for row in asof_rows}
    transformation_rows = phase_transformation_rows(next_phase_rows, daily, asof_by_date)
    forward_validation = build_forward_validation(machine, frozen_prediction_path, all_rows)
    forward_transformation_rows = phase_transformation_rows([forward_transformation_input(forward_validation, daily)], daily, asof_by_date)
    identity_distance_threshold, identity_centroid_threshold = refine_identity_classification(transformation_rows)
    refine_identity_classification(forward_transformation_rows, transformation_rows)
    # The 8/16 -> 8/17 predictions were frozen before actual data existed.
    # Prefer the persisted rows so this run can never regenerate or alter them.
    frozen_prediction_rows = read_csv_rows(frozen_two_way_path) or frozen_next_phase_predictions(machine, all_rows, transformation_rows, forward_transformation_rows)
    frozen_two_way_rows, frozen_two_way_transformations = frozen_two_way_validation(machine, frozen_two_way_path, validation_rows, transformation_rows)
    probability_history = transformation_rows + forward_transformation_rows + frozen_two_way_transformations
    probability_daily_rows = transformation_probability_daily_rows(probability_history, asof_rows)
    frozen_probability_source = probability_source_from_target_validation(frozen_two_way_rows[0]) if frozen_two_way_rows else None
    frozen_probability = frozen_transformation_probability(frozen_probability_source, probability_history, asof_by_date, "2026-08-18") if frozen_probability_source else None
    frozen_probability_path = out_dir / "next_transformation_probability_frozen.csv"
    persisted_probability = read_csv_rows(frozen_probability_path)
    if persisted_probability:
        # Preserve the preceding probability freeze byte-for-byte in meaning;
        # this run only consumes it and never recalculates or rewrites it.
        frozen_probability = persisted_probability[0]
    # Build only the 2026-08-17 prefix to obtain the source state for the new
    # freeze.  No target (2026-08-18) row is loaded or analyzed here.
    validation_components, validation_daily, _validation_centered, _validation_comparison = analyze(validation_rows)
    validation_convergence, _validation_convergence_threshold = phase_convergence_analysis(validation_daily, validation_components)
    validation_alignment, _validation_alignment_threshold = phase_alignment_analysis(validation_convergence)
    validation_alignment = add_repeat_metadata(validation_alignment, repeat_periods(validation_components)[1])
    validation_regime, _validation_events = period_regime_history(validation_daily, validation_convergence, validation_alignment)
    validation_asof = asof_phase_space_history(validation_daily, validation_regime)
    validation_predictions = asof_next_phase_predictions(validation_daily, validation_asof, validation_regime)
    source_prediction_0817 = next((row for row in validation_predictions if row.get("source_date") == "2026-08-17"), None)
    source_asof_0817 = next((row for row in validation_asof if row.get("date") == "2026-08-17"), None)
    frozen_0817_rows = frozen_phase_prediction_0817_0818(source_prediction_0817, source_asof_0817, frozen_probability, probability_history) if source_prediction_0817 and source_asof_0817 and frozen_probability else []
    component_fields = ["preprocessing", "rank", "role", "frequency", "period_days", "amplitude", "phase", "relative_power", "phase_definition", "n_observations", "n_fft", "sampling_interval", "frequency_unit", "period_basis"]
    daily_fields = ["date", "machine", "open", "high", "low", "close", "bullish", "next_day_bullish", "wave1_phase", "wave2_phase", "wave3_phase", "wave1_value", "wave2_value", "wave3_value", "combined_wave", "wave1_direction", "wave2_direction", "wave3_direction", "wave1_up", "wave2_up", "wave3_up", "wave_direction_pattern"]
    stats_fields = ["wave", "phase_start", "phase_end", "samples", "bullish_count", "bullish_rate"]
    nextday_fields = ["wave", "role", "phase_bin", "samples", "next_day_bullish_count", "next_day_bullish_rate"]
    pattern_fields = ["pattern", "samples", "next_day_bullish_count", "next_day_bullish_rate"]
    convergence_fields = ["date", "machine", "long_phase", "mid_phase", "short_phase", "long_x", "long_y", "mid_x", "mid_y", "short_x", "short_y", "distance_long_mid", "distance_mid_short", "distance_short_long", "mean_pair_distance", "max_pair_distance", "min_pair_distance", "convergence_score", "phase_convergence", "centroid_x", "centroid_y", "centroid_region", "bullish", "next_day_bullish", "wave_direction_pattern"]
    convergence_stat_fields = ["score_bin", "samples", "bullish_count", "bullish_rate", "next_day_samples", "next_day_bullish_count", "next_day_bullish_rate"]
    convergence_region_fields = ["centroid_region", "phase_convergence", "samples", "bullish_count", "bullish_rate", "next_day_samples", "next_day_bullish_count", "next_day_bullish_rate"]
    alignment_fields = convergence_fields + ["phase_alignment_score", "high_alignment", "repeat_position", "repeat_cycle_index"]
    alignment_stat_fields = convergence_stat_fields
    alignment_region_fields = ["centroid_region", "high_alignment", "samples", "bullish_count", "bullish_rate", "next_day_samples", "next_day_bullish_count", "next_day_bullish_rate"]
    position_fields = [
        "date", "machine", "long_x", "long_y", "long_region", "long_top_side",
        "mid_x", "mid_y", "mid_region", "mid_top_side", "short_x", "short_y", "short_region", "short_top_side",
        "top_wave_count", "top_wave_pattern", "centroid_x", "centroid_y", "centroid_y_offset", "centroid_region",
        "bullish", "next_day_bullish",
    ]
    position_stat_fields = ["category", "value", "samples", "bullish_count", "bullish_rate", "next_day_samples", "next_day_bullish_count", "next_day_bullish_rate"]
    position_pattern_fields = position_stat_fields
    asof_fields = ["date", "machine", "status", "n_observations", "n_fft", "regime"] + _asof_metric_fields() + ["bullish", "next_day_bullish"]
    asof_stat_fields = position_stat_fields
    asof_regime_stat_fields = ["category", "value", "samples", "bullish_count", "bullish_rate", "next_day_samples", "next_day_bullish_count", "next_day_bullish_rate"]
    asof_region_stat_fields = asof_regime_stat_fields
    asof_nfft_fields = ["n_fft", "top_wave_count", "samples", "bullish_count", "bullish_rate", "next_day_samples", "next_day_bullish_count", "next_day_bullish_rate"]
    asof_nfft_pattern_fields = ["n_fft", "top_wave_pattern", "samples", "bullish_rate", "next_day_samples", "next_day_bullish_rate"]
    asof_nfft_regime_fields = ["n_fft", "regime", "top_wave_count", "samples", "bullish_count", "bullish_rate", "next_day_samples", "next_day_bullish_count", "next_day_bullish_rate"]
    asof_transition_fields = [
        "date", "n_observations", "n_fft", "n_fft_changed", "long_k", "mid_k", "short_k",
        "long_frequency", "mid_frequency", "short_frequency", "long_period", "mid_period", "short_period",
        "dominant_rank_signature", "regime", "period_stability_score", "joint_repeat_period",
        "long_phase", "mid_phase", "short_phase", "top_wave_count", "top_wave_pattern", "centroid_region", "centroid_y_offset",
        "alignment_score", "convergence_score", "bullish", "next_day_bullish",
    ]
    prediction_fields = [
        "source_date", "target_date", "machine", "status", "source_n_observations", "source_n_fft", "source_regime",
        "source_period_stability_score", "source_dominant_rank_signature", "source_component_reorder", "target_n_observations", "target_n_fft", "n_fft_changed", "target_regime", "target_bullish", "target_next_day_bullish",
    ]
    for role in ("long", "mid", "short"):
        prediction_fields += [
            role + "_source_k", role + "_source_frequency", role + "_source_period", role + "_source_rank",
            role + "_predicted_phase", role + "_predicted_wave_value", role + "_predicted_amplitude", role + "_predicted_radius", role + "_predicted_x", role + "_predicted_y", role + "_predicted_top_side",
            role + "_actual_k", role + "_actual_frequency", role + "_actual_period", role + "_actual_rank", role + "_component_same_k",
            role + "_actual_phase", role + "_actual_wave_value", role + "_actual_amplitude", role + "_actual_x", role + "_actual_y", role + "_angular_error_deg", role + "_xy_error", role + "_top_side_match",
        ]
    prediction_fields += [
        "predicted_centroid_x", "predicted_centroid_y", "predicted_centroid_y_offset", "predicted_centroid_region", "predicted_top_wave_count", "predicted_top_wave_pattern",
        "actual_centroid_x", "actual_centroid_y", "actual_centroid_y_offset", "actual_centroid_region", "actual_top_wave_count", "actual_top_wave_pattern",
        "centroid_distance_error", "centroid_region_match", "top_wave_count_error", "top_wave_count_exact", "top_wave_pattern_match",
    ]
    prediction_stat_fields = ["scope", "samples", "mean_angular_error_deg", "median_angular_error_deg", "mean_xy_error", "top_side_accuracy", "component_same_k_rate", "centroid_mean_distance_error", "centroid_region_accuracy", "top_wave_count_exact_accuracy", "top_wave_pattern_accuracy"]
    prediction_group_fields = ["source_regime", "samples"] + [f"{role}_{metric}" for role in ("long", "mid", "short") for metric in ("mean_angular_error_deg", "mean_xy_error", "top_side_accuracy")] + ["centroid_mean_distance_error", "centroid_region_accuracy", "top_wave_count_exact_accuracy", "top_wave_pattern_accuracy"]
    prediction_nfft_fields = ["source_n_fft", "samples"] + [f"{role}_{metric}" for role in ("long", "mid", "short") for metric in ("mean_angular_error_deg", "mean_xy_error", "top_side_accuracy")] + ["centroid_mean_distance_error", "centroid_region_accuracy", "top_wave_count_exact_accuracy", "top_wave_pattern_accuracy"]
    transformation_fields = [
        "source_date", "target_date", "machine", "comparison_scope", "source_regime", "source_n_fft", "source_component_reorder", "source_bullish", "target_bullish",
        "transformation_type", "best_permutation", "identity_distance", "best_permutation_distance", "permutation_improvement",
        "best_rotation_deg", "rotation_residual", "inversion_distance", "inversion_improvement_vs_identity", "vertical_mirror_distance", "horizontal_mirror_distance", "best_mirror_type", "best_mirror_distance",
        "best_rotation_permutation", "best_rotation_plus_swap_deg", "best_transformation_distance", "transformation_improvement", "classification_confidence", "shape_similarity", "centroid_distance", "centroid_region_match", "predicted_centroid_region", "actual_centroid_region", "identity_distance_threshold", "identity_centroid_threshold",
        "source_top_wave_count", "source_top_wave_pattern", "source_centroid_region", "source_phase_alignment_score", "source_convergence_score", "source_long_k", "source_mid_k", "source_short_k", "source_perimeter", "source_area",
        "predicted_perimeter", "actual_perimeter", "predicted_area", "actual_area", "area_ratio",
    ]
    for side in ("long_mid", "mid_short", "short_long"):
        transformation_fields += ["predicted_" + side + "_side", "actual_" + side + "_side", side + "_side_ratio"]
    for role in ("long", "mid", "short"):
        transformation_fields += ["predicted_" + role + "_phase", "actual_" + role + "_phase", role + "_angular_error", role + "_xy_distance", "predicted_" + role + "_x", "predicted_" + role + "_y", "actual_" + role + "_x", "actual_" + role + "_y", "transformed_" + role + "_x", "transformed_" + role + "_y"]
    transformation_stat_fields = ["group", "value", "samples", "percentage", "source_bullish_count", "source_bullish_rate", "target_bullish_count", "target_bullish_rate", "mean_improvement", "mean_shape_similarity"]
    frozen_prediction_fields = ["source_date", "target_date", "machine", "prediction_version", "prediction_type", "source_regime", "source_n_fft", "selected_transformation", "selection_basis", "support_samples", "transformation_probability", "support_status", "generated_at", "source_cutoff", "logic_version", "source_commit", "prediction_status", "predicted_centroid_x", "predicted_centroid_y", "predicted_centroid_y_offset", "predicted_centroid_region", "predicted_top_wave_count", "predicted_top_wave_pattern", "predicted_phase_alignment_score", "predicted_convergence_score"]
    for role in ("long", "mid", "short"):
        frozen_prediction_fields += [role + "_phase", role + "_x", role + "_y"]
    frozen_two_way_fields = [
        "source_date", "target_date", "machine", "prediction_commit", "prediction_status", "prediction_type", "source_regime", "source_n_fft", "source_cutoff",
        "selected_transformation", "selection_basis", "support_samples", "transformation_probability", "support_status", "target_n_observations", "target_n_fft", "target_regime", "n_fft_changed",
        "actual_open", "actual_high", "actual_low", "actual_close", "actual_bullish",
    ]
    for role in ("long", "mid", "short"):
        frozen_two_way_fields += [
            "predicted_" + role + "_phase", "actual_" + role + "_phase", role + "_angular_error",
            "predicted_" + role + "_x", "predicted_" + role + "_y", "actual_" + role + "_x", "actual_" + role + "_y", role + "_xy_distance",
            "predicted_" + role + "_k", "actual_" + role + "_k", "predicted_" + role + "_frequency", "actual_" + role + "_frequency",
            "predicted_" + role + "_period", "actual_" + role + "_period", "predicted_" + role + "_rank", "actual_" + role + "_rank", role + "_component_same_k",
        ]
    frozen_two_way_fields += [
        "predicted_top_wave_count", "actual_top_wave_count", "top_count_match", "predicted_top_wave_pattern", "actual_top_wave_pattern", "pattern_match",
        "predicted_centroid_x", "predicted_centroid_y", "actual_centroid_x", "actual_centroid_y", "centroid_distance",
        "predicted_centroid_region", "actual_centroid_region", "centroid_region_match", "actual_phase_alignment_score", "actual_convergence_score",
        "actual_dominant_rank_signature", "actual_joint_repeat_period", "actual_period_stability_score",
        "actual_transformation_transformation_type", "actual_transformation_best_permutation", "actual_transformation_best_rotation_deg",
        "actual_transformation_identity_distance", "actual_transformation_best_transformation_distance", "actual_transformation_transformation_improvement",
        "actual_transformation_centroid_distance", "actual_transformation_shape_similarity",
    ]
    forward_fields = [
        "source_date", "target_date", "machine", "prediction_commit", "prediction_status", "source_regime", "source_n_fft", "target_n_observations", "target_n_fft", "target_regime", "n_fft_changed",
    ]
    for role in ("long", "mid", "short"):
        forward_fields += [
            "predicted_" + role + "_phase", "actual_" + role + "_phase", role + "_angular_error",
            "predicted_" + role + "_x", "predicted_" + role + "_y", "actual_" + role + "_x", "actual_" + role + "_y", role + "_xy_distance",
            "predicted_" + role + "_top_side", "actual_" + role + "_top_side", role + "_top_side_match",
            "predicted_" + role + "_k", "predicted_" + role + "_frequency", "predicted_" + role + "_period", "predicted_" + role + "_rank",
            "actual_" + role + "_k", "actual_" + role + "_frequency", "actual_" + role + "_period", "actual_" + role + "_rank", role + "_component_same_k",
        ]
    forward_fields += [
        "predicted_top_wave_count", "actual_top_wave_count", "top_count_match", "predicted_top_wave_pattern", "actual_top_wave_pattern", "pattern_match",
        "predicted_centroid_x", "predicted_centroid_y", "actual_centroid_x", "actual_centroid_y", "centroid_distance",
        "predicted_centroid_region", "actual_centroid_region", "centroid_region_match", "actual_phase_alignment_score", "actual_convergence_score", "actual_wave_direction_pattern", "actual_dominant_rank_signature", "actual_joint_repeat_period", "actual_period_stability_score",
        "actual_open", "actual_high", "actual_low", "actual_close", "actual_bullish",
    ]
    probability_fields = [
        "machine", "source_date", "target_date", "source_regime", "source_n_fft", "selected_support_level", "support_samples",
        "transform_samples", "non_transform_samples", "transform_probability", "confidence", "most_likely_transform_type",
        "conditional_type_probability", "selection_basis", "geometry_distance_threshold", "actual_transform", "actual_transformation_type",
        "actual_bullish", "probability_bucket", "status",
    ]
    probability_stat_fields = ["scope", "samples", "mean_predicted_probability", "actual_transform_count", "actual_transform_rate", "brier_score"]
    probability_frozen_fields = [
        "machine", "source_date", "target_date", "source_regime", "source_n_fft", "selected_support_level", "support_samples",
        "transform_samples", "non_transform_samples", "transform_probability", "confidence", "most_likely_transform_type",
        "conditional_type_probability", "selection_basis", "geometry_distance_threshold", "source_cutoff", "status", "logic_version", "generated_at",
    ]
    frozen_0817_fields = [
        "machine", "source_date", "target_date", "source_cutoff", "prediction_status", "prediction_type", "logic_version", "generated_at", "source_commit",
        "source_regime", "source_n_fft", "transform_probability", "probability_confidence", "probability_support_samples", "probability_transform_samples", "probability_non_transform_samples", "belt_state", "most_likely_type", "conditional_probability",
        "selected_transformation_type", "selected_permutation", "selection_basis", "selection_support_samples", "transform_applied",
    ]
    for prefix in ("baseline", "aware"):
        frozen_0817_fields += [prefix + "_top_wave_count", prefix + "_top_wave_pattern", prefix + "_centroid_x", prefix + "_centroid_y", prefix + "_centroid_region"]
        for role in ("long", "mid", "short"):
            frozen_0817_fields += [prefix + "_" + role + "_phase", prefix + "_" + role + "_x", prefix + "_" + role + "_y", prefix + "_" + role + "_top_side"]
    for role in ("long", "mid", "short"):
        frozen_0817_fields += ["baseline_" + role + "_wave_value", "baseline_" + role + "_amplitude", "baseline_" + role + "_radius"]
    regime_fields = [
        "date", "machine", "n_observations", "n_fft", "status", "open", "high", "low", "close", "bullish", "next_day_bullish",
        "long_k", "long_frequency", "long_period", "long_amplitude", "long_power", "long_rank",
        "mid_k", "mid_frequency", "mid_period", "mid_amplitude", "mid_power", "mid_rank",
        "short_k", "short_frequency", "short_period", "short_amplitude", "short_power", "short_rank",
        "long_period_change", "long_period_change_pct", "mid_period_change", "mid_period_change_pct", "short_period_change", "short_period_change_pct",
        "period_stability_score", "regime", "dominant_rank_signature", "component_reorder", "n_fft_changed",
        "long_repeat_period", "mid_repeat_period", "short_repeat_period", "joint_repeat_period", "joint_repeat_stable_count",
        "asof_long_phase", "asof_mid_phase", "asof_short_phase", "asof_phase_alignment_score", "asof_phase_convergence_score", "asof_centroid_region",
        "full_fft_alignment_score", "full_fft_convergence_score", "full_fft_centroid_region",
    ]
    event_fields = ["date", "event_type", "previous_value", "current_value", "details"]
    regime_stat_fields = ["regime", "samples", "bullish_count", "bullish_rate", "next_day_samples", "next_day_bullish_count", "next_day_bullish_rate", "avg_long_period", "avg_mid_period", "avg_short_period", "avg_alignment", "avg_convergence"]
    write_csv(out_dir / "fft_components.csv", comparison_components, component_fields)
    write_csv(out_dir / "fft_phase_daily.csv", daily, daily_fields)
    write_csv(out_dir / "phase_bullish_stats.csv", phase_stats(daily), stats_fields)
    write_csv(out_dir / "phase_nextday_bullish_stats.csv", phase_nextday_stats(daily, components), nextday_fields)
    write_csv(out_dir / "wave_pattern_nextday_stats.csv", pattern_nextday_stats(daily), pattern_fields)
    write_csv(out_dir / "phase_convergence_daily.csv", convergence_rows, convergence_fields)
    write_csv(out_dir / "phase_convergence_stats.csv", phase_convergence_stats(convergence_rows), convergence_stat_fields)
    write_csv(out_dir / "phase_convergence_region_stats.csv", phase_convergence_region_stats(convergence_rows), convergence_region_fields)
    write_csv(out_dir / "phase_alignment_daily.csv", alignment_rows, alignment_fields)
    write_csv(out_dir / "phase_alignment_stats.csv", phase_alignment_stats(alignment_rows), alignment_stat_fields)
    write_csv(out_dir / "phase_alignment_region_stats.csv", phase_alignment_region_stats(alignment_rows), alignment_region_fields)
    write_csv(out_dir / "phase_position_daily.csv", position_rows, position_fields)
    write_csv(out_dir / "phase_position_stats.csv", phase_position_stats(position_rows), position_stat_fields)
    write_csv(out_dir / "phase_position_pattern_stats.csv", phase_position_pattern_stats(position_rows), position_pattern_fields)
    write_csv(out_dir / "asof_phase_position_daily.csv", asof_rows, asof_fields)
    write_csv(out_dir / "asof_phase_position_stats.csv", phase_position_stats([row for row in asof_rows if row["status"] == "VALID"]), asof_stat_fields)
    write_csv(out_dir / "asof_phase_position_pattern_stats.csv", phase_position_pattern_stats([row for row in asof_rows if row["status"] == "VALID"]), asof_stat_fields)
    write_csv(out_dir / "asof_phase_regime_stats.csv", asof_phase_regime_stats(asof_rows), asof_regime_stat_fields)
    write_csv(out_dir / "asof_phase_region_stats.csv", asof_phase_region_stats(asof_rows), asof_region_stat_fields)
    write_csv(out_dir / "asof_phase_nfft_stats.csv", asof_nfft_stats(asof_rows), asof_nfft_fields)
    write_csv(out_dir / "asof_phase_nfft_pattern_stats.csv", asof_nfft_pattern_stats(asof_rows), asof_nfft_pattern_fields)
    write_csv(out_dir / "asof_phase_nfft_regime_stats.csv", asof_nfft_regime_stats(asof_rows), asof_nfft_regime_fields)
    write_csv(out_dir / "asof_nfft_transition_detail.csv", asof_nfft_transition_detail(asof_rows, regime_rows), asof_transition_fields)
    write_csv(out_dir / "period_regime_daily.csv", regime_rows, regime_fields)
    write_csv(out_dir / "period_regime_events.csv", regime_events, event_fields)
    write_csv(out_dir / "period_regime_stats.csv", period_regime_stats(regime_rows), regime_stat_fields)
    if not frozen_next_phase_rows:
        write_csv(out_dir / "next_phase_prediction_daily.csv", next_phase_rows, prediction_fields)
        write_csv(out_dir / "next_phase_prediction_stats.csv", next_phase_prediction_stats(next_phase_rows), prediction_stat_fields)
        write_csv(out_dir / "next_phase_prediction_regime_stats.csv", next_phase_prediction_group_stats(next_phase_rows, "source_regime", ("STABLE", "TRANSITION", "UNSTABLE")), prediction_group_fields)
        write_csv(out_dir / "next_phase_prediction_nfft_stats.csv", next_phase_prediction_group_stats(next_phase_rows, "source_n_fft", (32, 64)), prediction_nfft_fields)
    write_csv(out_dir / "next_phase_forward_validation.csv", [forward_validation], forward_fields)
    write_csv(out_dir / "phase_transformation_daily.csv", transformation_rows, transformation_fields)
    write_csv(out_dir / "phase_transformation_forward.csv", forward_transformation_rows, transformation_fields)
    write_csv(out_dir / "phase_transformation_stats.csv", phase_transformation_stats(transformation_rows), transformation_stat_fields)
    write_csv(out_dir / "phase_transformation_regime_stats.csv", phase_transformation_stats(transformation_rows, "source_regime", ("STABLE", "TRANSITION", "UNSTABLE")), transformation_stat_fields)
    write_csv(out_dir / "phase_transformation_nfft_stats.csv", phase_transformation_stats(transformation_rows, "source_n_fft", ("32", "64")), transformation_stat_fields)
    component_rows = [{**row, "component_continuity": "ALL_SAME_K" if all(row.get(role + "_component_same_k") is True or str(row.get(role + "_component_same_k")).lower() == "true" for role in ("long", "mid", "short")) else "ANY_K_CHANGE"} for row in next_phase_rows if row.get("status") == "VALID_PREDICTION" and row.get("long_angular_error_deg", "") != ""]
    component_transformation_rows = [row for row in transformation_rows if any(item["source_date"] == row["source_date"] for item in component_rows)]
    continuity_by_date = {row["source_date"]: row["component_continuity"] for row in component_rows}
    for row in component_transformation_rows:
        row["component_continuity"] = continuity_by_date.get(row["source_date"], "")
    write_csv(out_dir / "phase_transformation_component_stats.csv", phase_transformation_stats(component_transformation_rows, "component_continuity", ("ALL_SAME_K", "ANY_K_CHANGE")), transformation_stat_fields)
    write_csv(out_dir / "next_transformation_probability_daily.csv", probability_daily_rows, probability_fields)
    write_csv(out_dir / "next_transformation_probability_stats.csv", transformation_probability_stats(probability_daily_rows), probability_stat_fields)
    forward_probability_rows = [{**row, "status": "FORWARD_PROBABILITY_VALIDATION"} for row in probability_daily_rows if row.get("source_date") in (FORWARD_SOURCE_DATE, FROZEN_TWO_WAY_SOURCE_DATE)]
    write_csv(out_dir / "next_transformation_probability_forward_validation.csv", forward_probability_rows, probability_fields)
    if not frozen_probability_path.exists():
        write_csv(frozen_probability_path, [frozen_probability] if frozen_probability else [], probability_frozen_fields)
    write_csv(out_dir / "next_phase_prediction_frozen_20260817_20260818.csv", frozen_0817_rows, frozen_0817_fields)
    # Never rewrite an existing frozen prediction file; preserve its exact
    # bytes and metadata as the pre-actual baseline.
    if not frozen_two_way_path.exists():
        write_csv(frozen_two_way_path, frozen_prediction_rows, frozen_prediction_fields)
    write_csv(out_dir / "next_phase_forward_validation_20260816_20260817.csv", frozen_two_way_rows, frozen_two_way_fields)
    write_csv(out_dir / "phase_transformation_forward_20260816_20260817.csv", frozen_two_way_transformations, transformation_fields)
    validate_reconstruction()
    validate_daily_reconstruction(daily, components)
    # build_html_v4 contains the complete single dashboard script, including the
    # as-of/regime panels and the playback controls. Keep the static controls in
    # the HTML and avoid stacking a second initializer on top of it.
    html = add_pages_navigation(build_html_v4(machine, rows, components, daily, forward_validation, next_phase_rows, transformation_rows + forward_transformation_rows, frozen_prediction_rows, frozen_two_way_rows, frozen_probability, frozen_0817_rows))
    (out_dir / "fft_reconstruction.html").write_text(html, encoding="utf-8")
    pages_machine_dir = PAGES_OUTPUT_ROOT / machine
    pages_machine_dir.mkdir(parents=True, exist_ok=True)
    pages_machine_path = pages_machine_dir / "index.html"
    pages_machine_path.write_text(html, encoding="utf-8")
    # The multi-machine Pages landing page is maintained independently; this
    # research run must not rewrite its cards or links.
    pages_index_path = PAGES_OUTPUT_ROOT / "index.html"
    print(f"pages_output={pages_machine_path}")
    print(f"pages_index={pages_index_path}")
    print(f"machine={machine} rows={len(rows)} period={rows[0]['date']}..{rows[-1]['date']}")
    for component in components:
        print(f"rank={component['rank']} period_days={component['period_days']:.3f} amplitude={component['amplitude']:.3f} relative_power={component['relative_power']:.4f}")
    print(f"output={out_dir}")
    return out_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Research-only daily OHLC FFT reconstruction")
    parser.add_argument("--machine", default="075", help="machine number, default: 075")
    args = parser.parse_args()
    run(args.machine)


if __name__ == "__main__":
    main()
