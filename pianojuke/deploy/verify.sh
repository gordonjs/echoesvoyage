#!/usr/bin/env bash
# Check a running PianoJuke with curl.
#
#   deploy/verify.sh [URL]                 read-only checks; the piano stays silent
#   deploy/verify.sh --with-sound [URL]    also plays, queues, skips, stops and runs
#                                          rotation for a few seconds at volume 30
#   deploy/verify.sh --with-restart [URL]  with --with-sound: restart the service with
#                                          rotation on and check that it resumes
#
# URL defaults to http://127.0.0.1:8088. Exit status is the number of failures.
set -uo pipefail

URL="http://127.0.0.1:8088"
SOUND=0
RESTART=0
for arg in "$@"; do
  case "$arg" in
    --with-sound) SOUND=1 ;;
    --with-restart) SOUND=1; RESTART=1 ;;
    http*) URL="${arg%/}" ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 64 ;;
  esac
done
PY="${PYTHON:-python3}"
SERVICE="${PIANOJUKE_SERVICE:-pianojuke}"
passed=0
failed=0

pass() { printf '  \033[32mPASS\033[0m %s\n' "$1"; passed=$((passed + 1)); }
fail() {
  printf '  \033[31mFAIL\033[0m %s\n' "$1"
  [ -n "${2:-}" ] && printf '       %s\n' "$2"
  failed=$((failed + 1))
}
warn() { printf '  \033[33mWARN\033[0m %s\n' "$1"; }

# request METHOD PATH [JSON]  -> prints the body; non-zero on HTTP errors
request() {
  local body="${3:-}"
  [ -n "$body" ] || body='{}'
  if [ "$1" = GET ]; then
    curl -fsS -m 15 "$URL$2"
  else
    curl -fsS -m 15 -X "$1" -H 'Content-Type: application/json' -d "$body" "$URL$2"
  fi
}

# json BODY EXPR -> evaluates EXPR with the decoded body as d
json() { "$PY" -c 'import json, sys; d = json.loads(sys.argv[1]); print(eval(sys.argv[2]))' "$1" "$2"; }

# check NAME METHOD PATH JSON EXPR -> PASS if EXPR is truthy for the response
check() {
  local out
  if ! out="$(request "$2" "$3" "$4" 2>&1)"; then
    fail "$1" "$2 $3: $out"
    return 1
  fi
  if "$PY" -c 'import json, sys; d = json.loads(sys.argv[1]); sys.exit(0 if eval(sys.argv[2]) else 1)' "$out" "$5" 2>/dev/null; then
    pass "$1"
    LAST="$out"
  else
    fail "$1" "$2 $3 -> $(head -c 400 <<<"$out")"
    return 1
  fi
}

