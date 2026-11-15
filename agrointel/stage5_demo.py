# AgroIntel Stage 5: ranked crop-rotation recommendations.
#
#   python -m agrointel.stage5_demo
#
# Needs internet for NASA POWER (30 years, cached after the first run). SMAP is optional:
# without Earthdata credentials the satellite soil-moisture inputs are skipped and the report says so.
# Edit the three settings below to try other inputs.
from datetime import date, timedelta
from pathlib import Path

from agrointel import config
from agrointel.cleaning import clean_power, clean_smap, merge_power_smap
from agrointel.crops import load_crops
from agrointel.nasa_power import NasaPowerError, fetch_power_daily
from agrointel.recommend import format_markdown, recommend
from agrointel.smap import SmapError, fetch_smap
from agrointel.stress import analyze_stress
from agrointel.suitability import SoilInfo

# ---- settings you can change -------------------------------------------------
LAT, LON = config.DEFAULT_LOCATION["lat"], config.DEFAULT_LOCATION["lon"]
PREVIOUS_CROP = None          # e.g. "maize", "common_bean", "sorghum" (see crop ids in stage4_demo)
SOIL = None                   # e.g. SoilInfo(ph=5.8, texture="medium", drainage="well")
# ------------------------------------------------------------------------------

END = date.today() - timedelta(days=7)
START = date(END.year - 30, 1, 1)
try:
    power = fetch_power_daily(LAT, LON, START, END)
except NasaPowerError as e:
    raise SystemExit(f"NASA POWER failed: {e}")
merged, _ = clean_power(power.raw)
try:
    smap_res = fetch_smap(LAT, LON, end=END)
    smap_clean, _ = clean_smap(smap_res.raw)
    merged = merge_power_smap(merged, smap_clean)
except SmapError as e:
    print(f"SMAP skipped ({e}).")

stress = analyze_stress(merged)
ds = load_crops()
rs = recommend(merged, ds, LAT, LON, stress=stress, previous_crop=PREVIOUS_CROP, soil=SOIL)

report = format_markdown(rs)
print(report)
out = Path("agrointel_report.md")
out.write_text(report, encoding="utf-8")
print(f"\nSaved to {out.resolve()}")

print("\nCrops left out, and why:")
for e in rs.excluded:
    print(f"  - {e['crop']} ({e['season']}): {e['reason']}")
