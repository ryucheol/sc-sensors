# Pi를 Matter 온도조절기로 — "헤이 구글, 난방 18도" 설계 (2026-11-13, 설계안)

## 1. 목표와 원칙
- 구글 음성("헤이 구글, 난방 18도로 해 줘")이나 Google Home 앱의 온도조절기 화면으로 **Pi의 난방 모드와 설정온도**를 바꾼다.
- **판단은 계속 Pi가 한다.** 구글은 시트·텔레그램과 같은 "명령 입구" 하나가 늘어날 뿐이다. 플러그는 Pi만 직접 제어한다(데드맨·고장감지 유지).
- 시트·텔레그램과 **같은 명령 경로**(`heater/heater_cmd.py`)를 쓴다. 새 제어 로직은 만들지 않는다.

## 2. 구조
```
"헤이 구글, 난방 18도"
   │ (음성 인식은 구글 클라우드 — 농막 인터넷 필요)
Home Mini(H0A, Matter 허브) ──Matter(IPv6, 같은 Ryufarm 망, 암호화)──▶ Pi: matter-thermostat 서비스(matter.js)
                                                                        │ heater_cmd.py 난방 18  (시트·텔레그램과 같은 잠금)
                                                                        ▼
                                                     heater_state.json ─▶ heater_control.py ─▶ 플러그(Tuya/Shelly)
Pi → 구글 방향: 15초마다 heater_state.json·heater_status.json·latest.json 을 읽어 현재 온도·설정·가열 여부를 Matter 속성으로 갱신
```
네트워크(현재 구조): Pi가 공유기(eth0 10.42.0.1, NM shared)이고, 버팔로가 Ryufarm AP다. Home Mini와 Pi가 같은 L2라서 mDNS와 IPv6 링크로컬로 서로 찾는다.

## 3. Matter 모델
- 기기 종류 **Thermostat(0x0301)**, 엔드포인트 1개. Thermostat 클러스터는 **Heating 기능만** 켠다. 냉방 없음.
- 선택 사항: 엔드포인트 2에 TemperatureSensor로 **외기**(바닥외부)를 보여 준다. 구글에서 "농막 바깥 온도 몇 도야?"가 된다.

| Matter 속성 | Pi 쪽 원천 | 비고 |
|---|---|---|
| LocalTemperature | status `t_ctl`(제어 센서; 취침이면 다락) | 0.01°C 단위 |
| OccupiedHeatingSetpoint | state `on`(난방·취침) | 동파일 때는 마지막 난방 설정을 유지 표시 |
| SystemMode | 난방·취침·도착예약 → **Heat**, 동파 → **Off** | |
| ThermostatRunningState(Heat) | status `relay` | 앱의 "가열 중" 표시 |
| Min/MaxHeatSetpointLimit | 5°C / 25°C | 구글 슬라이더 범위를 제한 |

## 4. 명령 매핑 (구글 → Pi)
| 구글 동작 | Pi 명령 | 비고 |
|---|---|---|
| 설정온도 X로 (Heat) | `난방 X` (ON=X, OFF=X+밴드) | X는 5~25로 자르고 0.5 단위로 반올림 |
| 모드 Heat (온도 없이) | `난방` (기본 23/23.5) | |
| 모드 **Off** ("난방 꺼 줘") | **`동파`** | **히터를 끄는 게 아니다.** 동파 방지(3/5°C)로 돌아간다. 문서와 앱 이름에 명시 |
| 그 외(냉방·자동 등) | 거절(속성 되돌림) | |

- **디바운스 3초**: 앱 슬라이더를 밀면 값이 여러 번 온다. 마지막 값만 실행한다.
- 실행 실패 시 Matter 속성을 Pi 실제 상태로 되돌리고 로그(`matter.log`)를 남긴다.
- `heater_cmd.py`가 `last_cmd_ts`를 갱신하므로 **36시간 백스톱**(무신호 시 동파 복귀)이 그대로 적용된다.
- 같은 잠금 파일(`.sheet_cmd.lock`)을 써서 시트·텔레그램 명령과 동시에 실행되지 않게 한다.

## 5. 구현 선택
| 후보 | 판단 |
|---|---|
| **matter.js** (Node.js, 순수 TS 구현, Thermostat 기기 지원) | **채택.** Pi 4에서 빌드 없이 `npm` 설치. 예제가 테스트 VID/PID(0xFFF1/0x8000)를 기본으로 씀 |
| connectedhomeip C++ thermostat-app | Pi 빌드가 무겁고(수 GB, 수 시간) 수정이 어렵다 |
| python-matter-server | **컨트롤러용**이라 기기 역할을 못 한다 |
| Home Assistant + Matter Bridge | 가능하지만 이 용도엔 과하다(상주 메모리·관리 부담) |

