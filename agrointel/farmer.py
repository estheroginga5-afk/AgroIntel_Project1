"""Farmer view: few words, big pictures, traffic-light colours, gentle animation, English or Kiswahili.

Everything shown here is derived from the same stress and recommendation results as the detailed view; nothing
new is computed or invented. Where data is missing the card says so instead of guessing.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from .stress import INSUFFICIENT, monthly_anomalies
from .theme import PALETTE

T = {
    "en": {
        "today": "Your farm today", "rain": "Rain", "heat": "Heat", "soil": "Soil water",
        "good": "Good", "watch": "Watch", "danger": "Danger", "unknown": "No data",
        "rain_good": "Rain is normal for now.", "rain_dry": "Less rain than usual. Plan water carefully.",
        "rain_vdry": "Very dry. Drought is likely.", "rain_wet": "More rain than usual. Watch for flooding.",
        "rain_vwet": "Too much water. Crops may rot.",
        "heat_good": "No heat stress.", "heat_watch": "Some hot days. Crops may suffer.", "heat_danger": "Many very hot days. Protect crops.",
        "soil_good": "Soil has enough water.", "soil_dry": "Soil is dry below the surface.", "soil_vdry": "Soil is very dry.",
        "soil_wet": "Soil is very wet.", "soil_none": "Soil water not measured. Add NASA login to see it.",
        "headline_ok": "Looking good. Conditions are normal.", "headline_dry": "It is dry. Be careful.",
        "headline_wet": "It is wet. Be careful.", "headline_hot": "It is hot. Be careful.",
        "plant": "What to plant", "best": "Best choice", "second": "2nd choice", "third": "3rd choice",
        "then": "then", "from": "plant from", "why": "Why this plan",
        "why_rain": "Weather suited these crops in most past seasons.",
        "why_n": "{a} puts nitrogen back in the soil for {b}.",
        "why_norep": "No crop is planted twice in a row.",
        "first": "First planting", "in_days": "in {n} days", "far": "Check again closer to planting for the latest weather.",
        "ask": "Ask your extension officer about pests, diseases and seed varieties. This tool does not know them.",
        "missing": "Some information is missing. Open the Detailed view to see what.",
        "nonplan": "No plan could be made for this place. Open the Detailed view to see why.",
        "chart": "Rain each month", "chart_cap": "Blue bars: rain this year. Line: usual rain for that month.",
        "mm": "mm", "usual": "Usual",
        "foot": "From NASA satellites for your area (about 50 km wide), not your exact field. A guide, not a promise.",
        "view": "View", "simple": "Simple", "detail": "Detailed", "lang": "Language",
        "long rains": "Long rains", "short rains": "Short rains",
    },
    "sw": {
        "today": "Shamba lako leo", "rain": "Mvua", "heat": "Joto", "soil": "Maji ardhini",
        "good": "Nzuri", "watch": "Angalia", "danger": "Hatari", "unknown": "Hakuna data",
        "rain_good": "Mvua ni ya kawaida kwa sasa.", "rain_dry": "Mvua ni chache kuliko kawaida. Tumia maji kwa uangalifu.",
        "rain_vdry": "Kavu sana. Ukame unawezekana.", "rain_wet": "Mvua nyingi kuliko kawaida. Angalia mafuriko.",
        "rain_vwet": "Maji mengi sana. Mazao yanaweza kuoza.",
        "heat_good": "Hakuna joto kali.", "heat_watch": "Siku chache za joto. Mazao yanaweza kudhurika.", "heat_danger": "Siku nyingi za joto kali. Linda mazao.",
        "soil_good": "Udongo una maji ya kutosha.", "soil_dry": "Udongo ni mkavu chini.", "soil_vdry": "Udongo ni mkavu sana.",
        "soil_wet": "Udongo una maji mengi sana.", "soil_none": "Maji ya udongo hayajapimwa. Weka akaunti ya NASA kuyaona.",
        "headline_ok": "Hali ni nzuri. Hali ni ya kawaida.", "headline_dry": "Kuna ukavu. Kuwa makini.",
        "headline_wet": "Kuna unyevu mwingi. Kuwa makini.", "headline_hot": "Kuna joto. Kuwa makini.",
        "plant": "Panda nini", "best": "Chaguo bora", "second": "Chaguo la 2", "third": "Chaguo la 3",
        "then": "kisha", "from": "panda kuanzia", "why": "Kwa nini mpango huu",
        "why_rain": "Hali ya hewa ilifaa mazao haya katika misimu mingi iliyopita.",
        "why_n": "{a} huongeza naitrojeni ardhini kwa ajili ya {b}.",
        "why_norep": "Hakuna zao linalopandwa mara mbili mfululizo.",
        "first": "Kupanda kwa kwanza", "in_days": "baada ya siku {n}", "far": "Angalia tena karibu na wakati wa kupanda.",
        "ask": "Muulize afisa ugani kuhusu wadudu, magonjwa na mbegu. Zana hii haizijui.",
        "missing": "Baadhi ya taarifa hazipo. Fungua Mwonekano wa Kina kuona.",
        "nonplan": "Hakuna mpango uliopatikana kwa eneo hili. Fungua Mwonekano wa Kina kuona sababu.",
        "chart": "Mvua kila mwezi", "chart_cap": "Safu za bluu: mvua ya mwaka huu. Mstari: mvua ya kawaida ya mwezi huo.",
        "mm": "mm", "usual": "Kawaida",
        "foot": "Kutoka satelaiti za NASA kwa eneo lako (takriban km 50), si shamba lako kamili. Ni mwongozo, si ahadi.",
        "view": "Mwonekano", "simple": "Rahisi", "detail": "Wa kina", "lang": "Lugha",
        "long rains": "Mvua za masika", "short rains": "Mvua za vuli",
    },
}
CROP_SW = {"maize": "Mahindi", "common_bean": "Maharagwe", "sorghum": "Mtama", "finger_millet": "Ulezi",
           "pearl_millet": "Mawele", "cowpea": "Kunde", "groundnut": "Karanga", "soybean": "Soya",
           "green_gram": "Choroko", "pigeon_pea": "Mbaazi", "cassava": "Muhogo", "sweet_potato": "Viazi vitamu",
           "rice": "Mpunga"}
CROP_ICON = {"maize": "🌽", "common_bean": "🫘", "cowpea": "🫘", "soybean": "🫘", "groundnut": "🥜", "cassava": "🌿",
             "sweet_potato": "🍠", "rice": "🌾", "sorghum": "🌾", "finger_millet": "🌾", "pearl_millet": "🌾",
             "green_gram": "🌱", "pigeon_pea": "🌱"}
RANK = {1: "best", 2: "second", 3: "third"}
LEVEL_ORDER = {"unknown": -1, "good": 0, "watch": 1, "danger": 2}

CSS = """
<style>
@keyframes sway {0%,100%{transform:rotate(-6deg)} 50%{transform:rotate(6deg)}}
@keyframes bob {0%,100%{transform:translateY(0)} 50%{transform:translateY(-8px)}}
@keyframes pulse {0%,100%{transform:scale(1)} 50%{transform:scale(1.15)}}
@keyframes drift {0%{transform:translateX(-30px)} 100%{transform:translateX(30px)}}
@keyframes fall {0%{transform:translateY(-6px);opacity:0} 20%{opacity:1} 100%{transform:translateY(34px);opacity:0}}
@keyframes spin {to{transform:rotate(360deg)}}
@keyframes fadein {from{opacity:0;transform:translateY(10px)} to{opacity:1;transform:none}}
@media (prefers-reduced-motion: reduce){*{animation:none !important}}
.fgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:8px 0 18px}
@media (max-width:760px){.fgrid{grid-template-columns:1fr}}
.fcard{border-radius:18px;padding:16px 14px;text-align:center;color:#1f2d1f;animation:fadein .6s both;border:3px solid}
.fcard.good{background:#e8f5e9;border-color:#43a047}.fcard.watch{background:#fff8e1;border-color:#ffb300}
.fcard.danger{background:#ffebee;border-color:#e53935}.fcard.unknown{background:#eceff1;border-color:#90a4ae}
.ficon{font-size:3.2rem;display:inline-block}
.fcard.good .ficon{animation:sway 3s ease-in-out infinite}.fcard.watch .ficon{animation:bob 1.6s ease-in-out infinite}
.fcard.danger .ficon{animation:pulse 1s ease-in-out infinite}
.ftitle{font-size:1.15rem;font-weight:700;margin-top:4px}
.fstate{display:inline-block;font-size:1.3rem;font-weight:800;border-radius:999px;padding:2px 16px;margin:6px 0;color:#fff}
.good .fstate{background:#2e7d32}.watch .fstate{background:#e08600}.danger .fstate{background:#c62828}.unknown .fstate{background:#607d8b}
.fsay{font-size:1.05rem;line-height:1.35}
.scene{position:relative;height:150px;border-radius:18px;overflow:hidden;margin:6px 0 4px;border:1px solid #c5e1a5}
.scene .sky{position:absolute;inset:0}
.scene .ground{position:absolute;left:0;right:0;bottom:0;height:46px;background:linear-gradient(#6d4c41,#4e342e)}
.scene .sun{position:absolute;top:14px;right:48px;font-size:3.2rem;animation:spin 18s linear infinite}
.scene .cloud{position:absolute;top:18px;font-size:3rem;animation:drift 7s ease-in-out infinite alternate}
.scene .drop{position:absolute;top:62px;font-size:1.2rem;animation:fall 1.3s linear infinite}
.scene .plant{position:absolute;bottom:38px;font-size:2.2rem;transform-origin:bottom center;animation:sway 3s ease-in-out infinite}
.fbanner{font-size:1.5rem;font-weight:800;margin:2px 0 8px}
.plan{border-radius:18px;background:rgba(255,255,255,.85);border:2px solid #c5e1a5;padding:14px 16px;margin:10px 0;animation:fadein .6s both}
.plan.r1{border-color:#2e7d32;box-shadow:0 4px 16px rgba(46,125,50,.25)}
.badge{display:inline-block;border-radius:999px;padding:3px 14px;font-weight:800;color:#fff;background:#2e7d32;margin-bottom:8px}
.r2 .badge{background:#689f38}.r3 .badge{background:#9e9d24}
.steps{display:flex;flex-wrap:wrap;align-items:stretch;gap:6px}
.step{flex:1;min-width:140px;text-align:center;background:#f1f8e9;border-radius:14px;padding:10px 6px}
.step .ci{font-size:2.6rem;animation:sway 3.4s ease-in-out infinite;display:inline-block}
.step .cn{font-size:1.2rem;font-weight:800}.step .cs{font-size:.92rem;color:#3f5340}
.arrow{align-self:center;font-size:1.6rem;color:#2e7d32}
.why{margin:8px 0 0;font-size:1rem}
</style>
"""


def _level_from_prefix(text: str, *, danger=("probable severe", "probable drought", "probable excess", "severe"),
                       watch=("possible", "dry signal", "mild", "moderate", "probable")) -> str:
    low = (text or "").lower()
    if not low or low == INSUFFICIENT.lower():
        return "unknown"
    if low.startswith("no "):
        return "good"
    if any(low.startswith(p) for p in danger):
        return "danger"
    if any(low.startswith(p) for p in watch):
        return "watch"
    return "good"


def card_states(env) -> dict:
    """Return {'rain': (level, key), 'heat': ..., 'soil': ...} where key picks the sentence."""
    comp = env.stress.composites
    dry = _level_from_prefix(comp.get("drought", {}).get("classification", ""))
    wet = _level_from_prefix(comp.get("excess_moisture", {}).get("classification", ""))
    if dry == "unknown" and wet == "unknown":
        rain = ("unknown", "unknown")
    elif LEVEL_ORDER[dry] >= LEVEL_ORDER[wet] and dry != "good":
        rain = (dry, "rain_vdry" if dry == "danger" else "rain_dry")
    elif wet != "good" and LEVEL_ORDER[wet] > 0:
        rain = (wet, "rain_vwet" if wet == "danger" else "rain_wet")
    else:
        rain = ("good", "rain_good")
    hc = comp.get("heat", {}).get("classification", "")
    heat_level = "danger" if hc == "Severe" else _level_from_prefix(hc, danger=("severe",), watch=("mild", "moderate"))
    heat = (heat_level, f"heat_{heat_level}" if heat_level != "unknown" else "unknown")
    soil = ("unknown", "soil_none")
    ind = env.stress.get("soil_rootzone") if env.has_smap else None
    if ind and ind.classification != INSUFFICIENT:
        c = ind.classification
        soil = (("danger", "soil_vdry") if c == "Severe deficit" else ("watch", "soil_dry") if c == "Deficit"
                else ("watch", "soil_wet") if c.startswith("Very wet") else ("good", "soil_good"))
    return {"rain": rain, "heat": heat, "soil": soil}


def _crop_name(crop_id: str, fallback: str, lang: str) -> str:
    return CROP_SW.get(crop_id, fallback) if lang == "sw" else fallback.split(" (")[0].capitalize()


def _scene(states: dict, lang: str) -> str:
    t = T[lang]
    rain_level, rain_key = states["rain"]
    wet = rain_key in ("rain_wet", "rain_vwet")
    dry = rain_key in ("rain_dry", "rain_vdry")
    hot = states["heat"][0] in ("watch", "danger")
    if dry:
        head, sky = t["headline_dry"], "linear-gradient(#ffcc80,#ffe0b2)"
    elif wet:
        head, sky = t["headline_wet"], "linear-gradient(#90a4ae,#cfd8dc)"
    elif hot:
        head, sky = t["headline_hot"], "linear-gradient(#ffb74d,#ffe0b2)"
    else:
        head, sky = t["headline_ok"], "linear-gradient(#81d4fa,#e1f5fe)"
    parts = [f'<div class="sky" style="background:{sky}"></div>']
    if not wet:
        parts.append('<div class="sun">☀️</div>')
    if wet or not dry:
        parts.append('<div class="cloud" style="left:12%">☁️</div>' + ('<div class="cloud" style="left:48%;animation-delay:-3s">☁️</div>' if wet else ""))
    if wet:
        parts += [f'<div class="drop" style="left:{14 + i * 9}%;animation-delay:{(i % 5) * .25}s">💧</div>' for i in range(9)]
    parts.append('<div class="ground"></div>')
    icon = "🥀" if dry else "🌱" if wet else "🌽"
    parts += [f'<div class="plant" style="left:{8 + i * 12}%;animation-delay:{i * .3}s">{icon if (dry or wet) else ("🌽" if i % 2 else "🌱")}</div>' for i in range(8)]
    return f'<div class="fbanner">{t["today"]}: {head}</div><div class="scene">{"".join(parts)}</div>'


def _cards(states: dict, lang: str) -> str:
    t = T[lang]
    spec = [("rain", "🌧️"), ("heat", "🌡️"), ("soil", "💧")]
    out = ['<div class="fgrid">']
    for k, icon in spec:
        level, key = states[k]
        if k == "rain" and level == "good":
            icon = "🌦️"
        out.append(f'<div class="fcard {level}"><div class="ficon">{icon}</div><div class="ftitle">{t[k]}</div>'
                   f'<div class="fstate">{t[level]}</div><div class="fsay">{t.get(key, t["unknown"])}</div></div>')
    return "".join(out) + "</div>"


def _season_name(label: str, lang: str) -> str:
    low = label.lower()
    for k in ("long rains", "short rains"):
        if low.startswith(k):
            return T[lang][k]
    return label.split(" (")[0].capitalize()


def _plan_html(rec, ds, lang: str) -> str:
    t = T[lang]
    steps = []
    for s in rec.sequence:
        cid = s["crop_id"]
        pd_ = pd.Timestamp(s["plant_start"])
        when = pd_.strftime("%d %b %Y")
        steps.append(f'<div class="step"><div class="ci">{CROP_ICON.get(cid, "🌱")}</div>'
                     f'<div class="cn">{_crop_name(cid, s["crop"], lang)}</div>'
                     f'<div class="cs">{_season_name(s["season"], lang)}<br>{t["from"]} {when}</div></div>')
    html = f'<div class="plan r{rec.rank}"><span class="badge">{t[RANK.get(rec.rank, "third")]}</span><div class="steps">'
    html += f'<div class="arrow">➜</div>'.join(steps) + "</div>"
    why = []
    if all(s["n_years"] and s["good_years"] / s["n_years"] >= 0.7 for s in rec.sequence):
        why.append("✅ " + t["why_rain"])
    for a, b in zip(rec.sequence, rec.sequence[1:]):
        if ds.get(a["crop_id"]).is_legume and not ds.get(b["crop_id"]).is_legume:
            why.append("🌱 " + t["why_n"].format(a=_crop_name(a["crop_id"], a["crop"], lang), b=_crop_name(b["crop_id"], b["crop"], lang)))
            break
    if len({s["crop_id"] for s in rec.sequence}) == len(rec.sequence):
        why.append("🔁 " + t["why_norep"])
    if why:
        html += f'<div class="why"><b>{t["why"]}</b><br>' + "<br>".join(why[:3]) + "</div>"
    return html + "</div>"


def _rain_chart(env, lang: str):
    t = T[lang]
    m = monthly_anomalies(env.merged).tail(12)
    if m.empty or "rain_mm" not in m:
        return
    fig = go.Figure()
    fig.add_bar(x=m.index.strftime("%b"), y=m["rain_mm"], name=t["chart"], marker_color=PALETTE["sky"])
    fig.add_scatter(x=m.index.strftime("%b"), y=m["rain_mm_clim"], name=t["usual"], mode="lines+markers",
                    line=dict(color=PALETTE["wheat"], width=4, dash="dot"))
    fig.update_layout(height=300, margin=dict(t=10, b=10), legend=dict(orientation="h", font=dict(size=15)),
                      yaxis_title=t["mm"], font=dict(size=15))
    st.markdown(f"### 🌧️ {t['chart']}")
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    st.caption(t["chart_cap"])


def render(env, rs, ds, lang: str = "en") -> None:
    t = T[lang]
    st.markdown(CSS, unsafe_allow_html=True)
    states = card_states(env)
    st.markdown(_scene(states, lang) + _cards(states, lang), unsafe_allow_html=True)

    st.markdown(f"## 🌾 {t['plant']}")
    if not rs.recommendations:
        st.warning(t["nonplan"])
    else:
        first = rs.recommendations[0].sequence[0]
        days = (pd.Timestamp(first["plant_start"]).date() - date.today()).days
        if days > 0:
            st.markdown(f"**{t['first']}:** {pd.Timestamp(first['plant_start']).strftime('%d %b %Y')} ({t['in_days'].format(n=days)})")
            if days > 60:
                st.caption(t["far"])
        for r in rs.recommendations:
            st.markdown(_plan_html(r, ds, lang), unsafe_allow_html=True)
        st.info("👩‍🌾 " + t["ask"])
        if rs.warnings or rs.missing_information:
            st.caption("ℹ️ " + t["missing"])
    _rain_chart(env, lang)
    st.caption(t["foot"])
