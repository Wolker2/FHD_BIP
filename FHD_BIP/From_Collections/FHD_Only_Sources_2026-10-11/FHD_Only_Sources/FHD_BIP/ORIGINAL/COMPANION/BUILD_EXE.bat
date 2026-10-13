@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul || goto :no_python
if not exist ".build-venv\Scripts\python.exe" py -3 -m venv .build-venv || exit /b 1
".build-venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements-build.txt || exit /b 1
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
".build-venv\Scripts\pyinstaller.exe" --noconfirm --clean --onefile --windowed --name FHD_BIP_v0.8.0 --paths src launcher.py || exit /b 1
copy /y "dist\FHD_BIP_v0.8.0.exe" "FHD_BIP_v0.8.0.exe" >nul || exit /b 1
echo READY: %CD%\FHD_BIP_v0.8.0.exe
pause
exit /b 0

:no_python
echo ERROR: Python 3.11 or newer is required.
pause
exit /b 1
