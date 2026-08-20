@echo off
setlocal
cd /d "%~dp0"

where codex >nul 2>nul
if errorlevel 1 (
  echo Codex CLI was not found.
  echo Install it from https://developers.openai.com/codex and run: codex login
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating the local Python environment...
  where py >nul 2>nul
  if errorlevel 1 (
    python -m venv .venv
  ) else (
    py -3 -m venv .venv
  )
  if errorlevel 1 goto :error
)

.venv\Scripts\python.exe -c "import flask, openai_codex" >nul 2>nul
if errorlevel 1 (
  echo Installing Codex Bots dependencies...
  .venv\Scripts\python.exe -m pip install -r requirements.txt
  if errorlevel 1 goto :error
)

set CODEX_BOTS_OPEN_BROWSER=1
echo Starting Codex Bots at http://127.0.0.1:5055
echo Press Control-C to stop.
.venv\Scripts\python.exe app.py
exit /b %errorlevel%

:error
echo Setup failed. Review the error above and try again.
pause
exit /b 1
