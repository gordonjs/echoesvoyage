"""Turn raw downloads into normalized theme layers in work/normalized.gpkg.

Each output layer (states, wilderness, wsa, tribal, nm_nca, state_trust, usfs,
blm) has columns: name, agency, src, geometry. Themes are deliberately
over-inclusive (no clipping, no priority) -- build_classes.py resolves overlaps.

Modes
  authoritative  BLM SMA + BLM NLCS (WSA, Wilderness, NM/NCA) + PAD-US 4.1
                 Designation for all-agency wilderness. The brief's sources.
  padus          PAD-US 4.1 only (Fee + Designation). Fallback if the BLM hub
                 is unreachable but ScienceBase is.
  tnm            USGS National Map boundaries (PAD-US 4.1 federal subset + TIGER
                 2025). Has NO WSA and NO state trust land: pipeline test only,
                 never for flight use.
"""
import argparse
import json
import re
import sys
import warnings
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio

import config

warnings.filterwarnings("ignore", message=".*CLIPPOLY.*")
THEMES = ["wilderness", "wsa", "tribal", "nm_nca", "state_trust", "usfs", "blm"]
NOTES = {}  # facts about source handling, copied into normalized_meta.json


def log(*a):
    print(*a, flush=True)


def norm(gdf, name_col=None, agency=None, src=""):
    """Reduce to the standard schema."""
    out = gpd.GeoDataFrame(
        {
            "name": gdf[name_col].fillna("").astype(str) if name_col and name_col in gdf
            else "",
            "agency": gdf[agency].fillna("").astype(str) if agency in gdf.columns
            else (agency or ""),
            "src": src,
        },
        geometry=gdf.geometry.values,
        crs=gdf.crs,
    )
    out = out[~out.geometry.is_empty & out.geometry.notna()]
    return out


def write(layers, mode):
    path = config.NORMALIZED
    if path.exists():
        path.unlink()
    for theme in ["states"] + THEMES:
        gdf = layers.get(theme)
        if gdf is None or len(gdf) == 0:
            log(f"  {theme:12s} EMPTY")
            gdf = gpd.GeoDataFrame({"name": [], "agency": [], "src": []},
                                   geometry=[], crs="EPSG:4326")
        else:
            log(f"  {theme:12s} {len(gdf):7d} features  crs={gdf.crs.to_string()[:40]}")
        gdf = gdf.to_crs(4326)
        pyogrio.write_dataframe(gdf, path, layer=theme, driver="GPKG",
                                promote_to_multi=True)
    meta = {"mode": mode, "empty_themes": [t for t in THEMES if layers.get(t) is None
                                           or len(layers[t]) == 0], "notes": NOTES}
    (config.WORK / "normalized_meta.json").write_text(json.dumps(meta, indent=2))


# --------------------------------------------------------------------------- TNM
def tnm_gpkgs():
    out = []
    for st, (name, _) in config.STATES.items():
        hits = list((config.RAW / f"tnm_{name.replace(' ', '_')}").glob("*.gpkg"))
        if not hits:
            sys.exit(f"missing TNM GeoPackage for {name}; run fetch_sources.py")
        out.append(hits[0])
    return out


def load_tnm_states():
    parts = []
    for f in tnm_gpkgs():
        g = pyogrio.read_dataframe(f, layer="GU_StateOrTerritory")
        parts.append(g)
    g = pd.concat(parts, ignore_index=True)
    g = g[g.STATE_NAME.isin([v[0] for v in config.STATES.values()])]
    g = g.drop_duplicates("STATE_NAME")
    return norm(g, "STATE_NAME", src="TIGER/Line 2025 via USGS NBD")


def mode_tnm():
    res, nat = [], []
    for f in tnm_gpkgs():
        res.append(pyogrio.read_dataframe(f, layer="GU_Reserve"))
        nat.append(pyogrio.read_dataframe(f, layer="GU_NativeAmericanArea"))
    r = pd.concat(res, ignore_index=True).drop_duplicates("PERMANENT_IDENTIFIER")
    n = pd.concat(nat, ignore_index=True).drop_duplicates("PERMANENT_IDENTIFIER")
    ag = "OWNERORMANAGINGAGENCY_desc"
    src = "USGS NBD GU_Reserve (PAD-US 4.1)"
    blm = r[ag] == "Bureau of Land Management"
    usfs = r[ag] == "Forest Service"
    layers = {
        "states": load_tnm_states(),
        "wilderness": norm(r[r.FCODE == 67500], "NAME", ag, src),
        "wsa": None,           # not carried by NBD
        "tribal": norm(n, "NAME", src="TIGER/Line 2025 AIANNH via USGS NBD"),
        "nm_nca": norm(r[(r.FCODE == 67400) & blm], "NAME", ag, src),
        "state_trust": None,   # not carried by NBD
        "usfs": norm(r[r.FCODE.isin([67100, 67200]) & usfs
                       & (r.boundarytype_desc == "Fee")], "NAME", ag, src),
        "blm": norm(r[(r.FCODE == 67800) & blm & (r.boundarytype_desc == "Fee")],
                    "NAME", ag, src),
    }
    return layers


