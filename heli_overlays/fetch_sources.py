"""Download every source dataset and record provenance in work/manifest.json.

Sources
  tnm    USGS National Boundary Dataset state GeoPackages (TIGER/Line 2025
         state outlines; also the PAD-US 4.1 federal subset used by --mode tnm).
         prd-tnm.s3.amazonaws.com
  padus  USGS PAD-US 4.1 state geodatabases (Fee + Designation).
         ScienceBase item 6759abcfd34edfeb8710a004 (PAD-US 4.1 State Downloads).
  blm    BLM Geospatial Business Platform hub (gbp-blm-egis.hub.arcgis.com):
         National SMA, NLCS Wilderness Study Areas, NLCS Wilderness Areas,
         NLCS National Monuments / NCAs. Items are looked up by title through
         the ArcGIS Online search API (hub items live in the BLM-EGIS org),
         then queried from their feature service for the five-state envelope.

Usage: python fetch_sources.py [--only tnm,padus,blm] [--force]
       python fetch_sources.py --only blm --url sma=<FeatureServer/0 url> ...
"""
import argparse
import datetime as dt
import json
import re
import sys
import time
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
import requests

import config

UA = {"User-Agent": "heli-overlays/1.0 (data pipeline)"}
TNM_S3 = "https://prd-tnm.s3.amazonaws.com/StagedProducts/GovtUnit/GPKG"
PADUS_ITEM = "6759abcfd34edfeb8710a004"       # PAD-US 4.1 State Downloads
SB = "https://www.sciencebase.gov/catalog/item"
AGOL = "https://www.arcgis.com/sharing/rest"
BLM_ORG_KEY = "blm-egis"                       # blm-egis.maps.arcgis.com
BLM_ITEMS = {
    # key: exact hub title (verified against data.gov harvest of the hub)
    "sma": ["BLM Natl Surface Management Agency Area Polygons",
            "BLM Natl SMA Surface Management Agency Area Polygons",
            "Surface Management Agency"],
    "nlcs_wsa": ["BLM Natl NLCS Wilderness Study Areas Polygons"],
    "nlcs_wilderness": ["BLM Natl NLCS Wilderness Areas Polygons"],
    "nlcs_nm_nca": ["BLM Natl NLCS National Monuments National Conservation Areas Polygons"],
}
# Lon/lat envelope of the five states, padded.
ENVELOPE = (-120.1, 31.2, -101.9, 42.1)

