"""Offline Stage 2 tests: no network, no credentials."""
import numpy as np
import pandas as pd
import pytest

from agrointel import cleaning, config, smap, nasa_power


def _csv(days=40, fill_day=None, drop_obs_day=None):
    rows = []
    rng = np.random.default_rng(1)
    for d in pd.date_range("2024-01-01", periods=days, freq="D"):
        for h in range(0, 24, 3):
            sm = 0.25 + 0.02 * rng.normal()
            rz = 0.30 + 0.01 * rng.normal()
            if fill_day is not None and d == pd.Timestamp(fill_day) and h == 3:
                sm = -9999.0
            if drop_obs_day is not None and d == pd.Timestamp(drop_obs_day) and h > 3:
                continue
            rows.append((("farm"), -0.09, 34.77, d + pd.Timedelta(hours=h), sm, rz))
    df = pd.DataFrame(rows, columns=["ID", "Latitude", "Longitude", "Date",
                                     "SPL4SMGP_008_Geophysical_Data_sm_surface",
                                     "SPL4SMGP_008_Geophysical_Data_sm_rootzone"])
    return df.to_csv(index=False)


def test_parse_matches_layers_by_suffix():
    raw, cell = smap.parse_appeears_csv(_csv())
    assert list(raw.columns) == ["SM_SURFACE", "SM_ROOTZONE"]
    assert cell["cell_lat"] == pytest.approx(-0.09)
    assert len(raw) == 40 * 8


def test_parse_missing_layer_raises():
    bad = _csv().replace("sm_rootzone", "other")
    with pytest.raises(smap.SmapError):
        smap.parse_appeears_csv(bad)


def test_clean_daily_aggregation_and_fill_values():
    raw, _ = smap.parse_appeears_csv(_csv(fill_day="2024-01-10"))
    assert (raw["SM_SURFACE"] == -9999.0).sum() == 1          # raw untouched
    clean, rep = cleaning.clean_smap(raw)
    assert len(clean) == 40 and clean.index.is_unique
    assert rep.fill_values_to_nan["SM_SURFACE"] == 1
    assert clean.loc["2024-01-10", "SM_SURFACE_n"] == 7
    assert 0.1 < clean.loc["2024-01-10", "SM_SURFACE"] < 0.5   # mean of remaining 7, not -9999-skewed
    assert (raw["SM_SURFACE"] == -9999.0).sum() == 1          # still raw afterwards


def test_day_with_too_few_obs_is_nan_and_never_interpolated():
    raw, _ = smap.parse_appeears_csv(_csv(drop_obs_day="2024-01-15"))
    clean, rep = cleaning.clean_smap(raw)
    assert np.isnan(clean.loc["2024-01-15", "SM_SURFACE"])


def test_impossible_values_removed():
    raw, _ = smap.parse_appeears_csv(_csv())
    raw.iloc[5, 0] = 1.7
    _, rep = cleaning.clean_smap(raw)
    assert rep.out_of_range_to_nan["SM_SURFACE"] == 1


def test_merge_keeps_power_calendar_and_flags_smap():
    idx = pd.date_range("2023-12-01", "2024-02-15", freq="D", name="date")
    power = pd.DataFrame({"T2M": 22.0}, index=idx)
    raw, _ = smap.parse_appeears_csv(_csv())
    sm, _ = cleaning.clean_smap(raw)
    merged = cleaning.merge_power_smap(power, sm)
    assert len(merged) == len(power)
    assert not merged.loc["2023-12-15", "smap_available"]
    assert merged.loc["2024-01-20", "smap_available"]


def test_missing_credentials_raise(monkeypatch):
    monkeypatch.delenv("EARTHDATA_USERNAME", raising=False)
    monkeypatch.delenv("EARTHDATA_PASSWORD", raising=False)
    with pytest.raises(smap.SmapAuthError):
        smap.get_earthdata_credentials()


def test_cache_then_stale_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    calls = {"n": 0}

    def fake_live(lat, lon, start, end, key):
        calls["n"] += 1
        if calls["n"] > 1:
            raise smap.SmapError("network down")
        return _csv(), {"product": "SPL4SMGP.008", "task_id": "t1"}

    monkeypatch.setattr(smap, "_live_fetch", fake_live)
    r1 = smap.fetch_smap(-0.09, 34.77, "2024-01-01", "2024-02-09")
    assert not r1.from_cache and r1.metadata["product"] == "SPL4SMGP.008"
    r2 = smap.fetch_smap(-0.09, 34.77, "2024-01-01", "2024-02-09")      # fresh cache: no new call
    assert r2.from_cache and calls["n"] == 1
    r3 = smap.fetch_smap(-0.09, 34.77, "2024-01-01", "2024-02-09", use_cache=False)  # live fails
    assert r3.metadata["stale_cache"] and any("STALE" in w for w in r3.warnings)


def test_auth_error_not_hidden_by_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(smap, "_live_fetch",
                        lambda *a, **k: (_ for _ in ()).throw(smap.SmapAuthError("bad login")))
    with pytest.raises(smap.SmapAuthError):
        smap.fetch_smap(-0.09, 34.77, "2024-01-01", "2024-02-09")
