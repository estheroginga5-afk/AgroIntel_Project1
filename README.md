# AgroIntel

Climate and crop-rotation decision support using NASA Earth observation data.
**Status: Stage 6 of 6** (POWER + SMAP, cleaning, stress detection, crop dataset, recommendations, Streamlit dashboard).

## Layout
```
agrointel/
  config.py         settings, parameter metadata, cleaning thresholds (no secrets)
  nasa_power.py     POWER daily point client: retries, disk cache, provenance
  smap.py           SMAP L4 soil moisture via NASA AppEEARS point sampling
  cleaning.py       raw -> clean for POWER and SMAP; merge_power_smap()
  stress.py         percentile-baseline stress indicators, drought/excess/heat flags, trends
  crops.py          crop requirements loader, validation, regional planting calendars
  suitability.py    season windows, historical crop-season fit, soil fit
  recommend.py      rule-based rotation scoring, top 3 strategies, explanations, markdown report
  data/             ecocrop_subset.csv (FAO EcoCrop rows) + crops_overlay.json (curated, sourced)
  colab_stage1.py   Stage 1 walkthrough (POWER only)
  stage2_demo.py    Stage 2 walkthrough (SMAP + merge + chart)
  stage3_demo.py    Stage 3 walkthrough (30-year baseline, stress report, anomaly charts)
  stage4_demo.py    Stage 4 walkthrough (crop table, provenance, calendar; works offline)
  stage5_demo.py    Stage 5 walkthrough (full pipeline to ranked recommendations + saved report)
  pipeline.py       fetch -> clean -> merge -> stress -> recommend orchestration
  locations.py      example places and place-name search (OpenStreetMap Nominatim)
  report.py         downloadable Markdown analysis report
  auth.py           sign-up / log-in (SQLite, salted PBKDF2 hashes, lockout)
  theme.py          agricultural palette, hero banner, CSS
  map_view.py       satellite / NASA NDVI / terrain map layers
  dashboard.py      Streamlit UI (all science stays in the modules above)
  tests/            offline tests (no network or credentials needed)
app.py              Streamlit entry point
requirements.txt
.streamlit/         config.toml, secrets.toml.example
```

## Credentials (SMAP only; POWER needs none)
Free NASA Earthdata account: https://urs.earthdata.nasa.gov
Sign in once at https://appeears.earthdatacloud.nasa.gov to accept its terms.
Provide `EARTHDATA_USERNAME` and `EARTHDATA_PASSWORD` as environment variables,
Streamlit secrets or Colab secrets. Never put them in code or commit them.

## Run
```
pip install -r requirements.txt
python -m pytest -q
python -m agrointel.colab_stage1
python -m agrointel.stage2_demo
python -m agrointel.stage3_demo
python -m agrointel.stage4_demo
python -m agrointel.stage5_demo
streamlit run app.py
```

## Data honesty
- NASA POWER: model/satellite-derived grid cells (~0.5° x 0.625°), not farm sensors.
- SMAP L4 (SPL4SMGP): a land-model ESTIMATE constrained by SMAP satellite data, 9 km cells.
  Not a direct measurement and not a soil probe.
- Rainfall and soil moisture gaps are never interpolated; outliers are flagged, never removed.
- NSIDC reports a geolocation issue in SMAP data for 14 May to 28 July 2026, with L4 reprocessing pending.

## How stress is measured (Stage 3)
Each indicator compares the latest window (e.g. last 30 or 90 days) with the same calendar
window in every other year of the location's own record, and reports the percentile and a class.
Drought and excess-moisture flags are transparent heuristics built from those percentiles,
not official indices (SPI, PDSI, USDM). All thresholds live in `StressConfig` in `config.py`.
SMAP baselines are short (record starts 2015); the code says "Insufficient data" instead of guessing.

