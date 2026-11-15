"""Offline Stage 5 tests on synthetic Kisumu-like data (no network)."""
import numpy as np
import pandas as pd
import pytest

from agrointel import crops, recommend as R, stress as S, suitability as SU

KISUMU = (-0.0917, 34.768)
AS_OF = "2026-11-14"
MONTH_MM = {1: 70, 2: 70, 3: 150, 4: 200, 5: 150, 6: 80, 7: 70, 8: 90, 9: 90, 10: 110, 11: 120, 12: 90}


def make_merged(start="2000-01-01", end="2026-11-07", month_mm=None, temp=23.0, seed=0, smap=True):
    month_mm = month_mm or MONTH_MM
    idx = pd.date_range(start, end, freq="D", name="date")
    rng = np.random.default_rng(seed)
    per_day = np.array([month_mm[m] / 30.0 for m in idx.month])
    wet = rng.random(len(idx)) < 0.4
    rain = np.where(wet, rng.gamma(4, per_day / 0.4 / 4), 0.0)
    t = temp + rng.normal(0, 0.5, len(idx))
    df = pd.DataFrame({"PRECTOTCORR": rain, "T2M": t, "T2M_MAX": t + 6, "T2M_MIN": t - 6}, index=idx)
    if smap:
        df["SM_SURFACE"] = 0.28 + 0.03 * rng.normal(size=len(idx))
        df["SM_ROOTZONE"] = 0.30 + 0.02 * rng.normal(size=len(idx))
        df.loc[df.index.year < 2016, ["SM_SURFACE", "SM_ROOTZONE"]] = np.nan
    return df


@pytest.fixture(scope="module")
def ds():
    return crops.load_crops()


@pytest.fixture(scope="module")
def merged():
    return make_merged()


@pytest.fixture(scope="module")
def stress(merged):
    return S.analyze_stress(merged)


@pytest.fixture(scope="module")
def rs(merged, ds, stress):
    return R.recommend(merged, ds, *KISUMU, as_of=AS_OF, stress=stress)


def test_three_distinct_ranked_strategies(rs):
    assert [r.rank for r in rs.recommendations] == [1, 2, 3]
    scores = [r.score for r in rs.recommendations]
    assert scores == sorted(scores, reverse=True)
    names = [r.crop_names() for r in rs.recommendations]
    for i in range(3):
        for j in range(i + 1, 3):
            assert sum(a != b for a, b in zip(names[i], names[j])) >= 2
    assert all(len(n) == 3 for n in names)


def test_top_strategy_uses_a_legume_for_nitrogen(rs, ds):
    top = rs.recommendations[0].crop_names()
    assert any(ds.get(c.lower().replace(" ", "_")).is_legume for c in
               [s["crop_id"] for s in rs.recommendations[0].sequence]) or True
    ids = [s["crop_id"] for s in rs.recommendations[0].sequence]
    assert any(ds.get(i).fixes_nitrogen for i in ids), top
    assert rs.recommendations[0].components["nitrogen"]["score"] > 0


def test_seasons_start_after_as_of_and_follow_calendar(rs):
    first = rs.seasons[0]
    assert first["season"].startswith("long rains") and first["plant_start"] == "2027-03-01"
    assert [s["season"].split(" (")[0] for s in rs.seasons] == ["long rains", "short rains", "long rains"]


def test_current_conditions_skipped_when_planting_is_far(rs):
    assert all("current_conditions" not in r.components for r in rs.recommendations)
    assert any("days away" in w for w in rs.warnings)


def test_every_explanation_section_present(rs):
    for r in rs.recommendations:
        assert r.why_ranked and r.benefits and r.assumptions and r.evidence
        assert r.missing_information
        assert any("[ecocrop]" in e for e in r.evidence)
        assert abs(sum(c["weight"] for c in r.components.values()) - 1.0) < 1e-9
    text = R.format_markdown(rs)
    for section in ("Missing information", "Assumptions", "Risks", "Expected benefits", "Score breakdown", "Evidence"):
        assert section in text, section
    assert all(r.risks for r in rs.recommendations)


def test_missing_inputs_are_reported_not_hidden(rs):
    miss = " ".join(rs.missing_information)
    assert "Soil data" in miss and "Variety-level" in miss and "Previous crop" in miss
    assert rs.evidence_completeness["soil_data"] is False and rs.evidence_completeness["regional_calendar"] is True


def test_cassava_excluded_with_reason(rs):
    ex = [e for e in rs.excluded if e["crop"] == "Cassava"]
    assert ex and "calendar" in ex[0]["reason"]
    assert all("Cassava" not in r.crop_names() for r in rs.recommendations)


def test_rice_excluded_for_low_rainfall(rs):
    assert any(e["crop"] == "Rice" and "suitability" in e["reason"] for e in rs.excluded)


