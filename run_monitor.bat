@echo off
chcp 65001 >nul
REM ==============================================================
REM  Farmhut gateway - MONITOR (read-only) mode
REM   - Shows beacon receive logs only.
REM   - No server upload, no sensor connection.
REM   - Safe to run alongside the real gateway (passive is broadcast).
REM   - No SECRET needed.
REM ==============================================================
setlocal
set "GW_MONITOR=1"
set "PY=C:\Users\ryuch\AppData\Local\Programs\Python\Python39\python.exe"
set "SCRIPT=%~dp0passive_gateway.py"
"%PY%" "%SCRIPT%" %*
endlocal
