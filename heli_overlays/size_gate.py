"""Exit 1 if adding z13 is likely to push any MBTiles past the size limit.

Estimate from the z6-12 build: z13 image bytes ~= IMG_FACTOR x z12 image bytes
(edge tiles roughly double per zoom; interior tiles dedupe to one image) plus
4x the z12 tile count in map rows at ROW_BYTES each. On the NBD test build the
measured image ratio was 2.2x and the estimate came out ~5% high.
"""
import sqlite3
import sys

import config

IMG_FACTOR = 2.5
ROW_BYTES = 130   # map row + unique-index entry


def main():
    over = False
    for stem in config.PRODUCTS:
        p = config.OUTPUT / f"{stem}.mbtiles"
        db = sqlite3.connect(p)
        z12 = db.execute(
            "SELECT COALESCE(SUM(LENGTH(i.tile_data)), 0) FROM images i WHERE i.tile_id IN "
            "(SELECT tile_id FROM map WHERE zoom_level = 12)").fetchone()[0]
        rows12 = db.execute("SELECT COUNT(*) FROM map WHERE zoom_level = 12").fetchone()[0]
        db.close()
        now = p.stat().st_size
        est = now + IMG_FACTOR * z12 + 4 * rows12 * ROW_BYTES
        flag = est > config.SIZE_WARN_BYTES
        over |= flag
        print(f"{stem}: z6-12 {now / 1e6:,.1f} MB; estimated with z13 {est / 1e6:,.1f} MB"
              + ("  ** OVER LIMIT **" if flag else ""))
    sys.exit(1 if over else 0)


if __name__ == "__main__":
    main()
