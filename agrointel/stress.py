"""Environmental stress detection (Stage 3).

Input : the merged clean frame (POWER columns, optionally SM_SURFACE / SM_ROOTZONE).
Output: indicators with value, baseline, percentile, class and a clear label saying
        whether it is derived from NASA POWER grid data or from the SMAP L4 MODEL estimate.

Method (deliberately simple and interpretable):
  * For each indicator, take the most recent window (e.g. last 30 days).
  * Build a baseline from the SAME calendar window in every other year of the record.
    The current year is excluded so an event cannot dilute its own baseline.
  * Report the percentile rank of the current value in that baseline and a class.
Nothing here is an official drought index (SPI, PDSI, USDM). The drought and excess
moisture flags are transparent heuristics built from those percentiles.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from . import config

INSUFFICIENT = "Insufficient data"
KIND_POWER = "Derived indicator (NASA POWER grid data)"
KIND_SMAP = "Derived indicator (SMAP L4 model estimate)"
KIND_BOTH = "Heuristic combining the indicators above"

RAIN_LABELS = ["Very dry", "Dry", "Near normal", "Wet", "Very wet"]
TEMP_LABELS = ["Much cooler than normal", "Cooler than normal", "Near normal",
               "Warmer than normal", "Much warmer than normal"]
SOIL_LABELS = ["Severe deficit", "Deficit", "Near normal", "Moist", "Very wet (excess)"]


@dataclass
class Indicator:
    key: str
    label: str
    value: float | None
    unit: str
    window: str
    as_of: date | None
    baseline_median: float | None
    anomaly: float | None
    percentile: float | None
    classification: str
    kind: str
    n_baseline_years: int
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StressResult:
    indicators: list[Indicator] = field(default_factory=list)
    composites: dict = field(default_factory=dict)   # drought / excess_moisture / heat
    trends: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def get(self, key: str) -> Indicator | None:
        return next((i for i in self.indicators if i.key == key), None)

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([i.to_dict() for i in self.indicators]).set_index("key")


# ------------------------------------------------------------------ window helpers
def _anchor(year: int, month: int, day: int) -> pd.Timestamp:
    try:
        return pd.Timestamp(year, month, day)
    except ValueError:                       # 29 Feb in a non-leap year
        return pd.Timestamp(year, month, 28)


def _longest_run(mask: np.ndarray) -> int:
    best = cur = 0
    for m in mask:
        cur = cur + 1 if m else 0
        best = max(best, cur)
    return best


def _window_stat(s: pd.Series, end: pd.Timestamp, days: int, how: str,
                 cfg: config.StressConfig) -> float:
    """Statistic of `s` over the `days` days ending at `end`. NaN if too many gaps."""
    w = s.loc[end - pd.Timedelta(days=days - 1): end]
    n = int(w.notna().sum())
    if n < cfg.min_window_coverage * days:
        return np.nan
    if how == "sum":
        return float(w.sum() * days / n)      # scale for the few missing days
    if how == "mean":
        return float(w.mean())
    if how == "maxdry":
        return float(_longest_run((w < cfg.dry_day_mm).to_numpy()))
    raise ValueError(how)


def _baseline(s: pd.Series, end: pd.Timestamp, days: int, how: str,
              cfg: config.StressConfig) -> np.ndarray:
    first, last = s.first_valid_index(), s.last_valid_index()
    vals = []
    for y in sorted(set(s.index.year)):
        if y == end.year:                     # exclude the current year
            continue
        e = _anchor(y, end.month, end.day)
        if first is None or e > last or e - pd.Timedelta(days=days - 1) < first:
            continue
        v = _window_stat(s, e, days, how, cfg)
        if not np.isnan(v):
            vals.append(v)
    return np.array(vals, dtype=float)


def percentile_rank(value: float, dist: np.ndarray) -> float:
    if len(dist) == 0 or value is None or np.isnan(value):
        return np.nan
    return float(100.0 * ((dist < value).sum() + 0.5 * (dist == value).sum()) / len(dist))


def _classify_pct(p: float, labels: list[str], cuts: tuple) -> str:
    if np.isnan(p):
        return INSUFFICIENT
    for cut, lab in zip(cuts, labels):
        if p <= cut:
            return lab
    return labels[-1] if p > cuts[-1] else labels[2]


def _clean(x):
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else x


# ------------------------------------------------------------------ indicator builders
def _pct_indicator(key, label, s, as_of, days, how, unit, labels, kind, min_years,
                   cfg, anomaly_mode="diff", note="") -> Indicator:
    if as_of is None:
        return Indicator(key, label, None, unit, f"last {days} d", None, None, None, None,
                         INSUFFICIENT, kind, 0, "No data available.")
    value = _window_stat(s, as_of, days, how, cfg)
    base = _baseline(s, as_of, days, how, cfg)
    n = len(base)
    if np.isnan(value) or n < min_years:
        why = (f"Window has too many gaps." if np.isnan(value) else
               f"Only {n} baseline year(s); need {min_years}.")
        return Indicator(key, label, _clean(value), unit, f"last {days} d", as_of.date(), None, None,
                         None, INSUFFICIENT, kind, n, (note + " " + why).strip())
    med = float(np.median(base))
    anom = (value - med) / med * 100 if anomaly_mode == "pct" and med > 0 else value - med
    p = percentile_rank(value, base)
    return Indicator(key, label, value, unit, f"last {days} d", as_of.date(), med,
                     float(anom), p, _classify_pct(p, labels, cfg_cuts(cfg)), kind, n, note)


def cfg_cuts(cfg):
    return cfg.pct_cuts


def _heat_indicator(power: pd.DataFrame, as_of, cfg) -> Indicator:
    days = cfg.temp_window_days
    key, label, unit = "heat_days", f"Heat-stress days (Tmax ≥ {cfg.heat_threshold_c:g} °C)", "days"
    note = "Generic threshold; crop-specific thresholds are applied in the recommendation stage."
    if as_of is None or "T2M_MAX" not in power:
        return Indicator(key, label, None, unit, f"last {days} d", None, None, None, None,
                         INSUFFICIENT, KIND_POWER, 0, "T2M_MAX unavailable.")
    tmax = power["T2M_MAX"]
    hot = (tmax >= cfg.heat_threshold_c).astype(float).where(tmax.notna())
    value = _window_stat(hot, as_of, days, "sum", cfg)
    base = _baseline(hot, as_of, days, "sum", cfg)
    if np.isnan(value) or len(base) < cfg.min_baseline_years:
        return Indicator(key, label, _clean(value), unit, f"last {days} d", as_of.date(), None, None,
                         None, INSUFFICIENT, KIND_POWER, len(base), note)
    v = int(round(value))
    mild, mod, sev = cfg.heat_day_cuts
    cls = ("No heat stress" if v < mild else "Mild" if v < mod else
           "Moderate" if v < sev else "Severe")
    med = float(np.median(base))
    return Indicator(key, label, v, unit, f"last {days} d", as_of.date(), med, v - med,
                     percentile_rank(value, base), cls, KIND_POWER, len(base), note)


def _dry_spell_indicator(power: pd.DataFrame, as_of, cfg) -> Indicator:
    days = cfg.temp_window_days
    key, label, unit = "dry_spell", f"Longest dry spell (rain < {cfg.dry_day_mm:g} mm/day)", "days"
    if as_of is None or "PRECTOTCORR" not in power:
        return Indicator(key, label, None, unit, f"last {days} d", None, None, None, None,
                         INSUFFICIENT, KIND_POWER, 0, "Rainfall unavailable.")
    rain = power["PRECTOTCORR"]
    value = _window_stat(rain, as_of, days, "maxdry", cfg)
    base = _baseline(rain, as_of, days, "maxdry", cfg)
    if np.isnan(value) or len(base) < cfg.min_baseline_years:
        return Indicator(key, label, _clean(value), unit, f"last {days} d", as_of.date(), None, None,
                         None, INSUFFICIENT, KIND_POWER, len(base))
    a, b, c = cfg.dry_spell_cuts_days
    cls = ("Normal" if value < a else "Extended" if value < b else
           "Long" if value < c else "Very long")
    med = float(np.median(base))
    return Indicator(key, label, int(value), unit, f"last {days} d", as_of.date(), med, value - med,
                     percentile_rank(value, base), cls, KIND_POWER, len(base))


# ------------------------------------------------------------------ composites
def _low(ind: Indicator | None, cut: float) -> bool | None:
    return None if ind is None or ind.percentile is None or np.isnan(ind.percentile) else ind.percentile <= cut


def _high(ind: Indicator | None, cut: float) -> bool | None:
    return None if ind is None or ind.percentile is None or np.isnan(ind.percentile) else ind.percentile >= cut


def _composites(res: StressResult, cfg) -> dict:
    r90, r30 = res.get("rain_90d"), res.get("rain_30d")
    sroot = res.get("soil_rootzone")
    lo, lo2 = cfg.pct_cuts[1], cfg.pct_cuts[0] * 2          # 20th and 10th percentile
    rain_dry, soil_dry = _low(r90, lo), _low(sroot, lo)
    rain_vdry, soil_vdry = _low(r90, lo2), _low(sroot, lo2)

    if rain_dry is None:
        drought = "Insufficient data"
    elif soil_dry is None:
        drought = ("Possible meteorological dryness (soil moisture unavailable)" if rain_dry
                   else "No dry signal in rainfall (soil moisture unavailable)")
    elif rain_dry and soil_dry:
        drought = "Probable severe drought" if (rain_vdry and soil_vdry) else "Probable drought"
    elif rain_dry:
        drought = "Dry signal: rainfall only"
    elif soil_dry:
        drought = "Dry signal: soil moisture only"
    else:
        drought = "No drought signal"

    rain_wet, soil_wet = _high(r30, cfg.excess_pct), _high(sroot, cfg.excess_pct)
    if rain_wet is None and soil_wet is None:
        excess = "Insufficient data"
    elif rain_wet and soil_wet:
        excess = "Probable excess moisture (waterlogging risk)"
    elif rain_wet or soil_wet:
        excess = "Possible excess moisture"
    else:
        excess = "No excess-moisture signal"

    heat = res.get("heat_days")
    return {
        "drought": {"classification": drought, "kind": KIND_BOTH,
                    "rule": f"rainfall (90 d) and root-zone soil moisture at or below the "
                            f"{lo:g}th percentile of their own history; both at or below {lo2:g}th = severe. "
                            "A transparent heuristic, not SPI/PDSI/USDM."},
        "excess_moisture": {"classification": excess, "kind": KIND_BOTH,
                            "rule": f"30-day rainfall or root-zone soil moisture at or above the "
                                    f"{cfg.excess_pct:g}th percentile; both = probable."},
        "heat": {"classification": heat.classification if heat else INSUFFICIENT, "kind": KIND_POWER,
                 "rule": f"days with Tmax ≥ {cfg.heat_threshold_c:g} °C in the last "
                         f"{cfg.temp_window_days} days."},
    }


# ------------------------------------------------------------------ trends
def mann_kendall(y: np.ndarray) -> tuple[float, float]:
    """Mann–Kendall trend test (tie-corrected). Returns (z, two-sided p)."""
    y = np.asarray(y, dtype=float)
    n = len(y)
    iu = np.triu_indices(n, 1)
    s = float(np.sign(y[None, :] - y[:, None])[iu].sum())
    _, counts = np.unique(y, return_counts=True)
    var = (n * (n - 1) * (2 * n + 5) - (counts * (counts - 1) * (2 * counts + 5)).sum()) / 18.0
    if var <= 0:
        return 0.0, 1.0
    z = (s - 1) / math.sqrt(var) if s > 0 else (s + 1) / math.sqrt(var) if s < 0 else 0.0
    return z, math.erfc(abs(z) / math.sqrt(2))


def theil_sen(x: np.ndarray, y: np.ndarray) -> float:
    iu = np.triu_indices(len(x), 1)
    dx = (x[None, :] - x[:, None])[iu]
    dy = (y[None, :] - y[:, None])[iu]
    return float(np.median(dy / dx))


def _annual(power: pd.DataFrame, col: str, how: str) -> pd.Series:
    s = power[col]
    g = s.groupby(s.index.year)
    ok = g.count() >= 0.9 * g.size()          # keep (nearly) complete years only
    ok &= g.size() >= 360
    vals = g.sum() if how == "sum" else g.mean()
    return vals[ok]


def compute_trends(power: pd.DataFrame, cfg: config.StressConfig) -> list[dict]:
    out = []
    for col, how, label, unit in (("PRECTOTCORR", "sum", "Annual rainfall", "mm"),
                                  ("T2M", "mean", "Annual mean temperature", "°C")):
        if col not in power:
            continue
        a = _annual(power, col, how).dropna()
        if len(a) < cfg.trend_min_years:
            out.append({"variable": label, "unit": unit, "n_years": len(a), "slope_per_decade": None,
                        "p_value": None, "direction": INSUFFICIENT,
                        "note": f"Need at least {cfg.trend_min_years} complete years."})
            continue
        z, p = mann_kendall(a.to_numpy())
        slope = theil_sen(a.index.to_numpy(dtype=float), a.to_numpy()) * 10
        direction = ("No significant trend" if p >= cfg.trend_alpha
                     else "Increasing" if slope > 0 else "Decreasing")
        out.append({"variable": label, "unit": unit, "n_years": len(a),
                    "first_year": int(a.index.min()), "last_year": int(a.index.max()),
                    "slope_per_decade": slope, "p_value": p, "direction": direction,
                    "note": "Mann–Kendall test with Theil–Sen slope on annual values. Indicative only: "
                            "short records are dominated by natural variability."})
    return out


# ------------------------------------------------------------------ monthly anomalies (for charts)
def monthly_anomalies(merged: pd.DataFrame, min_coverage: float = 0.8) -> pd.DataFrame:
    """Monthly totals/means vs the median of the same calendar month across all years."""
    frames = {}
    spec = [("rain_mm", "PRECTOTCORR", "sum"), ("t_mean_c", "T2M", "mean"),
            ("sm_root", "SM_ROOTZONE", "mean"), ("sm_surface", "SM_SURFACE", "mean")]
    for name, col, how in spec:
        if col not in merged:
            continue
        s = merged[col]
        rs = s.resample("MS")
        val = rs.sum(min_count=1) if how == "sum" else rs.mean()
        cov = rs.count() / rs.size()
        val = val.where(cov >= min_coverage)
        clim = val.groupby(val.index.month).transform("median")
        frames[name] = val
        frames[f"{name}_clim"] = clim
        frames[f"{name}_anom"] = (val - clim) / clim * 100 if name == "rain_mm" else val - clim
    return pd.DataFrame(frames)


# ------------------------------------------------------------------ main entry
def analyze_stress(merged: pd.DataFrame, cfg: config.StressConfig = config.STRESS,
                   as_of: date | str | None = None) -> StressResult:
    res = StressResult()
    df = merged.sort_index()

    def last_valid(col):
        if col not in df:
            return None
        lv = df[col].last_valid_index()
        if lv is None:
            return None
        if as_of is not None:
            lv = min(lv, pd.Timestamp(as_of))
        return lv

    as_rain, as_temp = last_valid("PRECTOTCORR"), last_valid("T2M_MAX")
    as_sroot, as_ssurf = last_valid("SM_ROOTZONE"), last_valid("SM_SURFACE")
    if as_rain is None:
        res.warnings.append("No rainfall data: stress analysis not possible.")

    P = lambda col: df[col] if col in df else pd.Series(dtype=float)
    for d in cfg.rain_windows_days:
        res.indicators.append(_pct_indicator(
            f"rain_{d}d", f"Rainfall, last {d} days", P("PRECTOTCORR"), as_rain, d, "sum", "mm",
            RAIN_LABELS, KIND_POWER, cfg.min_baseline_years, cfg, anomaly_mode="pct",
            note="Anomaly is % difference from the baseline median."))
    res.indicators.append(_pct_indicator(
        "tmax_30d", f"Mean daily max temperature, last {cfg.temp_window_days} days", P("T2M_MAX"),
        as_temp, cfg.temp_window_days, "mean", "°C", TEMP_LABELS, KIND_POWER,
        cfg.min_baseline_years, cfg, note="Anomaly in °C versus baseline median."))
    res.indicators.append(_heat_indicator(df, as_temp, cfg))
    res.indicators.append(_dry_spell_indicator(df, as_rain, cfg))
    res.indicators.append(_pct_indicator(
        "soil_surface", f"Surface soil moisture, last {cfg.soil_surface_window_days} days",
        P("SM_SURFACE"), as_ssurf, cfg.soil_surface_window_days, "mean", "m³/m³", SOIL_LABELS,
        KIND_SMAP, cfg.smap_min_baseline_years, cfg,
        note="Model estimate on a 9 km cell. SMAP record starts 2015, so the baseline is short."))
    res.indicators.append(_pct_indicator(
        "soil_rootzone", f"Root-zone soil moisture, last {cfg.soil_root_window_days} days",
        P("SM_ROOTZONE"), as_sroot, cfg.soil_root_window_days, "mean", "m³/m³", SOIL_LABELS,
        KIND_SMAP, cfg.smap_min_baseline_years, cfg,
        note="Model estimate on a 9 km cell. SMAP record starts 2015, so the baseline is short."))

    for key in ("soil_surface", "soil_rootzone"):
        ind = res.get(key)
        if ind and ind.classification == INSUFFICIENT and ind.as_of is None:
            res.warnings.append("SMAP soil moisture not available: soil-based flags are skipped.")
            break
    rainy = res.get("rain_90d")
    if rainy and rainy.n_baseline_years and rainy.n_baseline_years < 25:
        res.warnings.append(f"Rainfall baseline uses {rainy.n_baseline_years} other years; the usual "
                            "climate-normal convention is 30 years. Fetch a longer POWER history "
                            "(available from 1981) for a more robust baseline.")
    res.composites = _composites(res, cfg)
    res.trends = compute_trends(df, cfg) if "PRECTOTCORR" in df else []
    return res
