@echo off
REM ============================================================
REM  Parcel RTK - standalone launcher for Windows
REM  Serves the app on http://localhost (so the browser allows
REM  USB / Web Serial) and opens it in a clean app window.
REM ============================================================
setlocal
set PORT=8000
set URL=http://localhost:%PORT%/index.html
cd /d "%~dp0"

REM --- find Python (needed for the tiny local web server) ---
set PY=
where py     >nul 2>nul && set PY=py
if not defined PY ( where python >nul 2>nul && set PY=python )
if not defined PY (
  echo.
  echo  Python was not found. Install it once from https://www.python.org/downloads/
  echo  and tick "Add Python to PATH", then run this again.
  echo.
  echo  ^(Or just double-click index.html - USB may still work directly.^)
  echo.
  pause
  exit /b
)

REM --- start the local server in its own minimized window ---
start "Parcel RTK server" /min cmd /c "%PY% -m http.server %PORT%"

REM --- give the server a moment to come up ---
ping -n 2 127.0.0.1 >nul

REM --- open in a chromeless app window (Edge, then Chrome), else default browser ---
set EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe
set CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe
set CHROME86=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe
if exist "%EDGE%"    ( start "" "%EDGE%"    --app=%URL% & goto done )
if exist "%CHROME%"  ( start "" "%CHROME%"  --app=%URL% & goto done )
if exist "%CHROME86%" ( start "" "%CHROME86%" --app=%URL% & goto done )
echo Edge/Chrome not found at the usual paths - opening your default browser.
echo (Use Chrome or Edge for USB support.)
start "" %URL%
:done
exit /b
