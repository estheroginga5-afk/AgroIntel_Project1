"""Data cleaning (Stage 1).

Design rules:
  * RAW data is never modified. `clean_power()` returns a new frame.
  * NASA fill values (-999) become NaN, never zeros.
  * Physically impossible values become NaN (logged in the report).
  * Statistical outliers are FLAGGED, not removed: real extremes (floods,
    heatwaves) must survive.
  * Only short gaps are interpolated, and every filled cell is flagged, so
    downstream code can tell observed from imputed values.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import config


@dataclass
class CleaningReport:
    n_rows_raw: int = 0
    n_rows_clean: int = 0
    duplicates_removed: int = 0
    dates_added_to_fill_calendar: int = 0
    fill_values_to_nan: dict = field(default_factory=dict)
    out_of_range_to_nan: dict = field(default_factory=dict)
    interpolated_cells: dict = field(default_factory=dict)
    remaining_missing: dict = field(default_factory=dict)
    outliers_flagged: dict = field(default_factory=dict)
    logical_conflicts: dict = field(default_factory=dict)
    rejected_parameters: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _robust_z_by_doy(s: pd.Series) -> pd.Series:
    """Robust z-score of each value relative to its day-of-year neighbourhood.

    Using median/MAD on a seasonal baseline avoids flagging normal seasonality
    and is not distorted by the very extremes we want to detect.
    """
    doy = np.asarray(pd.DatetimeIndex(s.index).day_of_year, dtype=int)
    out = pd.Series(np.nan, index=s.index)
    for d in range(1, 367):
        # +/- 7 day window around this day-of-year (wrapping the year end)
        window = ((doy - d + 183) % 366 - 183)
        mask = np.abs(window) <= 7
        vals = s[mask].dropna()
        if len(vals) < 10:
            continue
        med = vals.median()
        mad = (vals - med).abs().median() * 1.4826
        if mad == 0:
            continue
        sel = doy == d
        out[sel] = (s[sel] - med) / mad
    return out


def clean_power(
    raw: pd.DataFrame,
    cfg: config.CleaningConfig = config.CLEANING,
    fill_value: float = config.POWER_FILL_VALUE,
) -> tuple[pd.DataFrame, CleaningReport]:
    """Return (clean_df, report). `clean_df` has value columns plus
    `<param>_imputed` and `<param>_outlier` boolean flag columns."""
    rep = CleaningReport(n_rows_raw=len(raw))
    df = raw.copy()

    # 1. Index: datetime, sorted, unique, continuous daily calendar.
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    dup = df.index.duplicated(keep="first")
    rep.duplicates_removed = int(dup.sum())
    df = df[~dup]
    full = pd.date_range(df.index.min(), df.index.max(), freq="D")
    rep.dates_added_to_fill_calendar = len(full) - len(df)
    df = df.reindex(full)
    df.index.name = "date"

    # 2. Types.
    df = df.apply(pd.to_numeric, errors="coerce").astype("float64")

    params = [c for c in df.columns if c in config.POWER_PARAMETERS]

    # 3. Fill values -> NaN (check the documented value and a tolerant match).
    for p in params:
        m = np.isclose(df[p], fill_value) | (df[p] <= -990)
        rep.fill_values_to_nan[p] = int(m.sum())
        df.loc[m, p] = np.nan

    # 4. Physically impossible values -> NaN.
    for p in params:
        lo, hi = config.POWER_PARAMETERS[p]["min"], config.POWER_PARAMETERS[p]["max"]
        m = (df[p] < lo) | (df[p] > hi)
        rep.out_of_range_to_nan[p] = int(m.sum())
        df.loc[m, p] = np.nan

    # 5. Reject series that are too incomplete to trust.
    for p in list(params):
        frac = df[p].isna().mean()
        if frac > cfg.max_missing_fraction:
            rep.rejected_parameters.append(p)
            rep.notes.append(f"{p}: {frac:.0%} missing (> {cfg.max_missing_fraction:.0%}); dropped.")
            df = df.drop(columns=p)
            params.remove(p)

    # 6. Logical consistency (flag, do not silently alter).
    if {"T2M_MIN", "T2M_MAX"} <= set(params):
        bad = df["T2M_MIN"] > df["T2M_MAX"]
        rep.logical_conflicts["T2M_MIN>T2M_MAX"] = int(bad.sum())
        df.loc[bad, ["T2M_MIN", "T2M_MAX"]] = np.nan
    if {"T2M", "T2M_MIN", "T2M_MAX"} <= set(params):
        tol = 0.5  # °C tolerance for model rounding
        bad = (df["T2M"] < df["T2M_MIN"] - tol) | (df["T2M"] > df["T2M_MAX"] + tol)
        rep.logical_conflicts["T2M outside [MIN,MAX]"] = int(bad.sum())

    # 7. Outlier FLAGS on the raw (pre-interpolation) values; nothing removed.
    for p in params:
        if p == "PRECTOTCORR":
            # Rain is zero-inflated and heavy-tailed, so median/MAD on all days
            # degenerates (MAD = 0). Use wet days only, on a log scale, upper
            # tail only. Informational: flagged days are real events, kept as-is.
            wet = df[p].where(df[p] >= cfg.rain_wet_day_mm)
            lg = np.log1p(wet)
            med = lg.median()
            mad = (lg - med).abs().median() * 1.4826
            z = (lg - med) / mad if mad and not np.isnan(mad) else pd.Series(np.nan, index=df.index)
            flag = z > cfg.rain_extreme_z
        else:
            z = _robust_z_by_doy(df[p])
            flag = z.abs() > cfg.outlier_robust_z
        df[f"{p}_outlier"] = flag.fillna(False)
        rep.outliers_flagged[p] = int(df[f"{p}_outlier"].sum())

    # 8. Interpolate short gaps only; flag every imputed cell.
    for p in params:
        was_nan = df[p].isna()
        if p == "PRECTOTCORR":
            # Never invent rainfall: leave gaps as NaN.
            df[f"{p}_imputed"] = False
            rep.interpolated_cells[p] = 0
            continue
        filled = df[p].interpolate(method="time", limit=cfg.max_interp_gap_days,
                                   limit_area="inside")
        df[f"{p}_imputed"] = was_nan & filled.notna()
        df[p] = filled
        rep.interpolated_cells[p] = int(df[f"{p}_imputed"].sum())

    rep.remaining_missing = {p: int(df[p].isna().sum()) for p in params}
    rep.n_rows_clean = len(df)
    rep.notes.append("Rainfall gaps are never interpolated. Outliers are flagged, not removed.")
    return df, rep


def coverage_summary(clean: pd.DataFrame) -> pd.DataFrame:
    """Per-parameter overview for the data-inspection step."""
    allp = {**config.POWER_PARAMETERS, **config.SMAP_PARAMETERS}
    rows = []
    for p in [c for c in clean.columns if c in allp]:
        s = clean[p]
        rows.append({
            "parameter": p,
            "unit": allp[p]["unit"],
            "start": s.first_valid_index(),
            "end": s.last_valid_index(),
            "n": int(s.notna().sum()),
            "missing": int(s.isna().sum()),
            "min": s.min(), "mean": s.mean(), "max": s.max(),
        })
    return pd.DataFrame(rows).set_index("parameter")


# ====================================================================== SMAP (Stage 2)
def clean_smap(
    raw: pd.DataFrame,
    cfg: config.CleaningConfig = config.CLEANING,
    fill_value: float = config.SMAP_FILL_VALUE,
) -> tuple[pd.DataFrame, CleaningReport]:
    """Sub-daily SMAP raw -> daily clean frame (+ `<param>_n` obs counts, `<param>_outlier` flags).

    Same rules as POWER: raw untouched, fill values -> NaN, impossible values -> NaN,
    outliers flagged not removed. Extra rule: soil moisture is NEVER interpolated
    (a gap stays a gap), and a day built from too few sub-daily values is set to NaN.
    """
    rep = CleaningReport(n_rows_raw=len(raw))
    df = raw.copy()
    df.index = pd.to_datetime(df.index)
    df = df.sort_index().apply(pd.to_numeric, errors="coerce").astype("float64")
    params = [c for c in df.columns if c in config.SMAP_PARAMETERS]

    for p in params:
        m = np.isclose(df[p], fill_value) | (df[p] <= -990)
        rep.fill_values_to_nan[p] = int(m.sum())
        df.loc[m, p] = np.nan
        lo, hi = config.SMAP_PARAMETERS[p]["min"], config.SMAP_PARAMETERS[p]["max"]
        m = (df[p] < lo) | (df[p] > hi)
        rep.out_of_range_to_nan[p] = int(m.sum())
        df.loc[m, p] = np.nan

    day = df.index.normalize()
    daily = pd.DataFrame(index=pd.DatetimeIndex(sorted(set(day))))
    for p in params:
        g = df[p].groupby(day)
        mean, n = g.mean(), g.count()
        usual = n.mode().max() if len(n) else 0           # typical obs/day (e.g. 8 for 3-hourly)
        too_few = n < max(1, cfg.smap_min_obs_fraction * usual)
        mean[too_few] = np.nan
        daily[p], daily[f"{p}_n"] = mean, n
        rep.notes.append(f"{p}: {int(too_few.sum())} day(s) dropped for too few sub-daily values "
                         f"(usual {int(usual)}/day).")

    full = pd.date_range(daily.index.min(), daily.index.max(), freq="D")
    rep.dates_added_to_fill_calendar = len(full) - len(daily)
    daily = daily.reindex(full)
    daily.index.name = "date"
    for p in params:
        daily[f"{p}_n"] = daily[f"{p}_n"].fillna(0).astype(int)

    for p in list(params):
        frac = daily[p].isna().mean()
        if frac > cfg.max_missing_fraction:
            rep.rejected_parameters.append(p)
            rep.notes.append(f"{p}: {frac:.0%} missing (> {cfg.max_missing_fraction:.0%}); dropped.")
            daily = daily.drop(columns=[p, f"{p}_n"])
            params.remove(p)

    for p in params:
        z = _robust_z_by_doy(daily[p])
        daily[f"{p}_outlier"] = (z.abs() > cfg.outlier_robust_z).fillna(False)
        rep.outliers_flagged[p] = int(daily[f"{p}_outlier"].sum())

    rep.remaining_missing = {p: int(daily[p].isna().sum()) for p in params}
    rep.n_rows_clean = len(daily)
    rep.notes.append("Soil moisture gaps are never interpolated. Outliers are flagged, not removed.")
    return daily, rep


def merge_power_smap(power_clean: pd.DataFrame, smap_clean: pd.DataFrame) -> pd.DataFrame:
    """Align on calendar date (both are daily, UTC-based). POWER is the base calendar.

    The datasets have different footprints (POWER ~0.5° x 0.625°, SMAP 9 km) and
    different histories (POWER from 1981, SMAP from 2015). The merge does not
    resample or smooth anything: SMAP columns are NaN before SMAP coverage, and
    `smap_available` says where SMAP values exist.
    """
    out = power_clean.join(smap_clean, how="left")
    sm_cols = [c for c in ("SM_SURFACE", "SM_ROOTZONE") if c in out.columns]
    out["smap_available"] = out[sm_cols].notna().any(axis=1) if sm_cols else False
    return out
