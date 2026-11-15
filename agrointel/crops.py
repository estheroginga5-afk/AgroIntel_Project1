"""Crop requirements dataset (Stage 4).

Two data files in agrointel/data/ are merged at load time:
  ecocrop_subset.csv   raw FAO EcoCrop rows (temperature, rainfall, pH, soil, cycle length)
  crops_overlay.json   curated layer: crop group, nitrogen fixation, rotation benefits,
                       regional planting calendars and the source registry

Rules:
  * Nothing is guessed. Unknown values are None and listed in `crop.missing`.
  * Every curated number carries a source id that exists in the registry.
  * Raw values are validated; implausible ones are quarantined (set to None and
    listed in `crop.data_issues`) instead of silently trusted.
  * A crop is NOT assumed suitable anywhere: this module only holds requirements.
    Matching them to a location is the job of the recommendation stage.

To add a crop: append its EcoCrop row to ecocrop_subset.csv and add an entry under
`crops` in crops_overlay.json (same ecocrop_code).
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
ECOCROP_CSV = DATA_DIR / "ecocrop_subset.csv"
OVERLAY_JSON = DATA_DIR / "crops_overlay.json"


@dataclass
class Crop:
    id: str
    common_name: str
    scientific_name: str
    family: str | None
    group: str | None                      # cereal | grain_legume | root_tuber | ...
    ecocrop_code: int
    temp_opt: tuple[float | None, float | None]
    temp_abs: tuple[float | None, float | None]
    rain_opt_mm: tuple[float | None, float | None]
    rain_abs_mm: tuple[float | None, float | None]
    cycle_days: tuple[float | None, float | None]
    ph_opt: tuple[float | None, float | None]
    ph_abs: tuple[float | None, float | None]
    altitude_max_m: float | None
    kill_temp_early_c: float | None
    soil_depth_opt: str | None
    soil_depth_abs: str | None
    soil_texture_opt: list[str]
    soil_texture_abs: list[str]
    drainage_opt: list[str]
    drainage_abs: list[str]
    fertility_requirement: str | None      # EcoCrop optimal soil fertility: low/moderate/high
    fertility_abs: str | None
    salinity_opt: str | None
    salinity_abs: str | None
    photoperiod: list[str]
    fixes_nitrogen: bool
    n_fixation: dict | None
    rotation_benefit: dict | None
    sources: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    data_issues: list[str] = field(default_factory=list)

    @property
    def is_legume(self) -> bool:
        return self.group == "grain_legume"


@dataclass
class CropDataset:
    crops: dict[str, Crop]
    sources: dict[str, dict]
    calendars: list[dict]
    not_yet_covered: list[str]

    def get(self, crop_id: str) -> Crop:
        try:
            return self.crops[crop_id]
        except KeyError:
            raise KeyError(f"Unknown crop '{crop_id}'. Known: {sorted(self.crops)}") from None

    def ids(self) -> list[str]:
        return sorted(self.crops)

    def by_group(self, group: str) -> list[Crop]:
        return [c for c in self.crops.values() if c.group == group]

    def provenance_table(self) -> list[dict]:
        """One row per crop for the dashboard / report."""
        return [{"crop": c.common_name, "sources": ", ".join(c.sources),
                 "missing_fields": ", ".join(c.missing) or "none",
                 "data_issues": "; ".join(c.data_issues) or "none"} for c in self.crops.values()]


# ------------------------------------------------------------------ parsing helpers
def _num(x):
    x = (x or "").strip()
    if x == "":
        return None
    try:
        return float(x)
    except ValueError:
        return None


def _txt(x):
    x = (x or "").strip()
    return x or None


def _list(x):
    x = (x or "").strip()
    return [p.strip() for p in x.split(",") if p.strip()] if x else []


def _family(famname: str | None) -> str | None:
    # EcoCrop: "Magnoliopsida:Rosidae:Fabales:Leguminosae" -> "Leguminosae"
    return famname.split(":")[-1].strip() if famname else None


# ------------------------------------------------------------------ validation
def validate_ranges(c: dict) -> list[str]:
    """Return a list of plausibility problems found in raw numeric fields."""
    issues = []

    def chain(label, *vals):
        v = [x for x in vals if x is not None]
        if any(a > b for a, b in zip(v, v[1:])):
            issues.append(f"{label} not ordered: {vals}")

    chain("temperature", c["TMIN"], c["TOPMN"], c["TOPMX"], c["TMAX"])
    chain("rainfall", c["RMIN"], c["ROPMN"], c["ROPMX"], c["RMAX"])
    chain("pH", c["PHMIN"], c["PHOPMN"], c["PHOPMX"], c["PHMAX"])
    chain("cycle", c["GMIN"], c["GMAX"])
    if c["KTMP"] is not None and c["TOPMN"] is not None and c["KTMP"] >= c["TOPMN"]:
        issues.append(f"killing temperature {c['KTMP']} is not below optimal minimum {c['TOPMN']}")
    return issues


# ------------------------------------------------------------------ loading
def load_crops(ecocrop_csv: Path | str = ECOCROP_CSV, overlay_json: Path | str = OVERLAY_JSON) -> CropDataset:
    overlay = json.loads(Path(overlay_json).read_text(encoding="utf-8"))
    sources = overlay["sources"]
    with open(ecocrop_csv, newline="", encoding="utf-8") as f:
        rows = {int(r["EcoPortCode"]): r for r in csv.DictReader(f)}

    crops: dict[str, Crop] = {}
    for cid, meta in overlay["crops"].items():
        code = int(meta["ecocrop_code"])
        if code not in rows:
            raise ValueError(f"Crop '{cid}': EcoCrop code {code} not found in {ecocrop_csv}")
        r = rows[code]
        raw = {k: _num(r.get(k)) for k in ("TOPMN", "TOPMX", "TMIN", "TMAX", "ROPMN", "ROPMX", "RMIN", "RMAX",
                                          "PHOPMN", "PHOPMX", "PHMIN", "PHMAX", "ALTMX", "KTMP", "GMIN", "GMAX")}
        issues = validate_ranges(raw)
        if raw["KTMP"] is not None and raw["TOPMN"] is not None and raw["KTMP"] >= raw["TOPMN"]:
            raw["KTMP"] = None                       # quarantine the implausible value
            issues.append("kill_temp_early_c set to None (implausible EcoCrop value)")

        family = _family(_txt(r.get("FAMNAME")))
        group = meta.get("group")
        nfix = meta.get("n_fixation")
        used = ["ecocrop"] + sorted({s for blk in (nfix, meta.get("rotation_benefit")) if blk
                                      for k, s in blk.items() if k == "src" or k.endswith("_src")})
        for s in used:
            if s not in sources:
                raise ValueError(f"Crop '{cid}' cites unknown source '{s}'")

        crop = Crop(
            id=cid, common_name=meta["common_name"], scientific_name=r["ScientificName"].strip(),
            family=family, group=group, ecocrop_code=code,
            temp_opt=(raw["TOPMN"], raw["TOPMX"]), temp_abs=(raw["TMIN"], raw["TMAX"]),
            rain_opt_mm=(raw["ROPMN"], raw["ROPMX"]), rain_abs_mm=(raw["RMIN"], raw["RMAX"]),
            cycle_days=(raw["GMIN"], raw["GMAX"]), ph_opt=(raw["PHOPMN"], raw["PHOPMX"]),
            ph_abs=(raw["PHMIN"], raw["PHMAX"]), altitude_max_m=raw["ALTMX"], kill_temp_early_c=raw["KTMP"],
            soil_depth_opt=_txt(r.get("DEP")), soil_depth_abs=_txt(r.get("DEPR")),
            soil_texture_opt=_list(r.get("TEXT")), soil_texture_abs=_list(r.get("TEXTR")),
            drainage_opt=_list(r.get("DRA")), drainage_abs=_list(r.get("DRAR")),
            fertility_requirement=_txt(r.get("FER")), fertility_abs=_txt(r.get("FERR")),
            salinity_opt=_txt(r.get("SAL")), salinity_abs=_txt(r.get("SALR")),
            photoperiod=_list(r.get("PHOTO")),
            fixes_nitrogen=(family == "Leguminosae"),
            n_fixation=nfix, rotation_benefit=meta.get("rotation_benefit"),
            sources=used, data_issues=issues,
        )
        crop.missing = [name for name, val in (
            ("temperature optimum", crop.temp_opt), ("temperature absolute", crop.temp_abs),
            ("rainfall optimum", crop.rain_opt_mm), ("rainfall absolute", crop.rain_abs_mm),
            ("cycle length", crop.cycle_days), ("pH optimum", crop.ph_opt),
            ("soil texture", crop.soil_texture_opt), ("drainage", crop.drainage_opt),
            ("fertility requirement", crop.fertility_requirement),
            ("kill temperature", crop.kill_temp_early_c)) if not val or val == (None, None)]
        if crop.fixes_nitrogen and not nfix:
            crop.missing.append("nitrogen fixation estimate")
        crops[cid] = crop

    return CropDataset(crops, sources, overlay.get("calendars", []), overlay.get("not_yet_covered", []))


# ------------------------------------------------------------------ calendars
@dataclass
class CalendarMatch:
    calendar: dict
    warnings: list[str]


def calendar_for(ds: CropDataset, lat: float, lon: float) -> CalendarMatch | None:
    """Regional planting calendar covering this point, or None.

    None means 'no local calendar in the dataset': seasons must then be inferred
    from the climate data, and the dashboard must say so."""
    for cal in ds.calendars:
        b = cal["bbox"]
        if b["lat_min"] <= lat <= b["lat_max"] and b["lon_min"] <= lon <= b["lon_max"]:
            return CalendarMatch(cal, list(cal.get("caveats", [])) + [cal.get("bbox_note", "")])
    return None


def calendar_applies(cal: dict, crop: Crop) -> bool:
    """Grouped calendars apply only to the crop groups the source describes
    (here seasonal cereals and pulses). Roots/tubers such as cassava are not covered."""
    return crop.group in cal.get("applies_to_groups", [])
