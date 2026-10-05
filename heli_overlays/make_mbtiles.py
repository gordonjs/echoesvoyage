"""Render the class polygons into raster PNG MBTiles for ForeFlight.

One pass renders a class-ID raster per tile (all classes a-g) and derives the
three products from it, so every product sees the same pixels. Each tile is
rasterized with a 1 px buffer; a pixel is outline if any 4-neighbour holds a
different class (or nothing), which gives a 1 px outline drawn just inside the
class edge, continuous across tile seams and absent along the internal cuts
build_classes.py made.

Tiles are 256 px palette PNGs (transparent + fill + outline per class, alpha via
tRNS). Empty tiles are skipped, identical tiles stored once (map/images tables
with a `tiles` view, the TileMill/mbutil layout).

Work is a quadtree: each z8 root clips the geometry to its box once and hands
each child the part inside the child's box, so per-tile cost tracks local
vertex count. Areas wholly inside one class are not rendered at all below the
first full tile; their descendants all point at one shared image.

Usage:
  python make_mbtiles.py --zooms 6-12 --fresh   # build z6-12
  python make_mbtiles.py --zooms 13-13          # add z13 to the same files
"""
import argparse
import hashlib
import io
import multiprocessing as mp
import sqlite3
import time

import mercantile
import numpy as np
import pyogrio
import shapely
from affine import Affine
from PIL import Image
from rasterio.features import rasterize

import config

ORIGIN = 20037508.342789244
ROOT_Z = 8
MARGIN_PX = 2
T = config.TILE_SIZE
N = T + 2  # rendered raster incl. 1 px buffer

G = {}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def tile_bounds(z, x, y):
    s = 2 * ORIGIN / (1 << z)
    x0 = -ORIGIN + x * s
    y1 = ORIGIN - y * s
    return x0, y1 - s, x0 + s, y1


def margin_box(z, x, y):
    x0, y0, x1, y1 = tile_bounds(z, x, y)
    m = MARGIN_PX * (x1 - x0) / T
    return x0 - m, y0 - m, x1 + m, y1 + m


# ------------------------------------------------------------------ palettes
def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def product_palettes():
    """Per product: (palette rgb list, alpha bytes, fill LUT, edge LUT)."""
    info = {c[0]: c for c in config.CLASSES_DEF}
    out = {}
    for stem, (classes, *_rest) in config.PRODUCTS.items():
        rgb = [hex_rgb(info[classes[0]][3])]   # index 0: transparent
        alpha = [0]
        fill = np.zeros(len(config.CLASS_KEYS) + 1, np.uint8)
        edge = np.zeros_like(fill)
        for k in classes:
            r, g, b = hex_rgb(info[k][3])
            rgb.append((r, g, b))
            alpha.append(round(info[k][4] * 255))
            fill[config.CLASS_ID[k]] = len(rgb) - 1
            d = config.OUTLINE_DARKEN
            rgb.append((round(r * d), round(g * d), round(b * d)))
            alpha.append(round(config.OUTLINE_OPACITY * 255))
            edge[config.CLASS_ID[k]] = len(rgb) - 1
        out[stem] = ([v for c in rgb for v in c], bytes(alpha), fill, edge)
    return out


def encode(idx, pal):
    im = Image.frombytes("P", (T, T), np.ascontiguousarray(idx, np.uint8).tobytes())
    im.putpalette(pal[0])
    buf = io.BytesIO()
    im.save(buf, "PNG", transparency=pal[1], compress_level=9)
    return buf.getvalue()


# ----------------------------------------------------------------- rendering
def render(z, x, y, cids, geoms):
    x0, y0, x1, y1 = tile_bounds(z, x, y)
    res = (x1 - x0) / T
    tr = Affine(res, 0, x0 - res, 0, -res, y1 + res)
    arr = rasterize(zip(geoms, cids.tolist()), out_shape=(N, N), transform=tr,
                    fill=0, dtype="uint8")
    c = arr[1:-1, 1:-1]
    edge = ((c != arr[:-2, 1:-1]) | (c != arr[2:, 1:-1])
            | (c != arr[1:-1, :-2]) | (c != arr[1:-1, 2:]))
    out = []
    for stem in config.PRODUCTS:
        pal = G["pal"][stem]
        idx = np.where(edge, pal[3][c], pal[2][c])
        out.append(encode(idx, pal) if idx.any() else None)
    return out


def uniform_class(cids, geoms, box):
    """Class id if `geoms` (already clipped to `box`, non-overlapping) cover the
    box entirely, else 0."""
    if len(np.unique(cids)) != 1:
        return 0
    area = (box[2] - box[0]) * (box[3] - box[1])
    return int(cids[0]) if shapely.area(geoms).sum() >= area * (1 - 1e-9) else 0


