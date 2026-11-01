# 농막 히터 제어 별칭 — ~/.bashrc에서 source
alias 난방='python3 ~/smart-cabin/heater_cmd.py 난방'
alias 동파='python3 ~/smart-cabin/heater_cmd.py 동파'
alias 취침='python3 ~/smart-cabin/heater_cmd.py 취침'
alias heat='python3 ~/smart-cabin/heater_cmd.py 난방'
alias frost='python3 ~/smart-cabin/heater_cmd.py 동파'
alias heater-sleep='python3 ~/smart-cabin/heater_cmd.py 취침'
alias heater-log='tail -f ~/smart-cabin/heater.log'
alias heater-state='cat ~/smart-cabin/heater_state.json; echo'
# 떠나기 전 자가점검(플러그 켜서 실제 전력 확인) — venv python 필요(tinytuya)
alias 히터점검='~/tuya-venv/bin/python ~/smart-cabin/heater_selftest.py'
alias heater-check='~/tuya-venv/bin/python ~/smart-cabin/heater_selftest.py'
# 계측 스냅샷(전력량·전압·고장여부)
alias heater-status='cat ~/smart-cabin/heater_status.json; echo'
