"""Previews and spot checks for the built MBTiles.

  * output/preview_<product>.png: low-res mosaic of the product's own tiles
    over a state-outline basemap; preview_all.png stacks all three.
  * Spot checks: for each named area, the share of its area in each class
    (vector, from classes_3857.gpkg) and the class read back from the z13 tile
    pixel at a point inside it (raster, from the MBTiles).
  * Overlap check: total pairwise intersection area between classes.
Results go to work/validation.json (README.txt is written from it).
"""
import io
import json
import sqlite3
import sys

import numpy as np
import pyogrio
import shapely
from PIL import Image, ImageDraw, ImageFont

import config
from make_mbtiles import ORIGIN, T, hex_rgb

SPOT_CHECKS = [
    # label, theme layer to find it in, name regex, state bbox hint, expected class
    ("Labyrinth Canyon Wilderness (UT)", "wilderness", r"Labyrinth Canyon", "UT", "a"),
    ("Muddy Creek Wilderness (UT)", "wilderness", r"Muddy Creek", "UT", "a"),
    ("Dirty Devil WSA (UT)", "wsa", r"Dirty Devil", "UT", "b"),
    ("Organ Mountains-Desert Peaks NM (NM)", "nm_nca", r"Organ Mountains", "NM", "d"),
    ("Navajo Nation (AZ/NM)", "tribal", r"Navajo Nation", None, "c"),
]
# Fallback geometry for spot checks whose theme is empty (e.g. WSA in tnm mode):
# a point well inside the named area, lon/lat.
FALLBACK_POINTS = {
    "Dirty Devil WSA (UT)": (-110.42, 38.17),
}


def lonlat_to_m(lon, lat):
    x = np.radians(lon) * 6378137.0
    y = np.log(np.tan(np.pi / 4 + np.radians(lat) / 2)) * 6378137.0
    return x, y


def open_db(stem):
    p = config.OUTPUT / f"{stem}.mbtiles"
    if not p.exists():
        sys.exit(f"missing {p}")
    return sqlite3.connect(p)


def get_tile(db, z, x, y):
    r = db.execute("SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? "
                   "AND tile_row=?", (z, x, (1 << z) - 1 - y)).fetchone()
    return Image.open(io.BytesIO(r[0])).convert("RGBA") if r else None


def maxzoom(db):
    return int(db.execute("SELECT MAX(zoom_level) FROM map").fetchone()[0])


# ------------------------------------------------------------------ previews
def mosaic(stem, z, bounds_m):
    db = open_db(stem)
    s = 2 * ORIGIN / (1 << z)
    x0, y0, x1, y1 = bounds_m
    tx0, tx1 = int((x0 + ORIGIN) // s), int((x1 + ORIGIN) // s)
    ty0, ty1 = int((ORIGIN - y1) // s), int((ORIGIN - y0) // s)
    img = Image.new("RGBA", ((tx1 - tx0 + 1) * T, (ty1 - ty0 + 1) * T), (0, 0, 0, 0))
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            t = get_tile(db, z, tx, ty)
            if t:
                img.paste(t, ((tx - tx0) * T, (ty - ty0) * T))
    origin = (-ORIGIN + tx0 * s, ORIGIN - ty0 * s)
    return img, origin, s / T


def basemap(size, origin, res, states):
    base = Image.new("RGBA", size, (246, 244, 239, 255))
    d = ImageDraw.Draw(base)
    for g in states.geometry.values:
        for poly in shapely.get_parts(g):
            for ring in [poly.exterior, *poly.interiors]:
                xy = np.asarray(ring.coords)
                px = [((x - origin[0]) / res, (origin[1] - y) / res) for x, y in xy]
                d.line(px, fill=(60, 60, 60, 255), width=2)
    return base


def legend(img, classes, title):
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=22)
    except TypeError:
        font = ImageFont.load_default()
    info = {c[0]: c for c in config.CLASSES_DEF}
    x, y = 20, img.height - 40 - 32 * len(classes)
    d.rectangle([x - 10, y - 44, x + 520, img.height - 20], fill=(255, 255, 255, 220))
    d.text((x, y - 36), title, fill=(0, 0, 0, 255), font=font)
    for k in classes:
        r, g, b = hex_rgb(info[k][3])
        d.rectangle([x, y + 4, x + 26, y + 26], fill=(r, g, b, round(info[k][4] * 255) + 60),
                    outline=(round(r * .7), round(g * .7), round(b * .7), 255), width=2)
        d.text((x + 36, y + 2), f"{k}. {info[k][2]}", fill=(0, 0, 0, 255), font=font)
        y += 32


def label_states(img, states, origin, res):
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=30)
    except TypeError:
        font = ImageFont.load_default()
    abbr = {v[0]: k for k, v in config.STATES.items()}
    for name, g in zip(states.name, states.geometry.values):
        p = g.point_on_surface()
        d.text(((p.x - origin[0]) / res, (origin[1] - p.y) / res), abbr.get(name, name),
               fill=(30, 30, 30, 200), font=font, anchor="mm")


