"""Central configuration for AgroIntel.

Nothing secret lives here. Credentials (e.g. NASA Earthdata for SMAP, needed
in Stage 2) are read from environment variables or Streamlit secrets only.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = Path(os.environ.get("AGROINTEL_CACHE_DIR", BASE_DIR / ".cache"))
CACHE_TTL_HOURS = float(os.environ.get("AGROINTEL_CACHE_TTL_HOURS", 24))

# ---------------------------------------------------------------- locations
DEFAULT_LOCATION = {
    "name": "Demo farm, Western Kenya (near Kisumu)",
    "lat": -0.0917,
    "lon": 34.7680,
}

# ---------------------------------------------------------------- NASA POWER
POWER_DAILY_POINT_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
POWER_COMMUNITY = "AG"  # agroclimatology community
POWER_FILL_VALUE = -999.0
POWER_REQUEST_TIMEOUT_S = 90
POWER_MAX_RETRIES = 3
POWER_RETRY_BACKOFF_S = 2.0
POWER_EARLIEST_DATE = "1981-01-01"  # start of POWER daily meteorology

# parameter -> metadata. Units here are what POWER documents; verified at runtime
# against the API's own "parameters" block, which takes precedence.
POWER_PARAMETERS = {
    "PRECTOTCORR": {"label": "Precipitation (corrected)", "unit": "mm/day",
                    "min": 0.0, "max": 1500.0, "kind": "satellite+model"},
    "T2M":         {"label": "Mean air temperature at 2 m", "unit": "°C",
                    "min": -90.0, "max": 60.0, "kind": "reanalysis"},
    "T2M_MAX":     {"label": "Max air temperature at 2 m", "unit": "°C",
                    "min": -90.0, "max": 65.0, "kind": "reanalysis"},
    "T2M_MIN":     {"label": "Min air temperature at 2 m", "unit": "°C",
                    "min": -95.0, "max": 55.0, "kind": "reanalysis"},
    "RH2M":        {"label": "Relative humidity at 2 m", "unit": "%",
                    "min": 0.0, "max": 100.0, "kind": "reanalysis"},
    "ALLSKY_SFC_SW_DWN": {"label": "All-sky surface shortwave irradiance",
                          "unit": "MJ/m²/day", "min": 0.0, "max": 45.0,
                          "kind": "satellite+model"},
}
DEFAULT_POWER_PARAMS = list(POWER_PARAMETERS)

# ---------------------------------------------------------------- NASA SMAP
# SMAP is accessed through NASA AppEEARS point sampling (server-side extraction),
# so we download a small CSV instead of global 9 km granules.
# Credentials come ONLY from env vars / Streamlit secrets / Colab secrets:
#   EARTHDATA_USERNAME, EARTHDATA_PASSWORD
APPEEARS_BASE_URL = "https://appeears.earthdatacloud.nasa.gov/api/"
SMAP_PRODUCT_ID = "SPL4SMGP"      # SMAP L4 3-hourly 9 km surface + root-zone soil moisture
SMAP_PRODUCT_VERSION = None       # None = pick newest available version from AppEEARS at runtime
SMAP_LAYERS = {                   # our name -> AppEEARS layer name (validated at runtime)
    "SM_SURFACE": "Geophysical_Data_sm_surface",
    "SM_ROOTZONE": "Geophysical_Data_sm_rootzone",
}
SMAP_EARLIEST_DATE = "2015-03-31"
SMAP_FILL_VALUE = -9999.0
SMAP_DEFAULT_YEARS = 4            # default history requested (keeps AppEEARS tasks quick)
SMAP_POLL_INTERVAL_S = 20
SMAP_TASK_TIMEOUT_S = 900
SMAP_REQUEST_TIMEOUT_S = 90
SMAP_PARAMETERS = {
    "SM_SURFACE": {"label": "Surface soil moisture (~0-5 cm)", "unit": "m³/m³",
                   "min": 0.0, "max": 0.8, "kind": "model estimate (SMAP-assimilating land model)"},
    "SM_ROOTZONE": {"label": "Root-zone soil moisture (~0-100 cm)", "unit": "m³/m³",
                    "min": 0.0, "max": 0.8, "kind": "model estimate (SMAP-assimilating land model)"},
}

# ---------------------------------------------------------------- cleaning
@dataclass(frozen=True)
class CleaningConfig:
    # Gaps up to this many consecutive days may be linearly interpolated.
    max_interp_gap_days: int = 3
    # Robust z-score (median/MAD, on day-of-year residuals) above which a value
    # is FLAGGED as a statistical outlier. Flagged values are never removed:
    # genuine extreme weather must be preserved.
    outlier_robust_z: float = 6.0
    # Rainfall is zero-inflated and heavy-tailed, so it is checked on wet days
    # only (>= rain_wet_day_mm) using a robust z-score of log1p(rain).
    rain_extreme_z: float = 3.5
    rain_wet_day_mm: float = 1.0
    # A day of sub-daily SMAP values is kept only if it has at least this share
    # of the usual number of observations per day (adaptive to what the API returns).
    smap_min_obs_fraction: float = 0.5
    # Reject a series entirely if more than this share of days is missing.
    max_missing_fraction: float = 0.30


CLEANING = CleaningConfig()


# ---------------------------------------------------------------- stress detection
@dataclass(frozen=True)
class StressConfig:
    """All thresholds are transparent, adjustable defaults, NOT agronomic facts.
    Percentile classes compare the recent window with the same calendar window in
    other years of this location's own record."""
    rain_windows_days: tuple = (30, 90)
    temp_window_days: int = 30
    soil_surface_window_days: int = 14
    soil_root_window_days: int = 30
    # percentile cut points: very low | low | normal | high | very high
    pct_cuts: tuple = (5.0, 20.0, 80.0, 95.0)
    excess_pct: float = 90.0              # percentile at/above which moisture is "excess"
    dry_day_mm: float = 1.0               # a day below this rainfall counts as dry
    dry_spell_cuts_days: tuple = (7, 14, 21)
    # Generic heat threshold; crop-specific thresholds are applied in Stage 5.
    heat_threshold_c: float = 35.0
    heat_day_cuts: tuple = (1, 4, 10)     # heat days in window: mild | moderate | severe
    min_window_coverage: float = 0.8      # share of days with data needed to trust a window
    min_baseline_years: int = 5           # POWER-based indicators
    smap_min_baseline_years: int = 3      # SMAP-based indicators (record starts 2015)
    trend_min_years: int = 8
    trend_alpha: float = 0.10