wait_up() {
  for _ in $(seq 1 40); do
    curl -fsS -m 2 "$URL/api/nowplaying" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

echo "PianoJuke at $URL"
if ! wait_up; then
  fail "service answers on $URL" "no response after 40 s"
  exit 1
fi

# --- read-only checks
if page="$(curl -fsS -m 10 "$URL/")" && grep -q '<title>PianoJuke</title>' <<<"$page"; then
  pass "tablet UI page"
else
  fail "tablet UI page" "GET / did not return the PianoJuke page"
fi
for asset in /static/app.js /static/app.css /static/icons/icon-512.png /sw.js; do
  if curl -fsS -m 10 -o /dev/null "$URL$asset"; then pass "asset $asset"; else fail "asset $asset"; fi
done
check "PWA manifest (fullscreen, landscape)" GET /manifest.webmanifest '' \
  'd["display"] == "fullscreen" and d["orientation"] == "landscape" and len(d["icons"]) >= 2'
check "now-playing status" GET /api/nowplaying '' \
  'd["state"] in ("idle", "playing", "gap", "quiet") and "rotation" in d and "volume" in d'
status="$LAST"
if [ "$(json "$status" 'd["midi"]["connected"]')" = True ]; then
  pass "MIDI output: $(json "$status" 'd["midi"]["port"]')"
  [ "$(json "$status" 'd["midi"]["fallback"]')" = True ] && warn "expected port not found; using the first non-Through port"
else
  fail "MIDI output connected" "$(json "$status" 'd["midi"]["error"]')"
fi
check "library has pieces" GET /api/library '' 'd["count"] > 0 and d["tracks"][0]["composer"]'
library="$LAST"
echo "       $(json "$library" 'd["count"]') pieces by $(json "$library" 'len(d["composers"])') composers"
check "rotation status" GET /api/rotation '' 'isinstance(d["enabled"], bool) and d["total"] > 0'
check "volume" GET /api/volume '' '0 <= d["volume"] <= 100'
check "queue" GET /api/queue '' 'isinstance(d["queue"], list) and isinstance(d["history"], list)'
check "settings" GET /api/settings '' '"gap_seconds" in d["settings"]'
gap="$(json "$LAST" 'd["settings"]["gap_seconds"]')"
check "settings save (same value back)" POST /api/settings "{\"gap_seconds\": $gap}" \
  "d['settings']['gap_seconds'] == $gap"
if curl -s -m 10 -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' \
     -d '{"gap_seconds": -5}' "$URL/api/settings" | grep -q '^400$'; then
  pass "invalid setting answered with 400"
else
  fail "invalid setting answered with 400"
fi
if [ "$(json "$status" 'd["state"]')" = playing ]; then
  warn "something is playing now; leaving it alone"
fi

# --- checks that make sound
if [ "$SOUND" = 1 ]; then
  echo "Sound checks: the piano will play for about 20 seconds at volume 30."
  was_rotation="$(json "$status" 'd["rotation"]["enabled"]')"
  was_volume="$(json "$status" 'd["volume"]')"
  ids="$(json "$library" '"\n".join(json.dumps({"id": t["id"]}) for t in d["tracks"] if not t["never"])')"
  first="$(sed -n 1p <<<"$ids")"
  second="$(sed -n 2p <<<"$ids")"
  [ -n "$second" ] || second="$first"
  second_repr="$(json "$second" 'repr(d["id"])')"

  check "volume 30" POST /api/volume '{"volume": 30}' 'd["volume"] == 30'
  check "play a piece" POST /api/play "$first" 'd["state"] == "playing" and d["track"]'
  sleep 4
  check "it is playing" GET /api/nowplaying '' 'd["state"] == "playing" and d["running"] and d["elapsed"] > 1'
  check "queue a second piece" POST /api/queue "$second" 'len(d["queue"]) == 1'
  check "skip goes to the queued piece" POST /api/skip '' "d['track']['id'] == $second_repr"
  sleep 3
  check "stop" POST /api/stop '' 'd["state"] == "idle" and not d["rotation"]["enabled"]'
  check "rotation on" POST /api/rotation/on '' 'd["rotation"]["enabled"] and d["state"] in ("playing", "quiet")'
  [ "$(json "$LAST" 'd["state"]')" = quiet ] && warn "quiet hours are on now, so rotation is waiting"
  sleep 4
  if [ "$RESTART" = 1 ]; then
    echo "Restarting $SERVICE with rotation on..."
    sudo systemctl restart "$SERVICE"
    if wait_up; then pass "service back after restart"; else fail "service back after restart"; fi
    check "rotation survived the restart" GET /api/nowplaying '' \
      'd["rotation"]["enabled"] and d["state"] in ("gap", "quiet", "playing")'
  fi
  check "stop again" GET /api/stop '' 'd["state"] == "idle"'
  check "skip with nothing lined up stays quiet" GET /api/skip '' 'd["state"] == "idle"'
  request POST /api/volume "{\"volume\": $was_volume}" >/dev/null && echo "       volume restored to $was_volume"
  if [ "$was_rotation" = True ]; then
    request POST /api/rotation/on >/dev/null && echo "       rotation switched back on"
  fi
fi

echo
if [ "$failed" -eq 0 ]; then
  printf '\033[32mAll %d checks passed.\033[0m\n' "$passed"
else
  printf '\033[31m%d of %d checks failed.\033[0m\n' "$failed" "$((passed + failed))"
fi
exit "$failed"
