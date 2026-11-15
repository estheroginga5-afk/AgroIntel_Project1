"""Offline tests: no network needed. Run with `pytest -q` from the project root."""
import numpy as np
import pandas as pd
import pytest

from agrointel import cleaning, nasa_power


def _fake_payload(n_days=730):
    idx = pd.date_range("2022-01-01", periods=n_days, freq="D")
    rng = np.random.default_rng(0)
    doy = idx.day_of_year.values
    t = 22 + 3 * np.sin(2 * np.pi * doy / 365) + rng.normal(0, 0.5, n_days)
    rain = np.where(rng.random(n_days) < 0.35, rng.gamma(2, 6, n_days), 0.0)
    fmt = lambda a: {d.strftime("%Y%m%d"): float(v) for d, v in zip(idx, a)}
    p = {
        "PRECTOTCORR": fmt(rain),
        "T2M": fmt(t),
        "T2M_MAX": fmt(t + 5),
        "T2M_MIN": fmt(t - 5),
        "RH2M": fmt(np.clip(70 + rng.normal(0, 8, n_days), 0, 100)),
    }
    return {
        "properties": {"parameter": p},
        "parameters": {k: {"units": u} for k, u in
                       {"PRECTOTCORR": "mm/day", "T2M": "C", "T2M_MAX": "C",
                        "T2M_MIN": "C", "RH2M": "%"}.items()},
        "header": {"fill_value": -999.0, "api": {"version": "v2.x"}},
    }


def test_parse_and_fill_values():
    payload = _fake_payload()
    payload["properties"]["parameter"]["T2M"]["20220110"] = -999.0
    raw, meta = nasa_power.parse_power_json(payload, ["T2M"])
    assert raw.loc["2022-01-10", "T2M"] == -999.0  # raw is untouched
    clean, rep = cleaning.clean_power(raw)
    assert rep.fill_values_to_nan["T2M"] == 1
    assert clean.loc["2022-01-10", "T2M"] > -100          # interpolated, not -999
    assert bool(clean.loc["2022-01-10", "T2M_imputed"])
    assert raw.loc["2022-01-10", "T2M"] == -999.0          # still raw afterwards


def test_rain_never_interpolated():
    payload = _fake_payload()
    payload["properties"]["parameter"]["PRECTOTCORR"]["20220201"] = -999.0
    raw, _ = nasa_power.parse_power_json(payload, ["PRECTOTCORR"])
    clean, rep = cleaning.clean_power(raw)
    assert np.isnan(clean.loc["2022-02-01", "PRECTOTCORR"])


def test_extreme_rain_is_kept_and_flagged():
    payload = _fake_payload()
    payload["properties"]["parameter"]["PRECTOTCORR"]["20220615"] = 180.0  # real-ish flood
    raw, _ = nasa_power.parse_power_json(payload, ["PRECTOTCORR"])
    clean, rep = cleaning.clean_power(raw)
    assert clean.loc["2022-06-15", "PRECTOTCORR"] == 180.0   # preserved
    assert bool(clean.loc["2022-06-15", "PRECTOTCORR_outlier"])


def test_impossible_values_removed():
    payload = _fake_payload()
    payload["properties"]["parameter"]["RH2M"]["20220301"] = 140.0
    raw, _ = nasa_power.parse_power_json(payload, ["RH2M"])
    _, rep = cleaning.clean_power(raw)
    assert rep.out_of_range_to_nan["RH2M"] == 1


def test_duplicates_and_calendar_gaps():
    payload = _fake_payload(60)
    raw, _ = nasa_power.parse_power_json(payload, ["T2M"])
    raw = pd.concat([raw, raw.iloc[[5]]]).drop(raw.index[10])
    clean, rep = cleaning.clean_power(raw)
    assert rep.duplicates_removed == 1
    assert clean.index.is_unique and len(clean) == 60


def test_bad_coordinates():
    with pytest.raises(ValueError):
        nasa_power.validate_coordinates(95, 0)
    with pytest.raises(ValueError):
        nasa_power.validate_coordinates(0, 200)