def clip(cids, geoms, bounds_arr, box):
    x0, y0, x1, y1 = box
    m = ((bounds_arr[:, 0] <= x1) & (bounds_arr[:, 2] >= x0)
         & (bounds_arr[:, 1] <= y1) & (bounds_arr[:, 3] >= y0))
    if not m.any():
        return None
    g = shapely.clip_by_rect(geoms[m], x0, y0, x1, y1)
    keep = ~shapely.is_empty(g)
    if not keep.any():
        return None
    g = g[keep]
    return cids[m][keep], g, shapely.bounds(g)


def walk(z, x, y, cids, geoms, bnds, zlo, zhi, emit):
    box = margin_box(z, x, y)
    u = uniform_class(cids, geoms, box)
    if u:
        emit.append(("uni", z, x, y, (u, zhi)))
        return
    if zlo <= z <= zhi:
        imgs = render(z, x, y, cids, geoms)
        if any(i is not None for i in imgs):
            emit.append(("img", z, x, y, imgs))
    if z >= zhi:
        return
    for dx in (0, 1):
        for dy in (0, 1):
            cx, cy = 2 * x + dx, 2 * y + dy
            sub = clip(cids, geoms, bnds, margin_box(z + 1, cx, cy))
            if sub is not None:
                walk(z + 1, cx, cy, *sub, zlo, zhi, emit)


def task(args):
    z, x, y, zlo, zhi = args
    box = margin_box(z, x, y)
    idx = G["tree"].query(shapely.box(*box))
    if len(idx) == 0:
        return []
    sub = clip(G["cids"][idx], G["geoms"][idx], G["bounds"][idx], box)
    if sub is None:
        return []
    emit = []
    # Below ROOT_Z only render the single tile; roots recurse.
    walk(z, x, y, *sub, zlo, zhi if z >= ROOT_Z else z, emit)
    return emit