S = requests.Session()
S.headers.update(UA)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ms_to_date(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d") \
        if ms else None


def get(url, retries=4, **kw):
    for i in range(retries):
        try:
            r = S.get(url, timeout=kw.pop("timeout", 120), **kw)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if i == retries - 1:
                raise
            log(f"  retry {i + 1} {url[:90]}: {e}")
            time.sleep(2 ** (i + 1))


def download(url, dest, force=False):
    dest = Path(dest)
    if dest.exists() and not force:
        log(f"  have {dest.name}")
        return dest
    log(f"  GET {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with S.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    tmp.rename(dest)
    return dest


def load_manifest():
    if config.MANIFEST.exists():
        return json.loads(config.MANIFEST.read_text())
    return {"sources": {}}


def save_manifest(m):
    config.MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    config.MANIFEST.write_text(json.dumps(m, indent=2))


# ------------------------------------------------------------------------- TNM
def fetch_tnm(m, force):
    files, pubdates = [], {}
    for st, (name, _) in config.STATES.items():
        stem = f"GOVTUNIT_{name.replace(' ', '_')}_State_GPKG"
        z = download(f"{TNM_S3}/{stem}.zip", config.RAW / f"{stem}.zip", force)
        xml = download(f"{TNM_S3}/{stem}.xml", config.RAW / f"{stem}.xml", force)
        out = config.RAW / f"tnm_{name.replace(' ', '_')}"
        if force or not list(out.glob("*.gpkg")):
            with zipfile.ZipFile(z) as zf:
                zf.extractall(out)
        t = xml.read_text(errors="ignore")
        pub = re.search(r"\(published (\d{8})\)", t)
        pubdates[st] = pub.group(1) if pub else None
        files.append(str(z.relative_to(config.WORK)))
    m["sources"]["tnm"] = {
        "title": "USGS National Boundary Dataset (NBD) state GeoPackages",
        "used_for": "state outlines (GU_StateOrTerritory = TIGER/Line 2025)",
        "url": TNM_S3 + "/GOVTUNIT_<State>_State_GPKG.zip",
        "version": "NBD published " + ", ".join(sorted(set(v for v in pubdates.values() if v))),
        "component_versions": "TIGER/Line 2025 (pub 2025-06-01); PAD-US 4.1 (pub 2025-03-31)",
        "downloaded": now(),
        "files": files,
    }


# ---------------------------------------------------------------------- PAD-US
def fetch_padus(m, force):
    item = get(f"{SB}/{PADUS_ITEM}?format=json").json()
    title = item.get("title")
    files = item.get("files", []) or []
    for ex in item.get("distributionLinks", []) or []:
        files.append(ex)
    log(f"  ScienceBase: {title}; {len(files)} files")
    dates = {d.get("type"): d.get("dateString") for d in item.get("dates", [])}
    got = []
    dest_dir = config.RAW / "padus"
    dest_dir.mkdir(parents=True, exist_ok=True)
    for st in config.STATES:
        pat = re.compile(rf"State_?{st}[_.]", re.I)
        cand = [f for f in files if pat.search(f.get("name", "")) and
                f.get("name", "").lower().endswith(".zip")]
        if not cand:
            sys.exit(f"PAD-US: no state file for {st} in {[f.get('name') for f in files]}")
        f = cand[0]
        url = f.get("downloadUri") or f.get("url")
        z = download(url, dest_dir / f["name"], force)
        if force or not list(dest_dir.glob(f"**/*{st}*.gdb")):
            with zipfile.ZipFile(z) as zf:
                zf.extractall(dest_dir / z.stem)
        got.append({"state": st, "name": f["name"], "url": url, "size": f.get("size")})
    m["sources"]["padus"] = {
        "title": title,
        "used_for": "Wilderness, all agencies (Designation, Des_Tp='WA'); non-BLM WSAs",
        "url": f"https://www.sciencebase.gov/catalog/item/{PADUS_ITEM}",
        "doi": "https://doi.org/10.5066/P96WBCHS",
        "version": "PAD-US 4.1",
        "dates": dates,
        "last_updated": (item.get("provenance") or {}).get("lastUpdated"),
        "downloaded": now(),
        "files": got,
    }


# ------------------------------------------------------------------------- BLM
def blm_org_id():
    j = get(f"https://{BLM_ORG_KEY}.maps.arcgis.com/sharing/rest/portals/self?f=json").json()
    return j["id"]


def find_item(titles, orgid):
    """Return the portal item whose title matches best."""
    for t in titles:
        q = f'title:"{t}" AND orgid:{orgid}'
        j = get(f"{AGOL}/search", params={"q": q, "f": "json", "num": 50}).json()
        res = [r for r in j.get("results", []) if r.get("url")]
        exact = [r for r in res if r["title"].strip().lower() == t.lower()]
        pool = exact or res
        fs = [r for r in pool if r.get("type") == "Feature Service"] or pool
        if fs:
            fs.sort(key=lambda r: r.get("modified", 0), reverse=True)
            return fs[0], [(r["title"], r.get("type"), r.get("url")) for r in res[:10]]
    return None, []


def layer_url(service_url):
    service_url = service_url.rstrip("/")
    if re.search(r"/(FeatureServer|MapServer)/\d+$", service_url):
        return service_url
    j = get(service_url, params={"f": "json"}).json()
    layers = [l for l in j.get("layers", []) if "Polygon" in (l.get("geometryType") or "")
              or l.get("geometryType") is None]
    lid = layers[0]["id"] if layers else 0
    return f"{service_url}/{lid}"


def query_layer(url, dest):
    """Pull all features intersecting ENVELOPE from an ArcGIS REST layer."""
    info = get(url, params={"f": "json"}).json()
    oid = info.get("objectIdField") or next(
        (f["name"] for f in info.get("fields", []) if f["type"] == "esriFieldTypeOID"), "OBJECTID")
    maxrc = int(info.get("maxRecordCount") or 1000)
    common = {
        "geometry": ",".join(map(str, ENVELOPE)), "geometryType": "esriGeometryEnvelope",
        "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "f": "json",
    }
    ids = S.post(f"{url}/query", data={**common, "where": "1=1", "returnIdsOnly": "true"},
                 timeout=300).json()
    if "error" in ids:
        raise RuntimeError(f"{url}: {ids['error']}")
    ids = sorted(ids.get("objectIds") or [])
    log(f"  {len(ids):,} features in envelope ({info.get('name')})")
    chunk = max(25, min(200, maxrc))
    frames = []
    for i in range(0, len(ids), chunk):
        part = ids[i:i + chunk]
        for attempt in range(5):
            try:
                r = S.post(f"{url}/query", data={
                    "objectIds": ",".join(map(str, part)), "outFields": "*",
                    "returnGeometry": "true", "outSR": 4326, "f": "geojson"}, timeout=600)
                r.raise_for_status()
                j = r.json()
                if "error" in j:
                    raise RuntimeError(j["error"])
                break
            except Exception as e:  # noqa: BLE001 - retry anything transient
                if attempt == 4:
                    raise
                log(f"  retry chunk {i}: {e}")
                time.sleep(2 ** (attempt + 1))
        if j.get("features"):
            frames.append(gpd.GeoDataFrame.from_features(j["features"], crs=4326))
        if (i // chunk) % 20 == 0:
            log(f"    {min(i + chunk, len(ids)):,}/{len(ids):,}")
    gdf = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=4326) if frames else \
        gpd.GeoDataFrame(geometry=[], crs=4326)
    if dest.exists():
        dest.unlink()
    pyogrio.write_dataframe(gdf, dest, driver="GPKG", promote_to_multi=True)
    edit = info.get("editingInfo") or {}
    return {"features": len(gdf), "layer_name": info.get("name"),
            "data_last_edit": ms_to_date(edit.get("dataLastEditDate") or edit.get("lastEditDate")),
            "description_head": (info.get("description") or "")[:300]}


def fetch_blm(m, force, overrides):
    dest_dir = config.RAW / "blm"
    dest_dir.mkdir(parents=True, exist_ok=True)
    orgid = None
    out = m["sources"].setdefault("blm", {})
    for key, titles in BLM_ITEMS.items():
        dest = dest_dir / f"{key}.gpkg"
        if dest.exists() and not force and key in out:
            log(f"  have {dest.name}")
            continue
        rec = {"downloaded": now()}
        if key in overrides:
            url = overrides[key]
            rec.update(title=f"(manual URL) {key}", service=url)
        else:
            orgid = orgid or blm_org_id()
            item, cands = find_item(titles, orgid)
            if item is None:
                log(f"  {key}: no hub item found for {titles}")
                if key == "nlcs_wilderness":   # optional; PAD-US covers wilderness
                    continue
                sys.exit(f"cannot locate BLM item for {key}; pass --url {key}=<layer url>")
            log(f"  {key}: '{item['title']}' ({item['id']}) -> {item['url']}")
            url = item["url"]
            rec.update(title=item["title"], item_id=item["id"],
                       hub_page=f"https://gbp-blm-egis.hub.arcgis.com/datasets/{item['id']}",
                       service=url, item_modified=ms_to_date(item.get("modified")),
                       candidates=cands)
        lurl = layer_url(url)
        rec["layer_url"] = lurl
        rec.update(query_layer(lurl, dest))
        rec["file"] = str(dest.relative_to(config.WORK))
        out[key] = rec
        save_manifest(m)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="tnm,padus,blm")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--url", action="append", default=[],
                    help="override a BLM layer: key=https://.../FeatureServer/0")
    a = ap.parse_args()
    overrides = dict(u.split("=", 1) for u in a.url)
    config.RAW.mkdir(parents=True, exist_ok=True)
    m = load_manifest()
    for src in a.only.split(","):
        log(f"== {src}")
        {"tnm": fetch_tnm, "padus": fetch_padus,
         "blm": lambda mm, f: fetch_blm(mm, f, overrides)}[src](m, a.force)
        save_manifest(m)
    log(f"manifest -> {config.MANIFEST}")


if __name__ == "__main__":
    main()
