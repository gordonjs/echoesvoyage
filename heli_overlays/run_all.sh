#!/usr/bin/env bash
# Full rebuild: fetch -> normalize -> classes -> MBTiles z6-12 -> size gate -> z13
# -> previews / spot checks -> README.
#
#   ./run_all.sh                 # authoritative sources (BLM SMA + NLCS + PAD-US)
#   MODE=padus ./run_all.sh      # PAD-US 4.1 only
#   MODE=tnm ./run_all.sh        # pipeline test with USGS NBD (no WSA/state trust)
#   FORCE_Z13=1 ./run_all.sh     # build z13 even if the estimate exceeds 1 GB
set -euo pipefail
cd "$(dirname "$0")"
MODE=${MODE:-authoritative}

case "$MODE" in
  authoritative) SOURCES=tnm,padus,blm ;;
  padus)         SOURCES=tnm,padus ;;
  tnm)           SOURCES=tnm ;;
  *) echo "unknown MODE=$MODE"; exit 2 ;;
esac

python3 fetch_sources.py --only "$SOURCES"
python3 normalize_sources.py --mode "$MODE"
python3 build_classes.py
python3 make_mbtiles.py --zooms 6-12 --fresh

# Stop before z13 if any file is estimated to pass ~1 GB (see size_gate.py).
if ! python3 size_gate.py && [ "${FORCE_Z13:-0}" != 1 ]; then
  echo "z6-12 built; z13 NOT built (size estimate over limit). Re-run with FORCE_Z13=1"
  python3 validate.py
  python3 write_readme.py >/dev/null
  exit 3
fi
python3 make_mbtiles.py --zooms 13-13
python3 validate.py
python3 write_readme.py >/dev/null
ls -la "${HELI_OUTPUT:-../output}"
