SW HELICOPTER LANDING-STATUS OVERLAYS FOR FOREFLIGHT
CO / UT / NM / AZ / NV   -   raster PNG MBTiles, z6-13
Built 2026-10-06 01:51 UTC, source mode: authoritative

FILES
-----
SW_Landable_BLM.mbtiles   32.4 MB   classes g
    name='SW Landable BLM' format=png type=overlay zoom 6-13
    bounds=-120.00647,31.33216,-102.04152,42.00184   60,334 tiles, 52,069 unique images
SW_NoLand.mbtiles   21.1 MB   classes a, b, c, d
    name='SW No-Land' format=png type=overlay zoom 6-13
    bounds=-120.00647,31.33216,-102.04152,42.00184   34,305 tiles, 23,411 unique images
SW_CallFirst.mbtiles   38.7 MB   classes e, f
    name='SW Call-First' format=png type=overlay zoom 6-13
    bounds=-120.00647,31.33216,-102.04152,42.00184   63,204 tiles, 58,477 unique images
preview_<product>.png, preview_all.png - z7 mosaics of the tiles over state outlines
SW_Heli_ContentPack.zip   58.7 MB   ForeFlight content pack:
    the three overlays + one POI map layer per category (KMZ) +
    navdata/user_waypoints.csv (every POI as a searchable user waypoint)
SW_POI.csv - every POI with landing class, nearest landable BLM, elevation, flags
preview_POI.png - POIs over the faded landing classes

CLASSES (no overlaps; earlier class wins)
-----------------------------------------
  a. Wilderness (all agencies)          #D7263D  fill 45%      64,239 km2
  b. Wilderness Study Area              #F77F00  fill 45%      26,411 km2
  c. Tribal land                        #7B2CBF  fill 40%     133,378 km2
  d. BLM National Monument / NCA        #C9184A  fill 35%      25,732 km2
  e. State trust land                   #1E6FD9  fill 40%     107,512 km2
  f. USFS (non-wilderness)              #F2C14E  fill 30%     163,368 km2
  g. BLM (outside a-e) = landable       #2A9D3F  fill 40%     350,571 km2
  outline: 1 px, same hue darkened to 70% RGB, 80% opacity
  Not drawn: NPS, DoD, private, other federal/local (already on the sectional
  or obviously no-go).

SOURCES
-------
* USGS National Boundary Dataset (NBD) state GeoPackages
    version: NBD published 20260212  (TIGER/Line 2025 (pub 2025-06-01); PAD-US 4.1 (pub 2025-03-31))
    used for: state outlines / clip (GU_StateOrTerritory = TIGER/Line 2025); Navajo Nation reference boundary for the spot check (GU_NativeAmericanArea)
    https://prd-tnm.s3.amazonaws.com/StagedProducts/GovtUnit/GPKG/GOVTUNIT_<State>_State_GPKG.zip
    downloaded 2026-10-05T16:05:03Z
* PAD-US 4.1 State Downloads
    version: PAD-US 4.1
    dates: {'Publication': '2025-03-31', 'Start': '2005', 'End': '2025'}
    used for: Wilderness, all agencies (Designation, Des_Tp='WA'); USFS WSAs
    https://www.sciencebase.gov/catalog/item/6759abcfd34edfeb8710a004   doi https://doi.org/10.5066/P96WBCHS
    downloaded 2026-10-05T16:43:52Z
* BLM Natl NLCS Wilderness Study Areas Polygons  [Feature Service]
    used for: WSA (b)
    hub item c9e074e0433c4afab261df2256fe4ae4  item modified 2026-08-14  data last edit 2026-08-14
    https://services1.arcgis.com/KbxwQRRfWyEYLgp4/arcgis/rest/services/BLM_Natl_NLCS_Wilderness_Study_Areas_Polygons/FeatureServer/3
    layer BLM Natl NLCS Wilderness Study Areas Polygons
    811 features in 5-state envelope; downloaded 2026-10-05T16:46:28Z
