"""Rule-based crop-rotation recommendation engine (Stage 5).

A STRATEGY is a sequence of crops over the next N growing seasons (default 3).
Every candidate sequence is scored from transparent components, each 0..1:

  climate_fit         mean historical temperature/rainfall suitability of each crop in its season
  reliability         share of past seasons in which each crop was suitable
  current_conditions  present drought / excess-moisture signals vs the first crop (only if planting is near)
  soil_fit            pH / texture / drainage vs the first-hand soil data the user supplies (optional)
  nitrogen            legume nitrogen carried into the following crop
  fertility_load      penalty for back-to-back high fertility-demand crops
  diversity           penalty for repeating the same crop or botanical family

Weights live in config.RecommendationConfig, are normalised over the components that are
available, and are expert-judgement defaults, NOT calibrated parameters. Nothing here is a
yield forecast. Pest/disease effects are only represented by the generic 'do not repeat the
same crop/family' rule, because no crop-pair-specific source is in the dataset yet.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field, replace
from datetime import date

import numpy as np
import pandas as pd

from . import config, crops as crops_mod
from .crops import Crop, CropDataset
from .stress import INSUFFICIENT, StressResult
from .suitability import (SeasonWindow, SoilInfo, crop_season_fit, historical_season_stats, infer_seasons,
                          seasons_from_calendar, soil_fit, tolerates_waterlogging, water_demand_index)

COMPONENT_LABELS = {
    "climate_fit": "Historical climate fit", "reliability": "Season-to-season reliability",
    "current_conditions": "Current drought/moisture conditions", "soil_fit": "Soil match",
    "nitrogen": "Legume nitrogen benefit", "fertility_load": "Fertility-demand balance",
    "diversity": "Crop/family diversity",
}

GENERAL_ASSUMPTIONS = [
    "NASA POWER and SMAP values are grid-cell/model data (about 55 x 70 km and 9 km), not measurements on this farm.",
    "EcoCrop gives rainfall as an 'annual' range; it is compared here with total rainfall of the season window. This is a simplification.",
    "EcoCrop ranges cover all varieties and systems. Each crop is assumed to be grown within the season window with a suitable variety.",
    "The most limiting of temperature and rainfall sets a crop's suitability (as in Recocrop); other factors are not combined.",
    "Component weights are adjustable expert-judgement defaults, not calibrated against yield data. Scores rank options; they are not yield forecasts.",
    "Rainfed production is assumed. Irrigation, pests, markets, labour, seed access and farm management are not modelled.",
]


@dataclass
class Recommendation:
    rank: int
    sequence: list[dict]
    score: float                                 # 0..100
    components: dict                             # name -> {score, weight, contribution}
    why_ranked: str = ""
    benefits: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)

    def crop_names(self) -> list[str]:
        return [s["crop"] for s in self.sequence]


@dataclass
class RecommendationSet:
    recommendations: list[Recommendation]
    seasons: list[dict]
    excluded: list[dict]
    warnings: list[str]
    weights_used: dict
    evidence_completeness: dict
    assumptions: list[str]
    missing_information: list[str]
    context: dict


# ------------------------------------------------------------------ helpers
def _slots(seasons: list[SeasonWindow], as_of: pd.Timestamp, n: int) -> list[dict]:
    occ = []
    for s in seasons:
        for y in range(as_of.year - 1, as_of.year + n + 2):
            ps, pe = s.plant_window(y)
            if pe >= as_of:
                occ.append((ps, s, y, pe))
    occ.sort(key=lambda o: o[0])
    return [{"index": i + 1, "season": s, "year": y, "plant_start": ps, "plant_end": pe,
             "days_to_planting": max(0, (ps - as_of).days)} for i, (ps, s, y, pe) in enumerate(occ[:n])]


def _normalise(weights: dict, available: set) -> dict:
    w = {k: v for k, v in weights.items() if k in available and v > 0}
    tot = sum(w.values())
    return {k: v / tot for k, v in w.items()} if tot > 0 else {}


def _transitions(prev: Crop | None, seq: tuple[Crop, ...]) -> list[tuple[Crop, Crop]]:
    chain = ([prev] if prev else []) + list(seq)
    return list(zip(chain[:-1], chain[1:]))


# ------------------------------------------------------------------ scoring pieces
def _nitrogen_benefit(a: Crop, b: Crop, max_ndfa: float) -> float:
    if not (a.fixes_nitrogen and a.n_fixation) or b.fixes_nitrogen:
        return 0.0
    if b.fertility_requirement == "low":
        return 0.0
    return min(1.0, (a.n_fixation["pct_ndfa"] / 100.0) / max_ndfa)


def _fertility_load(a: Crop, b: Crop) -> float:
    if a.fertility_requirement == "high" and b.fertility_requirement == "high":
        legumes = int(a.fixes_nitrogen) + int(b.fixes_nitrogen)
        return 1.0 if legumes == 0 else 0.5 if legumes == 1 else 0.0
    return 0.0


def _diversity(a: Crop, b: Crop, same_family: float) -> float:
    if a.id == b.id:
        return 0.0
    if a.family and a.family == b.family:
        return same_family
    return 1.0


def _current_score(crop: Crop, stress: StressResult, cfg) -> float:
    score = 1.0
    cls = stress.composites.get("drought", {}).get("classification", INSUFFICIENT)
    sev = cfg.drought_severity.get(cls, 0.0)
    wd = water_demand_index(crop, cfg)
    if sev and wd is not None:
        score -= sev * wd
    ex = stress.composites.get("excess_moisture", {}).get("classification", INSUFFICIENT)
    if ex in cfg.excess_intolerant_score and not tolerates_waterlogging(crop):
        score *= cfg.excess_intolerant_score[ex]
    return float(np.clip(score, 0, 1))


# ------------------------------------------------------------------ main entry
def recommend(
    merged: pd.DataFrame,
    ds: CropDataset,
    lat: float,
    lon: float,
    as_of: date | str | None = None,
    stress: StressResult | None = None,
    previous_crop: str | None = None,
    soil: SoilInfo | None = None,
    cfg: config.RecommendationConfig = config.RECOMMEND,
    weights: dict | None = None,
) -> RecommendationSet:
    if weights:
        cfg = replace(cfg, weights={**cfg.weights, **weights})
    as_of_ts = pd.Timestamp(as_of) if as_of else pd.Timestamp(merged["PRECTOTCORR"].last_valid_index())
    warnings: list[str] = []
    excluded: list[dict] = []
    missing: list[str] = []
    prev = ds.get(previous_crop) if previous_crop else None

    # 1. Seasons: regional calendar if one covers this place, else inferred from rainfall.
    match = crops_mod.calendar_for(ds, lat, lon)
    if match:
        seasons, cal = seasons_from_calendar(match.calendar), match.calendar
        warnings += [w for w in match.warnings if w]
    else:
        seasons, cal = infer_seasons(merged, cfg.rainy_month_mm), None
        warnings.append("No regional planting calendar for this location. Seasons were inferred from median monthly "
                        f"rainfall (months with at least {cfg.rainy_month_mm:g} mm). Treat timing as low confidence "
                        "and check with local extension advice.")
        missing.append("Local planting calendar")
    context = {"lat": lat, "lon": lon, "as_of": str(as_of_ts.date()), "calendar": cal["id"] if cal else None,
               "previous_crop": prev.common_name if prev else None}

    def empty(reason: str) -> RecommendationSet:
        warnings.append(reason)
        return RecommendationSet([], [], excluded, warnings, {}, {}, GENERAL_ASSUMPTIONS, missing, context)

    if not seasons:
        return empty("No distinct rain-fed growing season could be identified, so no rotation was recommended. "
                     "Supply a local calendar or irrigation assumptions.")

    # 2. History per season, and per-crop fit.
    stats = {s.id: historical_season_stats(merged, s, cfg.min_window_coverage) for s in seasons}
    n_years = max((len(v) for v in stats.values()), default=0)
    if n_years < cfg.hard_min_history_years:
        return empty(f"Only {n_years} usable past seasons (need at least {cfg.hard_min_history_years}): "
                     "too little history to assess crop suitability.")
    if n_years < cfg.min_history_years:
        warnings.append(f"Only {n_years} past seasons available (fewer than {cfg.min_history_years}); "
                        "reliability estimates are weak.")

    fits, eligible = {}, {s.id: [] for s in seasons}
    for crop in ds.crops.values():
        covered = (cal is None) or crops_mod.calendar_applies(cal, crop)
        if not covered:
            excluded.append({"crop": crop.common_name, "season": "all",
                             "reason": "the regional calendar does not cover this crop group (e.g. long-cycle roots/tubers)"})
            continue
        for s in seasons:
            f = crop_season_fit(crop, s, stats[s.id], cfg)
            fits[(crop.id, s.id)] = f
            if f["eligible"]:
                eligible[s.id].append(crop)
            else:
                excluded.append({"crop": crop.common_name, "season": s.label(), "reason": f["reason"]})

    slots = _slots(seasons, as_of_ts, cfg.n_seasons)
    pools = [eligible[sl["season"].id] for sl in slots]
    if any(not p for p in pools):
        return empty("At least one upcoming season has no crop that is historically suitable here; "
                     "no rotation could be built. See the excluded-crops list.")

    # 3. Precompute per-crop inputs.
    max_ndfa = max([c.n_fixation["pct_ndfa"] / 100 for c in ds.crops.values() if c.n_fixation] or [1.0])
    current_applies = bool(stress) and slots[0]["days_to_planting"] <= cfg.current_conditions_horizon_days
    cur = {c.id: _current_score(c, stress, cfg) for c in ds.crops.values()} if current_applies else {}
    soils = {c.id: soil_fit(c, soil) for c in ds.crops.values()} if soil else {}
    soil_used = any(v[0] is not None for v in soils.values())
    if stress is None:
        missing.append("Current stress analysis (drought / excess moisture) was not supplied")
    elif not current_applies:
        warnings.append(f"The first planting window is {slots[0]['days_to_planting']} days away (more than "
                        f"{cfg.current_conditions_horizon_days}); current drought/moisture conditions were not used. "
                        "Re-run closer to planting.")
    if not soil_used:
        missing.append("Soil data (pH, texture, drainage): supply SoilInfo or connect a soil dataset")

    # 4. Score every candidate sequence.
    scored = []
    for seq in itertools.product(*pools):
        comp = {}
        fl = [fits[(c.id, sl["season"].id)] for c, sl in zip(seq, slots)]
        comp["climate_fit"] = float(np.mean([f["mean_fit"] for f in fl]))
        comp["reliability"] = float(np.mean([f["reliability"] for f in fl]))
        if current_applies:
            comp["current_conditions"] = cur[seq[0].id]
        if soil_used:
            vals = [soils[c.id][0] for c in seq if soils[c.id][0] is not None]
            if vals:
                comp["soil_fit"] = float(np.mean(vals))
        tr = _transitions(prev, seq)
        if tr:
            comp["nitrogen"] = float(np.mean([_nitrogen_benefit(a, b, max_ndfa) for a, b in tr]))
            comp["fertility_load"] = float(1 - np.mean([_fertility_load(a, b) for a, b in tr]))
            comp["diversity"] = float(np.mean([_diversity(a, b, cfg.same_family_score) for a, b in tr]))
        wn = _normalise(cfg.weights, set(comp))
        contrib = {k: wn[k] * comp[k] for k in wn}
        scored.append((sum(contrib.values()), seq, comp, wn, contrib, fl))

    scored.sort(key=lambda t: (-t[0], [c.id for c in t[1]]))

    # 5. Pick the top distinct strategies.
    chosen = []
    for item in scored:
        ids = [c.id for c in item[1]]
        if all(sum(a != b for a, b in zip(ids, [c.id for c in o[1]])) >= cfg.min_positions_different for o in chosen):
            chosen.append(item)
        if len(chosen) == cfg.n_recommendations:
            break

    # 6. Explain.
    recs = []
    for rank, (total, seq, comp, wn, contrib, fl) in enumerate(chosen, 1):
        recs.append(_explain(rank, total, seq, comp, wn, contrib, fl, slots, prev, soils, stress, current_applies,
                             cfg, ds, missing, cal is None))
    for i, r in enumerate(recs):
        r.why_ranked = _why_ranked(r, recs[i - 1] if i else None)

    completeness = {
        "regional_calendar": cal is not None,
        "history_20plus_seasons": n_years >= 20,
        "soil_data": soil_used,
        "satellite_soil_moisture": bool(stress and (stress.get("soil_rootzone") is not None)
                                        and stress.get("soil_rootzone").classification != INSUFFICIENT),
        "previous_crop": prev is not None,
        "variety_data": False,
    }
    for k, v in completeness.items():
        if not v and k == "satellite_soil_moisture":
            missing.append("SMAP soil moisture (not available or baseline too short)")
        if not v and k == "variety_data":
            missing.append("Variety-level data (duration, drought and disease tolerance)")
    if not prev:
        missing.append("Previous crop on the field (supply it to improve the first-season advice)")
    missing += ["Crop-pair pest and disease break effects (only a generic same-crop/same-family rule is used)",
                "Economics, market access, labour and seed availability"]

    season_info = [{"slot": sl["index"], "season": sl["season"].label(), "source": sl["season"].source,
                    "plant_start": str(sl["plant_start"].date()), "plant_end": str(sl["plant_end"].date()),
                    "days_to_planting": sl["days_to_planting"], "window_days": sl["season"].days,
                    "history_years": int(len(stats[sl["season"].id]))} for sl in slots]
    used_w = chosen[0][3] if chosen else {}
    for r in recs:
        r.missing_information = list(dict.fromkeys(missing))
    return RecommendationSet(recs, season_info, excluded, warnings, used_w, completeness,
                             GENERAL_ASSUMPTIONS, list(dict.fromkeys(missing)), context)


# ------------------------------------------------------------------ explanations
def _explain(rank, total, seq, comp, wn, contrib, fl, slots, prev, soils, stress, current_applies,
             cfg, ds, missing, inferred) -> Recommendation:
    sequence = []
    for c, f, sl in zip(seq, fl, slots):
        sequence.append({
            "slot": sl["index"], "season": sl["season"].label(), "plant_start": str(sl["plant_start"].date()),
            "crop_id": c.id, "crop": c.common_name,
            "mean_fit": f["mean_fit"], "reliability": f["reliability"], "good_years": f["good_years"],
            "n_years": f["n_years"], "mean_rain_mm": f["mean_rain_mm"], "mean_temp_c": f["mean_temp_c"],
            "rain_optimum_mm": c.rain_opt_mm, "temp_optimum_c": c.temp_opt,
            "detail": (f"{sl['season'].label()}: {c.common_name}. Historical suitability {f['mean_fit']:.2f}; "
                       f"suitable in {f['good_years']} of {f['n_years']} past seasons. Mean season rain "
                       f"{f['mean_rain_mm']:.0f} mm (crop optimum {c.rain_opt_mm[0]:g}-{c.rain_opt_mm[1]:g} mm), mean temperature "
                       f"{f['mean_temp_c']:.1f} °C (optimum {c.temp_opt[0]:g}-{c.temp_opt[1]:g} °C).")})
    components = {k: {"label": COMPONENT_LABELS[k], "score": comp[k], "weight": wn[k], "contribution": contrib[k]}
                  for k in wn}          # only components that carry weight
    benefits, risks, evidence = [], [], {"ecocrop"}
    chain = ([prev] if prev else []) + list(seq)

    # nitrogen
    for a, b in zip(chain[:-1], chain[1:]):
        if a.fixes_nitrogen and a.n_fixation and not b.fixes_nitrogen and b.fertility_requirement != "low":
            nf = a.n_fixation
            evidence.add(nf["src"])
            basis = nf["basis"].replace("group: ", "")
            txt = (f"{a.common_name} is a legume: about {nf['pct_ndfa']:g}% (±{nf['pct_ndfa_sd']:g}) of its nitrogen comes "
                   f"from the air (estimate for the group: {basis}). It is followed by {b.common_name}, which can use the nitrogen left behind.")
            rb = a.rotation_benefit
            if rb and b.id == "maize":
                evidence.add(rb["src"])
                txt += (f" One study site in South Africa measured about +{rb['grain_yield_gain_pct']:g}% maize grain yield "
                        f"after {a.common_name.lower()} ({rb['evidence']}); this is not a forecast for your field.")
            benefits.append(txt)
        if a.fixes_nitrogen and not a.n_fixation:
            risks.append(f"{a.common_name} is a legume but has no nitrogen-fixation estimate in the dataset.")
    if not any(c.fixes_nitrogen for c in chain):
        risks.append("No legume in this rotation, so no nitrogen is returned to the soil by the crops themselves.")

    # diversity / fertility
    div = [_diversity(a, b, cfg.same_family_score) for a, b in zip(chain[:-1], chain[1:])]
    if div and min(div) == 1.0:
        benefits.append("No crop or botanical family is repeated back-to-back (a general rotation principle; "
                        "no crop-specific pest or disease data is in the dataset).")
    for (a, b), d in zip(zip(chain[:-1], chain[1:]), div):
        if d == 0.0:
            risks.append(f"{b.common_name} follows {a.common_name}: same crop repeated.")
        elif d < 1.0:
            risks.append(f"{b.common_name} follows {a.common_name}: same botanical family ({a.family}).")
    heavy = [(a, b) for a, b in zip(chain[:-1], chain[1:]) if _fertility_load(a, b) == 1.0]
    for a, b in heavy:
        risks.append(f"{a.common_name} then {b.common_name}: two high fertility-demand crops (EcoCrop soil fertility "
                     "requirement) in a row with no legume between them.")
    if not heavy:
        benefits.append("No two high fertility-demand crops follow each other without a legume in between.")

    # climate
    weak = [s for s in sequence if s["reliability"] < cfg.good_year_fit]
    for s in weak:
        risks.append(f"{s['crop']} in {s['season']}: suitable in only {s['good_years']} of {s['n_years']} past seasons.")
    if not weak:
        benefits.append("Every crop was historically suitable in its season in most past years.")

    # current conditions
    if current_applies:
        d = stress.composites.get("drought", {}).get("classification")
        e = stress.composites.get("excess_moisture", {}).get("classification")
        c1 = seq[0]
        if d and d in cfg.drought_severity:
            risks.append(f"Current signal: {d}. {c1.common_name} has an EcoCrop optimum-minimum rainfall of "
                         f"{c1.rain_opt_mm[0]:g} mm, which is used as a rough water-demand proxy.")
        if e and e in cfg.excess_intolerant_score and not tolerates_waterlogging(c1):
            risks.append(f"Current signal: {e}. EcoCrop lists well-drained soil as optimal for {c1.common_name}.")
    # soil
    if soils:
        for c in seq:
            sc, notes = soils[c.id]
            if sc is not None and sc < 0.7:
                risks.append(f"Soil match for {c.common_name} is {sc:.2f} ({'; '.join(notes)}).")
    if inferred:
        risks.append("Seasons were inferred from rainfall, not taken from a local calendar: timing is low confidence.")
    if any(c.data_issues for c in seq):
        risks.append("Some EcoCrop values for these crops were flagged by validation and set aside.")

    if not risks:
        risks.append("No rule in this tool flagged a specific risk. That is not a guarantee: pests, diseases, varieties, "
                     "markets and farm-level soil conditions are not assessed.")
    notes = [GENERAL_ASSUMPTIONS[i] for i in (1, 2, 3, 4)]
    return Recommendation(rank=rank, sequence=sequence, score=100 * total, components=components,
                          benefits=benefits, risks=risks, assumptions=notes,
                          evidence=[f"[{s}] {ds.sources[s]['citation']} | Limitations: {ds.sources[s]['limitations']}"
                                    for s in sorted(evidence)])


def _why_ranked(r: Recommendation, above: Recommendation | None) -> str:
    comps = r.components
    if above is None:
        top = sorted(comps.items(), key=lambda kv: -kv[1]["contribution"])[:3]
        parts = ", ".join(f"{v['label'].lower()} ({v['score']:.2f}, {100*v['contribution']:.1f} pts)" for _, v in top)
        return f"Ranked first (score {r.score:.1f}/100). Largest contributions: {parts}."
    diffs = []
    for k, v in comps.items():
        a = above.components.get(k)
        if a:
            diffs.append((v["contribution"] - a["contribution"], k, v["label"]))
    diffs.sort()
    worse = [f"{lab.lower()} ({100*d:+.1f} pts)" for d, k, lab in diffs if d < -1e-9][:3]
    better = [f"{lab.lower()} ({100*d:+.1f} pts)" for d, k, lab in reversed(diffs) if d > 1e-9][:2]
    txt = f"Ranked #{r.rank} (score {r.score:.1f}/100), {above.score - r.score:.1f} points below #{above.rank}."
    if worse:
        txt += " Weaker than the option above on: " + ", ".join(worse) + "."
    if better:
        txt += " Stronger on: " + ", ".join(better) + "."
    return txt


# ------------------------------------------------------------------ report text
def format_markdown(rs: RecommendationSet) -> str:
    out = ["# Crop-rotation recommendations", ""]
    out.append(f"Location: {rs.context['lat']:.3f}, {rs.context['lon']:.3f} | as of {rs.context['as_of']} | "
               f"calendar: {rs.context['calendar'] or 'none (inferred seasons)'}"
               + (f" | previous crop: {rs.context['previous_crop']}" if rs.context.get("previous_crop") else ""))
    out.append("")
    if rs.seasons:
        out.append("Upcoming seasons considered: " + "; ".join(
            f"{s['season']} from {s['plant_start']}" for s in rs.seasons))
        out.append("")
    for w in rs.warnings:
        out.append(f"> Warning: {w}")
    if rs.warnings:
        out.append("")
    if not rs.recommendations:
        out.append("No recommendation could be made. See warnings above.")
    for r in rs.recommendations:
        out += [f"## #{r.rank}: {' → '.join(r.crop_names())}  (score {r.score:.1f}/100)", "", r.why_ranked, "",
                "**Plan**"] + [f"- {s['detail']}" for s in r.sequence]
        out += ["", "**Score breakdown** (weights are adjustable defaults)"]
        out += [f"- {v['label']}: score {v['score']:.2f} × weight {v['weight']:.2f} = {100*v['contribution']:.1f} pts"
                for v in r.components.values()]
        out += ["", "**Expected benefits**"] + [f"- {b}" for b in r.benefits]
        out += ["", "**Risks**"] + [f"- {x}" for x in r.risks] if r.risks else []
        out += ["", "**Assumptions**"] + [f"- {a}" for a in r.assumptions]
        out += ["", "**Evidence**"] + [f"- {e}" for e in r.evidence] + [""]
    if rs.missing_information:
        out += ["## Missing information that limits confidence"] + [f"- {m}" for m in rs.missing_information] + [""]
    return "\n".join(out)
