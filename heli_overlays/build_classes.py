"""Build non-overlapping landing classes a-g in EPSG:3857.

Reads work/normalized.gpkg, reprojects every theme to EPSG:3857, clips to the
union of the five states and resolves overlaps by priority (a wins over b, ...).
Work is split over a grid of square cells so the overlay stays fast and each
GEOS operation stays small; adjacent same-class pieces are not dissolved across
cell edges, which is invisible once rasterized (outlines are drawn from the
class raster, not from polygon edges).

Output: work/classes_3857.gpkg, layer "classes" (cls, geometry), plus
work/class_stats.json with areas in km^2.
"""
import json
import math
import multiprocessing as mp
import time

import geopandas as gpd
import numpy as np
import pyogrio
import shapely
from shapely import box

import config

CELL = 50_000  # m (Web Mercator)
GRID = 0.01    # snap-rounding grid for overlays, m

G = {}  # filled before fork: region, trees, geoms


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def polys_only(geoms):
    """make_valid, explode to Polygon parts, drop non-areal debris."""
    geoms = shapely.make_valid(geoms)
    parts = shapely.get_parts(geoms)
    # GeometryCollections from make_valid can contain lines/points.
    out = []
    for g in parts:
        t = shapely.get_type_id(g)
        if t == 3:
            out.append(g)
        elif t in (6, 7):
            out.extend(p for p in shapely.get_parts(g) if shapely.get_type_id(p) == 3)
    out = np.array(out, dtype=object)
    return out[shapely.area(out) > 0] if len(out) else out


def areal(g):
    """Keep only the polygonal part of an overlay result (drops slivers that
    collapsed to lines/points under snap rounding)."""
    if g is None or g.is_empty:
        return shapely.MultiPolygon()
    t = shapely.get_type_id(g)
    if t in (3, 6):
        return g
    if t == 7:
        ps = [p for p in shapely.get_parts(g) if shapely.get_type_id(p) in (3, 6)]
        return shapely.union_all(ps) if ps else shapely.MultiPolygon()
    return shapely.MultiPolygon()


def load_theme(layer, region_bounds):
    df = pyogrio.read_dataframe(config.NORMALIZED, layer=layer)
    if len(df) == 0:
        return np.array([], dtype=object)
    df = df.to_crs(config.WEB_MERCATOR)
    geoms = polys_only(df.geometry.values)
    b = box(*region_bounds)
    return geoms[shapely.intersects(geoms, b)]


def cell_work(cell):
    x0, y0 = cell
    cb = box(x0, y0, x0 + CELL, y0 + CELL)
    region = areal(shapely.intersection(G["region"], cb, grid_size=GRID))
    if region.is_empty:
        return []
    taken = None
    out = []
    for key, theme, *_ in config.CLASSES_DEF:
        tree, geoms = G["trees"][key], G["geoms"][key]
        if tree is None:
            continue
        idx = tree.query(cb, predicate="intersects")
        if len(idx) == 0:
            continue
        clipped = shapely.clip_by_rect(geoms[idx], *cb.bounds)
        clipped = clipped[~shapely.is_empty(clipped)]
        if len(clipped) == 0:
            continue
        clipped = [areal(g) for g in shapely.make_valid(clipped)]
        u = areal(shapely.union_all(clipped, grid_size=GRID))
        piece = areal(shapely.intersection(u, region, grid_size=GRID))
        if taken is not None and not piece.is_empty:
            piece = areal(shapely.difference(piece, taken, grid_size=GRID))
        if piece.is_empty:
            continue
        taken = piece if taken is None else areal(
            shapely.union(taken, piece, grid_size=GRID))
        for p in polys_only(np.array([piece], dtype=object)):
            if p.area > 1.0:
                out.append((key, shapely.to_wkb(p)))
    return out


def main():
    t0 = time.time()
    states = pyogrio.read_dataframe(config.NORMALIZED, layer="states").to_crs(
        config.WEB_MERCATOR)
    log("states:", sorted(states.name))
    region = areal(shapely.union_all(shapely.make_valid(states.geometry.values),
                                     grid_size=GRID))
    G["region"] = region
    rb = region.bounds
    G["trees"], G["geoms"] = {}, {}
    for key, theme, label, *_ in config.CLASSES_DEF:
        geoms = load_theme(theme, rb)
        G["geoms"][key] = geoms
        G["trees"][key] = shapely.STRtree(geoms) if len(geoms) else None
        log(f"class {key} <- {theme:12s} {len(geoms):7d} polygons, "
            f"{int(shapely.get_num_coordinates(geoms).sum()) if len(geoms) else 0:,} vertices")

    xs = np.arange(math.floor(rb[0] / CELL) * CELL, rb[2], CELL)
    ys = np.arange(math.floor(rb[1] / CELL) * CELL, rb[3], CELL)
    cells = [(x, y) for x in xs for y in ys]
    cell_boxes = shapely.box([c[0] for c in cells], [c[1] for c in cells],
                             [c[0] + CELL for c in cells], [c[1] + CELL for c in cells])
    keep = shapely.intersects(cell_boxes, region)
    cells = [c for c, k in zip(cells, keep) if k]
    log(f"{len(cells)} grid cells of {CELL/1000:.0f} km")

    rows = []
    with mp.get_context("fork").Pool(mp.cpu_count()) as pool:
        for i, res in enumerate(pool.imap_unordered(cell_work, cells, chunksize=2)):
            rows.extend(res)
            if (i + 1) % 100 == 0:
                log(f"  {i + 1}/{len(cells)} cells")
    log(f"{len(rows)} class polygons")

    gdf = gpd.GeoDataFrame(
        {"cls": [r[0] for r in rows]},
        geometry=shapely.from_wkb([r[1] for r in rows]), crs=config.WEB_MERCATOR)
    if config.CLASSES.exists():
        config.CLASSES.unlink()
    pyogrio.write_dataframe(gdf, config.CLASSES, layer="classes", driver="GPKG",
                            promote_to_multi=True)

    # True-area approximation: Mercator area scaled by cos^2(latitude).
    cy = shapely.get_y(shapely.centroid(gdf.geometry.values))
    lat = np.degrees(2 * np.arctan(np.exp(cy / 6378137.0)) - np.pi / 2)
    km2 = shapely.area(gdf.geometry.values) * np.cos(np.radians(lat)) ** 2 / 1e6
    gdf["km2"] = km2
    stats = {k: round(float(gdf.loc[gdf.cls == k, "km2"].sum()), 1) for k in config.CLASS_KEYS}
    stats["region_km2"] = round(float(
        shapely.area(region) * math.cos(math.radians(36.5)) ** 2 / 1e6), 0)
    config.CLASS_STATS.write_text(json.dumps(stats, indent=2))
    for key, theme, label, *_ in config.CLASSES_DEF:
        log(f"  {key} {label:34s} {stats[key]:>12,.1f} km2")
    log(f"done in {time.time() - t0:.0f}s -> {config.CLASSES}")


if __name__ == "__main__":
    main()