# ------------------------------------------------------------------------ PAD-US
def padus_layers():
    """Return (fee, designation, proclamation) from the five PAD-US state GDBs."""
    fee, des, pro = [], [], []
    for st in config.STATES:
        gdbs = list((config.RAW / "padus").glob(f"**/*{st}*.gdb"))
        if not gdbs:
            sys.exit(f"missing PAD-US 4.1 GDB for {st}; run fetch_sources.py")
        gdb = gdbs[0]
        names = [n for n, _ in pyogrio.list_layers(gdb)]
        f = [n for n in names if re.search(r"Fee_State", n)]
        d = [n for n in names if re.search(r"Designation_State", n)]
        p = [n for n in names if re.search(r"Proclamation_State", n)]
        if not f or not d or not p:
            sys.exit(f"{gdb}: cannot find Fee/Designation/Proclamation layers in {names}")
        fee.append(pyogrio.read_dataframe(gdb, layer=f[0]))
        des.append(pyogrio.read_dataframe(gdb, layer=d[0]))
        pro.append(pyogrio.read_dataframe(gdb, layer=p[0]))
    return tuple(gpd.GeoDataFrame(pd.concat(x, ignore_index=True), crs=x[0].crs)
                 for x in (fee, des, pro))


def padus_wilderness(des):
    return norm(des[des.Des_Tp == "WA"], "Unit_Nm", "Mang_Name", "PAD-US 4.1 Designation")


def padus_true_wsa(des, agencies=("BLM", "USFS")):
    """PAD-US Des_Tp 'WSA' also carries NPS/FWS proposed or recommended
    wilderness; keep only units actually named Wilderness Study Areas."""
    m = ((des.Des_Tp == "WSA") & des.Mang_Name.isin(agencies)
         & des.Unit_Nm.str.contains("Wilderness Study Area|WSA", case=False, na=False))
    return des[m]


def mode_padus():
    fee, des, pro = padus_layers()
    s = "PAD-US 4.1 "
    blm_des = des[des.Mang_Name == "BLM"]
    tribal = pro[pro.Mang_Type == "TRIB"]   # tribal lands are in Proclamation in 4.x
    return {
        "states": load_tnm_states(),
        "wilderness": padus_wilderness(des),
        "wsa": norm(padus_true_wsa(des), "Unit_Nm", "Mang_Name", s + "Designation"),
        "tribal": norm(tribal, "Unit_Nm", "Mang_Name", s + "Proclamation"),
        "nm_nca": norm(blm_des[blm_des.Des_Tp.isin(["NM", "NCA"])], "Unit_Nm",
                       "Mang_Name", s + "Designation"),
        "state_trust": norm(fee[fee.Mang_Name == "SLB"], "Unit_Nm", "Mang_Name", s + "Fee"),
        "usfs": norm(fee[fee.Mang_Name == "USFS"], "Unit_Nm", "Mang_Name", s + "Fee"),
        "blm": norm(fee[fee.Mang_Name == "BLM"], "Unit_Nm", "Mang_Name", s + "Fee"),
    }


# ----------------------------------------------------------------- authoritative
def read_any(path):
    """Read the single (largest) polygon layer from a GPKG / GDB / shapefile."""
    path = Path(path)
    layers = [(n, g) for n, g in pyogrio.list_layers(path) if g and "Polygon" in g]
    if not layers:
        sys.exit(f"{path}: no polygon layer")
    best = max(layers, key=lambda l: pyogrio.read_info(path, layer=l[0])["features"])
    return pyogrio.read_dataframe(path, layer=best[0])


def pick(df, *cands):
    low = {c.lower(): c for c in df.columns}
    for c in cands:
        if c.lower() in low:
            return low[c.lower()]
    return None


