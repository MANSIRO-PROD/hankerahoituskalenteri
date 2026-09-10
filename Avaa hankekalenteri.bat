@echo off
chcp 65001 >nul
title Hankerahoituskalenteri
cd /d "%~dp0"

REM Jos exe on rakennettu, kaytetaan sita - silloin Pythonia ei tarvita.
if exist "Hankekalenteri.exe" (
    Hankekalenteri.exe %*
    goto :loppu
)

call :etsi_python
if not defined PY goto :ei_pythonia

%PY% hankekalenteri.py %*
if errorlevel 1 goto :virhe

:loppu
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


:virhe
echo.
echo   Ohjelma paattyi virheeseen. Ylla nakyy virheilmoitus.
echo.
pause
exit /b 1


:ei_pythonia
echo.
echo   Pythonia ei loytynyt.
echo.
echo   Vaihtoehto 1: asenna Python
echo     Microsoft Store - hae "Python 3.12" ja asenna
echo     tai komentorivilla:  winget install Python.Python.3.12
echo.
echo   Vaihtoehto 2: rakenna exe koneella jossa Python on
echo     Aja "Rakenna exe.bat" ja kopioi koko kansio tanne.
echo.
echo   Vaihtoehto 3: avaa kalenteri ilman automaattista hakua
echo     Kaksoisklikkaa tiedostoa index.html. Se nayttaa
echo     viimeksi haetut tiedot, muttei paivita niita.
echo.
pause
exit /b 1
