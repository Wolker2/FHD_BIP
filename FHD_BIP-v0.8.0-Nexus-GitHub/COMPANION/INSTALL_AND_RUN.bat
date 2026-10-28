@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python 3.11 or newer is required for the first launch.
  echo Install 64-bit Python from python.org and enable the Python Launcher.
  pause
  exit /b 1
)

py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)"
if errorlevel 1 (
  echo ERROR: Python 3.11 or newer was not found.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\pythonw.exe" (
  echo Creating the private FHD_BIP environment...
  py -3 -m venv .venv || goto :failed
)

if not exist ".venv\.fhd_bip_runtime_0_8_0" (
  echo Installing FHD_BIP components...
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements-runtime.txt || goto :failed
  type nul > ".venv\.fhd_bip_runtime_0_8_0"
)

set "PYTHONPATH=%CD%\src"
start "FHD_BIP" ".venv\Scripts\pythonw.exe" -m fhd_bip
exit /b 0

:failed
echo.
echo FHD_BIP installation failed. Check the Internet connection and try again.
pause
exit /b 1