* BLM Natl NLCS Wilderness Areas Polygons  [Feature Service]
    used for: wilderness (a), with PAD-US
    hub item 4a73ec244ee84bc3aea00592fdc7b835  item modified 2026-08-14  data last edit 2026-08-14
    https://services1.arcgis.com/KbxwQRRfWyEYLgp4/arcgis/rest/services/BLM_Natl_NLCS_Wilderness_Areas_Polygons/FeatureServer/2
    layer BLM Natl NLCS Wilderness Areas Polygons
    273 features in 5-state envelope; downloaded 2026-10-05T16:46:46Z
* BLM Natl NLCS National Monuments National Conservation Areas Polygons  [Feature Service]
    used for: BLM National Monument / NCA (d)
    hub item 8defb341035545ed9560effea919484e  item modified 2026-08-06  data last edit 2026-08-06
    https://services1.arcgis.com/KbxwQRRfWyEYLgp4/arcgis/rest/services/BLM_Natl_NLCS_National_Monuments_National_Conservation_Areas_Polygons/FeatureServer/0
    layer BLM Natl NLCS National Monuments National Conservation Areas Polygons
    42 features in 5-state envelope; downloaded 2026-10-05T16:46:58Z
* BLM National SMA Surface Management Agency Area Polygons  [File Geodatabase]
    used for: BLM (g), USFS (f), state (e), tribal/BIA (c)
    hub item 6bf2e737c59d4111be92420ee5ab0b46  item modified 2026-06-30
    https://www.arcgis.com/sharing/rest/content/items/6bf2e737c59d4111be92420ee5ab0b46/data
    layer SMA_WM.gdb/SurfaceManagementAgency
    364 features in 5-state envelope; downloaded 2026-10-05T16:46:27Z
* USGS GNIS Domestic Names (state text files)
    version: published 20260929
    used for: POIs: Falls, Arch, Pillar, hot/warm Spring, Crater, Lava, Glacier, volcanic Summit
    https://prd-tnm.s3.amazonaws.com/StagedProducts/GeographicNames/DomesticNames/DomesticNames_<ST>_Text.zip
    downloaded 2026-10-06T00:40:09Z
* OpenStreetMap data via the Overture Maps base theme (water / land / infrastructure)
    version: Overture release 2026-09-23.1
    used for: POIs: waterfalls, hot springs, volcanic, glaciers, viewpoints
    license: ODbL 1.0 - (c) OpenStreetMap contributors
    https://overturemaps-us-west-2.s3.amazonaws.com/release/2026-09-23.1/theme=base/
    downloaded 2026-10-06T01:43:28Z

SOURCE HANDLING NOTES
---------------------
* Wilderness (a) = PAD-US 4.1 Designation Des_Tp 'WA' (BLM, USFS, NPS, FWS)
  unioned with the current BLM NLCS wilderness layer.
* WSA (b) = BLM NLCS WSA layer, minus 114 Utah features named 'Inholding'
  (state/private sections inside WSAs per SMA; the WSA polygons are holed
  there), plus 11 USFS WSAs that only PAD-US carries:
    Antelope, Bunk Robinson, Fandango, Guadalupe Escarpment
    Hell Hole, Lower San Francisco, Molas Pass, Morey Peak
    Mount Graham, Mount Stirling, Whitmire Canyon
  PAD-US 'WSA' rows that are NPS/FWS proposed or recommended wilderness
  are not used.
* Tribal (c) = SMA ADMIN_AGENCY_CODE 'BIA'.
* State (e) = SMA 'ST'. SMA does not split state trust land from other
  state land, so this also includes state parks, wildlife areas and
  sovereign lake/river beds - all call-first.
* USFS (f) = SMA 'USFS'; BLM (g) = SMA 'BLM'.
* The national SMA geodatabase's spatial index returns nothing for a bbox
  query in GDAL, so features are filtered on bounds after a full read.

PROCESSING
----------
1. fetch_sources.py    download sources, record URLs / versions / dates
2. normalize_sources.py  map each source onto themes (wilderness, wsa, tribal,
   nm_nca, state_trust, usfs, blm)
3. build_classes.py    reproject to EPSG:3857, clip to the union of the five
   TIGER 2025 state polygons, then per 50 km cell: class = theme minus every
   higher-priority class (snap-rounded GEOS overlay, 1 cm grid)
