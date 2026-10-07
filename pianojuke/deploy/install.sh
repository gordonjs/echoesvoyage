#!/usr/bin/env bash
# Install PianoJuke on the Pi over the running version, keeping a backup.
#
#   deploy/install.sh              check, back up, install, restart, verify with curl;
#                                  puts the old version back if verification fails
#   deploy/install.sh --check      run the checks only; changes nothing
#   deploy/install.sh --force      install even if routes the old version served
#                                  (Home Assistant's endpoints) would disappear
#   deploy/install.sh --rollback   restore the newest backup and restart
#
# Run it as the user that owns ~/pianojuke.py (admin). It uses sudo for systemctl.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE="${PIANOJUKE_SERVICE:-pianojuke}"
URL="${PIANOJUKE_URL:-http://127.0.0.1:8088}"
MODE=install
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --check) MODE=check ;;
    --force) FORCE=1 ;;
    --rollback) MODE=rollback ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 64 ;;
  esac
done

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# Find the script and interpreter systemd actually runs.
exec_start="$(systemctl show -p ExecStart --value "$SERVICE" 2>/dev/null || true)"
argv="$(sed -n 's/.*argv\[\]=\([^;]*\);.*/\1/p' <<<"$exec_start" | head -n 1)"
PY=""
APP=""
for word in $argv; do
  case "$word" in
    *.py) [ -n "$APP" ] || APP="$word" ;;
    *python*) [ -n "$PY" ] || PY="$word" ;;
  esac
done
[ -n "$PY" ] && [ "${PY#/}" = "$PY" ] && PY="$(command -v "$PY" || true)"
PY="${PY:-$(command -v python3)}"
if [ -z "$APP" ]; then
  APP="$HOME/pianojuke.py"
  echo "Could not read the script path from the $SERVICE unit; assuming $APP"
fi
DEST="$(dirname "$APP")"
WEB="$DEST/pianojuke_web"

rollback() {
  local backup
  backup="$(ls -1d "$DEST"/pianojuke-backup-* 2>/dev/null | tail -n 1 || true)"
  [ -n "$backup" ] || die "no backup found in $DEST"
  say "Restoring $backup"
  if [ -f "$backup/$(basename "$APP")" ]; then
    cp -a "$backup/$(basename "$APP")" "$APP"
  else
    echo "That backup has no $(basename "$APP") (it was a fresh install); leaving the new one."
  fi
  rm -rf "$WEB"
  [ -d "$backup/pianojuke_web" ] && cp -a "$backup/pianojuke_web" "$WEB"
  sudo systemctl restart "$SERVICE"
  say "Restored. $SERVICE restarted."
}

if [ "$MODE" = rollback ]; then
  rollback
  exit 0
fi

say "$SERVICE runs $APP with $PY"

say "Checking Python packages"
"$PY" -c 'import flask, mido' || die "$PY cannot import flask and mido"
"$PY" -c 'import rtmidi' 2>/dev/null || echo "   note: python-rtmidi not importable; fine only if mido uses another backend"

say "Running the test suite against a simulated piano"
if "$PY" -c 'import pytest' 2>/dev/null; then
  (cd "$SRC" && PIANOJUKE_PORT=fake "$PY" -m pytest -q -p no:cacheprovider tests) || die "tests failed; nothing installed"
else
  echo "   pytest is not installed, skipping (sudo apt install python3-pytest to enable)"
fi

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp "$SRC/pianojuke.py" "$STAGE/pianojuke.py"

if [ -f "$APP" ]; then
  say "Comparing HTTP routes with the installed version"
  if ! "$PY" "$SRC/deploy/routecheck.py" --python "$PY" "$APP" "$STAGE/pianojuke.py"; then
    if [ "$FORCE" = 1 ]; then
      echo "   --force given: installing anyway"
    else
      die "the routes above would stop working (Home Assistant may call them). Nothing installed.
       Add them to the new pianojuke.py, or re-run with --force if nothing uses them."
    fi
  fi
  minvel="$("$PY" "$SRC/deploy/routecheck.py" --min-velocity "$APP" || true)"
  if [ -n "$minvel" ]; then
    sed -i "s/^MIN_VELOCITY = [0-9]*/MIN_VELOCITY = $minvel/" "$STAGE/pianojuke.py"
    say "Keeping MIN_VELOCITY = $minvel from the installed version"
  else
    echo "   the installed version has no MIN_VELOCITY constant; using $(sed -n 's/^MIN_VELOCITY = \([0-9]*\).*/\1/p' "$STAGE/pianojuke.py")"
  fi
else
  say "No $APP yet: fresh install"
fi
"$PY" -m py_compile "$STAGE/pianojuke.py"
PIANOJUKE_PORT=none "$PY" "$STAGE/pianojuke.py" --routes >/dev/null || die "the new pianojuke.py does not start"

if [ "$MODE" = check ]; then
  say "Checks passed; nothing changed (--check)"
  exit 0
fi

BACKUP="$DEST/pianojuke-backup-$(date +%Y%m%d-%H%M%S)"
say "Backing up to $BACKUP"
mkdir -p "$BACKUP"
[ -f "$APP" ] && cp -a "$APP" "$BACKUP/"
[ -d "$WEB" ] && cp -a "$WEB" "$BACKUP/"
[ -f "$DEST/pianojuke_state.json" ] && cp -a "$DEST/pianojuke_state.json" "$BACKUP/"

say "Installing"
install -m 755 "$STAGE/pianojuke.py" "$APP"
rm -rf "$WEB.new"
cp -r "$SRC/pianojuke_web" "$WEB.new"
rm -rf "$WEB"
mv "$WEB.new" "$WEB"

say "Restarting $SERVICE"
sudo systemctl restart "$SERVICE"
sleep 2
if ! systemctl is-active --quiet "$SERVICE"; then
  sudo journalctl -u "$SERVICE" -n 40 --no-pager || true
  rollback
  die "$SERVICE did not stay up; the old version is back"
fi

say "Verifying with curl"
if ! PYTHON="$PY" "$SRC/deploy/verify.sh" "$URL"; then
  sudo journalctl -u "$SERVICE" -n 40 --no-pager || true
  rollback
  die "verification failed; the old version is back"
fi

say "Installed. Open $URL from the tablet (use the Pi's address, e.g. http://10.0.60.75:8088)."
echo "   To hear it work end to end:  $SRC/deploy/verify.sh --with-sound --with-restart"
echo "   To undo:                     $SRC/deploy/install.sh --rollback"
