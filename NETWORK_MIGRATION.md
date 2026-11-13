# 농막 네트워크 전환 계획 — 폰 핫스팟 → KT 상시회선

> **2026-11-08 디렉토리 재편**: 스크립트는 `gateway/`·`heater/`·`alert/`·`weather/`·`sheets/`·`systemd/`·`scripts/` 아래로 옮겼다(데이터·설정·비밀 파일은 루트 그대로). 아래 본문의 파일명은 그 폴더 안에서 찾을 것. 구조는 [README §7](README.md#7-디렉토리와-파일).


> 작성 2026-11-04. 개통일 미정. 비밀값(WiFi 비번·로컬키·Tailscale 키)은 이 문서에 쓰지 않는다(공개 저장소).

## 목표

무인기간에도 Pi가 인터넷에 붙어 있게 해서 **시트 업로드·원격 SSH·고장경보**를 살린다.
히터 제어는 지금처럼 **로컬(LAN)** 로 유지한다(인터넷이 끊겨도 동파방지는 계속).

**원칙: 공유기는 추가하지 않는다. 버팔로 WZR-HP-G300NH2가 유일한 공유기.**

## 구조 변화

**지금 (Pi가 라우터)**
```
폰 Ryuspot ─wlan0─ [Pi: NAT·DHCP 10.42.0.1, eth0 shared] ─eth0─ 버팔로(브리지/AP, Ryufarm) ─ 플러그 10.42.0.185
```

**전환 후 (버팔로가 라우터)**
```
KT 모뎀(광단말) ─WAN─ 버팔로(Router ON, DHCP, Ryufarm) ─┬─ 랜선 ─ Pi eth0 (DHCP 클라이언트)
                                                       └─ WiFi ─ Tuya 플러그 · 아이패드
폰 Ryuspot ─wlan0─ Pi   ← 방문 시 백업 경로(우선순위 낮춤)
원격: 폰/PC ──Tailscale──▶ Pi (포트포워딩 없음)
```

| 항목 | 지금 | 전환 후 |
|---|---|---|
| NAT·DHCP | Pi (eth0 `shared`) | 버팔로 |
| Pi eth0 | 10.42.0.1 고정 | 버팔로에서 DHCP (고정할당) |
| 플러그 IP | 10.42.0.185 | 버팔로 대역 새 IP (고정할당) |
| 인터넷 | 방문 시 폰 핫스팟만 | KT 상시 (핫스팟은 백업) |
| 원격 SSH | 불가 | Tailscale |
| SSID·비번 | `Ryufarm` | **그대로** → 플러그 재페어링 불필요 |

## 0. 개통 전 준비 (지금 할 수 있는 것)

- [ ] KT 신청 시 **"공유기 없이 모뎀만"** 요청. 일체형(홈허브)을 주면 **브리지 모드** 요청 → 이중 NAT·공유기 2대 방지.
- [ ] **Tailscale 미리 설치** — 다음 방문 때 핫스팟 인터넷으로:
  ```bash
  curl -fsSL https://tailscale.com/install.sh | sh
  sudo tailscale up          # 출력 링크를 폰에서 열어 로그인
  tailscale ip -4            # 100.x 주소 기록
  ```
  폰·PC에도 Tailscale 앱 설치 후 같은 계정 로그인. 핫스팟 상태에서 `ssh ryuch@smartcabin` 이 Tailscale로 되는지 확인.
- [ ] 현재 eth0 NM 프로필 이름 기록: `nmcli -f NAME,DEVICE,IPV4.METHOD con show --active`
- [ ] `tinytuya` 스캔이 되는지 확인(전환 후 플러그 IP 찾기용): `~/tuya-venv/bin/python -m tinytuya scan`

## 1. 개통 당일 — 현장 작업 순서

> 전환 중 몇 분간 Pi가 플러그를 못 찾는다. 플러그 릴레이는 마지막 상태를 유지하므로 **방문 중(실내 난방 상태)** 에 하는 게 안전. 작업 전 `heater-state` 로 현재 상태 확인.

1. **Pi eth0 를 클라이언트로** (먼저 해야 Pi·버팔로가 DHCP를 서로 하려 다투지 않음)
   ```bash
   sudo nmcli con mod "<eth0 프로필>" ipv4.method auto ipv4.route-metric 100
   ```
   (아직 `con up` 하지 않음)
2. **버팔로 Router ON** — 본체 Router 스위치 ON, KT 모뎀 랜선을 **WAN 포트**에. WAN 설정 = 자동(DHCP). KT 일반 인터넷은 PPPoE 불필요.
3. `sudo nmcli con up "<eth0 프로필>"` → `ip -4 addr show eth0` 로 버팔로 대역 IP 받았는지 확인.
4. **플러그 재접속** — 플러그가 옛 10.42.0.x 임대를 쥐고 있을 수 있으니 **전원 뽑았다 꽂기**.
5. **버팔로 관리화면에서 고정할당** — Pi(eth0 MAC)·플러그 MAC 을 DHCP 고정. (Router 모드에선 관리 IP가 브리지 때의 192.168.11.100 이 아니라 게이트웨이 주소일 수 있음 — `ip route` 의 default via 로 확인.)
6. **플러그 IP 갱신** — `tinytuya scan` 으로 새 IP 확인 → `tuya_keys.json` 의 `ip` 만 수정(키·id는 그대로, 파일은 gitignore).
7. `sudo systemctl restart heater-control` → `히터점검` → `heater-log` 에서 "히터 제어 시작 … @ <새 IP>" 확인.
8. `sudo systemctl restart farmhut-gateway` → 시트 `농막_현재` F·G열(SSID·IP)이 유선/새 IP로 바뀌는지 확인.

## 2. 마무리 설정

- [ ] **경로 우선순위**: eth0 metric 100, 핫스팟 프로필은 더 크게 → 방문 때 폰이 켜져도 KT 우선, KT 장애 때만 폰.
  ```bash
  sudo nmcli con mod hotspot ipv4.route-metric 600
  ```
  (OPERATIONS.md 3-D 의 `hotspot` 자동접속은 백업용으로 유지)
- [ ] **원격 확인**: 농막을 떠나 폰 데이터로 `ssh ryuch@smartcabin`(Tailscale) → `heater-status`.
- [ ] **재부팅 시험**: `sudo reboot` 후 원격으로 다시 붙는지, 두 서비스가 올라오는지.
- [ ] HEATER.md 「네트워크 토폴로지」, OPERATIONS.md 접속 정보를 새 구조로 갱신.

## 3. 되돌리기 (KT가 안 될 때)

```bash
sudo nmcli con mod "<eth0 프로필>" ipv4.method shared
sudo nmcli con up "<eth0 프로필>"
```
버팔로 Router 스위치 OFF(브리지/AP 복귀) → 플러그 전원 재투입 → 플러그가 10.42.0.x 로 돌아오면 `tuya_keys.json` 의 `ip` 원복 → `heater-control` 재시작.

## 4. 전환 후 2단계 (코드)

HEATER.md 「한계」에서 "KT 상시회선 연결 후"로 미뤄둔 것:
- `Code.gs` 에 히터 **고장경보·전력량** 업로드 핸들러 추가 → 시트 노출.
- 겨우내 쌓인 `heater_energy.csv` 를 원격으로 받아 **UA·C 피팅** → `ua_w_per_k`(현재 47, 추정치) 보정.

## 주의

- 버팔로는 2.4GHz 전용 구형 — 센서·플러그·SSH 용도엔 충분, 대용량 전송엔 느림.
- SSID·비번·보안방식(WPA2-PSK/AES)을 **바꾸면 플러그 재페어링 → 로컬키 변경**. 이번 전환에선 손대지 않는다.
- 버팔로에 포트포워딩·DMZ 를 열지 않는다(원격은 Tailscale 로만).

## 5. 대안: LTE USB 동글(예: Huawei E3372h-153)로 상시 인터넷 — 검토 메모 (2026-11-08)

> **2026-11-13 갱신**: E3372h-607을 사서 집에서 KT망 유심 두 개로 개통·시험 성공. 결과·현장 절차는 [docs/LTE_DONGLE.ko.md](docs/LTE_DONGLE.ko.md).

KT 유선 대신 **Pi USB에 LTE 동글 + 데이터 전용 USIM**을 꽂아 상시 인터넷을 만드는 방안. 유선 공사 없이 되고 트래픽(시트 업로드·SSH·예보 수집)은 월 1~2 GB면 충분해 저용량 알뜰폰 데이터 요금제가 유리할 수 있다. 결정 전 아래를 확인한다. (일반 지식 기반 메모 — 통신사 대역표·개통 가능 여부는 **공식 자료로 미확인**.)

### 구매 전 확인
- [ ] **현장 LTE 대역**: 폰으로 농막에서 붙는 대역을 확인(서비스 모드 또는 Network Cell Info 류 앱). E3372h-153은 **B1/B3/B7/B8/B20**만 지원 — **B5(850 MHz)·B26 없음**. 시골 저주파 커버리지가 B5면 이 모뎀은 그 신호를 못 쓴다. 현장이 B1/B3/B7/B8이면 통과.
- [ ] **변형·잠금**: 판매자에게 `E3372h-153`(HiLink, Pi에서 USB 이더넷으로 바로 잡힘)과 **Unlocked** 확인. `E3372s`(스틱 모드)는 설정이 번거로움. 화면 사양엔 변형이 안 적혀 있었음.
- [ ] **개통**: 해외 단말을 데이터 USIM으로 개통해 주는지 **알뜰폰 업체에 먼저 문의**. 업체마다 다름.
- [ ] **KC**: 개인 자가사용 해외직구는 전파인증 면제로 취급되나, LTE 모뎀 세부 요건(수량·재판매 금지)은 공식 안내로 확인 안 됨 → 국립전파연구원·소비자24 확인. 인증 없는 직구 무선기기가 전자파 기준 부적합으로 판정된 사례 있음. **중고 재판매는 하지 않는다.**

### 운용 시 유의점
- **동글 멈춤 대비 와치독**: 무인 운영에서 동글이 가끔 응답을 멈춘다 → Pi에서 핑 실패가 N분 지속되면 USB 전원 재투입(`uhubctl`) 또는 인터페이스 재시작, 그래도 안 되면 Pi 재부팅. 히터 제어는 로컬(LAN)이라 인터넷이 죽어도 동파방지는 계속된다 — 와치독은 업링크용.
- **경로 우선순위**: 동글은 `192.168.8.x`(HiLink 기본)라 현재 `10.42.0.x`·핫스팟과 충돌 없음. metric으로 동글 우선, 폰 핫스팟은 백업(§2와 동일 원칙).
- **시계**: 상시 인터넷이 생기면 NTP가 항상 되어 [TODO.md](TODO.md) 2번 H 규칙(시계 역행)의 위험이 줄어든다.
- **데이터 상한**: 요금제 상한을 넘기지 않도록 게이트웨이 업로드 빈도·예보 수집 주기를 확인. 월 사용량을 시트에 기록하는 것도 고려.
- **발열·전원**: 동글은 상시 발열. Pi USB 전원 여유(2.5 A 이상 어댑터) 확인, 필요하면 유전원 허브.
- **신호 약하면**: 외부 안테나 단자가 있는 변형/제품을 고르거나, 동글을 USB 연장선으로 창가에 둔다.

### 비교 기준 (결정용)
| | KT 유선 | LTE 동글 |
|---|---|---|
| 초기 | 공사·개통 | 동글 값(저렴) + USIM |
| 월 비용 | 유선 요금 | 데이터 요금(저용량이면 저렴) |
| 안정성 | 높음 | 신호·동글 멈춤 의존(와치독 필요) |
| 속도 | 충분 | 충분(트래픽 작음) |
| 작업 | 버팔로 Router 전환(§1) | Pi USB 꽂고 경로 설정만 |
