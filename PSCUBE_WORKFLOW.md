# P'sCUBE daily workflow

This file is the fixed runbook for morning/daytime acquisition. The user can request the workflow with only the target date and save directory; do not ask them to paste the full procedure again.

## Shared rules

- Read enabled machines from `pscube_targets.json` (currently 72 machines).
- Do not print the full machine list or per-machine result objects.
- Return only counts and machine numbers with an error or missing artifact.
- Keep page waits, history expansion, per-machine delay, and SVG checks unchanged.
- Use `tools/pscube_capture_iab.mjs` and `capturePendingBatch()` so interrupted runs resume only missing machines.
- Keep each browser call below the tool timeout: morning batch size 20; daytime batch size 36.
- Validate the complete set once after all batches. Do not repeatedly poll file counts while capture is running.
- Do not commit CSV, overlays, captured HTML, or screenshots.
- Use scoped Git status/diff commands and stage only workflow-related outputs.

## Morning

- Viewport: `590x1000`, producing a `575x975` viewport screenshot.
- `captureChart: true`, `chartClip: false`, `requireChartSvg: true`.
- The event CSV uses HTML history.
- `daily_ohlc` uses chart/SVG only; never derive OHLC from HTML event times.
- After capture validation succeeds, run steps 2-10 through the compact wrapper:
  `python tools/run_pscube_morning_pipeline.py --date YYYYMMDD --capture-root captures/pscube/YYYYMMDD/morning`
- The wrapper calls the existing scripts without changing their calculations or order and only prints a compact JSON summary.
- The same summary is saved as `pipeline_summary.json` under the capture root so it can be checked without rerunning the pipeline.
- It also updates `intraday_hit_regime.html`, tracking daily hit density, adjusted hit quality, and the concentration/diffusion/rest regime.
- Run the external Cycle Watch folder command from the summary separately because it writes outside the repository.

## Daytime

- Capture HTML for all enabled machines with the existing delay and history expansion.
- `captureChart: false`, `requireChartSvg: false` for the initial all-machine pass.
- After all HTML passes validation, run the compact candidate wrapper:
  `python tools/run_pscube_daytime_pipeline.py --date YYYYMMDD --capture-root captures/pscube/YYYYMMDD/daytime`
- The wrapper determines intraday cycle-hit candidates from HTML and updates PropagationLookup and the combined-signal candidate list.
- It saves `daytime_pipeline_summary.json` under the capture root and returns only the hit candidate machine numbers and related commit files.
- Capture `575x975` charts only for hit candidates when required.
- Do not settle daily OHLC from daytime data.
- Commit only the related daytime hit data and generated pages/reports. Do not commit captured HTML or candidate chart screenshots.

## Short requests

Morning: `いつものmorning。対象日: YYYYMMDD、保存先: captures/pscube/YYYYMMDD/morning`

Daytime: `いつものdaytime。対象日: YYYYMMDD、保存先: captures/pscube/YYYYMMDD/daytime`