STRESS = StressConfig()


# ---------------------------------------------------------------- recommendations
@dataclass(frozen=True)
class RecommendationConfig:
    """Every number here is an adjustable expert-judgement default, NOT a calibrated or
    validated agronomic parameter. Weights are normalised over the components that are
    actually available, so a missing input never silently counts as 'good' or 'bad'."""
    weights: dict = field(default_factory=lambda: {
        "climate_fit": 0.25,         # historical temperature/rainfall suitability of each crop in its season
        "reliability": 0.15,         # share of past seasons in which the crop was suitable
        "current_conditions": 0.10,  # present drought / excess-moisture signals (only near planting)
        "soil_fit": 0.10,            # pH, texture, drainage (only if the user supplies soil data)
        "nitrogen": 0.15,            # legume nitrogen carried into the next crop
        "fertility_load": 0.10,      # avoid back-to-back high fertility-demand crops
        "diversity": 0.15,           # avoid repeating the same crop or botanical family
    })
    n_seasons: int = 3                        # seasons per rotation strategy
    n_recommendations: int = 3
    min_positions_different: int = 2          # strategies must differ in at least this many seasons
    min_climate_fit: float = 0.4              # crop-season below this is not offered
    good_year_fit: float = 0.7                # a past season counts as "suitable" at or above this
    min_history_years: int = 8                # below this: recommend, but warn that reliability is weak
    hard_min_history_years: int = 5           # below this: refuse to recommend
    min_window_coverage: float = 0.8
    rainy_month_mm: float = 50.0              # proxy for a rain-fed month when seasons must be inferred
    same_family_score: float = 0.3            # diversity score for a different crop of the same family
    current_conditions_horizon_days: int = 60 # current stress matters only if planting is this close
    water_demand_range_mm: tuple = (400.0, 1500.0)   # EcoCrop optimum-minimum rainfall mapped to 0..1
    drought_severity: dict = field(default_factory=lambda: {
        "Probable severe drought": 1.0, "Probable drought": 0.7,
        "Possible meteorological dryness (soil moisture unavailable)": 0.4,
        "Dry signal: rainfall only": 0.3, "Dry signal: soil moisture only": 0.3})
    excess_intolerant_score: dict = field(default_factory=lambda: {
        "Probable excess moisture (waterlogging risk)": 0.6, "Possible excess moisture": 0.8})
    fertility_scores: dict = field(default_factory=lambda: {"high": 1.0, "moderate": 0.5, "low": 0.0})


RECOMMEND = RecommendationConfig()


# User accounts (SQLite). On Streamlit Community Cloud this disk is temporary; see README.
USER_DB_PATH = os.environ.get("AGROINTEL_DB", str(Path(__file__).parent / ".data" / "users.db"))
