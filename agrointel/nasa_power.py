"""NASA POWER client (Stage 1).

Purpose: fetch daily point data for any lat/lon, keep the RAW response intact,
attach provenance metadata, cache to disk, and fail gracefully.

Honest framing: POWER values are gridded (~0.5° x 0.625° for the MERRA-2
meteorology, ~1° for CERES solar), blended from satellite and reanalysis
models. They are NOT farm-level sensor readings.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

import pandas as pd
import requests

from . import config

log = logging.getLogger(__name__)


class NasaPowerError(RuntimeError):
    """Raised when POWER data cannot be retrieved or parsed."""


@dataclass
class PowerResult:
    raw: pd.DataFrame                 # untouched values as returned (fill values kept)
    metadata: dict                    # provenance: source, units, resolution, timestamps
    from_cache: bool = False
    warnings: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ helpers
def validate_coordinates(lat: float, lon: float) -> tuple[float, float]:
    if not (-90.0 <= lat <= 90.0):
        raise ValueError(f"Latitude {lat} out of range [-90, 90].")
    if not (-180.0 <= lon <= 180.0):
        raise ValueError(f"Longitude {lon} out of range [-180, 180].")
    return float(lat), float(lon)


def _cache_path(key: str):
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return config.CACHE_DIR / f"power_{key}.json"


def _cache_key(lat, lon, start, end, params) -> str:
    # Round coordinates to the grid scale: nearby points share one POWER cell,
    # so there is no point caching them separately.
    payload = f"{round(lat, 2)}|{round(lon, 2)}|{start}|{end}|{','.join(sorted(params))}"
    return hashlib.sha1(payload.encode()).hexdigest()[:16]


def _cache_fresh(path, end: date) -> bool:
    if not path.exists():
        return False
    # Historical windows never change much; windows touching "now" expire.
    ttl_h = config.CACHE_TTL_HOURS if end >= date.today() - timedelta(days=7) else 24 * 30
    age_h = (time.time() - path.stat().st_mtime) / 3600
    return age_h < ttl_h


def _request_with_retries(url: str, params: dict) -> dict:
    last_err: Exception | None = None
    for attempt in range(1, config.POWER_MAX_RETRIES + 1):
        try:
            r = requests.get(url, params=params, timeout=config.POWER_REQUEST_TIMEOUT_S)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                last_err = NasaPowerError(f"HTTP {r.status_code}: {r.text[:200]}")
            else:  # 4xx other than 429: retrying will not help
                raise NasaPowerError(f"HTTP {r.status_code}: {r.text[:300]}")
        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = e
        wait = config.POWER_RETRY_BACKOFF_S * 2 ** (attempt - 1)
        log.warning("POWER request failed (attempt %d): %s; retrying in %.0fs",
                    attempt, last_err, wait)
        time.sleep(wait)
    raise NasaPowerError(f"NASA POWER unreachable after retries: {last_err}")


def parse_power_json(payload: dict, requested: Iterable[str]) -> tuple[pd.DataFrame, dict]:
    """Turn a POWER JSON payload into a date-indexed DataFrame (raw values)."""
    try:
        param_block = payload["properties"]["parameter"]
    except (KeyError, TypeError) as e:
        raise NasaPowerError("Unexpected POWER response structure (no properties.parameter).") from e
    if not param_block:
        raise NasaPowerError("POWER returned no parameters.")

    df = pd.DataFrame(param_block)
    df.index = pd.to_datetime(df.index, format="%Y%m%d", errors="coerce")
    if df.index.isna().any():
        raise NasaPowerError("POWER response contained unparseable dates.")
    df.index.name = "date"
    df = df.apply(pd.to_numeric, errors="coerce")  # keep -999 as-is here (raw)

    missing = [p for p in requested if p not in df.columns]
    api_meta = {
        "api_units": {k: v.get("units") for k, v in payload.get("parameters", {}).items()},
        "api_long_names": {k: v.get("longname") for k, v in payload.get("parameters", {}).items()},
        "api_fill_value": payload.get("header", {}).get("fill_value"),
        "api_version": payload.get("header", {}).get("api", {}).get("version"),
        "grid_elevation_m": (payload.get("geometry", {}).get("coordinates") or [None, None, None])[2:3] or None,
        "missing_parameters": missing,
    }
    return df, api_meta


# ------------------------------------------------------------------ public
def fetch_power_daily(
    lat: float,
    lon: float,
    start: date | str,
    end: date | str,
    parameters: Iterable[str] | None = None,
    use_cache: bool = True,
) -> PowerResult:
    """Fetch daily NASA POWER data for a point. Raises NasaPowerError on failure
    (unless a stale cache exists, in which case it is returned with a warning)."""
    lat, lon = validate_coordinates(lat, lon)
    params = list(parameters or config.DEFAULT_POWER_PARAMS)
    unknown = [p for p in params if p not in config.POWER_PARAMETERS]
    if unknown:
        raise ValueError(f"Unsupported POWER parameters: {unknown}")

    start_d = pd.Timestamp(start).date()
    end_d = pd.Timestamp(end).date()
    if start_d > end_d:
        raise ValueError("start must be on or before end.")
    if start_d < pd.Timestamp(config.POWER_EARLIEST_DATE).date():
        raise ValueError(f"POWER daily data starts {config.POWER_EARLIEST_DATE}.")

    key = _cache_key(lat, lon, start_d, end_d, params)
    path = _cache_path(key)
    warnings: list[str] = []

    if use_cache and _cache_fresh(path, end_d):
        cached = json.loads(path.read_text())
        df, api_meta = parse_power_json(cached["payload"], params)
        meta = cached["metadata"]
        meta["served_from_cache"] = True
        return PowerResult(df, meta, from_cache=True, warnings=warnings)

    query = {
        "parameters": ",".join(params),
        "community": config.POWER_COMMUNITY,
        "longitude": lon,
        "latitude": lat,
        "start": start_d.strftime("%Y%m%d"),
        "end": end_d.strftime("%Y%m%d"),
        "format": "JSON",
    }
    try:
        payload = _request_with_retries(config.POWER_DAILY_POINT_URL, query)
    except NasaPowerError as e:
        if path.exists():  # stale cache beats nothing
            cached = json.loads(path.read_text())
            df, _ = parse_power_json(cached["payload"], params)
            meta = cached["metadata"]
            meta["served_from_cache"] = True
            meta["stale_cache"] = True
            warnings.append(f"Live NASA request failed ({e}); showing STALE cached data "
                            f"retrieved {meta.get('retrieved_utc')}.")
            return PowerResult(df, meta, from_cache=True, warnings=warnings)
        raise

    df, api_meta = parse_power_json(payload, params)
    if api_meta["missing_parameters"]:
        warnings.append(f"Parameters not returned by POWER: {api_meta['missing_parameters']}")

    metadata = {
        "source": "NASA POWER (Prediction Of Worldwide Energy Resources)",
        "product": "Daily point API, AG community (MERRA-2 meteorology, CERES/GEWEX solar)",
        "endpoint": config.POWER_DAILY_POINT_URL,
        "requested_lat": lat,
        "requested_lon": lon,
        "start": str(start_d),
        "end": str(end_d),
        "parameters": params,
        "units": {p: api_meta["api_units"].get(p) or config.POWER_PARAMETERS[p]["unit"] for p in params},
        "spatial_resolution": "Gridded: about 0.5° lat x 0.625° lon (meteorology), ~1° (solar). "
                              "One value represents a whole grid cell, not the farm.",
        "temporal_resolution": "Daily",
        "retrieved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "api_version": api_meta["api_version"],
        "fill_value": api_meta["api_fill_value"],
        "limitations": [
            "Model/satellite-derived grid values, not station or farm sensor measurements.",
            "Precipitation in particular can differ from local rain gauges, especially in "
            "convective or mountainous areas.",
            "Recent days may be revised as NASA finalises data (latency of a few days).",
        ],
        "served_from_cache": False,
    }
    try:
        path.write_text(json.dumps({"payload": payload, "metadata": metadata}))
    except OSError as e:  # read-only FS etc. must not break the app
        log.warning("Could not write cache: %s", e)

    return PowerResult(df, metadata, from_cache=False, warnings=warnings)