# ------------------------------------------------------------------- MBTiles
SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (name TEXT, value TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS metadata_name ON metadata (name);
CREATE TABLE IF NOT EXISTS map (zoom_level INTEGER, tile_column INTEGER,
                                tile_row INTEGER, tile_id TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS map_index ON map (zoom_level, tile_column, tile_row);
CREATE TABLE IF NOT EXISTS images (tile_data BLOB, tile_id TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS images_id ON images (tile_id);
CREATE VIEW IF NOT EXISTS tiles AS
  SELECT map.zoom_level AS zoom_level, map.tile_column AS tile_column,
         map.tile_row AS tile_row, images.tile_data AS tile_data
  FROM map JOIN images ON images.tile_id = map.tile_id;
"""


class Writer:
    def __init__(self, stem, fresh):
        self.path = config.OUTPUT / f"{stem}.mbtiles"
        if fresh and self.path.exists():
            self.path.unlink()
        self.db = sqlite3.connect(self.path)
        self.db.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;" + SCHEMA)
        self.known = {r[0] for r in self.db.execute("SELECT tile_id FROM images")}
        self.maprows, self.imgrows = [], []
        self.count = 0

    def add(self, z, x, y, data):
        h = hashlib.md5(data).hexdigest()
        if h not in self.known:
            self.known.add(h)
            self.imgrows.append((sqlite3.Binary(data), h))
        self.maprows.append((z, x, (1 << z) - 1 - y, h))   # TMS row
        self.count += 1
        if len(self.maprows) >= 50_000:
            self.flush()

    def flush(self):
        self.db.executemany("INSERT OR REPLACE INTO images VALUES (?,?)", self.imgrows)
        self.db.executemany("INSERT OR REPLACE INTO map VALUES (?,?,?,?)", self.maprows)
        self.db.commit()
        self.maprows, self.imgrows = [], []

    def finish(self, meta):
        self.flush()
        self.db.executemany("INSERT OR REPLACE INTO metadata VALUES (?,?)",
                            [(k, str(v)) for k, v in meta.items()])
        self.db.commit()
        self.db.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zooms", default=f"{config.MINZOOM}-{config.MAXZOOM}")
    ap.add_argument("--fresh", action="store_true", help="delete existing MBTiles first")
    ap.add_argument("--procs", type=int, default=mp.cpu_count())
    ap.add_argument("--meta-only", action="store_true",
                    help="rewrite metadata of existing files, render nothing")
    a = ap.parse_args()
    zlo, zhi = map(int, a.zooms.split("-"))
    t0 = time.time()
    if a.meta_only:
        write_metadata({stem: Writer(stem, False) for stem in config.PRODUCTS})
        report()
        return

    df = pyogrio.read_dataframe(config.CLASSES, layer="classes")
    geoms = shapely.get_parts(df.geometry.values)
    reps = shapely.get_num_geometries(df.geometry.values)
    cids = np.repeat(np.array([config.CLASS_ID[k] for k in df.cls], np.uint8), reps)
    G.update(geoms=geoms, cids=cids, bounds=shapely.bounds(geoms),
             tree=shapely.STRtree(geoms), pal=product_palettes())
    log(f"{len(geoms):,} polygons, {shapely.get_num_coordinates(geoms).sum():,} vertices")

    states = pyogrio.read_dataframe(config.NORMALIZED, layer="states")
    w, s, e, n = states.total_bounds
    lnglat = (round(w, 5), round(s, 5), round(e, 5), round(n, 5))

    # Single-tile jobs below ROOT_Z, quadtree roots at ROOT_Z.
    zs = list(range(zlo, min(zhi, ROOT_Z - 1) + 1)) + ([ROOT_Z] if zhi >= ROOT_Z else [])
    jobs = [(t.z, t.x, t.y, zlo, zhi) for t in mercantile.tiles(*lnglat, zooms=zs)]
    log(f"zooms {zlo}-{zhi}: {len(jobs)} tasks")

    writers = {stem: Writer(stem, a.fresh) for stem in config.PRODUCTS}
    pal = G["pal"]
    uni_png = {}

    def uniform_png(stem, cid):
        key = (stem, cid)
        if key not in uni_png:
            p = pal[stem]
            idx = np.full((T, T), p[2][cid], np.uint8)
            uni_png[key] = encode(idx, p) if p[2][cid] else None
        return uni_png[key]

    done = 0
    with mp.get_context("fork").Pool(a.procs) as pool:
        for emit in pool.imap_unordered(task, jobs, chunksize=1):
            for kind, z, x, y, payload in emit:
                if kind == "img":
                    for stem, data in zip(config.PRODUCTS, payload):
                        if data is not None:
                            writers[stem].add(z, x, y, data)
                    continue
                # uniform: this tile and all descendants to zmax share one image
                cid, zmax = payload
                for stem in config.PRODUCTS:
                    data = uniform_png(stem, cid)
                    if data is None:
                        continue
                    for zz in range(max(z, zlo), zmax + 1):
                        f = 1 << (zz - z)
                        for xx in range(x * f, x * f + f):
                            for yy in range(y * f, y * f + f):
                                writers[stem].add(zz, xx, yy, data)
            done += 1
            if done % 50 == 0 or done == len(jobs):
                log(f"  {done}/{len(jobs)} tasks; tiles: " + ", ".join(
                    f"{s}={wr.count:,}" for s, wr in writers.items()))

    write_metadata(writers)
    report()
    log(f"done in {time.time() - t0:.0f}s")


def write_metadata(writers):
    states = pyogrio.read_dataframe(config.NORMALIZED, layer="states")
    w, s, e, n = states.total_bounds
    for stem, wr in writers.items():
        classes, name, desc = config.PRODUCTS[stem]
        wr.flush()   # zoom range must include the last buffered batch
        zs = wr.db.execute("SELECT MIN(zoom_level), MAX(zoom_level) FROM map").fetchone()
        wr.finish({
            "name": name,
            "format": "png",
            "type": "overlay",
            "version": "1.0",
            "description": desc,
            "attribution": "BLM SMA/NLCS, USGS PAD-US, US Census TIGER",
            "bounds": f"{w:.5f},{s:.5f},{e:.5f},{n:.5f}",
            "center": f"{(w + e) / 2:.4f},{(s + n) / 2:.4f},{config.MINZOOM}",
            "minzoom": zs[0],
            "maxzoom": zs[1],
        })


def report():
    for stem in config.PRODUCTS:
        p = config.OUTPUT / f"{stem}.mbtiles"
        db = sqlite3.connect(p)
        rows = db.execute("SELECT zoom_level, COUNT(*), COUNT(DISTINCT tile_id) FROM map "
                          "GROUP BY zoom_level ORDER BY zoom_level").fetchall()
        nimg, nbytes = db.execute("SELECT COUNT(*), SUM(LENGTH(tile_data)) FROM images").fetchone()
        db.close()
        log(f"{p.name}: {p.stat().st_size / 1e6:,.1f} MB on disk, {nimg:,} unique images "
            f"({(nbytes or 0) / 1e6:,.1f} MB)")
        for z, cnt, uniq in rows:
            log(f"    z{z:<2d} {cnt:>9,} tiles  {uniq:>9,} unique")


if __name__ == "__main__":
    main()
