"""Offline Stage 3 tests on synthetic data with known answers."""
import numpy as np
import pandas as pd
import pytest

from agrointel import config, stress


def _merged(years=12, seed=0, smap_from=2018, temp_trend=0.0):
    idx = pd.date_range("2013-01-01", f"{2012 + years}-12-31", freq="D", name="date")
    rng = np.random.default_rng(seed)
    doy = idx.day_of_year.values
    yr = (idx.year - idx.year.min()).values
    wet_p = 0.25 + 0.2 * (np.sin(2 * np.pi * doy / 182.5) > 0)
    rain = np.where(rng.random(len(idx)) < wet_p, rng.gamma(2, 6, len(idx)), 0.0)
    t = 22 + 2 * np.sin(2 * np.pi * doy / 365) + rng.normal(0, 0.6, len(idx)) + temp_trend * yr
    df = pd.DataFrame({"PRECTOTCORR": rain, "T2M": t, "T2M_MAX": t + 6, "T2M_MIN": t - 6}, index=idx)
    sm = 0.28 + 0.03 * rng.normal(size=len(idx))
    df["SM_SURFACE"] = sm
    df["SM_ROOTZONE"] = 0.30 + 0.02 * rng.normal(size=len(idx))
    df.loc[df.index.year < smap_from, ["SM_SURFACE", "SM_ROOTZONE"]] = np.nan
    return df


def test_neutral_conditions_have_no_alarms():
    res = stress.analyze_stress(_merged())
    assert res.composites["drought"]["classification"] in ("No drought signal", "Dry signal: rainfall only",
                                                           "Dry signal: soil moisture only")
    assert res.get("rain_90d").classification != stress.INSUFFICIENT
    assert res.get("heat_days").classification == "No heat stress"


def test_baseline_excludes_current_year():
    res = stress.analyze_stress(_merged(years=12))
    assert res.get("rain_90d").n_baseline_years == 11      # 2013..2023, not 2024
    assert res.get("soil_rootzone").n_baseline_years == 6  # SMAP 2018..2023


def test_severe_drought_scenario():
    df = _merged()
    df.loc[df.index[-90:], "PRECTOTCORR"] = 0.0
    df.loc[df.index[-30:], "SM_ROOTZONE"] = 0.10
    res = stress.analyze_stress(df)
    assert res.get("rain_90d").classification == "Very dry"
    assert res.get("soil_rootzone").classification == "Severe deficit"
    assert res.composites["drought"]["classification"] == "Probable severe drought"
    assert res.get("dry_spell").classification == "Very long"


def test_wet_scenario_flags_excess_moisture():
    df = _merged()
    df.loc[df.index[-30:], "PRECTOTCORR"] = 40.0
    df.loc[df.index[-30:], "SM_ROOTZONE"] = 0.50
    res = stress.analyze_stress(df)
    assert res.get("rain_30d").classification == "Very wet"
    assert res.composites["excess_moisture"]["classification"].startswith("Probable excess")


def test_heat_wave_is_counted_and_extreme_preserved():
    df = _merged()
    df.loc[df.index[-10:], "T2M_MAX"] = 39.0
    res = stress.analyze_stress(df)
    heat = res.get("heat_days")
    assert heat.value == 10 and heat.classification == "Severe"
    assert res.get("tmax_30d").classification in ("Much warmer than normal", "Warmer than normal")


def test_missing_smap_degrades_gracefully():
    df = _merged().drop(columns=["SM_SURFACE", "SM_ROOTZONE"])
    df.loc[df.index[-90:], "PRECTOTCORR"] = 0.0
    res = stress.analyze_stress(df)
    assert res.get("soil_rootzone").classification == stress.INSUFFICIENT
    assert res.composites["drought"]["classification"].startswith("Possible meteorological dryness")
    assert any("SMAP" in w for w in res.warnings)


def test_short_smap_baseline_is_insufficient_not_guessed():
    df = _merged(smap_from=2023)                           # only 2023 + 2024 of SMAP
    res = stress.analyze_stress(df)
    assert res.get("soil_rootzone").classification == stress.INSUFFICIENT
    assert "baseline" in res.get("soil_rootzone").note


def test_window_with_gaps_is_not_trusted():
    df = _merged()
    df.loc[df.index[-60:], "PRECTOTCORR"] = np.nan
    res = stress.analyze_stress(df)
    assert res.get("rain_30d").classification == stress.INSUFFICIENT or res.get("rain_30d").as_of < df.index[-1].date()


def test_temperature_trend_detected():
    res = stress.analyze_stress(_merged(years=15, temp_trend=0.1))
    t = next(x for x in res.trends if x["variable"] == "Annual mean temperature")
    assert t["direction"] == "Increasing"
    assert t["slope_per_decade"] == pytest.approx(1.0, abs=0.3)


def test_mann_kendall_known_cases():
    z, p = stress.mann_kendall(np.arange(20.0))
    assert p < 0.001 and z > 0
    _, p2 = stress.mann_kendall(np.array([1, 5, 2, 5, 1, 5, 2, 5, 1, 5, 2, 5.0]))
    assert p2 > 0.1


def test_monthly_anomalies_shape():
    m = stress.monthly_anomalies(_merged())
    assert {"rain_mm", "rain_mm_anom", "t_mean_c_anom", "sm_root_anom"} <= set(m.columns)
    assert abs(m["rain_mm_anom"].median()) < 15
