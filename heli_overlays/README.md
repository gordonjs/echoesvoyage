# SW helicopter landing-status overlays (ForeFlight MBTiles)

Raster PNG MBTiles overlays for ForeFlight on iPad, covering CO, UT, NM, AZ and NV:

| File | Classes |
|---|---|
| `output/SW_Landable_BLM.mbtiles` | g. BLM outside a-e (green) |
| `output/SW_NoLand.mbtiles` | a. Wilderness, all agencies (red); b. WSA (orange); c. Tribal (purple); d. BLM NM/NCA (magenta) |
| `output/SW_CallFirst.mbtiles` | e. State trust (blue); f. USFS non-wilderness (yellow) |

Classes never overlap: a wins over b, b over c, and so on down to g. Each class has a fill and a 1 px darker outline in the same hue at 80% opacity. Tiles cover zoom 6-13 at 256 px, as 8-bit palette PNGs with transparency. Empty tiles are skipped and identical tiles are stored once.

## Run

```
pip install -r requirements.txt
./run_all.sh                      # authoritative: BLM SMA + BLM NLCS + PAD-US 4.1
MODE=padus ./run_all.sh           # PAD-US 4.1 only
MODE=tnm ./run_all.sh             # pipeline test, NOT for flight use (no WSA / state trust)
```

`run_all.sh` builds z6-12 first and estimates the finished size. It stops before z13 if any file looks likely to pass 1 GB; set `FORCE_Z13=1` to build z13 anyway. Downloads and intermediates go to `work/` (gitignored). Outputs, previews and `README.txt` go to `output/`.

Network hosts used: `prd-tnm.s3.amazonaws.com`, `www.sciencebase.gov` (and its S3 file bucket), `www.arcgis.com`, `blm-egis.maps.arcgis.com`, and the BLM feature-service hosts (`services*.arcgis.com` / `gis.blm.gov`).

## Steps

1. `fetch_sources.py` downloads the sources and writes `work/manifest.json` (URL, version, item date, download time).
2. `normalize_sources.py --mode ...` maps the sources onto themes in `work/normalized.gpkg`.
3. `build_classes.py` reprojects to EPSG:3857, clips to the five-state union and applies the priority overlay in 50 km cells. Output: `work/classes_3857.gpkg`.
4. `make_mbtiles.py` renders the tiles with a quadtree. Each z8 root clips its geometry once and passes it down to its children.
5. `size_gate.py` estimates the z13 size from the z6-12 build.
6. `validate.py` writes the previews, runs the spot checks, the overlap check, and the 2019 Dingell Act currency check.
7. `write_readme.py` writes `output/README.txt`.