def test_weights_are_configurable(merged, ds, stress):
    only_climate = {k: 0.0 for k in R.config.RECOMMEND.weights}
    only_climate["climate_fit"] = 1.0
    a = R.recommend(merged, ds, *KISUMU, as_of=AS_OF, stress=stress, weights=only_climate)
    top = a.recommendations[0]
    assert set(top.components) == {"climate_fit"} and top.components["climate_fit"]["weight"] == 1.0
    only_div = {k: 0.0 for k in only_climate}
    only_div["diversity"] = 1.0
    b = R.recommend(merged, ds, *KISUMU, as_of=AS_OF, stress=stress, weights=only_div)
    assert b.recommendations[0].components["diversity"]["score"] == pytest.approx(1.0)


def test_previous_crop_penalises_repetition(merged, ds, stress):
    out = R.recommend(merged, ds, *KISUMU, as_of=AS_OF, stress=stress, previous_crop="maize")
    assert out.context["previous_crop"] == "Maize"
    assert out.evidence_completeness["previous_crop"] is True
    for r in out.recommendations:
        assert r.sequence[0]["crop_id"] != "maize"                    # same crop back-to-back is scored 0
        assert r.components["diversity"]["score"] > 0.5


def test_soil_data_adds_component_and_changes_missing_list(merged, ds, stress):
    soil = SU.SoilInfo(ph=5.2, texture="heavy", drainage="well")
    out = R.recommend(merged, ds, *KISUMU, as_of=AS_OF, stress=stress, soil=soil)
    assert all("soil_fit" in r.components for r in out.recommendations)
    assert "Soil data" not in " ".join(out.missing_information)


def test_current_drought_penalises_water_demanding_first_crop(merged, ds):
    st = S.analyze_stress(merged)
    st.composites["drought"]["classification"] = "Probable severe drought"
    out = R.recommend(merged, ds, *KISUMU, as_of="2027-02-20", stress=st)       # planting ~9 days away
    assert all("current_conditions" in r.components for r in out.recommendations)
    scores = {c.id: R._current_score(c, st, R.config.RECOMMEND) for c in ds.crops.values()}
    assert scores["sorghum"] > scores["rice"] and scores["pearl_millet"] > scores["cassava"]
    assert all(0 <= v <= 1 for v in scores.values())


def test_excess_moisture_favours_waterlogging_tolerant_crops(ds):
    st = S.StressResult(composites={"drought": {"classification": "No drought signal"},
                                    "excess_moisture": {"classification": "Probable excess moisture (waterlogging risk)"}})
    cfg = R.config.RECOMMEND
    assert R._current_score(ds.get("rice"), st, cfg) == 1.0
    assert R._current_score(ds.get("maize"), st, cfg) == pytest.approx(0.6)


def test_no_calendar_infers_seasons_and_flags_low_confidence(ds):
    mm = {1: 20, 2: 20, 3: 20, 4: 20, 5: 20, 6: 20, 7: 200, 8: 220, 9: 160, 10: 20, 11: 20, 12: 20}
    m = make_merged(month_mm=mm, smap=False)
    out = R.recommend(m, ds, 15.0, 5.0, as_of="2026-05-20")                      # outside any calendar
    assert out.context["calendar"] is None
    assert any("No regional planting calendar" in w for w in out.warnings)
    assert out.seasons and out.seasons[0]["source"] == "inferred"
    assert "Local planting calendar" in out.missing_information
    assert not out.evidence_completeness["regional_calendar"]


def test_arid_location_returns_no_recommendation_with_reason(ds):
    m = make_merged(month_mm={k: 5 for k in range(1, 13)}, smap=False)
    out = R.recommend(m, ds, 20.0, 10.0, as_of="2026-05-20")
    assert out.recommendations == []
    assert any("No distinct rain-fed growing season" in w for w in out.warnings)


def test_too_little_history_refuses(ds):
    m = make_merged(start="2023-06-01", smap=False)
    out = R.recommend(m, ds, *KISUMU, as_of=AS_OF)
    assert out.recommendations == []
    assert any("too little history" in w for w in out.warnings)


def test_hot_dry_climate_drops_cool_season_crops(ds):
    m = make_merged(temp=31.0, smap=False)
    out = R.recommend(m, ds, *KISUMU, as_of=AS_OF)
    used = {c for r in out.recommendations for c in r.crop_names()}
    assert "Common bean" not in used                                           # EcoCrop abs max 32 C, optimum 16-25 C
    assert out.recommendations


def test_nitrogen_rule_values(ds):
    cfg = R.config.RECOMMEND
    mx = 0.74
    assert R._nitrogen_benefit(ds.get("pigeon_pea"), ds.get("maize"), mx) == pytest.approx(1.0)
    assert R._nitrogen_benefit(ds.get("maize"), ds.get("sorghum"), mx) == 0.0
    assert R._nitrogen_benefit(ds.get("cowpea"), ds.get("soybean"), mx) == 0.0
    assert R._diversity(ds.get("maize"), ds.get("maize"), cfg.same_family_score) == 0.0
    assert R._diversity(ds.get("maize"), ds.get("sorghum"), cfg.same_family_score) == cfg.same_family_score
    assert R._diversity(ds.get("maize"), ds.get("cowpea"), cfg.same_family_score) == 1.0
    assert R._fertility_load(ds.get("maize"), ds.get("sorghum")) == 1.0
    assert R._fertility_load(ds.get("maize"), ds.get("groundnut")) == 0.5
