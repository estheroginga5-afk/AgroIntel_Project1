# AgroIntel Stage 4: inspect the crop requirements dataset (works offline).
#   python -m agrointel.stage4_demo
import pandas as pd

from agrointel import config
from agrointel.crops import calendar_applies, calendar_for, load_crops

pd.set_option("display.width", 220, "display.max_columns", 30, "display.max_colwidth", 40)
ds = load_crops()

rows = []
for c in ds.crops.values():
    rows.append({
        "crop": c.common_name, "group": c.group, "family": c.family,
        "T opt °C": f"{c.temp_opt[0]:g}-{c.temp_opt[1]:g}",
        "T abs °C": f"{c.temp_abs[0]:g}-{c.temp_abs[1]:g}",
        "rain opt mm": f"{c.rain_opt_mm[0]:g}-{c.rain_opt_mm[1]:g}",
        "cycle d": f"{c.cycle_days[0]:g}-{c.cycle_days[1]:g}",
        "pH opt": f"{c.ph_opt[0]:g}-{c.ph_opt[1]:g}",
        "fertility": c.fertility_requirement,
        "fixes N": c.fixes_nitrogen,
        "%Ndfa": (c.n_fixation or {}).get("pct_ndfa"),
    })
print("--- CROP REQUIREMENTS (FAO EcoCrop + curated layer) ---")
print(pd.DataFrame(rows).to_string(index=False))

print("\n--- PROVENANCE / GAPS ---")
print(pd.DataFrame(ds.provenance_table()).to_string(index=False))

print("\n--- NOT YET COVERED (will limit recommendation confidence) ---")
for item in ds.not_yet_covered:
    print(" -", item)

lat, lon = config.DEFAULT_LOCATION["lat"], config.DEFAULT_LOCATION["lon"]
m = calendar_for(ds, lat, lon)
print(f"\n--- PLANTING CALENDAR for demo farm ({lat}, {lon}) ---")
if m is None:
    print("No local calendar in the dataset: seasons must be inferred from climate data.")
else:
    print(m.calendar["name"], "| applies to:", m.calendar["applies_to"])
    for s in m.calendar["seasons"]:
        print(f"  {s['name']}: plant months {s['plant_months']}, harvest months {s['harvest_months']}")
    print("  covered crops:", [c.common_name for c in ds.crops.values() if calendar_applies(m.calendar, c)])
    for w in m.warnings:
        print("  CAVEAT:", w)

print("\n--- SOURCES ---")
for sid, s in ds.sources.items():
    print(f"[{sid}] {s['citation']}\n    limitation: {s['limitations']}")
