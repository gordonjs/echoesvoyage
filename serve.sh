#!/usr/bin/env bash
# Local launcher for macOS / Linux / Android(Termux).
# Serves the app over http://localhost so Web Serial (USB) is allowed.
cd "$(dirname "$0")" || exit 1
PORT="${1:-8000}"
echo "Serving Parcel RTK on http://localhost:${PORT}/index.html"
( sleep 1; (command -v xdg-open >/dev/null && xdg-open "http://localhost:${PORT}/index.html") \
  || (command -v open >/dev/null && open "http://localhost:${PORT}/index.html") ) >/dev/null 2>&1 &
exec python3 -m http.server "$PORT"
