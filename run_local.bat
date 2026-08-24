@echo off
SET GIT="C:\Program Files\Git\mingw64\bin\git.exe"
setlocal
cd /d "D:\raa-veille"

set PYTHON=C:\Users\jdepelchin\AppData\Local\miniconda3\python.exe

echo.
echo === Veille Prefectorale - demarrage ===
echo.

:: ── 1. Scraping + telechargement ─────────────────────────────────────────────
echo [1/4] Scraping et telechargement des PDFs...
echo.

"%PYTHON%" scraper.py 14jours --download
if errorlevel 1 (
    echo.
    echo ERREUR : scraper.py a echoue ^(voir les logs ci-dessus^).
    goto :fin
)

:: ── 2. Analyse Claude ─────────────────────────────────────────────────────────
:: main.py decide lui-meme s'il y a du nouveau a analyser (via pdfs_nouveaux.txt,
:: avec repli sur un scan complet de pdfs_downloaded/ contre pdfs_analyses.txt).
:: Ne pas se fier a un comptage de fichiers cote .bat : peu fiable avec les noms
:: accentues du dossier, et ca peut faire sauter l'analyse en silence.
echo.
echo [2/4] Lancement de l'analyse Claude...
echo.
"%PYTHON%" main.py
if errorlevel 1 (
    echo.
    echo ERREUR : main.py a echoue ^(voir les logs ci-dessus^).
    goto :fin
)

:: ── 3. Git ───────────────────────────────────────────────────────────────────
echo.
echo [3/4] Mise a jour Git...

%GIT% add data/
%GIT% diff --cached --quiet
if errorlevel 1 (
    %GIT% commit -m "maj RAA %date%"
) else (
    echo    Rien a commiter dans data/.
)

:: ── 4. Push ──────────────────────────────────────────────────────────────────
echo.
echo [4/4] Git push...
%GIT% push
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
