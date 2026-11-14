"""Rewrite an MBTiles file in the most widely supported raster layout.

  * every tile as a standard 8-bit-per-channel RGBA PNG (colour type 6),
    instead of a 2/4-bit palette PNG with tRNS transparency;
  * a plain `tiles` table (one row per tile) instead of the deduplicated
    map/images tables behind a `tiles` view.

ForeFlight drew the palette / deduplicated tiles inconsistently (the same BLM
fill came out dark grey in Utah and nearly invisible in Colorado), so the
ForeFlight pack uses this layout. Pixels are unchanged; metadata is copied.

Usage: python convert_mbtiles.py IN.mbtiles OUT.mbtiles
"""
import io
import sqlite3
import sys

from PIL import Image


def to_rgba_png(data):
    im = Image.open(io.BytesIO(data)).convert("RGBA")
    buf = io.BytesIO()
    im.save(buf, "PNG", compress_level=9)
    return buf.getvalue()


def convert(src, dst):
    s = sqlite3.connect(src)
    d = sqlite3.connect(dst)
    d.executescript("""
        PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
        DROP TABLE IF EXISTS metadata; DROP TABLE IF EXISTS tiles;
        CREATE TABLE metadata (name TEXT, value TEXT);
        CREATE UNIQUE INDEX metadata_name ON metadata (name);
        CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER,
                            tile_data BLOB);
        CREATE UNIQUE INDEX tile_index ON tiles (zoom_level, tile_column, tile_row);
    """)
    d.executemany("INSERT INTO metadata VALUES (?,?)", s.execute("SELECT name, value FROM metadata"))
    cache, rows, n = {}, [], 0
    # Convert each distinct image once, then write it for every tile that uses it.
    for z, x, y, tid in s.execute(
            "SELECT zoom_level, tile_column, tile_row, tile_id FROM map ORDER BY tile_id"):
        if tid not in cache:
            cache.clear()   # rows are ordered by tile_id: one image in memory at a time
            data = s.execute("SELECT tile_data FROM images WHERE tile_id=?", (tid,)).fetchone()[0]
            cache[tid] = to_rgba_png(data)
        rows.append((z, x, y, sqlite3.Binary(cache[tid])))
        n += 1
        if len(rows) >= 20_000:
            d.executemany("INSERT INTO tiles VALUES (?,?,?,?)", rows)
            rows = []
    d.executemany("INSERT INTO tiles VALUES (?,?,?,?)", rows)
    d.commit()
    d.execute("VACUUM")
    d.close()
    return n


if __name__ == "__main__":
    print(convert(sys.argv[1], sys.argv[2]), "tiles")
