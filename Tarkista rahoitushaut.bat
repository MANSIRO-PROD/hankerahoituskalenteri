@echo off
chcp 65001 >nul
title Tarkistetaan rahoitushakuja
cd /d "%~dp0"

echo.
echo   TARKISTETAAN RAHOITUSHAKUJA
echo   ---------------------------
echo.

REM Jos exe on rakennettu, kaytetaan sita. Muuten Pythonia.
if exist "funding_watcher.exe" (
    funding_watcher.exe --embed
    goto :valmis
)

call :etsi_python
if not defined PY goto :ei_pythonia

%PY% funding_watcher.py --embed

:valmis
echo.
echo   ---------------------------
echo   Valmis. Avaa kalenteri tiedostolla "Kaynnista kalenteri.bat".
echo.
pause
exit /b 0


:etsi_python
set "PY="
where py >nul 2>&1
if not errorlevel 1 (
    py -3 --version >nul 2>&1
    if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
    where python >nul 2>&1
    if not errorlevel 1 set "PY=python"
)
exit /b 0


:ei_pythonia
echo   Pythonia ei loytynyt. Asenna se Microsoft Storesta
echo   tai komennolla:  winget install Python.Python.3.12
echo.
pause
exit /b 1