def previews(z=7):
    states = pyogrio.read_dataframe(config.NORMALIZED, layer="states").to_crs(
        config.WEB_MERCATOR)
    bounds_m = states.total_bounds
    layers = {}
    for stem in config.PRODUCTS:
        layers[stem] = mosaic(stem, z, bounds_m)
    img0, origin, res = layers[next(iter(config.PRODUCTS))]
    base = basemap(img0.size, origin, res, states)
    outs = []
    for stem, (img, _, _) in layers.items():
        out = Image.alpha_composite(base, img)
        label_states(out, states, origin, res)
        legend(out, config.PRODUCTS[stem][0], f"{stem}  (z{z} tiles)")
        p = config.OUTPUT / f"preview_{stem}.png"
        out.convert("RGB").save(p, optimize=True)
        outs.append(p)
    allimg = base.copy()
    for img, _, _ in layers.values():
        allimg = Image.alpha_composite(allimg, img)
    label_states(allimg, states, origin, res)
    legend(allimg, config.CLASS_KEYS, f"All classes (z{z} tiles)")
    p = config.OUTPUT / "preview_all.png"
    allimg.convert("RGB").save(p, optimize=True)
    outs.append(p)
    return [str(p.name) for p in outs]


# --------------------------------------------------------------- spot checks
def class_lookup():
    df = pyogrio.read_dataframe(config.CLASSES, layer="classes")
    geoms = shapely.get_parts(df.geometry.values)
    cls = np.repeat(df.cls.values, shapely.get_num_geometries(df.geometry.values))
    return geoms, cls, shapely.STRtree(geoms)


def raster_class(px, py):
    """Class at a Web Mercator point, read back from the highest-zoom tiles."""
    info = {c[0]: c for c in config.CLASSES_DEF}
    found = []
    for stem, (classes, *_r) in config.PRODUCTS.items():
        db = open_db(stem)
        z = maxzoom(db)
        s = 2 * ORIGIN / (1 << z)
        fx, fy = (px + ORIGIN) / s, (ORIGIN - py) / s
        t = get_tile(db, z, int(fx), int(fy))
        if t is None:
            continue
        rgba = t.getpixel((int((fx % 1) * T), int((fy % 1) * T)))
        if rgba[3] == 0:
            continue
        for k in classes:
            r, g, b = hex_rgb(info[k][3])
            d = config.OUTLINE_DARKEN
            if rgba[:3] in [(r, g, b), (round(r * d), round(g * d), round(b * d))]:
                found.append((stem, k, z))
    return found


def spot_checks():
    geoms, cls, tree = class_lookup()
    states = pyogrio.read_dataframe(config.NORMALIZED, layer="states").to_crs(
        config.WEB_MERCATOR)
    abbr = {v[0]: k for k, v in config.STATES.items()}
    state_geom = {abbr[n]: g for n, g in zip(states.name, states.geometry.values)}
    results = []
    for label, theme, rx, st, expect in SPOT_CHECKS:
        src = pyogrio.read_dataframe(config.NORMALIZED, layer=theme).to_crs(
            config.WEB_MERCATOR)
        hit = src[src.name.str.contains(rx, case=False, regex=True)] if len(src) else src
        if st and len(hit):
            hit = hit[shapely.intersects(hit.geometry.values, state_geom[st])]
        rec = {"check": label, "expected": expect, "source_theme": theme}
        if len(hit) == 0:
            rec["found_in_source"] = False
            if label in FALLBACK_POINTS:
                feat = shapely.Point(*lonlat_to_m(*FALLBACK_POINTS[label]))
                rec["note"] = (f"not present in '{theme}' source layer; checked at "
                               f"fallback point {FALLBACK_POINTS[label]}")
            else:
                rec["result"] = "FAIL (not found in source)"
                results.append(rec)
                continue
        else:
            rec["found_in_source"] = True
            rec["source_names"] = sorted(set(hit.name))[:5]
            feat = shapely.union_all(shapely.make_valid(hit.geometry.values))
            if st:
                feat = shapely.intersection(feat, state_geom[st])
        shares = {}
        if feat.area > 0:
            idx = tree.query(feat, predicate="intersects")
            for k in config.CLASS_KEYS:
                sel = idx[cls[idx] == k]
                if len(sel):
                    a = shapely.area(shapely.intersection(geoms[sel], feat)).sum()
                    if a > 0:
                        shares[k] = round(float(a / feat.area), 4)
            rec["area_share_by_class"] = shares
            # Probe a point inside the part that landed in the expected class,
            # or anywhere in the feature if none did.
            sel = idx[cls[idx] == expect]
            part = shapely.intersection(shapely.union_all(geoms[sel]), feat) if len(sel) else feat
            probe = (part if not part.is_empty else feat).point_on_surface()
        else:
            probe = feat
            idx = tree.query(feat, predicate="intersects")
            rec["area_share_by_class"] = {k: 1.0 for k in set(cls[idx])}
        lon = np.degrees(probe.x / 6378137.0)
        lat = np.degrees(2 * np.arctan(np.exp(probe.y / 6378137.0)) - np.pi / 2)
        rec["probe_lonlat"] = [round(float(lon), 5), round(float(lat), 5)]
        rec["tile_pixel"] = [f"{s}:{k} (z{z})" for s, k, z in raster_class(probe.x, probe.y)]
        shares = rec["area_share_by_class"]
        top = max(shares, key=shares.get) if shares else None
        raster_ok = any(f":{expect} " in t for t in rec["tile_pixel"])
        rec["majority_class"] = top
        rec["result"] = "PASS" if top == expect and raster_ok else "FAIL"
        results.append(rec)
    return results


