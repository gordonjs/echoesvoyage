"""Write output/README.txt from the manifest, class stats and validation results."""
import datetime as dt
import json
import sqlite3

import config


def load(p, default):
    return json.loads(p.read_text()) if p.exists() else default


def mb(n):
    return f"{n / 1e6:,.1f} MB"


def main():
    man = load(config.MANIFEST, {"sources": {}})
    stats = load(config.CLASS_STATS, {})
    val = load(config.WORK / "validation.json", {})
    nmeta = load(config.WORK / "normalized_meta.json", {"mode": "?", "empty_themes": []})
    mode = nmeta["mode"]
    L = []
    w = L.append

    w("SW HELICOPTER LANDING-STATUS OVERLAYS FOR FOREFLIGHT")
    w("CO / UT / NM / AZ / NV   -   raster PNG MBTiles, z%d-%d"
      % (config.MINZOOM, config.MAXZOOM))
    w(f"Built {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC, source mode: {mode}")
    w("")
    if mode != "authoritative":
        w("!" * 78)
        w("DRAFT - NOT FOR FLIGHT USE. Built in '%s' mode, not from the brief's BLM" % mode)
        w("SMA / NLCS sources. Empty themes: %s." % (", ".join(nmeta["empty_themes"]) or "none"))
        w("Missing WSAs draw as green 'landable' BLM. Rebuild in authoritative mode.")
        w("!" * 78)
        w("")

    w("FILES")
    w("-----")
    for stem, (classes, name, desc) in config.PRODUCTS.items():
        p = config.OUTPUT / f"{stem}.mbtiles"
        if not p.exists():
            w(f"{stem}.mbtiles   (not built)")
            continue
        db = sqlite3.connect(p)
        meta = dict(db.execute("SELECT name, value FROM metadata"))
        n, u = db.execute("SELECT COUNT(*), COUNT(DISTINCT tile_id) FROM map").fetchone()
        db.close()
        w(f"{stem}.mbtiles   {mb(p.stat().st_size)}   classes {', '.join(classes)}")
        w(f"    name='{meta.get('name')}' format={meta.get('format')} type={meta.get('type')} "
          f"zoom {meta.get('minzoom')}-{meta.get('maxzoom')}")
        w(f"    bounds={meta.get('bounds')}   {n:,} tiles, {u:,} unique images")
    w("preview_<product>.png, preview_all.png - z7 mosaics of the tiles over state outlines")
    w("")

    w("CLASSES (no overlaps; earlier class wins)")
    w("-----------------------------------------")
    for key, theme, label, color, op in config.CLASSES_DEF:
        area = stats.get(key)
        a = f"{area:>10,.0f} km2" if area is not None else ""
        w(f"  {key}. {label:34s} {color}  fill {op:.0%}  {a}")
    w(f"  outline: 1 px, same hue darkened to {config.OUTLINE_DARKEN:.0%} RGB, "
      f"{config.OUTLINE_OPACITY:.0%} opacity")
    w("  Not drawn: NPS, DoD, private, other federal/local (already on the sectional")
    w("  or obviously no-go).")
    w("")

    w("SOURCES")
    w("-------")
    for key, s in man.get("sources", {}).items():
        if key == "blm":
            used = {"sma": "BLM (g), USFS (f), state (e), tribal/BIA (c)",
                    "nlcs_wsa": "WSA (b)", "nlcs_wilderness": "wilderness (a), with PAD-US",
                    "nlcs_nm_nca": "BLM National Monument / NCA (d)"}
            for k, r in s.items():
                w(f"* {r.get('title')}  [{r.get('item_type', 'Feature Service')}]")
                w(f"    used for: {used.get(k, k)}")
                w(f"    hub item {r.get('item_id', '-')}  item modified {r.get('item_modified')}"
                  + (f"  data last edit {r['data_last_edit']}" if r.get("data_last_edit") else ""))
                w(f"    {r.get('layer_url') or r.get('download')}")
                if r.get("layer_name"):
                    w(f"    layer {r['layer_name']}")
                w(f"    {r.get('features', '?'):,} features in 5-state envelope; "
                  f"downloaded {r.get('downloaded')}")
            continue
        w(f"* {s.get('title')}")
        w(f"    version: {s.get('version')}"
          + (f"  ({s['component_versions']})" if s.get("component_versions") else ""))
        if s.get("dates"):
            w(f"    dates: {s['dates']}")
        w(f"    used for: {s.get('used_for')}")
        w(f"    {s.get('url')}" + (f"   doi {s['doi']}" if s.get("doi") else ""))
        w(f"    downloaded {s.get('downloaded')}")
    w("")

    if mode == "authoritative":
        w("SOURCE HANDLING NOTES")
        w("---------------------")
        w("* Wilderness (a) = PAD-US 4.1 Designation Des_Tp 'WA' (BLM, USFS, NPS, FWS)")
        w("  unioned with the current BLM NLCS wilderness layer.")
        notes = nmeta.get("notes", {})
        usfs = notes.get("usfs_wsas_added", [])
        w(f"* WSA (b) = BLM NLCS WSA layer, minus {notes.get('wsa_inholdings_dropped', '?')}"
          " Utah features named 'Inholding'")
        w("  (state/private sections inside WSAs per SMA; the WSA polygons are holed")
        w(f"  there), plus {len(usfs)} USFS WSAs that only PAD-US carries:")
        for i in range(0, len(usfs), 4):
            w("    " + ", ".join(usfs[i:i + 4]))
        w("  PAD-US 'WSA' rows that are NPS/FWS proposed or recommended wilderness")
        w("  are not used.")
        w("* Tribal (c) = SMA ADMIN_AGENCY_CODE 'BIA'.")
        w("* State (e) = SMA 'ST'. SMA does not split state trust land from other")
        w("  state land, so this also includes state parks, wildlife areas and")
        w("  sovereign lake/river beds - all call-first.")
        w("* USFS (f) = SMA 'USFS'; BLM (g) = SMA 'BLM'.")
        w("* The national SMA geodatabase's spatial index returns nothing for a bbox")
        w("  query in GDAL, so features are filtered on bounds after a full read.")
        w("")

    w("PROCESSING")
    w("----------")
    w("1. fetch_sources.py    download sources, record URLs / versions / dates")
    w("2. normalize_sources.py  map each source onto themes (wilderness, wsa, tribal,")
    w("   nm_nca, state_trust, usfs, blm)")
    w("3. build_classes.py    reproject to EPSG:3857, clip to the union of the five")
    w("   TIGER 2025 state polygons, then per 50 km cell: class = theme minus every")
    w("   higher-priority class (snap-rounded GEOS overlay, 1 cm grid)")
    w("4. make_mbtiles.py     256 px palette-PNG tiles z6-13; fill + 1 px outline from")
    w("   the class raster; empty tiles skipped; identical tiles stored once")
    w("   (map/images tables + tiles view)")
    w("5. validate.py         previews, spot checks, overlap check, 2019 designation")
    w("   currency check, state/private-inside-no-land tally")
    w("")

    if val:
        w("VALIDATION")
        w("----------")
        for r in val.get("spot_checks", []):
            w(f"  {r['result']:5s} {r['check']}  (expected class {r['expected']})")
            if r.get("area_share_by_class"):
                shares = ", ".join(f"{k} {v:.1%}" for k, v in
                                   sorted(r["area_share_by_class"].items(), key=lambda kv: -kv[1])
                                   if v >= 0.0005)
                w(f"        area share by class: {shares}")
            if r.get("tile_pixel") is not None:
                w(f"        tile pixel at {r.get('probe_lonlat')}: {', '.join(r['tile_pixel']) or 'transparent'}")
            if r.get("note"):
                w(f"        note: {r['note']}")
        cur = val.get("currency")
        if cur:
            pres = cur["dingell_2019_wilderness_present"]
            w(f"  2019 Dingell Act wilderness (Emery Co. UT, NM) found in wilderness source: "
              f"{len(pres)} of {len(pres) + len(cur['dingell_2019_wilderness_missing'])}")
            if cur["dingell_2019_wilderness_missing"]:
                w(f"        missing: {', '.join(cur['dingell_2019_wilderness_missing'])}")
            if "wsa_features" in cur:
                st = cur["wsa_overlapping_wilderness"]
                w(f"  WSA layer: {cur['wsa_features']} features; {len(st)} overlap designated "
                  "wilderness by >5% (wilderness wins there)")
                for r in st[:15]:
                    w(f"        {r['wsa']}: {r['share_now_wilderness']:.0%} now wilderness")
        nf = val.get("nonfederal_in_noland")
        if nf:
            w("  State (SMA ST) / private (SMA PVT) land inside no-land classes, km2")
            w("  (inholdings enclosed by a designation boundary; priority paints them")
            w("  with the designation):")
            for k, v in nf.items():
                w(f"        {k}: state {v.get('ST', 0):,.1f}   private {v.get('PVT', 0):,.1f}")
        ov = val.get("overlap", {})
        w(f"  Overlap between classes: {ov.get('overlap_area_m2_mercator', '?')} m2 "
          f"(Mercator) across {ov.get('pairs_touching', '?'):,} touching pairs "
          "- snap-rounding noise only")
        w("")

    w("CAVEATS")
    w("-------")
    w("* Advisory planning aid only. SMA shows the managing agency, not legal survey")
    w("  boundaries; check the agency, NOTAMs/TFRs, closures and landowner permission.")
    w("* 'Landable' means BLM surface outside the no-land/call-first classes. It does")
    w("  not account for ACEC or route closures, mining claims, grazing")
    w("  improvements, or unmapped private inholdings.")
    w("* Tribal land requires tribal permission; state trust land requires the state")
    w("  land office's permission; USFS non-wilderness: check forest orders.")
    (config.OUTPUT / "README.txt").write_text("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