4. make_mbtiles.py     256 px palette-PNG tiles z6-13; fill + 1 px outline from
   the class raster; empty tiles skipped; identical tiles stored once
   (map/images tables + tiles view)
5. validate.py         previews, spot checks, overlap check, 2019 designation
   currency check, state/private-inside-no-land tally

VALIDATION
----------
  PASS  Labyrinth Canyon Wilderness (UT)  (expected class a)
        area share by class: a 100.0%
        tile pixel at [-110.09375, 38.62991]: SW_NoLand:a (z13)
  PASS  Muddy Creek Wilderness (UT)  (expected class a)
        area share by class: a 100.0%
        tile pixel at [-110.97445, 38.64368]: SW_NoLand:a (z13)
  PASS  Dirty Devil WSA (UT)  (expected class b)
        area share by class: b 100.0%
        tile pixel at [-110.45541, 38.29494]: SW_NoLand:b (z13)
  PASS  Organ Mountains-Desert Peaks NM (NM)  (expected class d)
        area share by class: d 53.9%, a 46.1%
        tile pixel at [-107.13399, 32.46382]: SW_NoLand:d (z13)
  PASS  Navajo Nation (AZ/NM)  (expected class c)
        area share by class: c 97.6%, e 0.5%, g 0.1%
        tile pixel at [-109.92398, 36.19368]: SW_NoLand:c (z13)
        note: 'tribal' source has no unit names; area taken from Census TIGER 2025 'Navajo Nation Reservation'
  2019 Dingell Act wilderness (Emery Co. UT, NM) found in wilderness source: 29 of 29
  WSA layer: 712 features; 1 overlap designated wilderness by >5% (wilderness wins there)
        South Fork Owyhee River: 5% now wilderness
  State (SMA ST) / private (SMA PVT) land inside no-land classes, km2
  (inholdings enclosed by a designation boundary; priority paints them
  with the designation):
        a: state 165.5   private 118.3
        b: state 41.1   private 34.3
        c: state 0.0   private 0.0
        d: state 954.8   private 545.7
  Overlap between classes: 1324.3 m2 (Mercator) across 40,326 touching pairs - snap-rounding noise only

POINTS OF INTEREST
------------------
Waypoint IDs: WF waterfall, AR arch, HD hoodoo/pillar, HS hot spring,
VC crater/volcanic, GL glacier, VP viewpoint + 4 digits. Icon centre colour =
category; icon ring colour = landing class at the point (grey = NPS, DoD,
private or other). Tap a POI for: land-here status, nearest landable BLM
(distance, true bearing, ground elevation, position), POI ground elevation
(USGS 3DEP), and flags (NPS 36 CFR 2.17, AC 91-36D 2,000 ft AGL request,
Grand Canyon SFRA, DoD land).
  category      total  landable  call1st  no-land  no-go  other  median nm to landable / share within 2 nm
  arch            441        70       29      170    149     23    2.8 nm / 46%
  glacier          22         0        2       17      1      2    6.7 nm / 5%
  hoodoo          508        78       78      129    114    109    3.0 nm / 48%
  hot_spring      332        50       40       34     18    190    0.7 nm / 68%
  viewpoint      1175       199      253      185    301    237    3.9 nm / 45%
  volcanic        125        16       49       23     15     22    6.4 nm / 26%
  waterfall       303        14       95      114     30     50    5.1 nm / 23%
  by source: {'GNIS': 1287, 'GNIS+OSM': 168, 'OSM': 1451}
  Archaeological / cultural sites are deliberately not included.

CAVEATS
-------
* Advisory planning aid only. SMA shows the managing agency, not legal survey
  boundaries; check the agency, NOTAMs/TFRs, closures and landowner permission.
* 'Landable' means BLM surface outside the no-land/call-first classes. It does
  not account for ACEC or route closures, mining claims, grazing
  improvements, or unmapped private inholdings.
* Tribal land requires tribal permission; state trust land requires the state
  land office's permission; USFS non-wilderness: check forest orders.