# Wilderness designated by the 2019 Dingell Act (P.L. 116-9): Emery County, UT
# (mostly former WSAs) and Dona Ana / San Juan / Taos counties, NM.
DINGELL_2019 = {
    "UT": ["Big Wild Horse Mesa", "Cold Wash", "Desolation Canyon", "Eagle Canyon",
           "Horse Valley", "Labyrinth Canyon", "Little Ocean Draw",
           "Little Wild Horse Canyon", "Lower Last Chance", "Middle Wild Horse Mesa",
           "Mexican Mountain", "Muddy Creek", "Nelson Mountain", "Red.?s Canyon",
           "San Rafael Reef", "Sid.?s Mountain", "Turtle Canyon"],
    "NM": ["Ah-shi-sle-pah", "Aden Lava Flow", "Broad Canyon", "Cinder Cone",
           "East Potrillo", "Mount Riley", "Organ Mountains", "Potrillo Mountains",
           "Robledo Mountains", "Sierra de las Uvas", "Cerro del Yuta",
           "R.o San Antonio"],
}


def currency_checks():
    """Are the 2019 wildernesses present, and do WSA polygons still sit on
    land that is now wilderness (a stale WSA layer)?"""
    wild = pyogrio.read_dataframe(config.NORMALIZED, layer="wilderness").to_crs(
        config.WEB_MERCATOR)
    names = wild.name.fillna("")
    states = pyogrio.read_dataframe(config.NORMALIZED, layer="states").to_crs(
        config.WEB_MERCATOR)
    abbr = {v[0]: k for k, v in config.STATES.items()}
    in_state = {abbr[n]: shapely.intersects(wild.geometry.values, g)
                for n, g in zip(states.name, states.geometry.values)}
    out = {"dingell_2019_wilderness_present": {}, "dingell_2019_wilderness_missing": []}
    for st, lst in DINGELL_2019.items():
        for n in lst:
            m = names.str.contains(n, case=False, regex=True) & in_state[st]
            label = f"{n.replace('.?', chr(39)).replace('R.o', 'Rio')} ({st})"
            if m.any():
                km2 = float(shapely.area(wild.geometry.values[m.values]).sum()
                            * np.cos(np.radians(38)) ** 2 / 1e6)
                out["dingell_2019_wilderness_present"][label] = round(km2, 1)
            else:
                out["dingell_2019_wilderness_missing"].append(label)
    wsa = pyogrio.read_dataframe(config.NORMALIZED, layer="wsa").to_crs(config.WEB_MERCATOR)
    stale = []
    if len(wsa):
        wg = shapely.make_valid(wild.geometry.values)
        tree = shapely.STRtree(wg)
        for name, g in zip(wsa.name, shapely.make_valid(wsa.geometry.values)):
            idx = tree.query(g, predicate="intersects")
            if not len(idx) or g.area == 0:
                continue
            frac = shapely.intersection(g, shapely.union_all(wg[idx])).area / g.area
            if frac > 0.05:
                stale.append({"wsa": name, "share_now_wilderness": round(float(frac), 3)})
        out["wsa_features"] = len(wsa)
    out["wsa_overlapping_wilderness"] = sorted(stale, key=lambda r: -r["share_now_wilderness"])
    return out


def overlap_check():
    geoms, cls, tree = class_lookup()
    a, b = tree.query(geoms, predicate="intersects")
    m = (a < b) & (cls[a] != cls[b])
    inter = shapely.area(shapely.intersection(geoms[a[m]], geoms[b[m]])).sum() if m.any() else 0.0
    return {"pairs_touching": int(m.sum()), "overlap_area_m2_mercator": round(float(inter), 3)}


def main():
    out = {"previews": previews(), "spot_checks": spot_checks(), "overlap": overlap_check(),
           "currency": currency_checks()}
    for r in out["spot_checks"]:
        print(f"{r['result']:5s} {r['check']:40s} expect {r['expected']}  "
              f"shares={r.get('area_share_by_class')}  pixel={r.get('tile_pixel')}"
              + (f"  [{r['note']}]" if r.get("note") else ""))
    print("overlap:", out["overlap"])
    cur = out["currency"]
    print(f"2019 wilderness present: {len(cur['dingell_2019_wilderness_present'])}, "
          f"missing: {cur['dingell_2019_wilderness_missing']}")
    print("WSA polygons overlapping wilderness >5%:", cur["wsa_overlapping_wilderness"])
    (config.WORK / "validation.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