파일 구성(계획):
- `matter/package.json`, `matter/thermostat.mjs`: 브리지. 상태 읽기·속성 갱신·명령 실행
- `matter/mapping.mjs` + 테스트: 매핑·클램프·디바운스(순수 함수)
- `systemd/matter-thermostat.service`(User=ryuch)
- 저장소 밖 `~/smart-cabin/.matter/`: **커미셔닝 키(패브릭 인증서) 저장. 비밀이라 커밋 금지**(.gitignore 추가)

## 6. 구글 등록(커미셔닝) 조건
1. 구글은 **미인증 기기**를 받으려면 "테스트 기기" 등록이 필요하다.
   **Google Home Developer Console**에 프로젝트를 만들고, **Matter integration을 VID 0xFFF1 / PID 0x8000**으로 하나 만든다. 사용자가 브라우저에서 1회 한다.
   등록하지 않으면 페어링 중 "미인증 기기 허용"을 묻거나 실패할 수 있다.
2. Google Home 앱 2.62.1.15 이상.
3. 브리지가 로그에 **페어링 코드·QR**을 출력한다. 앱 ➕ → 기기 → Matter-enabled device로 스캔한다.
   Pi는 이미 IP망에 있으므로 블루투스 없이 **온네트워크 커미셔닝**을 기대한다(⚠ 구글 지원 여부 0단계에서 확인).
4. 페어링 코드는 **등록 전까지만** 의미가 있다. 등록 후엔 커미셔닝 창이 닫힌다. 그래도 로그를 공개 저장소에 올리지 않는다.

## 7. 네트워크 요구
- Pi eth0의 **IPv6 링크로컬 활성**(NM 연결의 `ipv6.method`가 disabled가 아니어야 함). mDNS(UDP 5353), Matter(UDP 5540) 허용. 방화벽이 없으면 그대로 된다.
- **음성은 인터넷이 있어야 한다**(구글 클라우드가 인식). LTE가 끊기면 음성·앱 제어는 안 되지만, 히터 제어와 데드맨은 영향이 없다.

## 8. 단계별 계획
| 단계 | 장소 | 내용 | 끝나는 조건 |
|---|---|---|---|
| **0. 가능성 확인** | 집, 노트북 | ① Home Mini가 Matter 허브로 인식되게 한다(같은 Home·펌웨어·지역 확인). ② Developer Console에 0xFFF1/0x8000 등록. ③ 노트북에서 matter.js **기본 thermostat 예제** 실행 → Google Home 등록 → "난방 18도로" 음성 → 예제 로그에 설정값 18 도착 | 음성 한 번이 노트북까지 온다(**최대 위험 해소**) |
| 1. 브리지 작성 | 저장소 | `matter/` 코드 + 매핑 테스트. `--dry-run`(heater_cmd 대신 로그만) | 테스트 통과, 노트북에서 dry-run 등록 |
| 2. Pi 설치 | 농막 | Node 20 LTS 설치, `npm ci`, 서비스 등록, Home Mini 농막 Wi-Fi 재설정 후 커미셔닝 | 음성 → `heater_state.json` 반영, 시트 명령과 상태 일치 |
| 3. 운영 | | 명령 로그를 시트·텔레그램 알림에 표시("구글: 난방 18") | 1주 무사고 |

## 9. 위험·미확인
- **Home Mini 허브 인식 실패**(2026-11-13 집에서 발생). 0단계 ①이 막히면 이 설계 전체가 막힌다. 대안 허브: Nest Mini 2세대 등 중고.
- 한국어 음성 문구: "난방 18도로 해 줘", "온도 18도로 맞춰 줘" 중 무엇을 온도조절기 명령으로 알아듣는지 0단계에서 확인.
- Off = 동파 매핑을 사용자가 "꺼짐"으로 오해할 수 있다 → 기기 이름을 **"농막 난방(끄면 동파방지)"**처럼 붙인다.
- matter.js 버전 변화. `package-lock.json`으로 버전을 고정한다.
- Pi 자원: Node 상주 메모리 약 100MB(추정). Pi 4엔 여유가 있다.

## 10. 다른 방법과 비교(요약)
- 텔레그램 + 휴대폰 음성 입력: **지금 바로 됨**("난방 18도" 인식 수정 2026-11-13). Home Mini 없이 어디서나 된다.
- 예비 Shelly + 구글 루틴: 켜기·끄기만, 숫자 불가.
- 자체 클라우드 연동(Developer Console cloud-to-cloud + OAuth): 숫자 가능하지만 서버·인증 구축이 과하다.

참고: matter.js 생태계 문서(미인증 기기·테스트 VID), Google Home Developers의 Matter integration·Virtual Device 문서.
