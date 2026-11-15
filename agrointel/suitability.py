"""Crop suitability building blocks (Stage 5).

  * trapezoid()          EcoCrop-style 0..1 score: 1 inside the optimum, tapering to 0 at the absolute limits
  * SeasonWindow         a growing-season window from a regional calendar or inferred from rainfall
  * historical_season_stats()   season rainfall/temperature for every past year
  * crop_season_fit()    how suitable a crop would have been in that season, year by year
  * soil_fit()           optional match to user-supplied soil pH / texture / drainage

Assumptions (also repeated in every recommendation):
  - EcoCrop rainfall is labelled 'annual'; here it is compared with the TOTAL rainfall of the
    season window, a simplification that is flagged to the user.
  - The crop is assumed to be grown within the season window; the most limiting of
    temperature and rainfall sets the score (as in Recocrop).
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import config
from .crops import Crop

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def trapezoid(x, abs_min, opt_min, opt_max, abs_max) -> float:
    if x is None or (isinstance(x, float) and np.isnan(x)) or opt_min is None or opt_max is None:
        return float("nan")
    lo = opt_min if abs_min is None else abs_min
    hi = opt_max if abs_max is None else abs_max
    if x < lo or x > hi:
        return 0.0
    if x < opt_min:
        return (x - lo) / (opt_min - lo) if opt_min > lo else 1.0
    if x > opt_max:
        return (hi - x) / (hi - opt_max) if hi > opt_max else 1.0
    return 1.0


# ------------------------------------------------------------------ season windows
@dataclass
class SeasonWindow:
    id: str
    name: str
    plant_months: list[int]
    start_month: int
    end_month: int
    source: str                      # "calendar" or "inferred"
    note: str = ""

    def bounds(self, start_year: int) -> tuple[pd.Timestamp, pd.Timestamp]:
        end_year = start_year + (1 if self.end_month < self.start_month else 0)
        return (pd.Timestamp(start_year, self.start_month, 1),
                pd.Timestamp(end_year, self.end_month, calendar.monthrange(end_year, self.end_month)[1]))

    @property
    def days(self) -> int:
        s, e = self.bounds(2001)                       # non-leap reference year
        return (e - s).days + 1

    def plant_window(self, start_year: int) -> tuple[pd.Timestamp, pd.Timestamp]:
        first, last = self.plant_months[0], self.plant_months[-1]
        s = pd.Timestamp(start_year, first, 1)
        ey = start_year + (1 if last < first else 0)
        return s, pd.Timestamp(ey, last, calendar.monthrange(ey, last)[1])

    def label(self) -> str:
        return f"{self.name} (plant {'-'.join(MONTHS[m - 1] for m in (self.plant_months[0], self.plant_months[-1]))})"


def seasons_from_calendar(cal: dict) -> list[SeasonWindow]:
    out = []
    for s in cal["seasons"]:
        start = s["plant_months"][0]
        end = max(s["harvest_months"], key=lambda m: (m - start) % 12)   # last harvest month in time order
        out.append(SeasonWindow(id=s["name"].replace(" ", "_"), name=s["name"], plant_months=list(s["plant_months"]),
                                start_month=start, end_month=end, source="calendar"))
    return out


def infer_seasons(merged: pd.DataFrame, rainy_month_mm: float) -> list[SeasonWindow]:
    """Runs of months whose median monthly rainfall is at/above `rainy_month_mm`.
    A crude, clearly-labelled fallback for places without a regional calendar."""
    if "PRECTOTCORR" not in merged:
        return []
    monthly = merged["PRECTOTCORR"].resample("MS").sum(min_count=20)
    clim = monthly.groupby(monthly.index.month).median().reindex(range(1, 13))
    wet = (clim >= rainy_month_mm).to_numpy()
    if wet.all() or not wet.any():
        return []
    start0 = next(i for i in range(12) if wet[i] and not wet[i - 1])      # a run start (circular)
    runs, i, n = [], start0, 0
    while n < 12:
        if wet[i % 12]:
            j = i
            while wet[(j + 1) % 12] and (j + 1 - i) < 12:
                j += 1
            runs.append((i % 12 + 1, j % 12 + 1, j - i + 1))
            n += j - i + 1
            i = j + 1
        else:
            i += 1
            n += 1
    out = []
    for k, (a, b, length) in enumerate(sorted((r for r in runs if r[2] >= 2), key=lambda r: r[0]), 1):
        out.append(SeasonWindow(id=f"inferred_{k}", name=f"rainy season {k} ({MONTHS[a-1]}-{MONTHS[b-1]})",
                                plant_months=[a], start_month=a, end_month=b, source="inferred",
                                note="Inferred from median monthly rainfall; onset month assumed to be planting month."))
    return out


# ------------------------------------------------------------------ history and fit
def historical_season_stats(merged: pd.DataFrame, season: SeasonWindow, min_coverage: float) -> pd.DataFrame:
    rows = []
    first, last = merged.index.min(), merged.index.max()
    for y in sorted(set(merged.index.year)):
        s, e = season.bounds(y)
        if s < first or e > last:
            continue
        w = merged.loc[s:e]
        days = (e - s).days + 1
        rain, temp = w["PRECTOTCORR"], w["T2M"]
        if rain.notna().sum() < min_coverage * days or temp.notna().sum() < min_coverage * days:
            continue
        rows.append({"year": y, "rain_mm": float(rain.sum() * days / rain.notna().sum()),
                     "t_mean_c": float(temp.mean())})
    return pd.DataFrame(rows).set_index("year") if rows else pd.DataFrame(columns=["rain_mm", "t_mean_c"])


def crop_season_fit(crop: Crop, season: SeasonWindow, stats: pd.DataFrame, cfg) -> dict:
    res = {"mean_fit": 0.0, "reliability": 0.0, "n_years": int(len(stats)), "good_years": 0,
           "mean_rain_mm": None, "mean_temp_c": None, "eligible": False, "reason": None}
    if stats.empty:
        res["reason"] = "no usable history for this season"
        return res
    fits, good = [], 0
    for _, r in stats.iterrows():
        tf = trapezoid(r["t_mean_c"], *(crop.temp_abs[0], crop.temp_opt[0], crop.temp_opt[1], crop.temp_abs[1]))
        rf = trapezoid(r["rain_mm"], *(crop.rain_abs_mm[0], crop.rain_opt_mm[0], crop.rain_opt_mm[1], crop.rain_abs_mm[1]))
        vals = [v for v in (tf, rf) if not np.isnan(v)]
        f = min(vals) if vals else np.nan
        if not np.isnan(f):
            fits.append(f)
            good += f >= cfg.good_year_fit
    if not fits:
        res["reason"] = "crop climate ranges missing"
        return res
    res.update(mean_fit=float(np.mean(fits)), reliability=good / len(fits), good_years=int(good),
               mean_rain_mm=float(stats["rain_mm"].mean()), mean_temp_c=float(stats["t_mean_c"].mean()))
    gmin = crop.cycle_days[0]
    if gmin is not None and season.days < gmin:
        res["reason"] = f"shortest cycle ({gmin:g} d) is longer than the {season.days}-day season window"
    elif res["mean_fit"] < cfg.min_climate_fit:
        res["reason"] = f"historical climate suitability {res['mean_fit']:.2f} is below {cfg.min_climate_fit:g}"
    else:
        res["eligible"] = True
    return res


# ------------------------------------------------------------------ soil
@dataclass
class SoilInfo:
    """Optional user-supplied soil data (no soil dataset is connected yet)."""
    ph: float | None = None
    texture: str | None = None        # EcoCrop classes: heavy | medium | light | organic
    drainage: str | None = None       # poorly | well | excessive


def _class_match(user: str, optimal: list[str], absolute: list[str]) -> float | None:
    if not optimal and not absolute:
        return None
    u = user.lower()
    opt = " ".join(optimal).lower()
    ab = " ".join(absolute).lower()
    if "wide" in opt or u in opt:
        return 1.0
    if "wide" in ab or u in ab:
        return 0.5
    return 0.0


def soil_fit(crop: Crop, soil: SoilInfo | None) -> tuple[float | None, list[str]]:
    if soil is None:
        return None, []
    parts, notes = [], []
    if soil.ph is not None:
        s = trapezoid(soil.ph, crop.ph_abs[0], crop.ph_opt[0], crop.ph_opt[1], crop.ph_abs[1])
        if not np.isnan(s):
            parts.append(s)
            notes.append(f"pH {soil.ph:g} vs optimum {crop.ph_opt[0]:g}-{crop.ph_opt[1]:g}")
    if soil.texture:
        s = _class_match(soil.texture, crop.soil_texture_opt, crop.soil_texture_abs)
        if s is not None:
            parts.append(s)
            notes.append(f"texture '{soil.texture}' vs optimum {crop.soil_texture_opt or 'n/a'}")
    if soil.drainage:
        s = _class_match(soil.drainage, crop.drainage_opt, crop.drainage_abs)
        if s is not None:
            parts.append(s)
            notes.append(f"drainage '{soil.drainage}' vs optimum {crop.drainage_opt or 'n/a'}")
    return (float(np.mean(parts)) if parts else None), notes


def water_demand_index(crop: Crop, cfg) -> float | None:
    """0..1 proxy derived from EcoCrop's optimum-minimum rainfall. NOT a measured water requirement."""
    lo, hi = cfg.water_demand_range_mm
    v = crop.rain_opt_mm[0]
    return None if v is None else float(np.clip((v - lo) / (hi - lo), 0, 1))


def tolerates_waterlogging(crop: Crop) -> bool:
    return any("poorly" in d.lower() for d in crop.drainage_opt)
