"""Interactive map with real basemaps: satellite imagery (vegetation green, water blue), NASA vegetation index, terrain.

Tiles are fetched by the viewer's browser from: Esri (World Imagery / World Topo) and NASA GIBS (MODIS NDVI).
Imagery is context only; it does not feed the analysis.
"""
from __future__ import annotations

from datetime import date, timedelta

import plotly.graph_objects as go

ESRI_IMAGERY = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
ESRI_TOPO = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}"
GIBS_NDVI = ("https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Terra_NDVI_8Day/default/{d}/"
             "GoogleMapsCompatible_Level9/{{z}}/{{y}}/{{x}}.png")

LAYERS = {
    "Satellite (vegetation green, water blue)": "satellite",
    "Vegetation greenness (NASA MODIS NDVI)": "ndvi",
    "Terrain, rivers and lakes": "topo",
}
ATTRIBUTION = {
    "satellite": "Imagery: Esri, Maxar, Earthstar Geographics",
    "ndvi": "Vegetation index: NASA GIBS / MODIS Terra NDVI 8-day (about 250 m); imagery: Esri",
    "topo": "Map: Esri World Topo Map",
}


def ndvi_composite_date(today: date | None = None, lag_days: int = 24) -> str:
    """MODIS 8-day composites start on day-of-year 1, 9, 17...; use a recent one that should be published."""
    d = (today or date.today()) - timedelta(days=lag_days)
    start = ((d.timetuple().tm_yday - 1) // 8) * 8 + 1
    return (date(d.year, 1, 1) + timedelta(days=start - 1)).isoformat()


def build_map(lat: float, lon: float, kind: str = "satellite", zoom: float = 8, height: int = 420,
              label: str = "Selected point") -> go.Figure:
    layers = []
    if kind == "topo":
        layers = [dict(sourcetype="raster", source=[ESRI_TOPO], below="traces")]
    else:
        layers = [dict(sourcetype="raster", source=[ESRI_IMAGERY], below="traces")]
        if kind == "ndvi":
            layers.append(dict(sourcetype="raster", source=[GIBS_NDVI.format(d=ndvi_composite_date())],
                               below="traces", opacity=0.75))
    fig = go.Figure(go.Scattermap(lat=[lat], lon=[lon], mode="markers", text=[label], hoverinfo="text",
                                  marker=dict(size=16, color="#ffca28")))
    fig.add_trace(go.Scattermap(lat=[lat], lon=[lon], mode="markers", hoverinfo="skip", showlegend=False,
                                marker=dict(size=7, color="#1b5e20")))
    fig.update_layout(map=dict(style="white-bg", center=dict(lat=lat, lon=lon), zoom=zoom, layers=layers),
                      margin=dict(l=0, r=0, t=0, b=0), height=height, showlegend=False)
    return fig
