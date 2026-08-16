from __future__ import annotations

import argparse
import csv
import html
import itertools
import json
import math
from pathlib import Path
from typing import Iterable

from daily_ohlc import load_chart_daily_ohlc


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_ROOT = ROOT / "wave_lab" / "output"
PAGES_OUTPUT_ROOT = ROOT / "docs" / "wave_lab"

# Analysis settings are intentionally explicit and easy to change for later experiments.
TOP_COMPONENTS = 3
MIN_PERIOD_DAYS = 2.0
MAX_PERIOD_FRACTION_OF_DATA = 0.80
PHASE_BIN_WIDTH_DEGREES = 45.0
DIRECTION_SLOPE_EPSILON_FRACTION = 0.02
REGIME_CUTOFF_DATE = "2026-08-15"
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


def load_machine_rows(machine: str) -> list[dict]:
    daily, _meta = load_chart_daily_ohlc({machine})
    rows = []
    for date, values in sorted(daily.get(str(int(machine)), {}).items()):
        rows.append({
            "date": normalize_date(date),
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
    amplitude = abs(float(component["amplitude"]))
    value = float(row[f"wave{wave}_value"])
    phase_degrees = float(row[f"wave{wave}_phase"])
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
slider.addEventListener('input',()=>render(Number(slider.value)));document.getElementById('prev').addEventListener('click',()=>{slider.value=String(Math.max(0,Number(slider.value)-1));render(Number(slider.value));});document.getElementById('next').addEventListener('click',()=>{slider.value=String(Math.min(rows.length-1,Number(slider.value)+1));render(Number(slider.value));});render(Number(slider.value));
</script></body></html>'''
    extra_script = r'''<script>
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
function drawRegimeHistory(){const svg=document.getElementById('periodRegimeChart');if(!svg||!regimeRows.length)return;const left=70,width=1080,top=18,periodH=155,jointTop=188,jointH=62,bandTop=278,bandH=28,xAtRegime=i=>left+width*i/Math.max(1,regimeRows.length-1);const valid=regimeRows.filter(r=>Number.isFinite(Number(r.long_period)));const allPeriods=valid.flatMap(r=>[Number(r.long_period),Number(r.mid_period),Number(r.short_period)]),lo=Math.min(...allPeriods),hi=Math.max(...allPeriods),sy=v=>top+periodH*(hi-v)/(hi-lo||1);const path=key=>{const pts=regimeRows.map((r,i)=>Number.isFinite(Number(r[key]))?`${xAtRegime(i).toFixed(2)},${sy(Number(r[key])).toFixed(2)}`:null).filter(Boolean);return pts.length?'M '+pts.join(' L '):'';};let out='<line class="svg-axis" x1="'+left+'" y1="'+top+'" x2="'+(left+width)+'" y2="'+top+'"/><line class="svg-axis" x1="'+left+'" y1="'+(top+periodH)+'" x2="'+(left+width)+'" y2="'+(top+periodH)+'"/>';for(const [key,label,color] of [['long_period','LONG','var(--long)'],['mid_period','MID','var(--mid)'],['short_period','SHORT','var(--short)']]){const d=path(key);if(d)out+='<path d="'+d+'" fill="none" stroke="'+color+'" stroke-width="'+(label==='LONG'?3:2)+'"/>';out+='<text class="svg-label" x="8" y="'+(top+20+(['LONG','MID','SHORT'].indexOf(label)*18))+'">'+label+'</text>';}out+='<text class="svg-label" x="8" y="'+(jointTop+18)+'">joint repeat</text>';const jointValues=regimeRows.filter(r=>Number.isFinite(Number(r.joint_repeat_period))).map(r=>Number(r.joint_repeat_period)),jlo=Math.min(...jointValues),jhi=Math.max(...jointValues),jy=v=>jointTop+jointH*(jhi-v)/(jhi-jlo||1);const jointPts=regimeRows.map((r,i)=>Number.isFinite(Number(r.joint_repeat_period))?`${xAtRegime(i).toFixed(2)},${jy(Number(r.joint_repeat_period)).toFixed(2)}`:null).filter(Boolean);if(jointPts.length)out+='<path d="M '+jointPts.join(' L ')+'" fill="none" stroke="var(--cursor)" stroke-width="3"/>';for(const r of regimeRows){const i=regimeRows.indexOf(r),fill=r.regime==='STABLE'?'var(--mid)':r.regime==='TRANSITION'?'var(--cursor)':r.regime==='UNSTABLE'?'var(--bull)':'var(--line)';out+='<rect x="'+(xAtRegime(i)-width/regimeRows.length/2)+'" y="'+bandTop+'" width="'+(width/regimeRows.length+1)+'" height="'+bandH+'" fill="'+fill+'" opacity=".6"/><text class="svg-label" x="'+xAtRegime(i)+'" y="'+(bandTop+19)+'" text-anchor="middle" font-size="10">'+(i===0||i===Math.floor((regimeRows.length-1)/2)||i===regimeRows.length-1?r.regime:'')+'</text>';if(r.n_fft_changed)out+='<line class="svg-cursor" x1="'+xAtRegime(i)+'" y1="'+top+'" x2="'+xAtRegime(i)+'" y2="'+(bandTop+bandH)+'"/>';}const cursor=xAtRegime(uiIndex);out+='<line class="svg-cursor" x1="'+cursor+'" y1="'+top+'" x2="'+cursor+'" y2="'+(bandTop+bandH)+'"/>';for(const i of [0,Math.floor((regimeRows.length-1)/2),regimeRows.length-1])out+='<text class="svg-label" text-anchor="middle" x="'+xAtRegime(i)+'" y="'+(bandTop+bandH+22)+'">'+esc(regimeRows[i].date)+'</text>';svg.innerHTML=out;const selected=regimeRows[uiIndex];document.getElementById('periodRegimeSummary').textContent=selected?'selected '+selected.date+' / regime='+selected.regime+' / n_fft='+(selected.n_fft||'-')+' / joint_repeat='+(selected.joint_repeat_period||'-')+' observations':'-';}
function uiRate(v){return v===''?'-':Number(v).toFixed(1)+'%';}
function uiDetail(){const r=rows[uiIndex],same=r.bullish?'BULLISH':'BEARISH',next=r.next_day_bullish===null?'N/A':(r.next_day_bullish?'BULLISH':'BEARISH'),cell=(l,v,c='')=>`<div class="metric"><span class="label">${l}</span><span class="value ${c}">${esc(v)}</span></div>`;document.getElementById('summary').innerHTML=cell('date',r.date,'selected')+cell('Open',r.open)+cell('High',r.high)+cell('Low',r.low)+cell('Close',r.close)+cell('same-day',same,same==='BULLISH'?'bull':'')+cell('next observation',next,next==='BULLISH'?'next':'');uiSliderLabel.textContent=`${r.date} (${uiIndex+1}/${rows.length})`;const dirs=roles.map(role=>{const w=roleIndex[role];return `<tr><td class="role-${role.toLowerCase()}">${role}</td><td>${Number(r[`wave${w}_phase`]).toFixed(1)} deg</td><td>${esc(r[`wave${w}_direction`])}</td><td>${r[`wave${w}_up`]?'UP':'DOWN'}</td><td>${Number(r[`wave${w}_value`]).toFixed(1)}</td></tr>`}).join('');const comps=components.map(c=>`<tr><td class="role-${c.role.toLowerCase()}">${c.role}</td><td>${c.rank}</td><td>${Number(c.frequency).toFixed(5)}</td><td>${Number(c.period_days).toFixed(3)}</td><td>${Number(c.amplitude).toFixed(1)}</td><td>${(Number(c.relative_power)*100).toFixed(1)}%</td></tr>`).join('');document.getElementById('info').innerHTML=`<div class="info-grid"><section><h3>Selected wave state</h3><table><thead><tr><th>role</th><th>phase</th><th>direction</th><th>UP/DOWN</th><th>value</th></tr></thead><tbody>${dirs}</tbody></table><p><b>pattern:</b> ${esc(r.wave_direction_pattern)}</p><p><b>combined:</b> ${Number(r.combined_wave).toFixed(1)}</p></section><section><h3>Components</h3><div class="table-wrap"><table><thead><tr><th>role</th><th>rank</th><th>freq</th><th>period</th><th>amp</th><th>power</th></tr></thead><tbody>${comps}</tbody></table></div></section></div>`;}
function uiDetail(){const r=rows[uiIndex],regime=regimeRows[uiIndex]||{},same=r.bullish?'BULLISH':'BEARISH',next=r.next_day_bullish===null?'N/A':(r.next_day_bullish?'BULLISH':'BEARISH');uiSliderLabel.textContent=`${r.date} (${uiIndex+1}/${rows.length})`;document.getElementById('summary').innerHTML='<div class="metric"><span class="label">date</span><span class="value selected">'+esc(r.date)+'</span></div><div class="metric"><span class="label">OHLC</span><span class="value">'+[r.open,r.high,r.low,r.close].map(esc).join(' / ')+'</span></div><div class="metric"><span class="label">same-day</span><span class="value">'+same+'</span></div><div class="metric"><span class="label">next observation</span><span class="value">'+next+'</span></div>';const dirs=roles.map(role=>{const w=roleIndex[role];return '<tr><td>'+role+'</td><td>'+Number(r['wave'+w+'_phase']).toFixed(1)+' deg</td><td>'+esc(r['wave'+w+'_direction'])+'</td><td>'+((r['wave'+w+'_up'])?'UP':'DOWN')+'</td><td>'+Number(r['wave'+w+'_value']).toFixed(1)+'</td></tr>';}).join('');document.getElementById('info').innerHTML='<h3>Selected wave state</h3><table><thead><tr><th>role</th><th>phase</th><th>direction</th><th>UP/DOWN</th><th>value</th></tr></thead><tbody>'+dirs+'</tbody></table><p><b>pattern:</b> '+esc(r.wave_direction_pattern)+'</p><p><b>combined:</b> '+Number(r.combined_wave).toFixed(1)+'</p><h3>PERIOD REGIME HISTORY</h3><p>regime='+esc(regime.regime||'-')+' / n_fft='+esc(regime.n_fft||'-')+' / stability='+(regime.period_stability_score===''?'-':Number(regime.period_stability_score).toFixed(3))+'</p><p>LONG='+(regime.long_period?Number(regime.long_period).toFixed(3):'-')+' / MID='+(regime.mid_period?Number(regime.mid_period).toFixed(3):'-')+' / SHORT='+(regime.short_period?Number(regime.short_period).toFixed(3):'-')+'</p><p>component reorder='+(regime.component_reorder?'YES':'NO')+' / joint repeat='+(regime.joint_repeat_period||'-')+' obs / stable count='+(regime.joint_repeat_stable_count||'-')+'</p><p class="tiny">MIN history='+DATA.min_regime_observations+'; shift threshold='+Number(DATA.regime_shift_pct*100).toFixed(0)+'%.</p>';}
function updateView(index){uiIndex=Math.max(0,Math.min(rows.length-1,index));uiSlider.value=String(uiIndex);uiDrawMain();uiDrawPhase();drawRegimeHistory();uiDetail();}
function stopPlayback(){if(uiTimer!==null){clearInterval(uiTimer);uiTimer=null;}document.getElementById('uiPlay').textContent='Play';}
function advance(){if(uiIndex<rows.length-1){updateView(uiIndex+1);return true;}if(uiLoop.checked){updateView(0);return true;}stopPlayback();return false;}
function startPlayback(){if(uiTimer!==null)return;if(uiIndex>=rows.length-1&&!uiLoop.checked)return;document.getElementById('uiPlay').textContent='Playing';uiTimer=setInterval(advance,Number(uiSpeed.value));}
document.getElementById('uiPlay').addEventListener('click',startPlayback);document.getElementById('uiStop').addEventListener('click',stopPlayback);document.getElementById('uiPrev').addEventListener('click',()=>{stopPlayback();updateView(uiIndex-1);});document.getElementById('uiNext').addEventListener('click',()=>{stopPlayback();updateView(uiIndex+1);});uiSlider.addEventListener('input',()=>updateView(Number(uiSlider.value)));uiMode.addEventListener('change',()=>updateView(uiIndex));uiSpeed.addEventListener('change',()=>{if(uiTimer!==null){stopPlayback();startPlayback();}});updateView(uiIndex);
</script>'''
    return page.replace("__MACHINE__", machine).replace("__DATA__", json.dumps(payload, ensure_ascii=False, separators=(",", ":"))).replace("</script></body>", "</script>" + extra_script + "</body>")


def build_html_v4(machine: str, rows: list[dict], components: list[dict], daily: list[dict]) -> str:
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
<div class="controls"><button id="prev" type="button">Prev</button><button id="next" type="button">Next</button><label for="dateSlider">Observation</label><input id="dateSlider" type="range" min="0" max="41" value="41"><span id="sliderLabel" class="selected"></span></div>
<div id="summary" class="summary"></div>
<section class="panel chart-panel"><h2>OHLC and Fourier reconstructed waves</h2><div class="legend"><span><i class="swatch sw-long"></i>LONG</span><span><i class="swatch sw-mid"></i>MID</span><span><i class="swatch sw-short"></i>SHORT</span><span><i class="swatch sw-combined"></i>COMBINED</span><span><i class="swatch sw-cursor"></i>selected date</span><span class="bull">● same-day bullish</span><span class="next">&#9733; next-observation bullish</span></div><svg id="mainChart" viewBox="0 0 1200 650" role="img" aria-label="OHLC and reconstructed waves"></svg></section>
<div class="bottom"><section class="panel phase-panel"><h2>Phase space</h2><div class="tiny">0 deg = trough / 90 deg = rising / 180 deg = crest / 270 deg = falling. Faint points are all observations; bright points are selected.</div><svg id="phaseSpace" viewBox="0 0 600 390" role="img" aria-label="LONG MID SHORT phase space"></svg></section><section class="panel"><h2>Selected date and statistics</h2><div id="info"></div></section></div>
<p class="note">Frequency = cycles / observation, d = 1 observation, period = 1 / frequency. The full FFT uses all observations through the cutoff; n_fft is the next power of two and may change in as-of history. Units are observations, not calendar days. Faint future trails are display-only research context, not an as-of replay.</p>
</main><script>
const DATA=__DATA__, rows=DATA.rows, components=DATA.components, roles=['LONG','MID','SHORT'];
const roleIndex={}; components.forEach((c,i)=>roleIndex[c.role]=i+1);
const colors={LONG:'var(--long)',MID:'var(--mid)',SHORT:'var(--short)',COMBINED:'var(--combined)'};
const slider=document.getElementById('dateSlider'), sliderLabel=document.getElementById('sliderLabel'); slider.max=String(rows.length-1); slider.value=String(rows.length-1);
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
function render(i){drawMain(i);drawPhase(i);detail(i);}slider.addEventListener('input',()=>render(Number(slider.value)));document.getElementById('prev').addEventListener('click',()=>{slider.value=String(Math.max(0,Number(slider.value)-1));render(Number(slider.value));});document.getElementById('next').addEventListener('click',()=>{slider.value=String(Math.min(rows.length-1,Number(slider.value)+1));render(Number(slider.value));});render(Number(slider.value));
</script></body></html>'''
    return page.replace("__MACHINE__", machine).replace("__CUTOFF__", REGIME_CUTOFF_DATE).replace("__DATA__", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def add_interactive_ui(html: str) -> str:
    """Add the browser-only controls without changing analysis data or CSV output."""
    script = r'''<script>
(() => {
  "use strict";
  const start = () => {
    const controls = document.querySelector(".controls");
    const mainChart = document.getElementById("mainChart");
    const phaseSpace = document.getElementById("phaseSpace");
    if (!controls || !mainChart || !phaseSpace || !Array.isArray(rows) || !rows.length) {
      console.error("Wave Lab UI init failed: required element or dataset missing");
      return;
    }
    const convergenceRows = Array.isArray(DATA.convergence_rows) ? DATA.convergence_rows : [];
    const alignmentRows = Array.isArray(DATA.alignment_rows) ? DATA.alignment_rows : [];
    const regimeRows = Array.isArray(DATA.regime_rows) ? DATA.regime_rows : [];
    controls.innerHTML = '<button id="uiPrev" type="button">Prev</button>' +
      '<button id="uiPlay" type="button">Play</button>' +
      '<button id="uiStop" type="button">Stop</button>' +
      '<button id="uiNext" type="button">Next</button>' +
      '<label><input id="uiLoop" type="checkbox"> Loop</label>' +
      '<label>Speed <select id="uiSpeed"><option value="500">Fast</option>' +
      '<option value="1000" selected>Normal (1 observation/sec)</option>' +
      '<option value="2000">Slow</option></select></label>' +
      '<label>OHLC <select id="uiOhlcMode"><option value="zero">0 BASE</option>' +
      '<option value="connect">CLOSE CONNECT</option></select></label>' +
      '<input id="uiSlider" type="range" min="0" max="' + (rows.length - 1) +
      '" value="' + (rows.length - 1) + '"><span id="uiSliderLabel" class="selected"></span>' +
      '<span id="uiDebug" class="tiny" aria-live="polite"></span>';
    document.querySelector('main').insertAdjacentHTML('beforeend', '<section class="panel chart-panel" id="periodRegimePanel"><h2>PERIOD REGIME HISTORY</h2><div class="tiny">Expanding/as-of FFT: each date uses only observations through that date. STABLE / TRANSITION / UNSTABLE and n_fft changes are shown on the same observation axis.</div><svg id="periodRegimeChart" viewBox="0 0 1200 360" role="img" aria-label="Period Regime History"></svg><div id="periodRegimeSummary" class="tiny"></div></section>');
    const slider = document.getElementById("uiSlider");
    const label = document.getElementById("uiSliderLabel");
    const mode = document.getElementById("uiOhlcMode");
    const loop = document.getElementById("uiLoop");
    const speed = document.getElementById("uiSpeed");
    const debug = document.getElementById("uiDebug");
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
        if (full) out += '<path class="phase-full" d="' + full + '" fill="none" stroke="' + colors[role] + '" stroke-width="2" opacity=".18"/>';
        if (past) out += '<path class="phase-past" d="' + past + '" fill="none" stroke="' + colors[role] + '" stroke-width="3" opacity=".9"/>';
        const point = phasePoint(role, selectedIndex);
        if (point) out += '<circle class="phase-current" cx="' + point[0] + '" cy="' + point[1] +
          '" r="8" fill="' + colors[role] + '" stroke="var(--cursor)" stroke-width="2"/><text class="svg-label" x="' +
          (point[0] + 8) + '" y="' + (point[1] - 8) + '">' + role + '</text>';
      }
      const convergence = convergenceRows[selectedIndex];
      const alignment = alignmentRows[selectedIndex];
      if (convergence) {
        const triangle = [[convergence.long_x, convergence.long_y], [convergence.mid_x, convergence.mid_y], [convergence.short_x, convergence.short_y]];
        out += '<polygon points="' + triangle.map(point => point[0] + ',' + point[1]).join(' ') +
          '" fill="none" stroke="var(--cursor)" stroke-width="1.5" opacity=".75"/>';
        out += '<circle cx="' + convergence.centroid_x + '" cy="' + convergence.centroid_y +
          '" r="4" fill="var(--cursor)" stroke="var(--text)" stroke-width="1"/>';
        if (convergence.phase_convergence || (alignment && alignment.high_alignment)) {
          const label = convergence.phase_convergence && alignment && alignment.high_alignment ? 'CONVERGENCE + ALIGNMENT' :
            (convergence.phase_convergence ? 'CONVERGENCE' : 'ALIGNMENT');
          out += '<text class="svg-label" x="' + (convergence.centroid_x + 8) + '" y="' +
            (convergence.centroid_y + 16) + '">' + label + '</text>';
        }
      }
      out += '<text class="svg-label" x="278" y="45">180 crest</text><text class="svg-label" x="278" y="350">0 trough</text>';
      out += '<text class="svg-label" x="438" y="194">90 rising</text><text class="svg-label" x="55" y="194">270 falling</text>';
      phaseSpace.innerHTML = out;
    };
    const updateInfo = () => {
      const row = rows[selectedIndex];
      const convergence = convergenceRows[selectedIndex] || {};
      const alignment = alignmentRows[selectedIndex] || {};
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
    updateView(selectedIndex);
    console.info('Wave Lab UI ready', {rows: rows.length, phasePoints: document.querySelectorAll('#phaseSpace path').length});
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true}); else start();
})();
</script>'''
    return html.replace('</body></html>', script + '</body></html>')


def run(machine: str) -> Path:
    machine = parse_machine(machine)
    all_rows = load_machine_rows(machine)
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
    out_dir = OUTPUT_ROOT / machine
    out_dir.mkdir(parents=True, exist_ok=True)
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
    write_csv(out_dir / "period_regime_daily.csv", regime_rows, regime_fields)
    write_csv(out_dir / "period_regime_events.csv", regime_events, event_fields)
    write_csv(out_dir / "period_regime_stats.csv", period_regime_stats(regime_rows), regime_stat_fields)
    validate_reconstruction()
    validate_daily_reconstruction(daily, components)
    html = add_interactive_ui(build_html_v4(machine, rows, components, daily))
    (out_dir / "fft_reconstruction.html").write_text(html, encoding="utf-8")
    if machine == "075":
        PAGES_OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        (PAGES_OUTPUT_ROOT / "index.html").write_text(html, encoding="utf-8")
        print(f"pages_output={PAGES_OUTPUT_ROOT / 'index.html'}")
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
