"""Downloadable analysis report (Markdown). Pure function: easy to test and to convert later."""
from __future__ import annotations

from datetime import datetime, timezone

from .crops import CropDataset
from .pipeline import EnvData
from .recommend import RecommendationSet, format_markdown


def _fmt(v, nd=1):
    if v is None:
        return "n/a"
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return str(v)


def _table(headers: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c).replace("|", "/") for c in r) + " |" for r in rows]
    return out


def _source_block(title: str, meta: dict) -> list[str]:
    lines = [f"### {title}", ""]
    for key, label in (("source", "Source"), ("product", "Product"), ("product_description", "Description"),
                       ("spatial_resolution", "Spatial resolution"), ("temporal_resolution", "Temporal resolution"),
                       ("start", "Period start"), ("end", "Period end"), ("retrieved_utc", "Retrieved (UTC)")):
        if meta.get(key):
            lines.append(f"- **{label}:** {meta[key]}")
    lines.append(f"- **Served from cache:** {meta.get('served_from_cache')}"
                 + (" (STALE: live request failed)" if meta.get("stale_cache") else ""))
    if meta.get("units"):
        lines.append("- **Units:** " + ", ".join(f"{k} = {v}" for k, v in meta["units"].items()))
    lines.append("- **Limitations:**")
    lines += [f"  - {x}" for x in meta.get("limitations", [])]
    return lines + [""]


def build_report(env: EnvData, rs: RecommendationSet, ds: CropDataset, location_name: str | None = None) -> str:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    L = ["# AgroIntel analysis report", "",
         f"Generated (UTC): {now}",
         f"Location: {location_name + ' ' if location_name else ''}({env.lat:.4f}, {env.lon:.4f})",
         f"Climate history requested: {env.years} years", "",
         "> NASA POWER values are model/satellite-derived grid-cell data and SMAP Level 4 soil moisture is a "
         "land-model estimate. Neither is a farm-level sensor reading. Recommendations rank options using "
         "transparent rules; they are not yield forecasts.", "",
         "## 1. Data sources and limitations", ""]
    L += _source_block("NASA POWER", env.power_meta)
    if env.smap_meta:
        L += _source_block("NASA SMAP (via AppEEARS)", env.smap_meta)
    else:
        L += ["### NASA SMAP", "", "Not used in this analysis (not requested, unavailable, or failed). "
              "Soil-moisture indicators are therefore missing.", ""]
    if env.warnings:
        L += ["### Data warnings", ""] + [f"- {w}" for w in env.warnings] + [""]

    L += ["## 2. Data cleaning summary", ""]
    pr = env.power_report
    L += [f"- POWER: {pr['n_rows_raw']} raw rows, {pr['n_rows_clean']} after cleaning; "
          f"{pr['duplicates_removed']} duplicate(s) removed; fill values set to missing: {pr['fill_values_to_nan']}; "
          f"out-of-range set to missing: {pr['out_of_range_to_nan']}; short gaps interpolated (not rainfall): "
          f"{pr['interpolated_cells']}; outliers FLAGGED, not removed: {pr['outliers_flagged']}."]
    if env.smap_report:
        sr = env.smap_report
        L.append(f"- SMAP: {sr['n_rows_raw']} raw sub-daily rows aggregated to {sr['n_rows_clean']} daily rows; "
                 f"fill values: {sr['fill_values_to_nan']}; out-of-range: {sr['out_of_range_to_nan']}; "
                 f"outliers flagged: {sr['outliers_flagged']}; remaining missing days: {sr['remaining_missing']}.")
    L += ["- Raw observations and cleaned data are kept separate; imputed values and outliers carry flag columns.", ""]

    L += ["## 3. Environmental stress indicators", ""]
    rows = []
    for i in env.stress.indicators:
        rows.append([i.label, i.window, _fmt(i.value, 2) + f" {i.unit}" if i.value is not None else "n/a",
                     _fmt(i.baseline_median, 2), _fmt(i.percentile, 0), i.classification, i.kind, i.n_baseline_years])
    L += _table(["Indicator", "Window", "Value", "Baseline median", "Percentile", "Class", "Basis", "Baseline years"], rows)
    L += ["", "### Composite flags", ""]
    for k, v in env.stress.composites.items():
        L.append(f"- **{k.replace('_', ' ').title()}:** {v['classification']} ({v['kind']}). Rule: {v['rule']}")
    L += ["", "### Long-term trends (indicative)", ""]
    L += _table(["Variable", "Years", "Slope per decade", "p-value", "Direction"],
                [[t["variable"], t["n_years"], _fmt(t["slope_per_decade"], 2), _fmt(t["p_value"], 3), t["direction"]]
                 for t in env.stress.trends])
    if env.stress.warnings:
        L += [""] + [f"- Warning: {w}" for w in env.stress.warnings]

    body = format_markdown(rs).split("\n")
    body = [("### " + ln[3:]) if ln.startswith("## ") else ln for ln in body[1:]]       # demote headings
    L += ["", "## 4. Crop-rotation recommendations", ""] + body

    L += ["", "## 5. What the crop dataset does not yet cover", ""] + [f"- {x}" for x in ds.not_yet_covered]
    L += ["", "## 6. Weights used", "", "Normalised over available components: "
          + ", ".join(f"{k} {v:.2f}" for k, v in rs.weights_used.items()) if rs.weights_used else ""]
    return "\n".join(L) + "\n"