def sma_split(sma):
    """BLM SMA -> tribal / state_trust / usfs / blm using ADMIN_AGENCY_CODE."""
    col = pick(sma, "ADMIN_AGENCY_CODE", "ADMIN_AGEN", "AGENCY_CODE")
    name = pick(sma, "ADMIN_UNIT_NAME", "ADMIN_UNIT", "SMA_NAME")
    if col is None:
        sys.exit(f"SMA: no agency code column in {list(sma.columns)}")
    codes = sma[col].astype(str).str.strip().str.upper()
    log("  SMA agency codes:", codes.value_counts().to_dict())
    src = "BLM National SMA"
    state_codes = {"ST", "STATE", "SLB", "STL", "SDNR", "SDOL", "SFW", "SPR"}
    return {
        "tribal": norm(sma[codes == "BIA"], name, col, src),
        "state_trust": norm(sma[codes.isin(state_codes)], name, col, src),
        "usfs": norm(sma[codes.isin(["USFS", "FS"])], name, col, src),
        "blm": norm(sma[codes == "BLM"], name, col, src),
    }


def mode_authoritative():
    b = config.RAW / "blm"
    sma = read_any(next(b.glob("sma*.gpkg")))
    layers = {"states": load_tnm_states()}
    layers.update(sma_split(sma))

    wsa = read_any(b / "nlcs_wsa.gpkg")
    log("  NLCS WSA columns:", list(wsa.columns))
    status = pick(wsa, "WSA_STATUS", "STATUS", "NLCS_STATUS")
    if status:
        log("  NLCS WSA status values:", wsa[status].value_counts().to_dict())
        wsa = wsa[~wsa[status].astype(str).str.contains("releas", case=False)]
    wname = pick(wsa, "NLCS_NAME", "WSA_NAME", "NAME")
    # The national WSA layer carries the non-federal sections inside Utah WSAs
    # as features named "Inholding" (state trust / private per SMA). Not WSA.
    inh = wsa[wname].astype(str).str.strip().str.lower() == "inholding"
    log(f"  NLCS WSA: dropping {int(inh.sum())} 'Inholding' features")
    NOTES["wsa_inholdings_dropped"] = int(inh.sum())
    wsa = wsa[~inh]
    wsa_n = norm(wsa, wname, src="BLM NLCS WSA")
    wsa_n["agency"] = "BLM"

    nm = read_any(b / "nlcs_nm_nca.gpkg")
    ncol = pick(nm, "sma_code", "NLCS_TYPE", "DESIG_TYPE", "TYPE")
    if ncol:
        log("  NLCS NM/NCA types:", nm[ncol].value_counts(dropna=False).to_dict())
    nm_n = norm(nm, pick(nm, "NCA_NAME", "NLCS_NAME", "NAME"), src="BLM NLCS NM/NCA")
    nm_n["agency"] = "BLM"

    fee, des, _ = padus_layers()
    wild = [padus_wilderness(des)]
    wpath = b / "nlcs_wilderness.gpkg"
    if wpath.exists():
        w = read_any(wpath)
        wcol = pick(w, "NLCS_NAME", "NAME")
        w = w[w[wcol].astype(str).str.strip().str.lower() != "inholding"]
        wn = norm(w, wcol, src="BLM NLCS Wilderness")
        wn["agency"] = "BLM"
        wild.append(wn)
    # USFS WSAs (Mount Graham AZ, Mount Stirling NV, ...) only exist in PAD-US.
    other_wsa = padus_true_wsa(des, agencies=("USFS",))
    log(f"  PAD-US USFS WSAs added: {sorted(set(other_wsa.Unit_Nm))}")
    NOTES["usfs_wsas_added"] = sorted(set(other_wsa.Unit_Nm.str.replace(
        r"\s*(Wsa\s*)?Wilderness Study Area$", "", regex=True)))
    layers.update({
        "wilderness": gpd.GeoDataFrame(pd.concat([w.to_crs(4326) for w in wild]),
                                       crs=4326),
        "wsa": gpd.GeoDataFrame(pd.concat([wsa_n.to_crs(4326), norm(
            other_wsa, "Unit_Nm", "Mang_Name", "PAD-US 4.1 Designation").to_crs(4326)]),
            crs=4326),
        "nm_nca": nm_n,
    })
    return layers


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["authoritative", "padus", "tnm"], required=True)
    a = ap.parse_args()
    log(f"normalizing ({a.mode}) -> {config.NORMALIZED}")
    layers = {"authoritative": mode_authoritative, "padus": mode_padus,
              "tnm": mode_tnm}[a.mode]()
    write(layers, a.mode)
