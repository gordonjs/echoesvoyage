@echo off
REM ============================================================
REM  Parcel RTK - local launcher for Windows
REM  Serves the app over http://localhost so the browser's
REM  Web Serial (USB) feature is allowed, then opens it.
REM ============================================================
setlocal
set PORT=8000
cd /d "%~dp0"

echo Starting local server on http://localhost:%PORT%/ ...
start "" "http://localhost:%PORT%/index.html"

REM Try the Windows Python launcher first, then python on PATH.
where py >nul 2>nul && (py -m http.server %PORT% & goto :eof)
where python >nul 2>nul && (python -m http.server %PORT% & goto :eof)

echo.
echo Python was not found. Install it from https://www.python.org/ (check "Add to PATH"),
echo or just open index.html directly in Chrome/Edge ^(USB mode may be blocked on file:// ^).
pause
