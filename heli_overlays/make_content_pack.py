"""Bundle the landing overlays and the POI layers into one ForeFlight content pack.

  SW_Heli/
    manifest.json
    layers/   SW_Landable_BLM.mbtiles, SW_NoLand.mbtiles, SW_CallFirst.mbtiles,
              SW_POI_<category>.kmz (one map layer per category)
    navdata/  user_waypoints.csv (POIs as searchable user waypoints)

The overlays go in as standard RGBA PNG tiles in a plain `tiles` table (see
convert_mbtiles.py): ForeFlight drew the compact palette / deduplicated tiles
from output/ inconsistently. The repeated tiles of the flat table compress
away in the zip, so the pack stays well under GitHub's 100 MB file limit.

Written to output/SW_Heli_ContentPack.zip (the zip holds the SW_Heli folder).
"""
import datetime as dt
import json
import multiprocessing as mp
import shutil
import zipfile

import config
from convert_mbtiles import convert

POI_DIR = config.WORK / "poi"            # written by poi.py
PACK_DIR = config.WORK / "pack" / "SW_Heli"
ZIP = config.OUTPUT / "SW_Heli_ContentPack.zip"
VERSION = 1.1
MAX_ZIP = 95_000_000


def _convert(stem):
    n = convert(config.OUTPUT / f"{stem}.mbtiles", PACK_DIR / "layers" / f"{stem}.mbtiles")
    return stem, n


def main():
    if PACK_DIR.exists():
        shutil.rmtree(PACK_DIR)
    (PACK_DIR / "layers").mkdir(parents=True)
    with mp.Pool(len(config.PRODUCTS)) as pool:
        for stem, n in pool.imap_unordered(_convert, config.PRODUCTS):
            print(f"  {stem}: {n:,} tiles -> RGBA PNG, flat tiles table")
    kmz = sorted((POI_DIR / "layers").glob("SW_POI_*.kmz"))
    wpt = POI_DIR / "navdata" / "user_waypoints.csv"
    if not kmz or not wpt.exists():
        raise SystemExit("POI layers missing: run poi.py first")
    for k in kmz:
        shutil.copy2(k, PACK_DIR / "layers" / k.name)
    (PACK_DIR / "navdata").mkdir()
    shutil.copy2(wpt, PACK_DIR / "navdata" / wpt.name)
    today = dt.date.today()
    (PACK_DIR / "manifest.json").write_text(json.dumps({
        "name": "SW Heli Landing & POI",
        "abbreviation": f"SWHELI.V{VERSION:g}",
        "version": VERSION,
        "organizationName": "echoesvoyage",
        "effectiveDate": f"{today:%Y%m%d}T00:00:00",
    }, indent=2))
    for old in config.OUTPUT.glob("SW_Heli_*.zip"):
        old.unlink()
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(PACK_DIR.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(PACK_DIR.parent))
    with zipfile.ZipFile(ZIP) as z:
        for i in z.infolist():
            print(f"  {i.filename:55s} {i.file_size / 1e6:8.2f} MB")
    size = ZIP.stat().st_size
    print(f"{ZIP.name}: {size / 1e6:.1f} MB")
    if size > MAX_ZIP:
        raise SystemExit(f"{ZIP.name} is over {MAX_ZIP / 1e6:.0f} MB (GitHub file limit)")


if __name__ == "__main__":
    main()
