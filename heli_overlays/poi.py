"""Points of interest for helicopter sightseeing, annotated with landing context.

Categories: waterfalls, arches, hoodoos / pillars, hot springs, craters and
volcanic features, glaciers, viewpoints.

Sources
  GNIS Domestic Names (USGS), per-state text files on prd-tnm.s3.amazonaws.com.
  OpenStreetMap state extracts from download.geofabrik.de (optional: used when
  it can be downloaded, otherwise the build is GNIS-only and says so).

Each POI gets
  * the landing class at the point (a-g from classes_3857.gpkg, else the SMA
    agency, e.g. NPS / DoD / private),
  * the nearest landable BLM (class g): distance, true bearing, position and
    ground elevation,
  * POI elevation from USGS 3DEP 1 arc-second (read by HTTP range requests),
  * flight flags: NPS no-landing (36 CFR 2.17), FAA AC 91-36D 2,000 ft AGL
    request, Grand Canyon SFRA, DoD land, tribal permission.

Outputs
  work/poi.gpkg, output/SW_POI.csv           full table
  work/pack/SW_Heli/layers/SW_POI_<cat>.kmz  one ForeFlight map layer per category
  work/pack/SW_Heli/navdata/user_waypoints.csv  ForeFlight user waypoints
  output/preview_POI.png
"""
import argparse
import csv
import html
import io
import json
import math
import re
import time
import unicodedata
import zipfile
from collections import defaultdict

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import rasterio
import shapely
from PIL import Image, ImageDraw, ImageFont
from pyproj import Geod

import config
from fetch_sources import download, load_manifest, mtime_utc, save_manifest

GNIS_S3 = "https://prd-tnm.s3.amazonaws.com/StagedProducts/GeographicNames/DomesticNames"
GEOFABRIK = "https://download.geofabrik.de/north-america/us"
OSM_STATES = {"CO": "colorado", "UT": "utah", "NM": "new-mexico", "AZ": "arizona",
              "NV": "nevada"}
DEM = ("https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/1/TIFF/current/"
       "{t}/USGS_1_{t}.tif")
