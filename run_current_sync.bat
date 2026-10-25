@echo off
chcp 65001 >nul
REM ==============================================================
REM  Farmhut CURRENT-value sync launcher (lightweight)
REM   - Uses FARMHUT_SECRET. If not set, loads saved value from
REM     user env (registry); if none, asks once and saves it.
REM   - Works even in the same terminal (reads registry directly).
REM   - Reads current values only (no history) -> fast.
REM  Usage:  run_current_sync.bat            (update current tab)
REM          run_current_sync.bat --dry-run  (read + time only)
REM ==============================================================
setlocal

if defined FARMHUT_SECRET goto run
for /f "tokens=2,*" %%A in ('reg query "HKCU\Environment" /v FARMHUT_SECRET 2^>nul') do set "FARMHUT_SECRET=%%B"
if defined FARMHUT_SECRET goto run
set /p "FARMHUT_SECRET=Enter SECRET: "
setx FARMHUT_SECRET "%FARMHUT_SECRET%" >nul
echo Saved. Future runs will not ask.

:run
set "PY=C:\Users\ryuch\AppData\Local\Programs\Python\Python39\python.exe"
set "SCRIPT=%~dp0current_sync.py"
"%PY%" "%SCRIPT%" %*
set "rc=%errorlevel%"
endlocal & exit /b %rc%
