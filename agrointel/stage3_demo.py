# AgroIntel Stage 3: environmental stress detection.
#
#   python -m agrointel.stage3_demo
#
# Uses a 30-year NASA POWER history (the usual climate-normal convention) for the
# baseline. SMAP is optional: without Earthdata credentials the soil-based flags
# are skipped and the report says so. First run downloads ~30 years of POWER
# (one request, then cached).
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from agrointel import config
from agrointel.cleaning import clean_power, clean_smap, merge_power_smap
from agrointel.nasa_power import NasaPowerError, fetch_power_daily
from agrointel.smap import SmapError, fetch_smap
from agrointel.stress import analyze_stress, monthly_anomalies

LAT, LON = config.DEFAULT_LOCATION["lat"], config.DEFAULT_LOCATION["lon"]
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
    print("SMAP loaded:", smap_res.metadata["product"])
except SmapError as e:
    print(f"SMAP skipped ({e}). Soil-based flags will be unavailable.")

res = analyze_stress(merged)

pd.set_option("display.width", 200, "display.max_columns", 20, "display.max_colwidth", 48)
print("\n--- INDICATORS ---")
print(res.to_frame()[["value", "unit", "window", "baseline_median", "percentile",
                      "classification", "n_baseline_years"]].round(2))
print("\n--- COMPOSITE FLAGS ---")
for k, v in res.composites.items():
    print(f"{k}: {v['classification']}\n    rule: {v['rule']}")
print("\n--- TRENDS ---")
for t in res.trends:
    print(t["variable"], "->", t["direction"], "| slope/decade:", t["slope_per_decade"],
          "| p:", t["p_value"], "| years:", t["n_years"])
for w in res.warnings:
    print("WARNING:", w)

# Charts: last 5 years of monthly anomalies
m = monthly_anomalies(merged).iloc[-60:]
rows = 3 if "sm_root_anom" in m else 2
fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, subplot_titles=(
    "Rainfall anomaly (% vs same month, all years; POWER grid data)",
    "Temperature anomaly (°C; POWER)",
    "Root-zone soil moisture anomaly (m³/m³; SMAP L4 model estimate)")[:rows])
fig.add_bar(x=m.index, y=m["rain_mm_anom"], row=1, col=1, name="Rain anomaly %")
fig.add_bar(x=m.index, y=m["t_mean_c_anom"], row=2, col=1, name="Temp anomaly °C")
if rows == 3:
    fig.add_bar(x=m.index, y=m["sm_root_anom"], row=3, col=1, name="Root-zone SM anomaly")
fig.update_layout(height=250 * rows + 80, showlegend=False)
fig.show()