GDAL_ENV = dict(GDAL_CURL_CA_BUNDLE="/root/.ccr/ca-bundle.crt",
                GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif")

PACK = config.WORK / "pack" / "SW_Heli"   # content-pack folder (see make_content_pack.py)
GEOD = Geod(ellps="WGS84")
M_TO_FT = 3.28084

CATEGORIES = {
    # key: (layer label, waypoint prefix, icon colour, icon glyph)
    "waterfall": ("Waterfalls", "WF", "#1565C0", "W"),
    "arch": ("Arches", "AR", "#BF360C", "A"),
    "hoodoo": ("Hoodoos & pillars", "HD", "#795548", "H"),
    "hot_spring": ("Hot springs", "HS", "#00838F", "S"),
    "volcanic": ("Craters & volcanic", "VC", "#37474F", "V"),
    "glacier": ("Glaciers", "GL", "#0288D1", "G"),
    "viewpoint": ("Viewpoints", "VP", "#212121", "VP"),
}
SINGULAR = {"waterfall": "Waterfall", "arch": "Arch", "hoodoo": "Hoodoo / pillar",
            "hot_spring": "Hot spring", "volcanic": "Crater / volcanic feature",
            "glacier": "Glacier", "viewpoint": "Viewpoint"}
STATUS = {  # class -> (short status, landing answer)
    "a": ("NO-LAND", "Wilderness"),
    "b": ("NO-LAND", "Wilderness Study Area"),
    "c": ("NO-LAND", "Tribal land - tribal permission required"),
    "d": ("NO-LAND", "BLM National Monument / NCA"),
    "e": ("CALL FIRST", "State land - state land office permission"),
    "f": ("CALL FIRST", "USFS - check forest orders"),
    "g": ("LANDABLE", "BLM land"),
}
SMA_NAMES = {"NPS": "National Park Service", "FWS": "Fish & Wildlife Service refuge",
             "PVT": "Private", "LG": "Local government", "USBR": "Bureau of Reclamation",
             "DOD": "Department of Defense", "ARMY": "Army", "USAF": "Air Force",
             "NAVY": "Navy", "USMC": "Marine Corps", "USACE": "Army Corps of Engineers",
             "DOE": "Department of Energy", "UND": "Undetermined"}
DOD = {"DOD", "ARMY", "USAF", "NAVY", "USMC"}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ------------------------------------------------------------------- fetching
def fetch(force=False):
    m = load_manifest()
    gdir = config.RAW / "gnis"
    gdir.mkdir(parents=True, exist_ok=True)
    pub = set()
    for st in config.STATES:
        z = download(f"{GNIS_S3}/DomesticNames_{st}_Text.zip",
                     gdir / f"DomesticNames_{st}_Text.zip", force)
        out = gdir / st
        if force or not out.exists():
            with zipfile.ZipFile(z) as zf:
                zf.extractall(out)
        for x in out.glob("*.xml"):
            p = re.search(r"<pubdate>(\d{8})</pubdate>", x.read_text(errors="ignore"))
            if p:
                pub.add(p.group(1))
    m["sources"]["gnis"] = {
        "title": "USGS GNIS Domestic Names (state text files)",
        "used_for": "POIs: Falls, Arch, Pillar, hot/warm Spring, Crater, Lava, Glacier, "
                    "volcanic Summit",
        "url": GNIS_S3 + "/DomesticNames_<ST>_Text.zip",
        "version": "published " + ", ".join(sorted(pub)) if pub else "see S3 date",
        "downloaded": min(mtime_utc(gdir / f"DomesticNames_{s}_Text.zip")
                          for s in config.STATES),
    }

    odir = config.RAW / "osm"
    odir.mkdir(parents=True, exist_ok=True)
    got, stamps = [], []
    for st, name in OSM_STATES.items():
        try:
            f = download(f"{GEOFABRIK}/{name}-latest.osm.pbf", odir / f"{name}-latest.osm.pbf",
                         force)
        except Exception as e:  # noqa: BLE001 - blocked host or outage: build without OSM
            log(f"  OSM {name}: not available ({type(e).__name__}); continuing without OSM")
            continue
        got.append(str(f.relative_to(config.WORK)))
        try:
            import osmium
            h = osmium.io.Reader(str(f), osmium.osm.osm_entity_bits.NOTHING).header()
            stamps.append(h.get("osmosis_replication_timestamp"))
        except Exception:  # noqa: BLE001
            pass
    if got:
        m["sources"]["osm"] = {
            "title": "OpenStreetMap (Geofabrik state extracts)",
            "used_for": "POIs: waterfalls, arches, hoodoos, hot springs, volcanic, glaciers, "
                        "viewpoints",
            "url": GEOFABRIK + "/<state>-latest.osm.pbf",
            "version": "data as of " + ", ".join(sorted(set(s for s in stamps if s))),
            "license": "ODbL 1.0 - (c) OpenStreetMap contributors",
            "downloaded": min(mtime_utc(config.WORK / g) for g in got),
            "files": got,
        }
    else:
        m["sources"].pop("osm", None)
    save_manifest(m)
    return bool(got)


# -------------------------------------------------------------------- reading
def gnis_category(cls, name):
    if cls == "Falls":
        return "waterfall"
    if cls == "Arch":
        return "arch"
    if cls == "Pillar":
        return "hoodoo"
    if cls == "Spring" and re.search(r"\b(hot|warm|thermal)\b", name, re.I):
        return "hot_spring"
    if cls in ("Crater", "Lava"):
        return "volcanic"
    if cls == "Summit" and re.search(r"\b(volcano|cinder cone)\b", name, re.I):
        return "volcanic"
    if cls == "Glacier":
        return "glacier"
    return None


def read_gnis():
    rows = []
    for st in config.STATES:
        for f in (config.RAW / "gnis" / st).rglob("DomesticNames_*.txt"):
            d = pd.read_csv(f, sep="|", dtype=str, encoding="utf-8")
            rows.append(d)
    d = pd.concat(rows, ignore_index=True)
    d["cat"] = [gnis_category(c, n) for c, n in zip(d.feature_class, d.feature_name)]
    # "(historical)" = no longer exists (e.g. Wall Arch, collapsed 2008)
    d = d[d.cat.notna() & ~d.feature_name.str.contains(r"\(historical\)", regex=True)]
    lat, lon = d.prim_lat_dec.astype(float), d.prim_long_dec.astype(float)
    d = d[(lat != 0) & (lon != 0)]
    return pd.DataFrame({
        "cat": d.cat.values, "name": d.feature_name.values,
        "lat": d.prim_lat_dec.astype(float).values, "lon": d.prim_long_dec.astype(float).values,
        "gnis_id": d.feature_id.values, "osm_id": None, "height_m": np.nan,
        "wikipedia": None, "src": "GNIS",
    })


def osm_category(t):
    nat, name = t.get("natural"), t.get("name", "")
    if nat == "waterfall" or t.get("waterway") == "waterfall":
        return "waterfall"
    if nat in ("arch", "natural_arch", "rock_arch") or t.get("geological") == "arch":
        return "arch"
    if nat in ("hoodoo", "spire", "rock_pillar", "pillar") or t.get("rock") == "hoodoo":
        return "hoodoo"
    hot = (t.get("hot_spring") in ("yes", "natural") or t.get("bath:type") == "hot_spring"
           or t.get("spring:type") in ("hot", "thermal") or "hot spring" in name.lower())
    if nat == "hot_spring" or (nat == "spring" and hot) or (
            t.get("leisure") == "bathing_place" and hot):
        return "hot_spring"
    if nat == "volcano" or t.get("geological", "").startswith("volcanic") or (
            nat == "crater"):
        return "volcanic"
    if nat == "glacier":
        return "glacier"
    if t.get("tourism") == "viewpoint":
        return "viewpoint"
    return None


OVERTURE_CLASSES = {
    # Overture base type -> {class: POI category, or None = decide from OSM tags}
    "water": {"waterfall": "waterfall", "hot_spring": "hot_spring", "geyser": "hot_spring",
              "spring": None},
    "land": {"volcano": "volcanic", "volcanic_caldera_rim": "volcanic",
             "meteor_crater": "volcanic", "glacier": "glacier"},
    "infrastructure": {"viewpoint": "viewpoint"},
}


def osm_row(cat, t, lon, lat, oid):
    h = t.get("height", "")
    mh = re.match(r"^\s*([\d.]+)\s*(m|ft|')?\s*$", h)
    height = (float(mh.group(1)) * (0.3048 if mh.group(2) in ("ft", "'") else 1)
              if mh else np.nan)
    return {"cat": cat, "name": t.get("name"), "lat": lat, "lon": lon,
            "gnis_id": t.get("gnis:feature_id") or t.get("gnis:id"),
            "osm_id": oid, "height_m": height,
            "wikipedia": t.get("wikipedia"), "src": "OSM"}


def keep_osm(d):
    """Keep named features; unnamed waterfalls only if tagged >= 10 m high."""
    if len(d) == 0:
        return d
    keep = d.name.notna() | ((d.cat == "waterfall") & (d.height_m >= 10))
    junk = (d.cat == "viewpoint") & d.name.fillna("").str.strip().str.match(JUNK_VIEWPOINT)
    return d[keep & ~junk].reset_index(drop=True)


def read_overture(area):
    """OpenStreetMap features via the Overture Maps base theme on S3 (used when
    the Geofabrik extracts cannot be downloaded)."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import overture as ov

    rel = ov.latest_release()
    cache = config.RAW / "osm" / f"overture_{rel}.pkl"
    if cache.exists():
        log(f"  using cached {cache.name}")
        return keep_osm(pd.read_pickle(cache)), rel
    out = []
    for otype, classes in OVERTURE_CLASSES.items():
        t = ov.read_area(rel, otype, area,
                         ["names", "class", "source_tags", "sources", "geometry"],
                         row_filter=lambda tb, c=classes: pc.is_in(
                             tb.column("class"), value_set=pa.array(list(c))))
        if t is None:
            continue
        geoms = shapely.from_wkb(t.column("geometry").to_pylist())
        reps = shapely.point_on_surface(geoms)
        for cls, tags, srcs, names, pt in zip(
                t.column("class").to_pylist(), t.column("source_tags").to_pylist(),
                t.column("sources").to_pylist(), t.column("names").to_pylist(), reps):
            tags = dict(tags or [])
            if names and names.get("primary") and "name" not in tags:
                tags["name"] = names["primary"]
            cat = classes[cls] or osm_category(tags)
            if cat is None:
                continue
            rid = (srcs or [{}])[0].get("record_id") or ""
            m = re.match(r"^([nwr])(\d+)", rid)
            oid = (f"{dict(n='node', w='way', r='relation')[m.group(1)]}/{m.group(2)}"
                   if m else rid)
            out.append(osm_row(cat, tags, pt.x, pt.y, oid))
    d = pd.DataFrame(out)
    log("  Overture/OSM raw counts:", d.cat.value_counts().to_dict() if len(d) else {})
    d = keep_osm(d)
    cache.parent.mkdir(parents=True, exist_ok=True)
    d.to_pickle(cache)
    return d, rel


def read_osm():
    import osmium

    out = []

    def add(cat, t, lon, lat, oid):
        out.append(osm_row(cat, t, lon, lat, oid))

    class H(osmium.SimpleHandler):
        def node(self, n):
            if not n.tags:
                return
            t = {k: v for k, v in n.tags}
            c = osm_category(t)
            if c:
                add(c, t, n.location.lon, n.location.lat, f"node/{n.id}")

        def way(self, w):
            if w.is_closed():
                return   # handled as an area
            t = {k: v for k, v in w.tags}
            c = osm_category(t)
            if c:
                pts = [(nd.lon, nd.lat) for nd in w.nodes if nd.location.valid()]
                if pts:
                    p = shapely.LineString(pts).interpolate(0.5, normalized=True) \
                        if len(pts) > 1 else shapely.Point(pts[0])
                    add(c, t, p.x, p.y, f"way/{w.id}")

        def area(self, a):
            t = {k: v for k, v in a.tags}
            c = osm_category(t)
            if not c:
                return
            polys = []
            for outer in a.outer_rings():
                ring = [(nd.lon, nd.lat) for nd in outer]
                if len(ring) >= 4:
                    polys.append(shapely.Polygon(ring))
            if polys:
                p = shapely.MultiPolygon(polys).representative_point() if len(polys) > 1 \
                    else polys[0].representative_point()
                oid = (f"way/{a.orig_id()}" if a.from_way() else f"relation/{a.orig_id()}")
                add(c, t, p.x, p.y, oid)

    for f in sorted((config.RAW / "osm").glob("*.osm.pbf")):
        log(f"  reading {f.name}")
        H().apply_file(str(f), locations=True, idx="flex_mem")
    d = pd.DataFrame(out)
    log("  OSM raw counts:", d.cat.value_counts().to_dict() if len(d) else {})
    return keep_osm(d)


# ------------------------------------------------------------------- merging
def norm_name(s):
    if not isinstance(s, str):
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", "", s.replace("'", ""))
    return re.sub(r"\s+", " ", s).strip()


PLURAL = {"springs": "spring", "falls": "fall", "arches": "arch", "hoodoos": "hoodoo",
          "pinnacles": "pinnacle", "craters": "crater"}
GENERIC = {"the", "hot", "warm", "spring", "fall", "waterfall", "arch", "viewpoint",
           "overlook", "vista", "lookout", "scenic"}
JUNK_VIEWPOINT = re.compile(
    r"^(tour )?stop\s*#?\d*$|^\d+$|interpretive|^(scenic )?(viewpoint|overlook|vista|"
    r"lookout|view point|view|scenic view|scenic overlook)$", re.I)


def core_name(s):
    """Name reduced for matching: 'Gillard Hot Springs' == 'Gillard Hot Spring'."""
    toks = [PLURAL.get(t, t) for t in norm_name(s).split()]
    core = [t for t in toks if t not in GENERIC]
    return " ".join(core or toks)


def merge(gnis, osm):
    """GNIS is the name authority; OSM adds features GNIS lacks, plus heights,
    wikipedia links and (for matched features) its usually better position."""
    if osm is None or len(osm) == 0:
        return gnis.reset_index(drop=True)
    g = gnis.reset_index(drop=True).copy()
    g["nn"] = g.name.map(core_name)
    o = osm.copy()
    o["nn"] = o.name.map(core_name)
    by_id = {gid: i for i, gid in enumerate(g.gnis_id)}
    lon0, lat0 = g.lon.values, g.lat.values
    extra, matched = [], 0
    for r in o.itertuples():
        gi = by_id.get(str(r.gnis_id)) if isinstance(r.gnis_id, str) else None
        if gi is None:
            same = np.where((g.cat.values == r.cat))[0]
            if len(same):
                _, _, dist = GEOD.inv(np.full(len(same), r.lon), np.full(len(same), r.lat),
                                      lon0[same], lat0[same])
                if r.nn:
                    hit = same[(g.nn.values[same] == r.nn) & (dist < 2000)]
                else:
                    hit = same[dist < 200]
                gi = hit[0] if len(hit) else None
        if gi is None:
            extra.append(r)
            continue
        matched += 1
        _, _, dd = GEOD.inv(r.lon, r.lat, g.at[gi, "lon"], g.at[gi, "lat"])
        if dd < 2000:   # OSM positions are usually on the feature itself
            g.at[gi, "lon"], g.at[gi, "lat"] = r.lon, r.lat
        g.at[gi, "osm_id"] = r.osm_id
        g.at[gi, "src"] = "GNIS+OSM"
        if not math.isnan(r.height_m):
            g.at[gi, "height_m"] = r.height_m
        if isinstance(r.wikipedia, str):
            g.at[gi, "wikipedia"] = r.wikipedia
    log(f"  merged: {matched} OSM features matched GNIS, {len(extra)} OSM-only")
    e = pd.DataFrame([x._asdict() for x in extra]).drop(columns=["Index"], errors="ignore")
    # collapse OSM-only duplicates (same category + name within 2 km)
    if len(e):
        e = e.sort_values(["cat", "nn", "lat"])
        keep, last = [], {}
        for r in e.itertuples():
            k = (r.cat, r.nn)
            if r.nn and k in last:
                _, _, d = GEOD.inv(r.lon, r.lat, last[k][0], last[k][1])
                if d < 2000:
                    continue
            last[k] = (r.lon, r.lat)
            keep.append(r.Index)
        e = e.loc[keep]
    out = pd.concat([g, e], ignore_index=True)
    # A viewpoint named after a nearby feature we already have ("Havasu Falls"
    # viewpoint next to the falls) is the same place twice: keep the feature.
    vp = out.index[out.cat == "viewpoint"]
    feat = out[out.cat != "viewpoint"]
    drop = []
    for i in vp:
        same = feat[feat.nn == out.at[i, "nn"]]
        if out.at[i, "nn"] and len(same):
            _, _, d = GEOD.inv(np.full(len(same), out.at[i, "lon"]),
                               np.full(len(same), out.at[i, "lat"]), same.lon.values,
                               same.lat.values)
            if (d < 1500).any():
                drop.append(i)
    log(f"  dropped {len(drop)} viewpoints duplicating a nearby named feature")
    return out.drop(index=drop).drop(columns=["nn"])


# ---------------------------------------------------------------- annotating
DEM_CACHE = config.WORK / "dem_cache.json"


def sample_dem(lons, lats):
    """Ground elevation (ft) from 3DEP 1 arc-second, grouped by 1-degree tile.
    Results are cached by rounded position (about 1 m) in work/dem_cache.json."""
    cache = json.loads(DEM_CACHE.read_text()) if DEM_CACHE.exists() else {}
    keys = [f"{y:.5f},{x:.5f}" for x, y in zip(lons, lats)]
    out = np.array([cache.get(k, np.nan) for k in keys], dtype=float)
    tiles = defaultdict(list)
    for i, (x, y) in enumerate(zip(lons, lats)):
        if keys[i] not in cache:
            tiles[f"n{math.floor(y) + 1:02d}w{-math.floor(x):03d}"].append(i)
    with rasterio.Env(**GDAL_ENV):
        for k, (t, idx) in enumerate(sorted(tiles.items())):
            try:
                with rasterio.open("/vsicurl/" + DEM.format(t=t)) as ds:
                    vals = [v[0] for v in ds.sample([(lons[i], lats[i]) for i in idx])]
                    nod = ds.nodata
            except Exception as e:  # noqa: BLE001
                log(f"  DEM {t}: {e}")
                continue
            for i, v in zip(idx, vals):
                if nod is None or v != nod:
                    out[i] = round(float(v) * M_TO_FT, 1)
                    cache[keys[i]] = out[i]
            if (k + 1) % 25 == 0:
                log(f"  DEM tiles {k + 1}/{len(tiles)}")
    DEM_CACHE.write_text(json.dumps(cache))
    return out


def annotate(p):
    pts = gpd.GeoSeries(gpd.points_from_xy(p.lon, p.lat), crs=4326).to_crs(3857).values
    region = shapely.union_all(pyogrio.read_dataframe(config.NORMALIZED, layer="states")
                               .to_crs(3857).geometry.values)
    inside = shapely.contains(region, pts)
    p, pts = p[inside].reset_index(drop=True), pts[inside]
    log(f"  {len(p)} POIs inside the five states")

    cl = pyogrio.read_dataframe(config.CLASSES, layer="classes")
    g = shapely.get_parts(cl.geometry.values)
    c = np.repeat(cl.cls.values, shapely.get_num_geometries(cl.geometry.values))
    pi, gi = shapely.STRtree(g).query(pts, predicate="within")
    cls = np.array([None] * len(p), dtype=object)
    cls[pi] = c[gi]
    p["landing_class"] = cls

    # SMA agency at each point. The SMA polygons are a few hundred huge
    # multipolygons; test points against each instead of repairing them.
    sma = pyogrio.read_dataframe(config.RAW / "blm" / "sma.gpkg").to_crs(3857)
    ptree = shapely.STRtree(pts)
    agency = np.array([None] * len(p), dtype=object)
    for geom, code in zip(sma.geometry.values, sma.ADMIN_AGENCY_CODE.values):
        idx = ptree.query(geom)
        if len(idx):
            shapely.prepare(geom)
            hit = idx[shapely.contains(geom, pts[idx])]
            agency[hit] = np.where(agency[hit] == None, code, agency[hit])  # noqa: E711
    p["sma_agency"] = agency

    # Nearest landable BLM: skip pieces under 2 ha (slivers are not landing spots).
    lat_c = np.degrees(2 * np.arctan(np.exp(shapely.get_y(shapely.centroid(g))
                                            / 6378137.0)) - np.pi / 2)
    big = (c == "g") & (shapely.area(g) * np.cos(np.radians(lat_c)) ** 2 >= 20_000)
    gg = g[big]
    near = gg[shapely.STRtree(gg).nearest(pts)]
    on_g = p.landing_class.values == "g"
    nearest_pts = shapely.get_point(shapely.shortest_line(pts, near), 1)
    np_ll = gpd.GeoSeries(nearest_pts, crs=3857).to_crs(4326)
    az, _, dist = GEOD.inv(p.lon.values, p.lat.values, np_ll.x.values, np_ll.y.values)
    p["blm_nm"] = np.where(on_g, 0.0, dist / 1852.0)
    p["blm_bearing"] = np.where(on_g, np.nan, (az + 360) % 360)
    p["blm_lat"] = np.where(on_g, p.lat, np_ll.y.values)
    p["blm_lon"] = np.where(on_g, p.lon, np_ll.x.values)

    log("  sampling 3DEP elevations")
    p["elev_ft"] = sample_dem(p.lon.values, p.lat.values)
    p["blm_elev_ft"] = np.where(on_g, p.elev_ft,
                                sample_dem(p.blm_lon.values, p.blm_lat.values))

    # Flight flags
    gc = grand_canyon()
    if gc is not None:
        shapely.prepare(gc[0])
        shapely.prepare(gc[1])
    flags = []
    for i, r in enumerate(p.itertuples()):
        f = []
        if r.sma_agency == "NPS" and r.landing_class is None:
            f.append("NPS land: landing prohibited (36 CFR 2.17)")
        if r.landing_class == "a" or r.sma_agency in ("NPS", "FWS"):
            f.append("FAA AC 91-36D: pilots asked to stay >= 2,000 ft AGL over NPS, "
                     "wilderness and refuges")
        if gc is not None:
            if shapely.contains(gc[0], pts[i]):
                f.append("Inside Grand Canyon NP: Grand Canyon SFRA rules apply "
                         "(14 CFR 93 Subpart U)")
            elif shapely.contains(gc[1], pts[i]):
                f.append("Within 15 nm of Grand Canyon NP: check the Grand Canyon SFRA "
                         "on the Grand Canyon VFR chart")
        if r.sma_agency in DOD:
            f.append("DoD land: check restricted / military airspace")
        flags.append(f)
    p["flags"] = flags
    return p


def grand_canyon():
    gdbs = list((config.RAW / "padus").glob("**/*StateAZ.gdb"))
    if not gdbs:
        return None
    d = pyogrio.read_dataframe(gdbs[0], layer="PADUS4_1Fee_State_AZ")
    d = d[d.Unit_Nm == "Grand Canyon National Park"].to_crs(3857)
    if not len(d):
        return None
    park = shapely.union_all(shapely.make_valid(d.geometry.values))
    # 15 nm in Web Mercator units at ~36 N
    return park, park.buffer(15 * 1852 / math.cos(math.radians(36.2)))


# ------------------------------------------------------------------- writing
def display_name(r):
    if isinstance(r.name, str) and r.name.strip():
        return r.name.strip()
    h = r.height_m
    if isinstance(h, float) and not math.isnan(h):
        return f"{SINGULAR[r.cat]} ({h:.0f} m)"
    return SINGULAR[r.cat]


def status_of(r):
    if r.landing_class in STATUS:
        return STATUS[r.landing_class]
    a = r.sma_agency
    if a == "NPS":
        return ("NO-GO", "National Park Service")
    if a in SMA_NAMES:
        return ("NO-GO" if a in DOD | {"FWS"} else "OTHER", SMA_NAMES[a])
    return ("OTHER", "Unclassified (private / other)")


def ring_color(r):
    info = {k: col for k, _, _, col, _ in config.CLASSES_DEF}
    return info.get(r.landing_class if isinstance(r.landing_class, str) else None, "#9E9E9E")


def make_icon(cat, ring):
    _, _, fill, glyph = CATEGORIES[cat]
    S = 64
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse([2, 2, S - 3, S - 3], fill=ring)
    d.ellipse([9, 9, S - 10, S - 10], fill=fill, outline="white", width=3)
    try:
        font = ImageFont.load_default(size=26 if len(glyph) == 1 else 19)
    except TypeError:
        font = ImageFont.load_default()
    d.text((S / 2, S / 2 + 1), glyph, fill="white", font=font, anchor="mm")
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def describe(r):
    status, what = status_of(r)
    lines = [f"<b>{html.escape(SINGULAR[r.cat])}"
             + (f", {r.height_m:.0f} m / {r.height_m * M_TO_FT:.0f} ft high"
                if isinstance(r.height_m, float) and not math.isnan(r.height_m) else "")
             + "</b>",
             f"<b>Land here: {status}</b> - {html.escape(what)}"]
    if not math.isnan(r.elev_ft):
        lines.append(f"Ground elevation: {r.elev_ft:,.0f} ft MSL")
    if r.landing_class != "g":
        e = f", ground {r.blm_elev_ft:,.0f} ft" if not math.isnan(r.blm_elev_ft) else ""
        lines.append(f"Nearest landable BLM: {r.blm_nm:.1f} nm, {r.blm_bearing:03.0f} deg true "
                     f"from here{e} ({r.blm_lat:.5f}, {r.blm_lon:.5f})")
    for f in r.flags:
        lines.append("! " + html.escape(f))
    src = [s for s in (f"GNIS {r.gnis_id}" if isinstance(r.gnis_id, str) else None,
                       f"OSM {r.osm_id}" if isinstance(r.osm_id, str) else None) if s]
    if isinstance(getattr(r, "waypoint", None), str):
        lines.append(f"User waypoint: <b>{r.waypoint}</b> (Direct-To / flight plan)")
    lines.append(f"{r.lat:.5f}, {r.lon:.5f} - " + ", ".join(src))
    return "<br/>".join(lines)


def write_kmz(p):
    layers = PACK / "layers"
    layers.mkdir(parents=True, exist_ok=True)
    for old in layers.glob("SW_POI_*.kmz"):
        old.unlink()
    files = []
    for cat, (label, *_r) in CATEGORIES.items():
        sub = p[p.cat == cat]
        if not len(sub):
            continue
        icons, styles, marks = {}, [], []
        for r in sub.itertuples():
            ring = ring_color(r)
            sid = f"{cat}_{r.landing_class if isinstance(r.landing_class, str) else 'x'}"
            if sid not in icons:
                icons[sid] = make_icon(cat, ring)
                styles.append(
                    f'<Style id="{sid}"><IconStyle><scale>0.9</scale><Icon>'
                    f"<href>icons/{sid}.png</href></Icon></IconStyle>"
                    f"<LabelStyle><scale>0.8</scale></LabelStyle></Style>")
            marks.append(
                f"<Placemark><name>{html.escape(display_name(r))}</name>"
                f"<description><![CDATA[{describe(r)}]]></description>"
                f"<styleUrl>#{sid}</styleUrl><Point><coordinates>{r.lon:.6f},{r.lat:.6f}"
                f"</coordinates></Point></Placemark>")
        kml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
               f"<name>SW POI - {html.escape(label)}</name>"
               "<description>Data: USGS GNIS; (c) OpenStreetMap contributors (ODbL), "
               "via Overture Maps. Landing classes: BLM SMA/NLCS, USGS PAD-US. "
               "Advisory planning aid only.</description>" + "".join(styles)
               + "".join(marks) + "</Document></kml>")
        path = layers / f"SW_POI_{label.replace(' & ', '_').replace(' ', '_')}.kmz"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("doc.kml", kml)
            for sid, png in icons.items():
                z.writestr(f"icons/{sid}.png", png)
        files.append(path)
        log(f"  {path.name}: {len(sub)} points")
    return files


def write_waypoints(p):
    nav = PACK / "navdata"
    nav.mkdir(parents=True, exist_ok=True)
    rows = []
    for cat, (label, prefix, *_r) in CATEGORIES.items():
        sub = p[p.cat == cat].sort_values(["name", "lat"], na_position="last")
        for n, r in enumerate(sub.itertuples(), 1):
            ident = f"{prefix}{n:04d}"
            desc = norm_name(display_name(r)).upper()[:25]
            rows.append((ident, desc, f"{r.lat:.6f}", f"{r.lon:.6f}",
                         "" if math.isnan(r.elev_ft) else f"{r.elev_ft:.0f}"))
            p.loc[r.Index, "waypoint"] = ident
    with open(nav / "user_waypoints.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["WAYPOINT_NAME", "Waypoint description", "Latitude", "Longitude",
                    "Elevation"])
        w.writerows(rows)
    log(f"  user_waypoints.csv: {len(rows)} waypoints")


def write_table(p):
    t = p.copy()
    t["name"] = [display_name(r) for r in t.itertuples()]
    t["status"] = [status_of(r)[0] for r in t.itertuples()]
    t["landing_detail"] = [status_of(r)[1] for r in t.itertuples()]
    t["flags"] = t["flags"].map(lambda f: " | ".join(f))
    cols = ["waypoint", "cat", "name", "lat", "lon", "elev_ft", "status", "landing_class",
            "landing_detail", "sma_agency", "blm_nm", "blm_bearing", "blm_lat", "blm_lon",
            "blm_elev_ft", "height_m", "flags", "src", "gnis_id", "osm_id", "wikipedia"]
    t = t[cols].sort_values(["cat", "name"])
    t.round({"blm_nm": 2, "blm_bearing": 0, "elev_ft": 0, "blm_elev_ft": 0,
             "blm_lat": 6, "blm_lon": 6}).to_csv(config.OUTPUT / "SW_POI.csv", index=False)
    g = gpd.GeoDataFrame(t, geometry=gpd.points_from_xy(t.lon, t.lat), crs=4326)
    if (config.WORK / "poi.gpkg").exists():
        (config.WORK / "poi.gpkg").unlink()
    pyogrio.write_dataframe(g, config.WORK / "poi.gpkg")
    stats = {
        "by_category": t.groupby("cat").size().to_dict(),
        "by_status": pd.crosstab(t.cat, t.status).to_dict(orient="index"),
        "by_source": t.groupby("src").size().to_dict(),
        "median_nm_to_landable": t[t.status != "LANDABLE"].groupby("cat").blm_nm
        .median().round(1).to_dict(),
        "within_2nm_of_landable": (t.blm_nm <= 2).groupby(t.cat).mean().round(2).to_dict(),
    }
    (config.WORK / "poi_stats.json").write_text(json.dumps(stats, indent=2, default=str))
    return stats


def preview(p):
    from validate import basemap, label_states, mosaic
    states = pyogrio.read_dataframe(config.NORMALIZED, layer="states").to_crs(3857)
    img, origin, res = mosaic("SW_Landable_BLM", 7, states.total_bounds)
    base = basemap(img.size, origin, res, states)
    for stem in config.PRODUCTS:
        layer, _, _ = mosaic(stem, 7, states.total_bounds)
        a = np.asarray(layer).copy()
        a[..., 3] = (a[..., 3] * 0.5).astype(np.uint8)   # fade the land classes
        base = Image.alpha_composite(base, Image.fromarray(a))
    label_states(base, states, origin, res)
    xy = gpd.GeoSeries(gpd.points_from_xy(p.lon, p.lat), crs=4326).to_crs(3857)
    d = ImageDraw.Draw(base)
    for (x, y), cat in zip(zip(xy.x, xy.y), p.cat):
        px, py = (x - origin[0]) / res, (origin[1] - y) / res
        d.ellipse([px - 4, py - 4, px + 4, py + 4], fill=CATEGORIES[cat][2], outline="white")
    try:
        font = ImageFont.load_default(size=22)
    except TypeError:
        font = ImageFont.load_default()
    x0, y0 = 20, base.height - 40 - 30 * len(CATEGORIES)
    d.rectangle([x0 - 10, y0 - 40, x0 + 420, base.height - 20], fill=(255, 255, 255, 230))
    d.text((x0, y0 - 34), "SW POI (over faded landing classes)", fill="black", font=font)
    for i, (cat, (label, *_r)) in enumerate(CATEGORIES.items()):
        n = int((p.cat == cat).sum())
        d.ellipse([x0, y0 + 30 * i + 4, x0 + 18, y0 + 30 * i + 22], fill=CATEGORIES[cat][2],
                  outline="white")
        d.text((x0 + 30, y0 + 30 * i), f"{label} ({n})", fill="black", font=font)
    base.convert("RGB").save(config.OUTPUT / "preview_POI.png", optimize=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    have_osm = fetch(a.force)
    log("reading GNIS")
    g = read_gnis()
    log("  GNIS:", g.cat.value_counts().to_dict())
    if have_osm:
        log("reading OSM (Geofabrik)")
        o = read_osm()
    else:
        log("reading OSM via Overture Maps (Geofabrik unavailable)")
        o, rel = read_overture((-120.1, 31.2, -101.9, 42.1))
        m = load_manifest()
        m["sources"]["osm"] = {
            "title": "OpenStreetMap data via the Overture Maps base theme "
                     "(water / land / infrastructure)",
            "used_for": "POIs: waterfalls, hot springs, volcanic, glaciers, viewpoints",
            "url": f"https://overturemaps-us-west-2.s3.amazonaws.com/release/{rel}/theme=base/",
            "version": f"Overture release {rel}",
            "license": "ODbL 1.0 - (c) OpenStreetMap contributors",
            "downloaded": mtime_utc(config.RAW / "osm" / f"overture_{rel}.pkl"),
        }
        save_manifest(m)
        have_osm = len(o) > 0
    log("  OSM (kept):", o.cat.value_counts().to_dict() if len(o) else {})
    p = merge(g, o)
    p = annotate(p)
    p["waypoint"] = None
    write_waypoints(p)
    write_kmz(p)
    stats = write_table(p)
    preview(p)
    stats["osm_used"] = have_osm
    (config.WORK / "poi_stats.json").write_text(json.dumps(stats, indent=2, default=str))
    log(json.dumps(stats, indent=1, default=str))


if __name__ == "__main__":
    main()
