"""Orchestration: fetch -> clean -> align -> stress analysis, then recommendations.

Kept separate from the dashboard so the same code runs in Colab, tests, scripts and Streamlit.
The environment stage depends only on the location, so the dashboard caches it; the
recommendation stage is cheap and re-runs when previous crop, soil or weights change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable

import pandas as pd

from . import config
from .cleaning import clean_power, clean_smap, merge_power_smap
from .crops import CropDataset
from .nasa_power import NasaPowerError, fetch_power_daily, validate_coordinates
from .recommend import RecommendationSet, recommend
from .smap import SmapAuthError, SmapError, fetch_smap
from .stress import StressResult, analyze_stress
from .suitability import SoilInfo


class PipelineError(RuntimeError):
    """A failure the user should see in plain words."""


@dataclass
class EnvData:
    lat: float
    lon: float
    years: int
    merged: pd.DataFrame                 # cleaned, aligned daily data with imputed/outlier flags
    power_raw: pd.DataFrame              # untouched POWER response (fill values kept)
    smap_raw: pd.DataFrame | None        # untouched sub-daily SMAP response
    power_meta: dict
    smap_meta: dict | None
    power_report: dict
    smap_report: dict | None
    stress: StressResult
    warnings: list[str] = field(default_factory=list)
    generated_utc: str = ""

    @property
    def has_smap(self) -> bool:
        return self.smap_meta is not None


def load_environment(lat: float, lon: float, years: int = 30, use_smap: bool = True,
                     progress: Callable[[str], None] | None = None,
                     end: date | None = None) -> EnvData:
    say = progress or (lambda _m: None)
    try:
        lat, lon = validate_coordinates(lat, lon)
    except ValueError as e:
        raise PipelineError(str(e)) from e
    end = end or (date.today() - timedelta(days=7))      # POWER lags real time by a few days
    start = max(date(end.year - years, 1, 1), pd.Timestamp(config.POWER_EARLIEST_DATE).date())
    warnings: list[str] = []

    say("Fetching NASA POWER climate data")
    try:
        power = fetch_power_daily(lat, lon, start, end)
    except NasaPowerError as e:
        raise PipelineError(f"Could not get NASA POWER data: {e}. Try again later; cached data is used when available.") from e
    except ValueError as e:
        raise PipelineError(str(e)) from e
    warnings += power.warnings
    say("Cleaning POWER data")
    clean, power_rep = clean_power(power.raw)
    if "PRECTOTCORR" not in clean or "T2M" not in clean:
        raise PipelineError("NASA POWER returned too little usable rainfall or temperature data for this location.")

    smap_raw = smap_meta = smap_rep = None
    if use_smap:
        say("Fetching SMAP soil moisture (can take several minutes the first time)")
        try:
            smap_res = fetch_smap(lat, lon, end=end)
            warnings += smap_res.warnings
            smap_clean, smap_rep_obj = clean_smap(smap_res.raw)
            clean = merge_power_smap(clean, smap_clean)
            smap_raw, smap_meta, smap_rep = smap_res.raw, smap_res.metadata, smap_rep_obj.to_dict()
        except SmapAuthError as e:
            warnings.append(f"SMAP skipped: {e}")
        except SmapError as e:
            warnings.append(f"SMAP skipped: {e}")
        except Exception as e:                              # processing problems must not kill the app
            warnings.append(f"SMAP data could not be processed ({type(e).__name__}); soil moisture skipped.")
    else:
        warnings.append("SMAP soil moisture was not requested; soil-moisture indicators are unavailable.")

    say("Analysing environmental stress")
    stress = analyze_stress(clean)
    return EnvData(lat, lon, years, clean, power.raw, smap_raw, power.metadata, smap_meta,
                   power_rep.to_dict(), smap_rep, stress, warnings,
                   datetime.now(timezone.utc).isoformat(timespec="seconds"))


def make_recommendations(env: EnvData, ds: CropDataset, previous_crop: str | None = None,
                         soil: SoilInfo | None = None, weights: dict | None = None) -> RecommendationSet:
    return recommend(env.merged, ds, env.lat, env.lon, stress=env.stress,
                     previous_crop=previous_crop, soil=soil, weights=weights)
