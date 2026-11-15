# AgroIntel Stage 1 — run in Google Colab (paste cells, or upload this file and `%run` it).
#
# Colab setup (cell 1):
#   !pip install -q pandas numpy requests plotly
#   # upload the agrointel/ folder (or clone your GitHub repo), then:
#   import sys; sys.path.insert(0, "/content")          # folder that CONTAINS agrointel/
#
# Cell 2 onward:
from datetime import date, timedelta

import pandas as pd
import plotly.express as px

from agrointel import config
from agrointel.nasa_power import fetch_power_daily, NasaPowerError
from agrointel.cleaning import clean_power, coverage_summary

# 1. Choose a location (any lat/lon on Earth). Default = Western Kenya demo farm.
LAT, LON = config.DEFAULT_LOCATION["lat"], config.DEFAULT_LOCATION["lon"]
END = date.today() - timedelta(days=7)        # POWER lags real time by a few days
START = date(END.year - 10, 1, 1)             # ~10 years: enough for a baseline

# 2. Fetch RAW data (cached on disk; second run is instant).
try:
    result = fetch_power_daily(LAT, LON, START, END)
except NasaPowerError as e:
    raise SystemExit(f"NASA POWER request failed: {e}")
for w in result.warnings:
    print("WARNING:", w)

raw = result.raw
print("\n--- PROVENANCE ---")
for k in ("source", "product", "spatial_resolution", "temporal_resolution",
          "retrieved_utc", "served_from_cache"):
    print(f"{k}: {result.metadata[k]}")
print("units:", result.metadata["units"])

# 3. Inspect RAW before touching anything: how many fill values are there?
print("\n--- RAW CHECK ---")
print(raw.describe().T[["count", "min", "max"]])
print("fill values (-999) per column:\n", (raw <= -990).sum())

# 4. Clean (raw stays untouched).
clean, report = clean_power(raw)
print("\n--- CLEANING REPORT ---")
for k, v in report.to_dict().items():
    print(f"{k}: {v}")
print("\n--- COVERAGE ---")
print(coverage_summary(clean))

# 5. Quick visual sanity check.
px.line(clean.resample("MS").agg({"PRECTOTCORR": "sum"}).reset_index(),
        x="date", y="PRECTOTCORR", title="Monthly rainfall (NASA POWER grid cell, mm)").show()
px.line(clean[["T2M_MIN", "T2M", "T2M_MAX"]].resample("MS").mean().reset_index(),
        x="date", y=["T2M_MIN", "T2M", "T2M_MAX"], title="Monthly temperature (°C)").show()
