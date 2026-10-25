@echo off
chcp 65001 >nul
REM ==============================================================
REM  Farmhut CURRENT-value sync launcher (lightweight)
REM   - First run: asks for SECRET, saves it (setx) for next time.
REM   - Next runs (new terminal): no prompt, uses saved FARMHUT_SECRET.
REM   - Reads current values only (no history) -> fast.
REM   - No secret stored in this file (saved to user env) -> safe to commit.
REM  Usage:  run_current_sync.bat            (update current-value tab)
REM          run_current_sync.bat --dry-run  (read + time only)
REM ==============================================================
setlocal EnableDelayedExpansion

if not defined FARMHUT_SECRET (
  set /p "FARMHUT_SECRET=Enter SECRET: "
  setx FARMHUT_SECRET "!FARMHUT_SECRET!" >nul
  echo Saved. Next new terminal will not ask.
)

set "PY=C:\Users\ryuch\AppData\Local\Programs\Python\Python39\python.exe"
set "SCRIPT=%~dp0current_sync.py"

"%PY%" "%SCRIPT%" %*
set "rc=%errorlevel%"

endlocal & exit /b %rc%
