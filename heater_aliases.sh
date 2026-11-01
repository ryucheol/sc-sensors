# 농막 히터 제어 별칭 — ~/.bashrc에서 source
alias 난방='python3 ~/smart-cabin/heater_cmd.py 난방'
alias 동파='python3 ~/smart-cabin/heater_cmd.py 동파'
alias heat='python3 ~/smart-cabin/heater_cmd.py 난방'
alias frost='python3 ~/smart-cabin/heater_cmd.py 동파'
alias heater-log='tail -f ~/smart-cabin/heater.log'
alias heater-state='cat ~/smart-cabin/heater_state.json; echo'
