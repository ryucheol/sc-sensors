@echo off
chcp 65001 >nul
REM ==============================================================
REM  Farmhut HISTORY sync launcher
REM   - First run: asks for SECRET, saves it (setx) for next time.
REM   - Next runs (new terminal): no prompt, uses saved FARMHUT_SECRET.
REM   - Python retries failed sensors only.
REM   - No secret stored in this file (saved to user env) -> safe to commit.
REM  Usage:  run_sync.bat            (upload)
REM          run_sync.bat --dry-run  (preview only)
REM ==============================================================
setlocal EnableDelayedExpansion

if not defined FARMHUT_SECRET (
  set /p "FARMHUT_SECRET=Enter SECRET: "
  setx FARMHUT_SECRET "!FARMHUT_SECRET!" >nul
  echo Saved. Next new terminal will not ask.
)

set "PY=C:\Users\ryuch\AppData\Local\Programs\Python\Python39\python.exe"
set "SCRIPT=%~dp0farmhut_sync.py"

"%PY%" "%SCRIPT%" %*
set "rc=%errorlevel%"

REM pass python exit code out (for Task Scheduler success/fail checks)
endlocal & exit /b %rc%
