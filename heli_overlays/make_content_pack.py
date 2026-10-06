"""Bundle the landing overlays and the POI layers into one ForeFlight content pack.

  SW_Heli/
    manifest.json
    layers/   SW_Landable_BLM.mbtiles, SW_NoLand.mbtiles, SW_CallFirst.mbtiles,
              SW_POI_<category>.kmz (one map layer per category)
    navdata/  user_waypoints.csv (POIs as searchable user waypoints)

Written to output/SW_Heli_ContentPack.zip (the zip holds the SW_Heli folder).
"""
import datetime as dt
import json
import shutil
import zipfile

import config

PACK_DIR = config.WORK / "pack" / "SW_Heli"
ZIP = config.OUTPUT / "SW_Heli_ContentPack.zip"
VERSION = 1.0


def main():
    layers = PACK_DIR / "layers"
    layers.mkdir(parents=True, exist_ok=True)
    for stem in config.PRODUCTS:
        shutil.copy2(config.OUTPUT / f"{stem}.mbtiles", layers / f"{stem}.mbtiles")
    kmz = sorted(layers.glob("SW_POI_*.kmz"))
    wpt = PACK_DIR / "navdata" / "user_waypoints.csv"
    if not kmz or not wpt.exists():
        raise SystemExit("POI layers missing: run poi.py first")
    today = dt.date.today()
    manifest = {
        "name": "SW Heli Landing & POI",
        "abbreviation": f"SWHELI.V{VERSION:g}",
        "version": VERSION,
        "organizationName": "echoesvoyage",
        "effectiveDate": f"{today:%Y%m%d}T00:00:00",
    }
    (PACK_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    if ZIP.exists():
        ZIP.unlink()
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(PACK_DIR.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(PACK_DIR.parent))
    with zipfile.ZipFile(ZIP) as z:
        for i in z.infolist():
            print(f"  {i.filename:55s} {i.file_size / 1e6:8.2f} MB")
    print(f"{ZIP.name}: {ZIP.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
