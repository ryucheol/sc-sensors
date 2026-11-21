# 2노드(공기 + 가구) 예열 모델 — 11-22 연구 자료

- `hourly.txt`: 시트 `농막_시간요약` 11-08 09:00 ~ 11-22 06:00(334시간) — 히터 kWh, 1층, 다락, 바닥외부 °C
- `stn.txt`: 같은 시각 부여 ASOS 기온·전운량(시트 `부여_시간별`)
- `fit.py N 태그`: 앞 N시간으로 Ca·Cm·Hao·Ham·Hmo(+날마다 일사 크기) 피팅 → `p_태그.npy` (`334 all`, `279 train`=11-19까지)
- `eval.py 태그`: 예열 6번을 실제 제어처럼(가구 온도 관측기 + 보조 켜는 조건) 예측해 실제와 비교
- `eval_current.py`: 운영 중인 1노드 모델(`heater_control.preheat_lead_hours1`)로 같은 6번 예측
- `cooling_check.py`: 밤 자연 냉각 3·6·12시간 예측 오차(1노드 tau 12h)
- `weather_effect.py`: 바깥 기온·운량이 냉각 속도·유지 전력에 주는 영향

실행: `cd docs/preheat2 && python3 fit.py 334 all && python3 eval.py all` (numpy·scipy 필요, Pi 말고 PC). 결과 요약은 `docs/THERMAL_MODEL.ko.md` §11.
