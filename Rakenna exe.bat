@echo off
chcp 65001 >nul
title Rakennetaan exet
cd /d "%~dp0"

echo.
echo   EXEN RAKENTAMINEN
echo   -----------------
echo.
echo   Rakennetaan kaksi ohjelmaa:
echo.
echo     Hankekalenteri.exe    Avaa kalenterin ja hakee tuoreet tiedot.
echo                           Tama on se, jota klikataan.
echo.
echo     funding_watcher.exe   Pelkka haku ilman kayttoliittymaa.
echo                           Ajastettua taustaajoa varten.
echo.
echo   Kumpikaan ei tarvita Pythonia toimiakseen. Rakentaminen
echo   kestaa pari minuuttia ja vaatii verkkoyhteyden kerran.
echo.
pause

call :etsi_python
if not defined PY goto :ei_pythonia

echo.
echo   [1/4] Asennetaan PyInstaller...
%PY% -m pip install --upgrade --quiet pyinstaller
if errorlevel 1 goto :pip_virhe

echo   [2/4] Kaannetaan Hankekalenteri.exe...
%PY% -m PyInstaller --onefile --console --clean --noconfirm ^
    --name Hankekalenteri ^
    --hidden-import funding_watcher ^
    --hidden-import email.message ^
    hankekalenteri.py
if errorlevel 1 goto :kaannos_virhe

echo   [3/4] Kaannetaan funding_watcher.exe...
%PY% -m PyInstaller --onefile --console --clean --noconfirm ^
    --name funding_watcher ^
    --hidden-import email.message ^
    funding_watcher.py
if errorlevel 1 goto :kaannos_virhe

echo   [4/4] Siirretaan exet kansion juureen ja siivotaan...
copy /y "dist\Hankekalenteri.exe" "Hankekalenteri.exe" >nul
copy /y "dist\funding_watcher.exe" "funding_watcher.exe" >nul
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
del /q Hankekalenteri.spec 2>nul
del /q funding_watcher.spec 2>nul

echo.
echo   VALMIS
echo.
echo   Kaksoisklikkaa Hankekalenteri.exe - kalenteri aukeaa selaimeen
echo   ja hakee tuoreimmat rahoitushaut taustalla.
echo.
echo   TOISELLE KONEELLE: kopioi KOKO tama kansio, ei pelkkaa exea.
echo   Exe lukee vierestaan tiedostot index.html, sources.json,
echo   funding_data.json ja .env, eika toimi ilman niita.
echo.
echo   Huom: virustorjunta saattaa merkita PyInstaller-exen
echo   epailyttavaksi. Se on tunnettu vaara halytys. Jos exe
echo   poistetaan automaattisesti, kayta .bat-tiedostoja.
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


:pip_virhe
echo.
echo   PyInstallerin asennus epaonnistui.
echo   Tarkista verkkoyhteys. Yrityksen koneella proxy voi
echo   estaa pip-asennukset.
echo.
pause
exit /b 1


:kaannos_virhe
echo.
echo   Kaannos epaonnistui. Ylla nakyy virheilmoitus.
echo   Voit kayttaa .bat-tiedostoja ilman exeja normaalisti.
echo.
pause
exit /b 1


:ei_pythonia
echo   Pythonia ei loytynyt. Asenna se ensin:
echo     winget install Python.Python.3.12
echo.
pause
exit /b 1
