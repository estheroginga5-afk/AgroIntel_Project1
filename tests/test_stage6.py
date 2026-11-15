"""Offline Stage 6 tests: pipeline orchestration, report, and the Streamlit app (headless)."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from agrointel import crops, pipeline, report
from agrointel.cleaning import clean_power
from agrointel.nasa_power import PowerResult
from agrointel.smap import SmapAuthError

APP = next(str(p / "app.py") for p in Path(__file__).resolve().parents if (p / "app.py").exists())
MONTH_MM = {1: 70, 2: 70, 3: 150, 4: 200, 5: 150, 6: 80, 7: 70, 8: 90, 9: 90, 10: 110, 11: 120, 12: 90}


def make_merged(smap=False, seed=0):
    idx = pd.date_range("2000-01-01", "2026-11-07", freq="D", name="date")
    rng = np.random.default_rng(seed)
    per_day = np.array([MONTH_MM[m] / 30.0 for m in idx.month])
    rain = np.where(rng.random(len(idx)) < 0.4, rng.gamma(4, per_day / 0.4 / 4), 0.0)
    t = 23.0 + rng.normal(0, 0.5, len(idx))
    return pd.DataFrame({"PRECTOTCORR": rain, "T2M": t, "T2M_MAX": t + 6, "T2M_MIN": t - 6}, index=idx)


META = {"source": "NASA POWER (test)", "product": "daily", "spatial_resolution": "0.5x0.625", "temporal_resolution": "daily",
        "retrieved_utc": "2026-11-14T00:00:00+00:00", "units": {"T2M": "C"}, "limitations": ["grid cell"],
        "served_from_cache": False}


def fake_power(lat, lon, start, end, *a, **k):
    m = make_merged(smap=False)
    raw = m[["PRECTOTCORR", "T2M", "T2M_MAX", "T2M_MIN"]].copy()
    return PowerResult(raw=raw, metadata=dict(META), from_cache=False, warnings=[])


def no_smap(*a, **k):
    raise SmapAuthError("No Earthdata credentials found.")


@pytest.fixture()
def patched(monkeypatch):
    monkeypatch.setattr(pipeline, "fetch_power_daily", fake_power)
    monkeypatch.setattr(pipeline, "fetch_smap", no_smap)


def test_pipeline_survives_missing_smap_and_says_so(patched):
    env = pipeline.load_environment(-0.09, 34.77, years=26)
    assert not env.has_smap
    assert any("SMAP skipped" in w for w in env.warnings)
    assert env.stress.indicators


def test_pipeline_rejects_bad_coordinates():
    with pytest.raises(pipeline.PipelineError):
        pipeline.load_environment(95, 10)


def test_pipeline_reports_power_failure_plainly(monkeypatch):
    from agrointel.nasa_power import NasaPowerError

    def boom(*a, **k):
        raise NasaPowerError("503")
    monkeypatch.setattr(pipeline, "fetch_power_daily", boom)
    with pytest.raises(pipeline.PipelineError, match="NASA POWER"):
        pipeline.load_environment(-0.09, 34.77)


def test_report_has_all_sections_and_honest_notes(patched):
    env = pipeline.load_environment(-0.09, 34.77, years=26)
    ds = crops.load_crops()
    rs = pipeline.make_recommendations(env, ds)
    md = report.build_report(env, rs, ds, "Test farm")
    for h in ("## 1. Data sources", "## 2. Data cleaning", "## 3. Environmental stress", "## 4. Crop-rotation",
              "## 5. What the crop dataset", "## 6. Weights"):
        assert h in md
    assert "not a farm-level sensor" in md.replace("farm-level sensor reading", "not a farm-level sensor") or "farm-level" in md
    assert "SMAP" in md and "Not used" in md


def test_app_shows_login_when_signed_out(tmp_path, monkeypatch):
    monkeypatch.setenv("AGROINTEL_DB", str(tmp_path / "u.db"))
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert [t.label for t in at.tabs][:2] == ["Log in", "Create account"]


def signed_in():
    at = AppTest.from_file(APP, default_timeout=120)
    at.session_state["user"] = {"id": 1, "email": "a@b.co", "name": "Test Farmer"}
    return at


def test_app_renders_before_run():
    at = signed_in().run()
    assert not at.exception
    assert any("Run analysis" in i.value for i in at.info)


def test_app_full_run_headless(patched):
    at = signed_in()
    at.run()
    [r for r in at.sidebar.radio if r.key == "view_pick"][0].set_value("detail")
    run_btn = [b for b in at.sidebar.button if b.label == "Run analysis"][0]
    run_btn.click().run()
    assert not at.exception, at.exception
    assert any("SMAP skipped" in w.value for w in at.warning)
    assert any("Top 3 rotation strategies" in s.value for s in at.subheader)
    assert len(at.tabs) >= 3


def test_simple_view_is_default_and_has_plan_cards(patched):
    at = signed_in()
    at.run()
    [b for b in at.sidebar.button if b.label == "Run analysis"][0].click().run()
    assert not at.exception, at.exception
    html = " ".join(m.value for m in at.markdown)
    assert "Your farm today" in html and "What to plant" in html and "Best choice" in html


def test_simple_view_in_kiswahili(patched):
    at = signed_in()
    at.run()
    [r for r in at.sidebar.radio if r.key == "lang_pick"][0].set_value("Kiswahili")
    [b for b in at.sidebar.button if b.label == "Run analysis"][0].click().run()
    assert not at.exception, at.exception
    html = " ".join(m.value for m in at.markdown)
    assert "Shamba lako leo" in html and "Chaguo bora" in html


def test_card_levels_map_from_stress_labels():
    from types import SimpleNamespace as NS
    from agrointel import farmer

    def env(drought, excess, heat):
        comp = {"drought": {"classification": drought}, "excess_moisture": {"classification": excess},
                "heat": {"classification": heat}}
        return NS(stress=NS(composites=comp, get=lambda k: None), has_smap=False)
    ok = farmer.card_states(env("No dry signal in rainfall (soil moisture unavailable)", "No excess-moisture signal", "No heat stress"))
    assert ok["rain"][0] == "good" and ok["heat"][0] == "good" and ok["soil"][0] == "unknown"
    bad = farmer.card_states(env("Probable severe drought", "No excess-moisture signal", "Severe"))
    assert bad["rain"] == ("danger", "rain_vdry") and bad["heat"][0] == "danger"
    wet = farmer.card_states(env("No drought signal", "Possible excess moisture", "Mild"))
    assert wet["rain"] == ("watch", "rain_wet") and wet["heat"][0] == "watch"
    unk = farmer.card_states(env("Insufficient data", "Insufficient data", "Insufficient data"))
    assert unk["rain"][0] == "unknown" and unk["heat"][0] == "unknown"
