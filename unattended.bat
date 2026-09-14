@echo off
rem ===========================================================================
rem  Houdini Lab : unattended mode (double-click to start)
rem
rem  ASCII only on purpose. A .bat is read with the console code page (cp932 on
rem  Japanese Windows), so Japanese text saved as UTF-8 comes out as mojibake.
rem  All Japanese messages are printed by Python instead.
rem ===========================================================================
setlocal
cd /d "%~dp0"

set HIP=scenes\vellum_cloth.hip
set COUNT=6
set HOURS=4

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   .venv\Scripts\python.exe not found.
    echo   Run this from the houdini-lab folder.
    echo.
    pause
    exit /b 1
)

:menu
cls
echo ============================================================
echo   Houdini Lab : unattended mode
echo ============================================================
echo.
echo     scene : %HIP%
echo     count : %COUNT% candidates to screen
echo     limit : %HOURS% hours
echo     log   : %CD%\unattended.log
echo.
echo     [1]  dry-run  - show what would run (Houdini stays closed)
echo     [2]  run      - screen, auto-approve, shoot, draft
echo     [Q]  quit
echo.

choice /c 12Q /n /m "     select : "
if errorlevel 3 exit /b 0
if errorlevel 2 goto run
if errorlevel 1 goto dry

:dry
echo.
.venv\Scripts\python.exe tools\unattended.py --hip %HIP% --count %COUNT% --hours %HOURS% --dry-run
echo.
pause
goto menu

:run
echo.
echo   Houdini will open and minimize itself. Do NOT touch it.
echo   Stop serve.py first if you can (it may lock media/).
echo.
pause
echo.
.venv\Scripts\python.exe tools\unattended.py --hip %HIP% --count %COUNT% --hours %HOURS% --log
echo.
echo ============================================================
echo   finished. log : %CD%\unattended.log
echo   next : start serve.py and open /houdini-lab/watch/
echo ============================================================
pause
