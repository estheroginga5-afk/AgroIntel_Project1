"""NASA SMAP soil moisture client (Stage 2).

Why AppEEARS: SMAP files are global (tens of MB each, 8 per day for L4), so
downloading them to read one farm is wasteful. AppEEARS extracts a point time
series on NASA's side and returns a small CSV. It needs a free NASA Earthdata
login; sign in once at https://appeears.earthdatacloud.nasa.gov in a browser
to accept its terms if the API login is refused.

Honest framing: SPL4SMGP is SMAP LEVEL 4: a land-surface MODEL constrained by
SMAP satellite brightness temperatures. It is a model estimate on a 9 km grid,
not a direct measurement, and not a farm soil probe.

Credentials are never stored in code. See `get_earthdata_credentials`.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests

from . import config
from .nasa_power import _cache_key, validate_coordinates

log = logging.getLogger(__name__)


class SmapError(RuntimeError):
    """SMAP data could not be retrieved or parsed."""


class SmapAuthError(SmapError):
    """Earthdata credentials missing or rejected."""


@dataclass
class SmapResult:
    raw: pd.DataFrame            # sub-daily values exactly as returned (fill values kept)
    metadata: dict
    from_cache: bool = False
    warnings: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ credentials
def get_earthdata_credentials() -> tuple[str, str]:
    """Env vars first, then Streamlit secrets, then Colab secrets."""
    u, p = os.environ.get("EARTHDATA_USERNAME"), os.environ.get("EARTHDATA_PASSWORD")
    if u and p:
        return u, p
    try:  # Streamlit Community Cloud
        import streamlit as st
        u, p = st.secrets["estheroginga"], st.secrets["5ndQGiK2Zvt/$73"]
        if u and p:
            return str(u), str(p)
    except Exception:
        pass
    try:  # Google Colab "Secrets" panel
        from google.colab import userdata
        u, p = userdata.get("estheroginga"), userdata.get("5ndQGiK2Zvt/$73")
        if u and p:
            return u, p
    except Exception:
        pass
    raise SmapAuthError(
        "NASA Earthdata credentials not found. Set EARTHDATA_USERNAME and "
        "EARTHDATA_PASSWORD as environment variables, Streamlit secrets or Colab secrets."
    )


# ------------------------------------------------------------------ HTTP helpers
def _api(path: str) -> str:
    return config.APPEEARS_BASE_URL.rstrip("/") + "/" + path.lstrip("/")


def _call(method: str, path: str, *, token: str | None = None, **kw) -> requests.Response:
    headers = kw.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.request(method, _api(path), headers=headers,
                             timeout=config.SMAP_REQUEST_TIMEOUT_S, **kw)
    except (requests.Timeout, requests.ConnectionError) as e:
        raise SmapError(f"AppEEARS unreachable: {e}") from e
    if r.status_code in (401, 403):
        raise SmapAuthError(f"AppEEARS rejected the request (HTTP {r.status_code}). Check your "
                            "Earthdata login and that you have signed in to AppEEARS once.")
    if r.status_code >= 400:
        raise SmapError(f"AppEEARS HTTP {r.status_code} on {path}: {r.text[:300]}")
    return r


def _login(user: str, password: str) -> str:
    r = _call("POST", "login", auth=(user, password))
    token = r.json().get("token")
    if not token:
        raise SmapAuthError("AppEEARS login returned no token.")
    return token


def _version_key(v: str):
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", v) if x]


def _resolve_product(token: str) -> str:
    """Return 'SPL4SMGP.008'-style id: configured version, else newest available."""
    if config.SMAP_PRODUCT_VERSION:
        return f"{config.SMAP_PRODUCT_ID}.{config.SMAP_PRODUCT_VERSION}"
    products = _call("GET", "product", token=token).json()
    found = [p for p in products
             if p.get("Product") == config.SMAP_PRODUCT_ID and p.get("Available", True)]
    if not found:
        smap = sorted({p.get("ProductAndVersion", "?") for p in products
                       if str(p.get("Product", "")).startswith("SPL")})
        raise SmapError(f"{config.SMAP_PRODUCT_ID} not available in AppEEARS. "
                        f"SMAP products listed: {smap or 'none'}")
    found.sort(key=lambda p: _version_key(str(p.get("Version", ""))))
    return found[-1]["ProductAndVersion"]


def _validate_layers(token: str, product: str) -> None:
    layers = _call("GET", f"product/{product}", token=token).json()
    missing = [l for l in config.SMAP_LAYERS.values() if l not in layers]
    if missing:
        close = [l for l in layers if "sm_" in l.lower() or "soil" in l.lower()][:15]
        raise SmapError(f"Layers {missing} not found in {product}. Soil-moisture-like layers: {close}")


# ------------------------------------------------------------------ parsing
def parse_appeears_csv(text: str, layers: dict[str, str] | None = None) -> tuple[pd.DataFrame, dict]:
    """AppEEARS point CSV -> (raw DataFrame indexed by UTC timestamp, cell info).

    Columns are matched by layer-name suffix because AppEEARS prefixes them with
    the product/version. Values are left untouched (fill values kept)."""
    layers = layers or config.SMAP_LAYERS
    try:
        df = pd.read_csv(io.StringIO(text))
    except Exception as e:
        raise SmapError(f"Could not read AppEEARS CSV: {e}") from e
    date_col = next((c for c in df.columns if c.lower() == "date"), None)
    if date_col is None or df.empty:
        raise SmapError("AppEEARS CSV has no Date column or no rows.")
    cols = {}
    for name, layer in layers.items():
        match = [c for c in df.columns if c.endswith(layer)]
        if not match:
            raise SmapError(f"Layer {layer} missing from CSV columns: {list(df.columns)[:12]}")
        cols[name] = pd.to_numeric(df[match[0]], errors="coerce")
    raw = pd.DataFrame(cols)
    raw.index = pd.to_datetime(df[date_col], errors="coerce")
    if raw.index.isna().any():
        raise SmapError("AppEEARS CSV contained unparseable dates.")
    raw.index.name = "timestamp_utc"
    cell = {}
    for key, names in (("cell_lat", ("Latitude", "latitude")), ("cell_lon", ("Longitude", "longitude"))):
        c = next((n for n in names if n in df.columns), None)
        if c is not None:
            cell[key] = float(df[c].iloc[0])
    return raw.sort_index(), cell


# ------------------------------------------------------------------ live fetch
def _task_file(key: str):
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return config.CACHE_DIR / f"smap_task_{key}.json"


def _live_fetch(lat, lon, start: date, end: date, key: str) -> tuple[str, dict]:
    """Submit (or resume) an AppEEARS point task and return (csv_text, info)."""
    user, pw = get_earthdata_credentials()
    token = _login(user, pw)
    product = _resolve_product(token)
    _validate_layers(token, product)

    task_id = None
    tf = _task_file(key)
    if tf.exists() and (time.time() - tf.stat().st_mtime) < 24 * 3600:
        try:  # resume a task from an earlier (e.g. timed-out) run
            saved = json.loads(tf.read_text())
            if saved.get("product") == product:
                st = _call("GET", f"task/{saved['task_id']}", token=token).json().get("status")
                if st in ("pending", "queued", "processing", "done"):
                    task_id = saved["task_id"]
        except (SmapError, OSError, KeyError, ValueError):
            task_id = None

    if task_id is None:
        body = {
            "task_type": "point",
            "task_name": f"agrointel_{key}",
            "params": {
                "dates": [{"startDate": start.strftime("%m-%d-%Y"), "endDate": end.strftime("%m-%d-%Y")}],
                "layers": [{"layer": l, "product": product} for l in config.SMAP_LAYERS.values()],
                "coordinates": [{"latitude": lat, "longitude": lon, "id": "farm"}],
            },
        }
        task_id = _call("POST", "task", token=token, json=body).json()["task_id"]
        try:
            tf.write_text(json.dumps({"task_id": task_id, "product": product}))
        except OSError:
            pass

    deadline = time.time() + config.SMAP_TASK_TIMEOUT_S
    while True:
        st = _call("GET", f"task/{task_id}", token=token).json().get("status")
        if st == "done":
            break
        if st in ("error", "failed"):
            raise SmapError(f"AppEEARS task {task_id} failed.")
        if time.time() > deadline:
            raise SmapError(f"AppEEARS task {task_id} still '{st}' after "
                            f"{config.SMAP_TASK_TIMEOUT_S}s. Run again later: it will resume.")
        time.sleep(config.SMAP_POLL_INTERVAL_S)

    files = _call("GET", f"bundle/{task_id}", token=token).json().get("files", [])
    csvs = [f for f in files if f.get("file_type") == "csv" and "results" in f.get("file_name", "").lower()]
    if not csvs:
        raise SmapError("AppEEARS bundle contains no results CSV.")
    r = _call("GET", f"bundle/{task_id}/{csvs[0]['file_id']}", token=token)
    return r.text, {"product": product, "task_id": task_id}


# ------------------------------------------------------------------ public
def fetch_smap(lat: float, lon: float, start: date | str | None = None,
               end: date | str | None = None, use_cache: bool = True) -> SmapResult:
    """Fetch SMAP L4 surface + root-zone soil moisture for a point (raw, sub-daily)."""
    lat, lon = validate_coordinates(lat, lon)
    end_d = pd.Timestamp(end).date() if end else date.today() - timedelta(days=4)
    start_d = (pd.Timestamp(start).date() if start
               else date(end_d.year - config.SMAP_DEFAULT_YEARS, end_d.month, 1))
    warnings: list[str] = []
    earliest = pd.Timestamp(config.SMAP_EARLIEST_DATE).date()
    if start_d < earliest:
        warnings.append(f"SMAP starts {earliest}; start date moved from {start_d}.")
        start_d = earliest
    if start_d > end_d:
        raise ValueError("start must be on or before end.")

    key = _cache_key(lat, lon, start_d, end_d, list(config.SMAP_LAYERS))
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = config.CACHE_DIR / f"smap_{key}.csv"
    meta_path = config.CACHE_DIR / f"smap_{key}.json"

    def _from_cache(stale: bool) -> SmapResult:
        raw, cell = parse_appeears_csv(csv_path.read_text())
        meta = json.loads(meta_path.read_text())
        meta["served_from_cache"] = True
        meta["stale_cache"] = stale
        return SmapResult(raw, meta, True, list(warnings))

    cache_ok = csv_path.exists() and meta_path.exists()
    if use_cache and cache_ok:
        ttl_h = config.CACHE_TTL_HOURS if end_d >= date.today() - timedelta(days=10) else 24 * 30
        if (time.time() - csv_path.stat().st_mtime) / 3600 < ttl_h:
            return _from_cache(False)

    try:
        text, info = _live_fetch(lat, lon, start_d, end_d, key)
    except SmapAuthError:
        raise                      # a stale cache must not hide a credentials problem
    except SmapError as e:
        if cache_ok:
            res = _from_cache(True)
            res.warnings.append(f"Live SMAP request failed ({e}); showing STALE cached data "
                                f"retrieved {res.metadata.get('retrieved_utc')}.")
            return res
        raise

    raw, cell = parse_appeears_csv(text)
    metadata = {
        "source": "NASA SMAP via NASA AppEEARS point sampling",
        "product": info["product"],
        "product_description": "SMAP Level 4 Global 3-hourly 9 km EASE-Grid Surface and Root Zone "
                               "Soil Moisture (land-model estimate constrained by SMAP satellite data)",
        "task_id": info["task_id"],
        "requested_lat": lat, "requested_lon": lon, **cell,
        "start": str(start_d), "end": str(end_d),
        "layers": dict(config.SMAP_LAYERS),
        "units": {k: v["unit"] for k, v in config.SMAP_PARAMETERS.items()},
        "spatial_resolution": "9 km EASE-Grid 2.0 cell. The value describes a ~9 km cell, not the farm.",
        "temporal_resolution": "3-hourly (aggregated to daily means in cleaning)",
        "retrieved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fill_value": config.SMAP_FILL_VALUE,
        "limitations": [
            "Level 4 is a model estimate that assimilates SMAP satellite brightness temperatures; "
            "it is NOT a direct sensor measurement and NOT a farm probe.",
            "Surface layer is roughly the top 5 cm; root zone roughly the top 100 cm. Values are "
            "volumetric (m³/m³) and depend on the model's soil properties, not your field's.",
            "Per NSIDC, SMAP Standard/NRT data had a geolocation issue from 14 May to 28 July 2026; "
            "L4 for that period is being reprocessed and will be replaced. Treat that window with care.",
            "Latest days are near-real-time and may be revised.",
        ],
        "served_from_cache": False,
    }
    try:
        csv_path.write_text(text)
        meta_path.write_text(json.dumps(metadata))
    except OSError as e:
        log.warning("Could not write SMAP cache: %s", e)
    return SmapResult(raw, metadata, False, warnings)
