@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv
    if errorlevel 1 python -m venv .venv
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -c "import cryptography" >nul 2>nul
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements-evidence.txt
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" "LiteLLM-Request-Evidence-UI.py"
if errorlevel 1 goto failed
exit /b 0
:failed
echo Could not start LiteLLM Request Evidence. See the README in this folder.
pause
exit /b 1
