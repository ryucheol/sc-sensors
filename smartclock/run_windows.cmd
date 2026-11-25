@echo off
rem 윈도우에서 손으로 한 번 실행해 보는 용도(창이 뜸). 자동 실행은 README의 schtasks(pyw, 창 없음) 참고.
cd /d "%~dp0"
py -3 smartclock_push.py --farm-url %*
pause
