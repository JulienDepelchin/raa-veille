@echo off
SET PATH=%PATH%;C:\Program Files\Git\bin;C:\Program Files\Git\cmd
setlocal
cd /d "D:\raa-veille"

set PYTHON=C:\Users\jdepelchin\AppData\Local\Programs\Python\Python312\python.exe

echo.
echo === Veille Prefectorale - demarrage ===
echo.

:: ── 1. Scraping + telechargement ─────────────────────────────────────────────
echo [1/4] Scraping et telechargement des PDFs...
echo.

:: Compter les PDFs avant pour savoir si de nouveaux ont ete telecharges
for /f %%c in ('dir /b /a-d "pdfs_downloaded\*.pdf" 2^>nul ^| find /c /v ""') do set NB_AVANT=%%c
if not defined NB_AVANT set NB_AVANT=0

"%PYTHON%" scraper.py 14jours --download
if errorlevel 1 (
    echo.
    echo ERREUR : scraper.py a echoue ^(voir les logs ci-dessus^).
    goto :fin
)

for /f %%c in ('dir /b /a-d "pdfs_downloaded\*.pdf" 2^>nul ^| find /c /v ""') do set NB_APRES=%%c
if not defined NB_APRES set NB_APRES=0

set /a NB_NOUVEAUX=%NB_APRES%-%NB_AVANT%
echo.
echo    PDFs avant : %NB_AVANT%  ^|  apres : %NB_APRES%  ^|  nouveaux : %NB_NOUVEAUX%

:: ── 2. Analyse Claude (seulement si nouveaux PDFs) ───────────────────────────
echo.
if %NB_NOUVEAUX% GTR 0 (
    echo [2/4] %NB_NOUVEAUX% nouveau^(x^) PDF^(s^) - lancement de l'analyse Claude...
    echo.
    "%PYTHON%" main.py
    if errorlevel 1 (
        echo.
        echo ERREUR : main.py a echoue ^(voir les logs ci-dessus^).
        goto :fin
    )
) else (
    echo [2/4] Aucun nouveau PDF - analyse Claude ignoree.
)

:: ── 3. Git ───────────────────────────────────────────────────────────────────
echo.
echo [3/4] Mise a jour Git...

git add data/
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "maj RAA %date%"
) else (
    echo    Rien a commiter dans data/.
)

:: ── 4. Push ──────────────────────────────────────────────────────────────────
echo.
echo [4/4] Git push...
git push
if errorlevel 1 (
    echo.
    echo ERREUR : git push a echoue.
    goto :fin
)

:fin
echo.
echo === Termine ===
echo.
pause
endlocal
