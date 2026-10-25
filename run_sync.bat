@echo off
chcp 65001 >nul
REM ==============================================================
REM  Farmhut sensor sync launcher
REM   - If FARMHUT_SECRET is not set, ask for it, then run.
REM   - Python does the retry (failed sensors only). No loop here.
REM   - No secret stored in this file  ->  safe to commit.
REM  Usage:  run_sync.bat            (upload)
REM          run_sync.bat --dry-run  (preview only)
REM  Tip:  to skip the prompt, set it once (persists):
REM          setx FARMHUT_SECRET your-secret
REM ==============================================================
setlocal

if not defined FARMHUT_SECRET (
  set /p "FARMHUT_SECRET=Enter SECRET (same as Code.gs): "
)

set "PY=C:\Users\ryuch\AppData\Local\Programs\Python\Python39\python.exe"
set "SCRIPT=%~dp0farmhut_sync.py"

"%PY%" "%SCRIPT%" %*
set "rc=%errorlevel%"

REM pass python exit code out (for Task Scheduler success/fail checks)
endlocal & exit /b %rc%
