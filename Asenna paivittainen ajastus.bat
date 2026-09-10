@echo off
chcp 65001 >nul
title Ajastuksen asennus
cd /d "%~dp0"

set "TEHTAVA=Hankerahoituskalenteri"
set "KELLO=07:00"

echo.
echo   PAIVITTAINEN AJASTUS
echo   --------------------
echo.
echo   Rekisteroi Windowsin Tehtavien ajoitukseen paivittaisen ajon
echo   kello %KELLO%. Ajo tapahtuu taustalla ilman nakyvaa ikkunaa.
echo.
echo   Huom: tama on VALINNAINEN. Hankekalenteri hakee tuoreet tiedot
echo   joka kerta kun se avataan. Ajastusta tarvitaan vain, jos haluat
echo   ilmoituksen myos silloin kun ohjelma ei ole auki.
echo.

REM Onko tehtava jo olemassa?
schtasks /query /tn "%TEHTAVA%" >nul 2>&1
if not errorlevel 1 (
    echo   Tehtava "%TEHTAVA%" on jo olemassa. Korvataan se.
    echo.
)

REM Exe on ensisijainen: se ei tarvitse Pythonia lainkaan.
if exist "%~dp0funding_watcher.exe" (
    set "KOMENTO=\"%~dp0funding_watcher.exe\" --embed --quiet"
    goto :luo
)

REM Muuten pythonw.exe, jotta konsoli-ikkuna ei valahda ruudulla.
call :etsi_pythonw
if not defined PYW goto :ei_pythonia

set "KOMENTO=\"%PYW%\" \"%~dp0funding_watcher.py\" --embed --quiet"

:luo
schtasks /create /tn "%TEHTAVA%" /tr "%KOMENTO%" /sc daily /st %KELLO% /f
if errorlevel 1 goto :virhe

echo.
echo   Valmis. Ajo tapahtuu joka paiva kello %KELLO%.
echo.
echo   Testaa heti:      schtasks /run /tn "%TEHTAVA%"
echo   Poista ajastus:   schtasks /delete /tn "%TEHTAVA%" /f
echo.
echo   Muista tayttaa .env-tiedostoon Discord-webhookin osoite,
echo   muuten ajo paivittaa datan mutta ei laheta ilmoitusta.
echo.
pause
exit /b 0


:etsi_pythonw
set "PYW="
for /f "usebackq delims=" %%i in (`py -3 -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2^>nul`) do set "PYW=%%i"
if defined PYW if exist "%PYW%" exit /b 0
set "PYW="
for /f "usebackq delims=" %%i in (`python -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))" 2^>nul`) do set "PYW=%%i"
if defined PYW if exist "%PYW%" exit /b 0
set "PYW="
exit /b 0


:virhe
echo.
echo   Tehtavan luonti epaonnistui.
echo.
echo   Yleisin syy: kayttooikeudet. Klikkaa tata tiedostoa hiiren
echo   oikealla ja valitse "Suorita jarjestelmanvalvojana".
echo.
pause
exit /b 1


:ei_pythonia
echo   Pythonia ei loytynyt. Asenna Python tai rakenna ensin exe
echo   tiedostolla "Rakenna exe.bat".
echo.
pause
exit /b 1
