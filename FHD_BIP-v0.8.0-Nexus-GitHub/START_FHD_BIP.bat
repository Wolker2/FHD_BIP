@echo off
setlocal
cd /d "%~dp0"

if exist "COMPANION\FHD_BIP_v0.8.0.exe" (
  start "FHD_BIP" "COMPANION\FHD_BIP_v0.8.0.exe"
  exit /b 0
)

if not exist "COMPANION\INSTALL_AND_RUN.bat" (
  echo ERROR: COMPANION\INSTALL_AND_RUN.bat not found.
  echo Reinstall FHD_BIP into Mods\FHD_MODS\FHD_BIP\
  pause
  exit /b 1
)

call "COMPANION\INSTALL_AND_RUN.bat"
