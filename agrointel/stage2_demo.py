# AgroIntel Stage 2: fetch SMAP soil moisture, clean it, align with POWER.
#
# Run from the project root (the folder containing agrointel/):
#   Windows PowerShell:
#     $env:EARTHDATA_USERNAME="your_username"; $env:EARTHDATA_PASSWORD="your_password"
#   Mac/Linux:
#     export EARTHDATA_USERNAME=your_username EARTHDATA_PASSWORD=your_password
#   python -m agrointel.stage2_demo
#
# In Colab: add both names in the Secrets panel (key icon) and enable notebook access.
# The first SMAP run submits a NASA AppEEARS task and may take several minutes.
from datetime import date, timedelta

import plotly.graph_objects as go

from agrointel import config
from agrointel.cleaning import clean_power, clean_smap, coverage_summary, merge_power_smap
from agrointel.nasa_power import NasaPowerError, fetch_power_daily
from agrointel.smap import SmapAuthError, SmapError, fetch_smap

LAT, LON = config.DEFAULT_LOCATION["lat"], config.DEFAULT_LOCATION["lon"]
END = date.today() - timedelta(days=7)
START = date(END.year - 10, 1, 1)

# 1. POWER (Stage 1, cached)
try:
    power = fetch_power_daily(LAT, LON, START, END)
except NasaPowerError as e:
    raise SystemExit(f"NASA POWER failed: {e}")
power_clean, _ = clean_power(power.raw)

# 2. SMAP
try:
    smap_res = fetch_smap(LAT, LON, end=END)
except SmapAuthError as e:
    raise SystemExit(f"Credentials problem: {e}")
except SmapError as e:
    raise SystemExit(f"SMAP failed: {e}")
for w in smap_res.warnings:
    print("WARNING:", w)

print("\n--- SMAP PROVENANCE ---")
for k in ("source", "product", "product_description", "spatial_resolution",
          "temporal_resolution", "retrieved_utc", "served_from_cache", "cell_lat", "cell_lon"):
    print(f"{k}: {smap_res.metadata.get(k)}")
print("limitations:")
for l in smap_res.metadata["limitations"]:
    print("  -", l)

print("\n--- SMAP RAW (sub-daily, fill values kept) ---")
print(smap_res.raw.describe().T[["count", "min", "max"]])

smap_clean, rep = clean_smap(smap_res.raw)
print("\n--- SMAP CLEANING REPORT ---")
for k, v in rep.to_dict().items():
    print(f"{k}: {v}")

merged = merge_power_smap(power_clean, smap_clean)
print("\n--- MERGED COVERAGE ---")
print(coverage_summary(merged))
print("days with SMAP:", int(merged["smap_available"].sum()), "of", len(merged))

# 3. Look at it: soil moisture vs rainfall over the SMAP period
m = merged[merged["smap_available"]]
fig = go.Figure()
fig.add_bar(x=m.index, y=m["PRECTOTCORR"], name="Rain (mm/day, POWER)", yaxis="y2", opacity=0.35)
fig.add_scatter(x=m.index, y=m["SM_SURFACE"], name="Surface SM (SMAP L4 model)")
fig.add_scatter(x=m.index, y=m["SM_ROOTZONE"], name="Root-zone SM (SMAP L4 model)")
fig.update_layout(title="Soil moisture (model estimate) vs rainfall (grid data)",
                  yaxis=dict(title="m³/m³"),
                  yaxis2=dict(title="mm/day", overlaying="y", side="right"))
fig.show()
