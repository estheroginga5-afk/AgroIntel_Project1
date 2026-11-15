"""Look and feel: an agricultural palette, a hero banner, and CSS. No logic here."""
from __future__ import annotations

import base64

import streamlit as st

PALETTE = {"forest": "#1b5e20", "leaf": "#43a047", "lime": "#9ccc65", "soil": "#6d4c41",
           "wheat": "#f4c542", "cream": "#f7f3e3", "sky": "#1e88e5", "ink": "#1f2d1f"}
CHART_COLORS = [PALETTE["leaf"], PALETTE["sky"], PALETTE["wheat"], PALETTE["soil"], PALETTE["forest"], PALETTE["lime"]]


def _hero_svg(h: int = 360) -> str:
    rows = "".join(f'<path d="M-50 {250 + i * 14} Q 400 {200 + i * 14} 1250 {255 + i * 14}" '
                   f'stroke="{"#33691e" if i % 2 else "#558b2f"}" stroke-width="7" fill="none" opacity=".55"/>' for i in range(8))
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 {h}" preserveAspectRatio="xMidYMid slice">
<defs><linearGradient id="s" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffe082"/><stop offset=".55" stop-color="#fff3c4"/>
<stop offset="1" stop-color="#e8f5c8"/></linearGradient></defs>
<rect width="1200" height="{h}" fill="url(#s)"/>
<circle cx="980" cy="90" r="52" fill="#ffca28" opacity=".95"/><circle cx="980" cy="90" r="78" fill="#ffca28" opacity=".25"/>
<path d="M0 210 Q 220 140 450 200 T 900 190 T 1200 170 V{h} H0Z" fill="#7cb342"/>
<path d="M0 250 Q 300 190 600 245 T 1200 220 V{h} H0Z" fill="#558b2f"/>
<path d="M0 300 Q 350 250 700 295 T 1200 280 V{h} H0Z" fill="#33691e"/>{rows}
<path d="M0 {h - 40} H1200 V{h} H0Z" fill="#5d4037"/></svg>"""


def hero_data_uri(h: int = 360) -> str:
    return "data:image/svg+xml;base64," + base64.b64encode(_hero_svg(h).encode()).decode()


CSS = f"""
<style>
.stApp {{ background: linear-gradient(180deg, #eef5dc 0%, {PALETTE['cream']} 40%, #f2ecd4 100%); }}
header[data-testid="stHeader"] {{ background: transparent; }}
section[data-testid="stSidebar"] {{ background: linear-gradient(180deg, #1b5e20 0%, #2e7d32 100%); }}
section[data-testid="stSidebar"] * {{ color: #f1f8e9; }}
section[data-testid="stSidebar"] input, section[data-testid="stSidebar"] [data-baseweb="select"] * {{ color: {PALETTE['ink']} !important; }}
section[data-testid="stSidebar"] .stButton button, section[data-testid="stSidebar"] .stButton button[kind="primary"] {{ background: {PALETTE['wheat']}; border: 0; font-weight: 700; }}
section[data-testid="stSidebar"] .stButton button, section[data-testid="stSidebar"] .stButton button * {{ color: {PALETTE['ink']} !important; }}
section[data-testid="stSidebar"] details {{ background: rgba(255,255,255,.12); border-radius: 10px; }}
section[data-testid="stSidebar"] details summary * {{ color: #fff !important; }}
/* readable text whatever theme the browser or Streamlit picks (dark mode, missing config.toml) */
:root {{ color-scheme: light; }}
.stApp, .stApp [data-testid="stMain"], .stApp [data-testid="stAppViewContainer"] {{ background: linear-gradient(180deg, #eef5dc 0%, {PALETTE['cream']} 40%, #f2ecd4 100%) !important; }}
.stApp [data-testid="stMain"] p, .stApp [data-testid="stMain"] li, .stApp [data-testid="stMain"] label,
.stApp [data-testid="stMain"] span, .stApp [data-testid="stMain"] summary, .stApp [data-testid="stMain"] td,
.stApp [data-testid="stMain"] th, .stApp [data-testid="stMain"] div[data-testid="stMarkdownContainer"] *,
.stApp [data-testid="stMain"] [data-testid="stWidgetLabel"] *, .stApp [data-testid="stMain"] [data-testid="stMetricValue"] *,
.stApp [data-testid="stMain"] [data-testid="stMetricLabel"] *, .stApp [data-testid="stMain"] button[role="tab"] *,
.stApp [data-testid="stMain"] [data-testid="stExpander"] * {{ color: {PALETTE['ink']} !important; }}
.stApp [data-testid="stMain"] [data-testid="stCaptionContainer"] * {{ color: #3f5340 !important; }}
.stApp [data-testid="stMain"] h1, .stApp [data-testid="stMain"] h2, .stApp [data-testid="stMain"] h3 {{ color: {PALETTE['forest']} !important; }}
.stApp [data-testid="stMain"] input, .stApp [data-testid="stMain"] textarea {{ color: {PALETTE['ink']} !important; background: #fff !important; -webkit-text-fill-color: {PALETTE['ink']} !important; }}
.stApp [data-testid="stMain"] [data-baseweb="input"], .stApp [data-testid="stMain"] [data-baseweb="select"] > div {{ background: #fff !important; }}
.stApp [data-testid="stMain"] [data-testid="stMetric"], .stApp [data-testid="stMain"] [data-testid="stExpander"] {{ background: rgba(255,255,255,.8) !important; }}
.stApp [data-testid="stMain"] button[kind="primary"] *, .stApp [data-testid="stMain"] button[kind="primaryFormSubmit"] * {{ color: #fff !important; }}
.stApp [data-testid="stMain"] .stDownloadButton button, .stApp [data-testid="stMain"] .stDownloadButton button * {{ background: #fff; color: {PALETTE['forest']} !important; }}
section[data-testid="stSidebar"] p, section[data-testid="stSidebar"] label, section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] h1, section[data-testid="stSidebar"] h2, section[data-testid="stSidebar"] h3,
section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] *, section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] * {{ color: #f1f8e9 !important; }}
section[data-testid="stSidebar"] input, section[data-testid="stSidebar"] [data-baseweb="select"] * {{ color: {PALETTE['ink']} !important; -webkit-text-fill-color: {PALETTE['ink']} !important; }}
.stApp [data-testid="stMain"] div[data-testid="stMarkdownContainer"] .hero, .stApp [data-testid="stMain"] div[data-testid="stMarkdownContainer"] .hero * {{ color: #fff !important; }}
.stApp [data-testid="stMain"] div[data-testid="stMarkdownContainer"] .badge, .stApp [data-testid="stMain"] div[data-testid="stMarkdownContainer"] .fstate {{ color: #fff !important; }}
.stApp [data-testid="stMain"] div[data-testid="stMarkdownContainer"] .hero .pill {{ color: {PALETTE['forest']} !important; }}
[data-baseweb="base-input"], [data-baseweb="input"], [data-baseweb="select"] > div, [data-baseweb="base-input"] button {{ background: #fffdf5 !important; }}
[data-baseweb="base-input"] button svg {{ fill: {PALETTE['ink']} !important; }}
section[data-testid="stSidebar"] [data-baseweb="base-input"] button, section[data-testid="stSidebar"] [data-baseweb="base-input"] button * {{ color: {PALETTE['ink']} !important; }}
section[data-testid="stSidebar"] [data-baseweb="select"] svg {{ fill: {PALETTE['ink']} !important; }}
section[data-testid="stSidebar"] [data-baseweb="input"], section[data-testid="stSidebar"] [data-baseweb="input"] *, section[data-testid="stSidebar"] [data-baseweb="base-input"] *,
section[data-testid="stSidebar"] [data-baseweb="select"] > div, section[data-testid="stSidebar"] [data-baseweb="select"] > div * {{ background: #fffdf5 !important; color: {PALETTE['ink']} !important; -webkit-text-fill-color: {PALETTE['ink']} !important; }}
section[data-testid="stSidebar"] [data-testid="stExpander"] {{ background: rgba(0,0,0,.22) !important; border-color: rgba(255,255,255,.25) !important; }}
section[data-testid="stSidebar"] [data-testid="stExpander"] * {{ color: #fff !important; background: transparent; }}
section[data-testid="stSidebar"] [data-testid="stExpander"] input, section[data-testid="stSidebar"] [data-testid="stExpander"] [data-baseweb] * {{ color: {PALETTE['ink']} !important; background: #fffdf5 !important; }}
[data-testid="stNumberInputContainer"], [data-testid="stNumberInput"] input, [data-testid="stNumberInput"] button,
[data-testid="stTextInput"] [data-testid="stTextInputRootElement"], [data-testid="stTextInput"] input, [data-testid="stTextInput"] button,
[data-testid="stSelectbox"] div[role="combobox"], [data-testid="stSelectbox"] [data-baseweb="select"] > div, [data-testid="stSelectbox"] input {{
    background: #fffdf5 !important; color: {PALETTE['ink']} !important; -webkit-text-fill-color: {PALETTE['ink']} !important; }}
[data-testid="stNumberInput"] button svg, [data-testid="stTextInput"] button svg, [data-testid="stSelectbox"] svg {{ fill: {PALETTE['ink']} !important; color: {PALETTE['ink']} !important; }}
[data-testid="stSelectbox"] [data-baseweb="select"], [data-testid="stSelectbox"] [data-baseweb="select"] * {{ background: #fffdf5 !important; color: {PALETTE['ink']} !important; -webkit-text-fill-color: {PALETTE['ink']} !important; }}
header[data-testid="stHeader"] *, [data-testid="stToolbar"] * {{ color: {PALETTE['ink']} !important; }}
div[data-testid="stAlert"] {{ background: #fff8e1; border: 1px solid #ffe082; }}
div[data-testid="stAlert"] * {{ color: #5d4300 !important; }}
div[data-testid="stAlert"]:has([data-testid="stAlertContentInfo"]) {{ background: #e3f2fd; border-color: #90caf9; }}
div[data-testid="stAlert"]:has([data-testid="stAlertContentInfo"]) * {{ color: #0d3c66 !important; }}
div[data-testid="stAlert"]:has([data-testid="stAlertContentError"]) {{ background: #ffebee; border-color: #ef9a9a; }}
div[data-testid="stAlert"]:has([data-testid="stAlertContentError"]) * {{ color: #7f1d1d !important; }}
h1, h2, h3 {{ color: {PALETTE['forest']}; letter-spacing: -.01em; }}
.hero {{ border-radius: 18px; padding: 34px 34px 120px; color: #fff; margin-bottom: 18px;
        background: linear-gradient(rgba(27,94,32,.55), rgba(27,94,32,.15)), url("{hero_data_uri()}") center/cover;
        box-shadow: 0 6px 22px rgba(27,94,32,.25); }}
.hero h1 {{ color: #fff; margin: 0; font-size: 2.6rem; text-shadow: 0 2px 8px rgba(0,0,0,.35); }}
.hero p {{ margin: 6px 0 0; font-size: 1.05rem; text-shadow: 0 1px 6px rgba(0,0,0,.4); max-width: 46rem; }}
div[data-testid="stTabs"] button[aria-selected="true"] {{ color: {PALETTE['forest']}; font-weight: 700; }}
div[data-testid="stMetric"], div[data-testid="stDataFrame"], div[data-testid="stExpander"] {{
    background: rgba(255,255,255,.75); border-radius: 12px; border: 1px solid #dcedc8; }}
div[data-testid="stMetric"] {{ padding: 10px 14px; }}
.stButton button[kind="primary"], .stFormSubmitButton button[kind="primaryFormSubmit"] {{ background: {PALETTE['leaf']}; border: 0; font-weight: 700; }}
.stButton button[kind="primary"]:hover {{ background: {PALETTE['forest']}; }}
.stDownloadButton button {{ border: 1px solid {PALETTE['leaf']}; color: {PALETTE['forest']}; font-weight: 600; }}
div[data-testid="stVerticalBlockBorderWrapper"] {{ background: rgba(255,255,255,.92); border-radius: 16px; border-color: #c5e1a5; box-shadow: 0 8px 30px rgba(27,94,32,.15); }}
.pill {{ display:inline-block; background:#e8f5e9; color:{PALETTE['forest']}; border:1px solid #a5d6a7; border-radius:999px;
        padding:2px 12px; margin:2px 6px 2px 0; font-size:.85rem; }}
</style>
"""


def apply():
    st.markdown(CSS, unsafe_allow_html=True)


def hero(title: str, subtitle: str, pills: list[str] | None = None):
    chips = "".join(f'<span class="pill">{p}</span>' for p in (pills or []))
    st.markdown(f'<div class="hero"><h1>🌱 {title}</h1><p>{subtitle}</p><div style="margin-top:12px">{chips}</div></div>',
                unsafe_allow_html=True)
