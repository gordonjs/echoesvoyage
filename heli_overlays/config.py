"""Shared settings for the SW helicopter landing-status overlays."""
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WORK = Path(os.environ.get("HELI_WORK", REPO / "work"))
RAW = WORK / "raw"
OUTPUT = Path(os.environ.get("HELI_OUTPUT", REPO / "output"))

MANIFEST = WORK / "manifest.json"          # what was downloaded, from where, when
NORMALIZED = WORK / "normalized.gpkg"      # one layer per source theme, native CRS
CLASSES = WORK / "classes_3857.gpkg"       # final non-overlapping classes a-g, EPSG:3857
CLASS_STATS = WORK / "class_stats.json"

STATES = {  # USPS code -> (name, FIPS)
    "CO": ("Colorado", "08"),
    "UT": ("Utah", "49"),
    "NM": ("New Mexico", "35"),
    "AZ": ("Arizona", "04"),
    "NV": ("Nevada", "32"),
}

# Normalized source themes (layers in NORMALIZED) feeding each class.
# Order is the priority order: earlier classes win where they overlap later ones.
CLASSES_DEF = [
    # key, theme layer,   label,                              hex,       fill opacity
    ("a", "wilderness",  "Wilderness (all agencies)",         "#D7263D", 0.45),
    ("b", "wsa",         "Wilderness Study Area",             "#F77F00", 0.45),
    ("c", "tribal",      "Tribal land",                       "#7B2CBF", 0.40),
    ("d", "nm_nca",      "BLM National Monument / NCA",       "#C9184A", 0.35),
    ("e", "state_trust", "State trust land",                  "#1E6FD9", 0.40),
    ("f", "usfs",        "USFS (non-wilderness)",             "#F2C14E", 0.30),
    ("g", "blm",         "BLM (outside a-e) = landable",      "#2A9D3F", 0.40),
]
CLASS_KEYS = [c[0] for c in CLASSES_DEF]
CLASS_ID = {k: i + 1 for i, k in enumerate(CLASS_KEYS)}   # raster value; 0 = nothing

OUTLINE_OPACITY = 0.80
OUTLINE_DARKEN = 0.70   # outline = fill hue with RGB scaled by this factor

PRODUCTS = {
    # file stem: (classes, MBTiles name, description)
    "SW_Landable_BLM": (["g"], "SW Landable BLM",
                        "BLM-administered surface outside wilderness, WSA, tribal, "
                        "BLM NM/NCA and state trust land (class g)."),
    "SW_NoLand": (["a", "b", "c", "d"], "SW No-Land",
                  "Wilderness (all agencies), BLM WSA, tribal land, BLM National "
                  "Monuments / NCAs (classes a-d)."),
    "SW_CallFirst": (["e", "f"], "SW Call-First",
                     "State trust land and non-wilderness USFS (classes e-f)."),
}

MINZOOM = 6
MAXZOOM = 13
TILE_SIZE = 256
SIZE_WARN_BYTES = 1_000_000_000

WEB_MERCATOR = "EPSG:3857"