## Crop dataset (Stage 4)
13 crops: maize, common bean, sorghum, finger millet, pearl millet, cowpea, groundnut, soybean,
green gram, pigeon pea, cassava, sweet potato, rice. Climate, soil and cycle ranges come from FAO
EcoCrop. Nitrogen fixation (%Ndfa) comes from Herridge et al. 2021; rotation yield gains from a
single-site study (Lengwati et al. 2020) and are labelled as such. Unknown values are `None`, never guessed.
`not_yet_covered` in `crops_overlay.json` lists what is missing (variety data, per-crop drought classes,
pest/disease breaks, crop-specific calendars). Only a grouped Western Kenya calendar is included;
elsewhere the app must say no local calendar exists.
To add a crop: append its EcoCrop row to `ecocrop_subset.csv` and an entry to `crops_overlay.json`.

## How recommendations are made (Stage 5)
A strategy is a sequence of crops over the next 3 growing seasons. Each candidate is scored from seven
components (historical climate fit, reliability across past seasons, current drought/moisture signals when
planting is near, soil match if soil data is supplied, legume nitrogen carried to the next crop, avoiding
back-to-back high fertility-demand crops, avoiding repeated crops/families). Weights are in
`RecommendationConfig` (config.py), normalised over the components that are available, and can be overridden
per call: `recommend(..., weights={"nitrogen": 0.3})`. They are expert-judgement defaults, not calibrated.
The top three strategies must differ in at least two seasons. Every strategy lists benefits, risks,
assumptions, missing information and the sources used. Pest and disease effects are only represented by a
generic no-repeat rule because no crop-pair evidence is in the dataset. Scores rank options; they are not yield forecasts.
Known weakness: EcoCrop ranges act as hard filters, so a crop that farmers do grow locally can be excluded if
its EcoCrop range is narrow. The excluded-crops list shows every such decision.

## Dashboard (Stage 6)
`streamlit run app.py`, pick a location (examples, place search, or lat/lon), press **Run analysis**.
Shows the top 3 strategies with explanations, stress classes, rainfall/temperature/soil-moisture charts,
data provenance, and downloads (cleaned CSV with flag columns, Markdown report). If SMAP credentials are
missing or SMAP fails, the app still runs and says soil moisture is unavailable. Scoring weights can be
changed in the sidebar. Place search needs internet; set `AGROINTEL_CONTACT` (Nominatim usage policy).

## Deploy to Streamlit Community Cloud
1. Push the project to a GitHub repo (`.gitignore` already excludes secrets and the cache).
2. At https://share.streamlit.io choose the repo, branch, and main file `app.py`.
3. In App settings > Secrets paste the three keys from `.streamlit/secrets.toml.example`.
4. The cache lives on the app's temporary disk and is rebuilt after restarts; the first run per location is slow.

## Accounts and map (Stage 6b)
- Users sign up and log in on the first screen. Passwords are stored only as salted PBKDF2-SHA256 hashes in
  `agrointel/.data/users.db` (git-ignored; override with `AGROINTEL_DB`). Five failed log-ins lock the account for 5 minutes.
- **Streamlit Community Cloud wipes its disk on restart, so accounts created there will not persist.** For a real
  deployment, back `UserStore` (auth.py) with a hosted database such as Supabase or Postgres; the UI does not change.
- Map layers: Esri satellite imagery (vegetation appears green, water blue), NASA GIBS MODIS NDVI vegetation index
  (greener = denser vegetation, about 250 m, 8-day composite), and Esri terrain with rivers and lakes.
  Imagery is context only and does not feed the analysis.

## Farmer view
The default "Simple" view (sidebar > View) shows three traffic-light cards (rain, heat, soil water), an animated farm
scene, and the three plans as crop pictures with a few plain-language reasons. English and Kiswahili are built in
(`farmer.py`; add a language by adding a block to `T`). It uses the same results as the Detailed view and says
"No data" instead of guessing. Kiswahili wording should be checked by a native speaker or extension officer.
