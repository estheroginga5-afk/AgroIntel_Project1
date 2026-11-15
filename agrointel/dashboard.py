"""Streamlit dashboard. All science lives in the other modules; this file only displays it.

Run from the project root:  streamlit run app.py
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from . import config, theme
from .auth import AuthError, UserStore
from .map_view import ATTRIBUTION, LAYERS, build_map
from .crops import load_crops
from .locations import PRESETS, LocationError, geocode
from .pipeline import PipelineError, load_environment, make_recommendations
from .recommend import COMPONENT_LABELS
from .report import build_report
from .stress import INSUFFICIENT, monthly_anomalies
from .suitability import SoilInfo

CLASS_COLORS = {"Normal": "#2e7d32", "Insufficient data": "#9e9e9e"}


def _apply_dashboard_styles() -> None:
    """Apply lightweight AgroIntel styling while keeping Streamlit components native."""
    st.markdown(
        """
        <style>
        :root { --agro-green: #1f6b45; --agro-dark: #173b2b; --agro-soft: #edf5ef; }
        .stApp { background: linear-gradient(180deg, #f7faf7 0%, #ffffff 34%); }
        [data-testid="stSidebar"] { background: #f0f5f0; border-right: 1px solid #dce8dd; }
        [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 { color: var(--agro-dark); }
        h1, h2, h3 { letter-spacing: -0.025em; }
        div[data-testid="stMetric"] { background: #f4f8f4; border: 1px solid #e1ebe2;
            padding: 0.8rem 1rem; border-radius: 0.85rem; }
        div[data-testid="stMetric"] label { color: #52705c; }
        div.stButton > button[kind="primary"] { background: var(--agro-green); border-color: var(--agro-green); }
        div.stButton > button[kind="primary"]:hover { background: var(--agro-dark); border-color: var(--agro-dark); }
        div[data-testid="stTabs"] button[aria-selected="true"] { color: var(--agro-green); }
        div[data-testid="stAlert"] { border-radius: 0.75rem; }
        .agro-eyebrow { color: #52705c; text-transform: uppercase; letter-spacing: .12em;
            font-size: .72rem; font-weight: 700; margin-bottom: .25rem; }
        .agro-note { color: #607568; font-size: .9rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )
SEVERE_WORDS = ("severe", "extreme", "very", "high", "wet", "dry", "hot", "cold", "drought", "excess")


# ------------------------------------------------------------------ cached helpers
@st.cache_resource(show_spinner=False)
def _crops():
    return load_crops()


@st.cache_data(show_spinner=False, ttl=6 * 3600)
def _env(lat: float, lon: float, years: int, use_smap: bool):
    return load_environment(lat, lon, years=years, use_smap=use_smap)


@st.cache_data(show_spinner=False, ttl=3600)
def _search(q: str):
    return geocode(q)


# ------------------------------------------------------------------ sidebar
def _sidebar() -> dict:
    sb = st.sidebar
    sb.header("Location")
    names = [p["name"] for p in PRESETS]
    preset = sb.selectbox("Example locations", names, index=0, key="preset")
    p = next(x for x in PRESETS if x["name"] == preset)
    if st.session_state.get("_last_preset") != preset:           # only overwrite inputs when the preset changes
        st.session_state["_last_preset"] = preset
        st.session_state["lat"], st.session_state["lon"] = p["lat"], p["lon"]
        st.session_state["place_name"] = p["name"]
    sb.caption(p["note"])

    with sb.expander("Search a place by name"):
        q = st.text_input("Place name", key="place_q", placeholder="e.g. Eldoret, Kenya")
        if st.button("Search", key="do_search"):
            if not q.strip():
                st.warning("Enter a town, county, region or place name first.")
            else:
                try:
                    st.session_state["search_results"] = _search(q.strip())
                except LocationError as e:
                    st.session_state["search_results"] = []
                    st.warning(str(e))
        res = st.session_state.get("search_results") or []
        if res:
            pick = st.selectbox("Results", range(len(res)), format_func=lambda i: res[i]["name"][:70], key="pick")
            if st.button("Use this place", key="use_place"):
                st.session_state["lat"], st.session_state["lon"] = res[pick]["lat"], res[pick]["lon"]
                st.session_state["place_name"] = res[pick]["name"].split(",")[0]
                st.rerun()

    lat = sb.number_input("Latitude", -90.0, 90.0, step=0.01, format="%.4f", key="lat")
    lon = sb.number_input("Longitude", -180.0, 180.0, step=0.01, format="%.4f", key="lon")
    years = sb.slider("Years of climate history", 10, 40, 30, help="Longer records give better baselines.")
    use_smap = sb.checkbox("Include SMAP soil moisture", value=True,
                           help="Needs free NASA Earthdata credentials. The first fetch can take several minutes.")

    sb.header("Farm context (optional)")
    ds = _crops()
    ids = ["(none)"] + ds.ids()
    prev = sb.selectbox("Previous crop", ids, format_func=lambda i: i if i == "(none)" else ds.get(i).common_name)
    ph = sb.number_input("Soil pH (0 = unknown)", 0.0, 14.0, 0.0, step=0.1)
    tex = sb.selectbox("Soil texture", ["unknown", "light", "medium", "heavy", "organic"])
    drn = sb.selectbox("Drainage", ["unknown", "poorly", "well", "excessive"])
    soil = None
    if ph > 0 or tex != "unknown" or drn != "unknown":
        soil = SoilInfo(ph=ph or None, texture=None if tex == "unknown" else tex,
                        drainage=None if drn == "unknown" else drn)

    with sb.expander("Scoring weights (advanced)"):
        st.caption("Expert-judgement defaults, not calibrated. They are normalised, so only ratios matter.")
        weights = {k: st.slider(COMPONENT_LABELS[k], 0.0, 1.0, float(v), 0.05, key=f"w_{k}")
                   for k, v in config.RECOMMEND.weights.items()}
        if sum(weights.values()) == 0:
            st.warning("All weights are zero; defaults will be used.")
            weights = None
    run = sb.button("Run analysis", type="primary", width="stretch")
    return dict(lat=lat, lon=lon, years=years, use_smap=use_smap, previous_crop=None if prev == "(none)" else prev,
                soil=soil, weights=weights, run=run, name=st.session_state.get("place_name", ""))


# ------------------------------------------------------------------ sections
def _map(lat, lon, name):
    st.subheader("Farm location")
    st.caption("Select a location in the sidebar. The marker identifies the analysis grid cell, not an exact farm boundary.")
    choice = st.radio("Map layer", list(LAYERS), horizontal=True, label_visibility="collapsed")
    kind = LAYERS[choice]
    st.plotly_chart(build_map(lat, lon, kind, label=name or "Selected point"), width="stretch",
                    config={"scrollZoom": True, "displaylogo": False})
    st.caption(f"{name or 'Selected point'}: {lat:.4f}, {lon:.4f}. {ATTRIBUTION[kind]}. "
               "Imagery is for context only; the analysis uses the surrounding grid cell, not this exact spot.")


def _badge(cls: str) -> str:
    low = cls.lower()
    if cls == "Normal" or low.startswith(("no ", "near normal", "normal")):
        return f":green[{cls}]"
    if cls == INSUFFICIENT:
        return f":gray[{cls}]"
    return f":orange[{cls}]" if not any(w in low for w in ("severe", "extreme")) else f":red[{cls}]"


def _indicators(env):
    st.subheader("Current conditions vs this location's own history")
    st.caption("Each value is compared with the same calendar window in other years. "
               "'Observed' = POWER grid data; 'modelled' = SMAP model estimate; 'derived' = computed by AgroIntel.")
    comps = env.stress.composites
    cols = st.columns(len(comps) or 1)
    for col, (k, v) in zip(cols, comps.items()):
        with col:
            st.markdown(f"**{k.replace('_', ' ').title()}**")
            st.markdown(_badge(v["classification"]))
            st.caption(f"{v['kind']} heuristic")
            with st.popover("Rule"):
                st.write(v["rule"])
    rows = [{"Indicator": i.label, "Window": i.window,
             "Value": None if i.value is None else round(i.value, 2), "Unit": i.unit,
             "Typical (median)": None if i.baseline_median is None else round(i.baseline_median, 2),
             "Percentile": None if i.percentile is None else round(i.percentile),
             "Class": i.classification, "Basis": i.kind, "Baseline yrs": i.n_baseline_years} for i in env.stress.indicators]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _rain_temp(env):
    m = monthly_anomalies(env.merged)
    st.subheader("Rainfall and temperature")
    tab1, tab2, tab3 = st.tabs(["Monthly rainfall", "Temperature", "Annual trends"])
    with tab1:
        recent = m.tail(60)
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_bar(x=recent.index, y=recent["rain_mm"], name="Monthly rainfall (mm)", marker_color=theme.PALETTE["sky"], secondary_y=False)
        fig.add_scatter(x=recent.index, y=recent["rain_mm_clim"], name="Typical month (median)", mode="lines", line=dict(color=theme.PALETTE["wheat"], width=3), secondary_y=False)
        fig.update_yaxes(title_text="mm", secondary_y=False)
        fig.update_layout(height=340, margin=dict(t=20, b=10), legend=dict(orientation="h"))
        st.plotly_chart(fig, width="stretch")
        st.caption("NASA POWER PRECTOTCORR (bias-corrected precipitation), grid-cell estimate. Last 5 years; "
                   "months with under 80% coverage are left blank.")
    with tab2:
        d = env.merged.tail(730)
        fig = go.Figure()
        for col, nm, colr in (("T2M_MAX", "Daily max", "#e65100"), ("T2M", "Daily mean", theme.PALETTE["leaf"]),
                              ("T2M_MIN", "Daily min", theme.PALETTE["sky"])):
            if col in d:
                fig.add_scatter(x=d.index, y=d[col], name=nm, mode="lines", line=dict(width=1, color=colr))
        fig.add_hline(y=config.STRESS.heat_threshold_c, line_dash="dot",
                      annotation_text=f"heat threshold {config.STRESS.heat_threshold_c:g} °C")
        fig.update_layout(height=340, margin=dict(t=20, b=10), yaxis_title="°C", legend=dict(orientation="h"))
        st.plotly_chart(fig, width="stretch")
        st.caption("NASA POWER 2 m air temperature, last 2 years. The heat threshold is a configurable setting, not a crop-specific limit.")
    with tab3:
        a = env.merged.copy()
        ann = pd.DataFrame({"Annual rainfall (mm)": a["PRECTOTCORR"].resample("YS").sum(min_count=300),
                            "Mean temperature (°C)": a["T2M"].resample("YS").mean()}).dropna(how="all")
        ann.index = ann.index.year
        c1, c2 = st.columns(2)
        for c, col in zip((c1, c2), ann.columns):
            f = go.Figure(go.Scatter(x=ann.index, y=ann[col], mode="lines+markers", line=dict(color=theme.PALETTE["forest"])))
            f.update_layout(height=260, margin=dict(t=30, b=10), title=col)
            c.plotly_chart(f, width="stretch")
        if env.stress.trends:
            st.dataframe(pd.DataFrame(env.stress.trends)[["variable", "n_years", "slope_per_decade", "p_value", "direction"]],
                         hide_index=True, width="stretch")
        st.caption("Mann-Kendall test with Theil-Sen slope. Indicative only; the current partial year is excluded.")


def _soil(env):
    st.subheader("Soil moisture")
    if not env.has_smap or not {"SM_SURFACE", "SM_ROOTZONE"} & set(env.merged.columns):
        st.info("No soil-moisture data in this analysis. Add Earthdata credentials (see README) and tick "
                "'Include SMAP soil moisture' to enable it. Everything else still works.")
        return
    d = env.merged.dropna(subset=["SM_ROOTZONE"], how="all").tail(730) if "SM_ROOTZONE" in env.merged else env.merged.tail(730)
    fig = go.Figure()
    for col, nm, colr in (("SM_SURFACE", "Surface (~0-5 cm)", theme.PALETTE["sky"]),
                          ("SM_ROOTZONE", "Root zone (~0-100 cm)", theme.PALETTE["soil"])):
        if col in d:
            fig.add_scatter(x=d.index, y=d[col], name=nm, mode="lines", line=dict(width=1.2, color=colr))
    fig.update_layout(height=320, margin=dict(t=20, b=10), yaxis_title="m³/m³", legend=dict(orientation="h"))
    st.plotly_chart(fig, width="stretch")
    st.warning("SMAP Level 4 is a land-model ESTIMATE constrained by satellite data on a 9 km grid. "
               "It is not a soil probe and cannot see local soil differences on your farm.")


def _recs(rs):
    st.subheader("Top 3 rotation strategies")
    st.caption("Ranked by transparent rules. Scores compare options; they are not yield forecasts.")
    for w in rs.warnings:
        st.warning(w)
    if not rs.recommendations:
        st.error("No recommendation could be produced from the available data. See the warnings above.")
        return
    tabs = st.tabs([f"#{r.rank}  " + " > ".join(r.crop_names()) for r in rs.recommendations])
    for tab, r in zip(tabs, rs.recommendations):
        with tab:
            c1, c2 = st.columns([1, 2])
            c1.metric("Score (0-100)", f"{r.score:.0f}")
            c2.markdown("**Plan**")
            for s in r.sequence:
                c2.markdown(f"- {s['detail']}")
            st.markdown(f"**Why it ranks here:** {r.why_ranked}")
            comp = pd.DataFrame([{"Component": v["label"], "Score (0-1)": round(v["score"], 2),
                                  "Weight": round(v["weight"], 2), "Contribution": round(v["contribution"], 3)}
                                 for v in r.components.values()])
            st.dataframe(comp, hide_index=True, width="stretch")
            a, b = st.columns(2)
            with a:
                st.markdown("**Benefits**")
                for x in r.benefits or ["None recorded."]:
                    st.markdown(f"- {x}")
            with b:
                st.markdown("**Risks**")
                for x in r.risks or ["None recorded."]:
                    st.markdown(f"- {x}")
            with st.expander("Assumptions, missing information and evidence"):
                for title, items in (("Assumptions", r.assumptions), ("Missing information", r.missing_information),
                                     ("Evidence", r.evidence)):
                    st.markdown(f"**{title}**")
                    for x in items or ["None."]:
                        st.markdown(f"- {x}")
    if rs.excluded:
        with st.expander(f"Crops left out and why ({len(rs.excluded)})"):
            st.dataframe(pd.DataFrame(rs.excluded), hide_index=True, width="stretch")
    with st.expander("General assumptions and missing information"):
        for x in rs.assumptions:
            st.markdown(f"- {x}")
        st.markdown("**Missing**")
        for x in rs.missing_information:
            st.markdown(f"- {x}")


def _sources(env):
    st.subheader("Data sources and provenance")
    for title, meta in (("NASA POWER", env.power_meta), ("NASA SMAP L4 (via AppEEARS)", env.smap_meta)):
        if not meta:
            continue
        with st.expander(title):
            st.markdown(f"- **Source:** {meta.get('source')}\n- **Product:** {meta.get('product')}\n"
                        f"- **Spatial resolution:** {meta.get('spatial_resolution')}\n"
                        f"- **Temporal resolution:** {meta.get('temporal_resolution')}\n"
                        f"- **Retrieved (UTC):** {meta.get('retrieved_utc')}\n"
                        f"- **Served from cache:** {meta.get('served_from_cache')}"
                        + (" (stale: live request failed)" if meta.get("stale_cache") else ""))
            if meta.get("units"):
                st.markdown("**Units:** " + ", ".join(f"{k} = {v}" for k, v in meta["units"].items()))
            st.markdown("**Limitations**")
            for x in meta.get("limitations", []):
                st.markdown(f"- {x}")
    with st.expander("Cleaning summary"):
        st.json({"POWER": env.power_report, "SMAP": env.smap_report})
    with st.expander("Crop dataset sources"):
        st.dataframe(_crops().provenance_table(), hide_index=True, width="stretch")
    st.caption(f"Analysis generated {env.generated_utc} UTC.")


def _downloads(env, rs, name):
    st.subheader("Downloads")
    c1, c2 = st.columns(2)
    csv = env.merged.to_csv(index_label="date").encode("utf-8")
    c1.download_button("Cleaned dataset (CSV)", csv, file_name="agrointel_clean_data.csv", mime="text/csv", width="stretch")
    c1.caption("Includes <parameter>_imputed and _outlier flag columns. Raw data is kept separately and not altered.")
    report = build_report(env, rs, _crops(), name)
    c2.download_button("Analysis report (Markdown)", report.encode("utf-8"), file_name="agrointel_report.md",
                       mime="text/markdown", width="stretch")


# ------------------------------------------------------------------ accounts
@st.cache_resource(show_spinner=False)
def _store() -> UserStore:
    return UserStore()


def _auth_page() -> None:
    theme.hero("AgroIntel", "Know your climate. Plan your rotation. Grow with confidence.",
               ["NASA satellite data", "Crop rotation planning", "Drought and heat watch"])
    _, mid, _ = st.columns([1, 1.4, 1])
    with mid:
        card = st.container(border=True)
        t_in, t_up = card.tabs(["Log in", "Create account"])
        with t_in:
            with st.form("login"):
                email = st.text_input("Email")
                pw = st.text_input("Password", type="password")
                if st.form_submit_button("Log in", type="primary", width="stretch"):
                    try:
                        u = _store().login(email, pw)
                        st.session_state["user"] = {"id": u.id, "email": u.email, "name": u.name}
                        st.rerun()
                    except AuthError as e:
                        st.error(str(e))
        with t_up:
            with st.form("signup"):
                name = st.text_input("Full name")
                email = st.text_input("Email", key="su_email")
                pw = st.text_input("Password", type="password", key="su_pw", help="8+ characters, with a capital letter and a number.")
                pw2 = st.text_input("Confirm password", type="password")
                if st.form_submit_button("Create account", type="primary", width="stretch"):
                    try:
                        u = _store().signup(name, email, pw, pw2)
                        st.session_state["user"] = {"id": u.id, "email": u.email, "name": u.name}
                        st.rerun()
                    except AuthError as e:
                        st.error(str(e))
    st.caption("Passwords are stored only as salted hashes. Your details are used for your account and nothing else.")


# ------------------------------------------------------------------ main
def main():
    st.set_page_config(
        page_title="AgroIntel | Climate & Crop Rotation",
        page_icon="🌱",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    theme.apply()
    _apply_dashboard_styles()
    user = st.session_state.get("user")
    if not user:
        _auth_page()
        return
    st.sidebar.markdown(f"### 👩‍🌾 {user['name']}")
    if st.sidebar.button("Log out", key="logout"):
        st.session_state.clear()
        st.rerun()
    st.markdown('<div class="agro-eyebrow">Climate intelligence for better farm decisions</div>', unsafe_allow_html=True)
    theme.hero("AgroIntel", "Climate and crop-rotation decision support from NASA Earth observation data. "
               "Grid and satellite-model data are not farm sensors: use results as decision support, not as a forecast.")
    s = _sidebar()
    _map(s["lat"], s["lon"], s["name"])

    key = (round(s["lat"], 3), round(s["lon"], 3), s["years"], s["use_smap"])
    if s["run"]:
        st.session_state["run_key"] = key
    active_key = st.session_state.get("run_key")
    if active_key is None:
        st.info(
            "Choose a location and press **Run analysis** in the sidebar to generate your first report. "
            "The initial NASA data request may take a little while."
        )
        return
    if key != active_key:
        st.warning(
            "Your location or climate-data settings have changed. The results below are not updated yet. "
            "Press **Run analysis** to refresh the analysis for the selected settings."
        )
        return
    lat, lon, years, use_smap = active_key
    try:
        with st.spinner("Fetching and analysing NASA data..."):
            env = _env(lat, lon, years, use_smap)
    except PipelineError as e:
        st.error(str(e))
        return
    for w in env.warnings:
        st.warning(w)

    rs = make_recommendations(env, _crops(), s["previous_crop"], s["soil"], s["weights"])
    _recs(rs)
    st.divider()
    _indicators(env)
    _rain_temp(env)
    _soil(env)
    st.divider()
    _sources(env)
    _downloads(env, rs, s["name"])
