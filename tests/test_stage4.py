"""Offline Stage 4 tests for the crop dataset."""
import csv
import json
import shutil

import pytest

from agrointel import crops


@pytest.fixture(scope="module")
def ds():
    return crops.load_crops()


def test_all_crops_load_with_core_fields(ds):
    assert len(ds.crops) == 13
    for c in ds.crops.values():
        assert c.temp_opt[0] is not None and c.rain_opt_mm[0] is not None and c.cycle_days[0] is not None
        assert c.family and c.group


def test_maize_matches_fao_ecocrop_sheet(ds):
    # Values read directly from the FAO EcoCrop data sheet for Zea mays (EcoPort 2175).
    m = ds.get("maize")
    assert m.temp_opt == (18, 33) and m.temp_abs == (10, 47)
    assert m.rain_opt_mm == (600, 1200) and m.rain_abs_mm == (400, 1800)
    assert m.ph_opt == (5, 7) and m.ph_abs == (4.5, 8.5)
    assert m.cycle_days == (65, 365) and m.altitude_max_m == 4000


def test_no_validation_issues_in_shipped_data(ds):
    assert all(not c.data_issues for c in ds.crops.values())


def test_legumes_flagged_from_family_and_have_fixation_values(ds):
    legumes = {c.id for c in ds.crops.values() if c.fixes_nitrogen}
    assert legumes == {"common_bean", "cowpea", "groundnut", "soybean", "green_gram", "pigeon_pea"}
    assert all(c.group == "grain_legume" for c in ds.crops.values() if c.fixes_nitrogen)
    assert all(c.n_fixation and c.n_fixation["pct_ndfa"] for c in ds.crops.values() if c.fixes_nitrogen)
    assert ds.get("maize").n_fixation is None


def test_every_curated_number_cites_a_registered_source(ds):
    for c in ds.crops.values():
        for s in c.sources:
            assert s in ds.sources, (c.id, s)
    for s in ds.sources.values():
        assert s["citation"] and s["accessed"] and s["limitations"]


def test_single_site_evidence_is_labelled(ds):
    for cid in ("cowpea", "groundnut", "green_gram"):
        rb = ds.get(cid).rotation_benefit
        assert "single site" in rb["evidence"]
    assert "ONE site" in ds.sources["lengwati2020"]["limitations"]


def test_unknowns_are_none_not_guessed(ds):
    assert ds.get("maize").kill_temp_early_c is None
    assert "kill temperature" in ds.get("maize").missing
    assert ds.get("soybean").rotation_benefit is None


def test_calendar_lookup_and_applicability(ds):
    m = crops.calendar_for(ds, -0.0917, 34.768)            # Kisumu demo farm
    assert m and m.calendar["id"] == "kenya_west_medium_altitude"
    assert any("unimodal" in w for w in m.warnings)
    assert crops.calendar_applies(m.calendar, ds.get("maize"))
    assert crops.calendar_applies(m.calendar, ds.get("cowpea"))
    assert not crops.calendar_applies(m.calendar, ds.get("cassava"))
    assert crops.calendar_for(ds, 52.5, 13.4) is None       # no local calendar elsewhere: say so


def test_validator_flags_bad_ranges_and_quarantines_kill_temp(tmp_path):
    src = crops.ECOCROP_CSV
    bad = tmp_path / "eco.csv"
    rows = list(csv.DictReader(open(src, encoding="utf-8")))
    for r in rows:
        if r["EcoPortCode"] == "2175":
            r["TOPMN"], r["TOPMX"] = "40", "20"            # reversed optimum
            r["KTMP"] = "50"                               # implausible killing temperature
    with open(bad, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader(); w.writerows(rows)
    ds2 = crops.load_crops(bad)
    m = ds2.get("maize")
    assert any("temperature not ordered" in i for i in m.data_issues)
    assert m.kill_temp_early_c is None
    assert any("kill_temp" in i for i in m.data_issues)


def test_unknown_source_is_rejected(tmp_path):
    ov = json.loads(crops.OVERLAY_JSON.read_text(encoding="utf-8"))
    ov["crops"]["cowpea"]["n_fixation"]["src"] = "made_up_2024"
    p = tmp_path / "o.json"
    p.write_text(json.dumps(ov), encoding="utf-8")
    with pytest.raises(ValueError):
        crops.load_crops(overlay_json=p)


def test_extensible_new_crop(tmp_path):
    """Adding a crop = one EcoCrop row + one overlay entry."""
    rows = list(csv.DictReader(open(crops.ECOCROP_CSV, encoding="utf-8")))
    new = dict(rows[0]); new.update(EcoPortCode="999999", ScientificName="Testcrop novus")
    rows.append(new)
    csv_p = tmp_path / "eco.csv"
    with open(csv_p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
    ov = json.loads(crops.OVERLAY_JSON.read_text(encoding="utf-8"))
    ov["crops"]["testcrop"] = {"common_name": "Test crop", "ecocrop_code": 999999, "group": "cereal"}
    ov_p = tmp_path / "o.json"; ov_p.write_text(json.dumps(ov), encoding="utf-8")
    ds2 = crops.load_crops(csv_p, ov_p)
    assert "testcrop" in ds2.ids() and len(ds2.crops) == 14
