@echo off
chcp 65001 >nul
REM ==============================================================
REM  Farmhut gateway launcher (Windows test)
REM   - Loads FARMHUT_SECRET from session or saved user env (registry);
REM     if none, asks once and saves it.
REM   - Runs passive_gateway.py (continuous). Ctrl+C to stop.
REM   - (On Raspberry Pi use systemd / farmhut-gateway.service instead.)
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
set "SCRIPT=%~dp0passive_gateway.py"
"%PY%" "%SCRIPT%" %*

endlocal
