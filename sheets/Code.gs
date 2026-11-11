/*
 * Code.gs — 농막/선반 센서 데이터 수집 Apps Script 웹앱 (SITE 접두사로 여러 시트 공용).
 *
 * ★★ 버전: 2026-11-11 v11  ← 붙여넣기 전에 이 줄 확인! (아래 CODE_VERSION 과 같아야 함) ★★
 *   이 버전 요약: SITE 접두사 · mode current(+SSID/IP+배터리 예상일) · avg · hist(upsert) ·
 *                 batt · presence · cmd_pull/ack(+캘린더) · weather(기상청 → 예보·관측 탭) · 멱등 업로드.
 *   배포된 버전 확인법: 브라우저로 /exec 주소를 열면 {"version":"2026-11-11 v11", ...} 가 보임.
 *   (구버전이 배포돼 있으면 version 이 다르거나 없음 → 다시 붙여넣고 새 버전 배포)
 *   ⚠ 붙여넣은 뒤 SECRET 을 실제 값으로 다시 넣을 것. '새 배포'를 만들면 URL이 바뀌어 Pi 업로드가 모두 끊긴다.
 *
 * 변경 이력:
 *   v11 (2026-11-11) mode:weather — Pi(weather/kma_sync.py)가 보낸 단기예보 81행 → '동지집_예보' A3:G83(+A1 설명),
 *       부여 관측 → '부여_시간별' 끝에 추가(마지막 시각 이후만), '안내'!A14 갱신. doGet ?q=weather 는
 *       큰 탭을 읽지 않고 weather_last(관측 마지막 시각·예보 설명)만 빠르게 반환. 설계: docs/KMA_SHEET_PUSH.ko.md
 *   v10 (2026-11-08) 캘린더 연동 — 기본 캘린더에 '동지집 도착[, 희망온도 24도]' 일정이 있으면 cmd_pull 때(10분마다 검사)
 *       '도착 YYYY-MM-DD HH:MM T' 명령을 '명령' 탭에 자동 기입(F 출처=캘린더). 일정 변경→교체, 삭제→'도착 취소'. 종일 일정은 19:00, 기본 23.5°C
 *   v9 (2026-11-08) '명령' 탭 원격 명령 — Pi가 매분 mode:cmd_pull 로 B(상태) 빈 행을 받아 실행하고 mode:cmd_ack 로 결과 기록. onEdit 가 입력시각 스탬프
 *   v8 (2026-11-08) mode:presence — 게이트웨이 재실 자동기록(도착=핫스팟 등장/난방·취침 명령, 출발=동파 명령)을 '재실기록' 탭에 기록
 *   v7 (2026-11-08) 배터리 예상일 재작성: pvvx 전압눈금(2.2V=0%,3.1V=100%) 전제, 종료 2.6V(44%),
 *                   전지교체·펌웨어전환 불연속(>8%p)에서 추세창 재시작, CR2032 방전곡선 추정과 추세 추정 중
 *                   보수적인 값 표시, 순정(3.1V/100% 고정)은 '순정 펌웨어' 표시. 2.65V 이하 즉시 빨강.
 *   v6 (2026-10-27) 배터리 이력(_배터리 탭) + 현재 탭 H열에 예상 소진일(임박 시 빨강)
 *   v5 (2026-10-27) 현재 탭에 접속 SSID·IP 표시(F·G열, 헤더 자동보강)
 *   v4 (2026-10-27) SITE 접두사(농막/선반 공용) · mode:hist upsert(변화행만)
 *   v3  mode:avg(시간평균) · mode:current(현재 스냅샷)
 *   v1  시간별 min/max 증분 기록(멱등)
 *
 * 배포:
 *   1) 대상 구글시트 > 확장 프로그램 > Apps Script 열고 '이 내용 전체'를 붙여넣기(기존 것 교체)
 *   2) 맨 위 SITE('농막' 또는 '선반') 와 SECRET 를 게이트웨이와 일치하게 설정
 *   3) 배포 > 배포 관리 > (기존 웹앱) 새 버전 > 배포  (실행:나 / 액세스:모든 사용자, URL 유지)
 *
 * 특징: 중복 제거를 서버에서 처리(멱등). 중복 기준 (센서, 정시 버킷). 시각은 실제 날짜값으로 저장.
 */

var CODE_VERSION = '2026-11-11 v11';   // ← /exec(doGet) 응답·붙여넣기 확인용. 고칠 때마다 갱신.
var SITE = '농막';                 // ← 장소 접두사. 두 번째 시트(예: 선반)는 '선반' 으로만 바꾸면 됨
var SECRET = 'CHANGE-ME';          // ← 게이트웨이의 FARMHUT_SECRET 과 동일하게
var SHEET_NAME = SITE + '_시간별';
var CURRENT_SHEET = SITE + '_현재';   // 현재값 스냅샷(센서별 1행, 덮어쓰기)
var AVG_SHEET = SITE + '_평균';       // 게이트웨이가 passive로 계산한 시간가중 평균(파생값)
var BATT_SHEET = SITE + '_배터리';    // 배터리 전압·% 이력(수명 추세 → 현재 탭 예상 소진일)
var CMD_SHEET = '명령';           // A 명령(사람 입력) B 상태 C 처리시각 D 결과 E 입력시각. Pi(heater/sheet_cmd_poll.py)가 매분 폴링
var PRESENCE_SHEET = '재실기록';      // A도착 B출발(빈칸=체류 중) C메모. 게이트웨이 mode:presence 가 자동 기록
var FC_SHEET = '동지집_예보';        // v11 mode:weather. A1 설명 · A2 머리글 · A3:G83 81행(덮어쓰기). W~Z 수식은 건드리지 않음
var FC_ROWS = 81;
var OBS_SHEET = '부여_시간별';       // v11 A시각 B기온 C습도 D강수 E운량. 열린 범위 수식이 있어 끝에 추가만
var GUIDE_SHEET = '안내';            // v11 A14 '마지막 조회 …'
// 컬럼: A시각 B센서 C최고온도 D최저온도 E최고습도 F최저습도 G센서idx H업로드시각

function _sheet() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(SHEET_NAME);
  if (!sh) {
    sh = ss.insertSheet(SHEET_NAME);
    sh.appendRow(['시각', '센서', '최고온도(°C)', '최저온도(°C)', '최고습도(%)', '최저습도(%)', '센서idx', '업로드시각']);
    sh.setFrozenRows(1);
  }
  return sh;
}

function _hourKey(sensor, ms) {
  return sensor + '|' + Math.round(ms / 3600000);
}

// 기존 행에서 (센서|정시) 키 집합 구성
function _existingKeys(sh) {
  var set = {};
  var last = sh.getLastRow();
  if (last < 2) return set;
  var vals = sh.getRange(2, 1, last - 1, 2).getValues(); // A:시각, B:센서
  for (var i = 0; i < vals.length; i++) {
    var t = vals[i][0], s = vals[i][1];
    if (!t || !s) continue;
    var ms = (t instanceof Date) ? t.getTime() : new Date(t).getTime();
    if (isNaN(ms)) continue;
    set[_hourKey(s, ms)] = true;
  }
  return set;
}

function _currentSheet() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(CURRENT_SHEET);
  if (!sh) {
    sh = ss.insertSheet(CURRENT_SHEET);
    sh.appendRow(['센서', '온도(°C)', '습도(%)', '배터리(V)', '갱신시각', 'SSID', 'IP']);
    sh.setFrozenRows(1);
  } else if (sh.getRange(1, 6).getValue() !== 'SSID') {
    sh.getRange(1, 6, 1, 2).setValues([['SSID', 'IP']]);   // 기존 시트에 헤더 보강
  }
  return sh;
}

// 현재값: 센서별 한 행을 덮어씀(없으면 추가). 게이트웨이 접속 SSID·IP를 갱신시각 옆에 표시.
function _postCurrent(body) {
  var sh = _currentSheet();
  var readings = body.readings || [];
  var ssid = body.ssid || '', ip = body.ip || '';
  var last = sh.getLastRow();
  var names = last >= 2 ? sh.getRange(2, 1, last - 1, 1).getValues().map(function (r) { return r[0]; }) : [];
  var n = 0;
  for (var i = 0; i < readings.length; i++) {
    var r = readings[i];
    var idx = names.indexOf(r.sensor);
    var row = idx >= 0 ? idx + 2 : sh.getLastRow() + 1;
    var t = r.time_ms ? new Date(r.time_ms) : new Date();
    sh.getRange(row, 1, 1, 7).setValues([[r.sensor, r.temp, r.hum, r.volt, t, ssid, ip]]);
    if (idx < 0) names.push(r.sensor);
    n++;
  }
  return _json({ ok: true, mode: 'current', updated: n });
}

function _avgSheet() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(AVG_SHEET);
  if (!sh) {
    sh = ss.insertSheet(AVG_SHEET);
    sh.appendRow(['시각', '센서', '평균온도(°C)', '평균습도(%)', '기록시각']);
    sh.setFrozenRows(1);
  }
  return sh;
}

// (센서|정시) 중복 제거하며 시간별 평균 행을 append. readings=[{sensor,time_ms,temp,hum}]
function _postAvg(body) {
  var sh = _avgSheet();
  var readings = body.readings || [];
  var existing = {};
  var last = sh.getLastRow();
  if (last >= 2) {
    var vals = sh.getRange(2, 1, last - 1, 2).getValues();  // A시각 B센서
    for (var i = 0; i < vals.length; i++) {
      var t = vals[i][0], s = vals[i][1];
      if (!t || !s) continue;
      var ms0 = (t instanceof Date) ? t.getTime() : new Date(t).getTime();
      if (!isNaN(ms0)) existing[_hourKey(s, ms0)] = true;
    }
  }
  var rows = [], now = new Date(), seen = {};
  for (var j = 0; j < readings.length; j++) {
    var r = readings[j];
    var ms = Number(r.time_ms);
    if (isNaN(ms)) continue;
    var key = _hourKey(r.sensor, ms);
    if (existing[key] || seen[key]) continue;
    seen[key] = true;
    rows.push([new Date(ms), r.sensor, (r.temp == null ? '' : r.temp), (r.hum == null ? '' : r.hum), now]);
  }
  if (rows.length) {
    var start = sh.getLastRow() + 1;
    sh.getRange(start, 1, rows.length, rows[0].length).setValues(rows);
  }
  return _json({ ok: true, mode: 'avg', added: rows.length, skipped: readings.length - rows.length });
}

// 시간별 min/max upsert. readings=[{sensor,time_ms,maxT,minT,maxH,minH}]
//  - 같은 (센서|정시) 행이 있고 값이 다르면 '덮어씀'(authoritative가 passive 근사값을 교정)
//  - 값이 같으면 건너뜀(쓰기 0). 없으면 append.
//  ★ 첫 센서 sync는 이미 시트에 있는 이력을 통째로 다시 보냄 → 대부분 '변화없음'으로 걸러
//    setValues 폭주를 막는다(스크립트 락·타임아웃 회피). 센서idx(G열)는 기존값 보존.
function _postHist(body) {
  var sh = _sheet();
  var readings = body.readings || [];
  var last = sh.getLastRow();
  var info = {};  // "센서|정시" -> {row, c,d,e,f: 기존 min/max, g: 기존 idx}
  if (last >= 2) {
    var vals = sh.getRange(2, 1, last - 1, 7).getValues();  // A..G
    for (var i = 0; i < vals.length; i++) {
      var t = vals[i][0], s = vals[i][1];
      if (!t || !s) continue;
      var ms0 = (t instanceof Date) ? t.getTime() : new Date(t).getTime();
      if (!isNaN(ms0)) info[_hourKey(s, ms0)] = { row: i + 2, c: vals[i][2], d: vals[i][3], e: vals[i][4], f: vals[i][5], g: vals[i][6] };
    }
  }
  function num(x) { return (x === '' || x == null) ? null : Number(x); }
  function same(ex, r) { return num(ex.c) === num(r.maxT) && num(ex.d) === num(r.minT) && num(ex.e) === num(r.maxH) && num(ex.f) === num(r.minH); }
  var now = new Date(), added = 0, updated = 0, unchanged = 0, appendRows = [], seen = {};
  for (var j = 0; j < readings.length; j++) {
    var r = readings[j];
    var ms = Number(r.time_ms);
    if (isNaN(ms) || !r.sensor) continue;
    var key = _hourKey(r.sensor, ms);
    var ex = info[key];
    if (ex) {
      if (same(ex, r)) { unchanged++; continue; }
      sh.getRange(ex.row, 1, 1, 8).setValues([[new Date(ms), r.sensor, r.maxT, r.minT, r.maxH, r.minH, ex.g, now]]);
      updated++;
    } else if (seen[key] !== undefined) {
      appendRows[seen[key]] = [new Date(ms), r.sensor, r.maxT, r.minT, r.maxH, r.minH, '', now];  // 배치 내 중복→마지막 값
    } else {
      seen[key] = appendRows.length;
      appendRows.push([new Date(ms), r.sensor, r.maxT, r.minT, r.maxH, r.minH, '', now]);
      added++;
    }
  }
  if (appendRows.length) {
    var need = sh.getLastRow() + appendRows.length + 1;
    if (need > sh.getMaxRows()) sh.insertRowsAfter(sh.getMaxRows(), need - sh.getMaxRows());
    var start = sh.getLastRow() + 1;
    sh.getRange(start, 1, appendRows.length, 8).setValues(appendRows);
  }
  return _json({ ok: true, mode: 'hist', added: added, updated: updated, unchanged: unchanged });
}

function _battSheet() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(BATT_SHEET);
  if (!sh) {
    sh = ss.insertSheet(BATT_SHEET);
    sh.appendRow(['시각', '센서', '배터리(V)', '배터리(%)', '기록시각']);
    sh.setFrozenRows(1);
  }
  return sh;
}

// 배터리 이력 append(센서|정시 중복 제거) + 현재 탭에 예상 소진일 갱신. readings=[{sensor,time_ms,volt,pct}]
function _postBatt(body) {
  var sh = _battSheet();
  var readings = body.readings || [];
  var existing = {};
  var last = sh.getLastRow();
  if (last >= 2) {
    var vals = sh.getRange(2, 1, last - 1, 2).getValues();
    for (var i = 0; i < vals.length; i++) {
      var t = vals[i][0], s = vals[i][1];
      if (!t || !s) continue;
      var ms0 = (t instanceof Date) ? t.getTime() : new Date(t).getTime();
      if (!isNaN(ms0)) existing[_hourKey(s, ms0)] = true;
    }
  }
  var rows = [], now = new Date(), seen = {};
  for (var j = 0; j < readings.length; j++) {
    var r = readings[j];
    var ms = Number(r.time_ms);
    if (isNaN(ms) || !r.sensor) continue;
    var key = _hourKey(r.sensor, ms);
    if (existing[key] || seen[key]) continue;
    seen[key] = true;
    rows.push([new Date(ms), r.sensor, (r.volt == null ? '' : r.volt), (r.pct == null ? '' : r.pct), now]);
  }
  if (rows.length) {
    var start = sh.getLastRow() + 1;
    sh.getRange(start, 1, rows.length, rows[0].length).setValues(rows);
  }
  try { _updateBatteryForecast(sh); } catch (e) {}   // 예상일 실패해도 업로드엔 영향 없게
  return _json({ ok: true, mode: 'batt', added: rows.length });
}

// 배터리 예상 소진일 → 현재 탭 H열. (v7)
//  - pvvx 펌웨어의 %는 용량이 아니라 전압 눈금(2.2V=0%, 3.1V=100%, 선형). 게이트웨이 volt 열도 같은 환산.
//  - 종료 기준 2.6V(=44%): LCD·라디오가 불안정해지는 전압. CR2032는 2.9V까지 평평하다가 2.8V 아래서 꺾인다.
//  - 추정 2가지 중 보수적인(가까운) 날짜를 표시:
//      (a) 전압곡선: 현재 전압 → CR2032 소모 비율(아래 표) → 2.6V까지 남은 mAh ÷ 가정 소비(≈12µA, 순정 실측×0.4)
//      (b) 추세: 불연속(전지교체·펌웨어 전환, 인접 점 >8%p) 이후 구간의 %/일 최소제곱 기울기로 44%까지
//  - 순정 펌웨어(3.1V/100% 고정)는 정보가 없으므로 '순정 펌웨어(표시 100% 고정)'.
function _updateBatteryForecast(battSh) {
  var END_PCT = 44, WARN_DAYS = 30, MIN_PTS = 5, MIN_SPAN_D = 3, WINDOW_D = 60, DAY = 86400000;
  // DRAW_UA: 순정 실측(1층 2026-01 구입 → 11-08 2.85V ≈ 66% 소모, 10개월 → 평균 ≈20µA, 자기방전·추위 포함)에
  //          pvvx 라디오 비율 0.4를 적용한 ≈12µA. 야외 센서는 추위로 더 짧을 수 있음(추세가 쌓이면 추세가 이김).
  var CAP_MAH = 220, DRAW_UA = 12, JUMP = 8, LOW_V = 2.65, END_V = 2.6;
  // 개방전압(V) → 소모 비율(0~1). 저전류(~10µA) CR2032 전형값.
  var CURVE = [[3.05, 0.00], [3.00, 0.10], [2.95, 0.30], [2.90, 0.50], [2.85, 0.65], [2.80, 0.78],
               [2.75, 0.86], [2.70, 0.91], [2.65, 0.95], [2.60, 0.98], [2.50, 1.00]];
  function usedFrac(v) {
    if (v >= CURVE[0][0]) return 0;
    for (var i = 1; i < CURVE.length; i++) {
      if (v >= CURVE[i][0]) {
        var v1 = CURVE[i - 1][0], f1 = CURVE[i - 1][1], v2 = CURVE[i][0], f2 = CURVE[i][1];
        return f1 + (f2 - f1) * (v1 - v) / (v1 - v2);
      }
    }
    return 1;
  }
  var last = battSh.getLastRow();
  if (last < 2) return;
  var n = Math.min(last - 1, 4000);
  var vals = battSh.getRange(last - n + 1, 1, n, 4).getValues();  // 시각,센서,V,%
  var now = Date.now(), cutoff = now - WINDOW_D * DAY, bySensor = {};
  for (var i = 0; i < vals.length; i++) {
    var t = vals[i][0], s = vals[i][1], volt = vals[i][2], pct = vals[i][3];
    if (!t || !s || pct === '' || pct == null) continue;
    var ms = (t instanceof Date) ? t.getTime() : new Date(t).getTime();
    if (isNaN(ms) || ms < cutoff) continue;
    (bySensor[s] = bySensor[s] || []).push([ms, Number(pct), Number(volt)]);
  }
  var cur = _currentSheet(), cl = cur.getLastRow();
  var names = cl >= 2 ? cur.getRange(2, 1, cl - 1, 1).getValues().map(function (r) { return r[0]; }) : [];
  if (cur.getRange(1, 8).getValue() !== '배터리 예상') cur.getRange(1, 8).setValue('배터리 예상');
  for (var s2 in bySensor) {
    var idx = names.indexOf(s2);
    if (idx < 0) continue;
    var all = bySensor[s2].sort(function (a, b) { return a[0] - b[0]; });
    var cell = cur.getRange(idx + 2, 8), msg = '', red = false;
    var lastPt = all[all.length - 1], curPct = lastPt[1], curV = lastPt[2];
    if (!(curV > 0)) curV = 2.2 + 0.9 * curPct / 100;
    if (curPct >= 100 && curV >= 3.1) {                 // 순정 펌웨어: 죽기 직전까지 100%
      msg = '순정 펌웨어(표시 100% 고정)';
    } else {
      // 불연속(전지교체·펌웨어 전환) 이후 구간만 추세에 사용
      var pts = [];
      for (var k = 0; k < all.length; k++) {
        if (pts.length && Math.abs(all[k][1] - pts[pts.length - 1][1]) > JUMP) pts = [];
        if (all[k][1] >= 100 && all[k][2] >= 3.1) { pts = []; continue; }   // 순정 구간 제외
        pts.push(all[k]);
      }
      // (a) 전압곡선 추정
      var remainMah = CAP_MAH * Math.max(0, usedFrac(END_V) - usedFrac(curV));   // 2.6V까지 쓸 수 있는 양
      var daysCurve = remainMah / (DRAW_UA / 1000) / 24;
      var daysBest = daysCurve, how = '전압';
      // (b) 추세 추정
      var spanD = pts.length ? (pts[pts.length - 1][0] - pts[0][0]) / DAY : 0;
      if (pts.length >= MIN_PTS && spanD >= MIN_SPAN_D) {
        var t0 = pts[0][0], nP = pts.length, sx = 0, sy = 0, sxx = 0, sxy = 0;
        for (var j = 0; j < nP; j++) { var x = (pts[j][0] - t0) / DAY, y = pts[j][1]; sx += x; sy += y; sxx += x * x; sxy += x * y; }
        var denom = nP * sxx - sx * sx;
        if (denom !== 0) {
          var slope = (nP * sxy - sx * sy) / denom;          // %p/일
          if (slope < -0.03) {
            var daysTrend = (curPct - END_PCT) / (-slope);
            if (daysTrend < daysBest) { daysBest = daysTrend; how = '추세'; }
          }
        }
      }
      if (daysBest < 0) daysBest = 0;
      var ds = Utilities.formatDate(new Date(now + daysBest * DAY), Session.getScriptTimeZone(), 'yyyy-MM-dd');
      if (curV <= LOW_V) { msg = '⚠ ' + curV.toFixed(2) + 'V 교체'; red = true; }
      else if (daysBest <= WARN_DAYS) { msg = '⚠ ' + ds + ' 교체 (' + Math.round(daysBest) + '일)'; red = true; }
      else {
        msg = '예상 ' + ds + ' (' + how;
        if (how === '전압') msg += ', 추세 ' + Math.round(spanD) + '일째';
        msg += ')';
      }
    }
    cell.setValue(msg);
    cell.setFontColor(red ? 'red' : null);
    cell.setFontWeight(red ? 'bold' : null);
  }
}

// 재실 자동기록. events=[{time_ms, kind:'arrive'|'depart', note}]
//  arrive: 마지막 행이 '체류 중'(B 빈칸)이고 그 도착이 24h 이내면 중복으로 보고 무시, 아니면 [도착, '', 메모] 추가
//  depart: 마지막 '체류 중' 행의 B 를 채움(메모 덧붙임). 체류 중 행이 없으면 ['', 출발, 메모] 추가(수동 보정용)
//  시각은 시트 표기 규약(yyyy-MM-dd HH:mm 문자열)과 맞춤. 멱등: 같은 분의 같은 kind 는 1회만.
function _postPresence(body) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(PRESENCE_SHEET);
  if (!sh) { sh = ss.insertSheet(PRESENCE_SHEET); sh.appendRow(['도착', '출발 (빈칸 = 아직 머무는 중)', '메모']); sh.setFrozenRows(1); }
  var tz = Session.getScriptTimeZone(), added = 0, closed = 0, skipped = 0;
  var events = (body.events || []).slice().sort(function (a, b) { return Number(a.time_ms) - Number(b.time_ms); });
  for (var i = 0; i < events.length; i++) {
    var ev = events[i], ms = Number(ev.time_ms); if (isNaN(ms)) { skipped++; continue; }
    var ts = Utilities.formatDate(new Date(ms), tz, 'yyyy-MM-dd HH:mm');
    var note = String(ev.note || '');
    var last = sh.getLastRow();
    var lastRow = last >= 2 ? sh.getRange(last, 1, 1, 3).getValues()[0] : null;
    var open = lastRow && String(lastRow[0]) !== '' && String(lastRow[1]) === '';
    if (ev.kind === 'arrive') {
      if (open) { var a = new Date(String(lastRow[0]).replace(' ', 'T')).getTime(); if (!isNaN(a) && Math.abs(ms - a) < 24 * 3600000) { skipped++; continue; } }
      if (lastRow && String(lastRow[0]) === ts) { skipped++; continue; }
      sh.appendRow([ts, '', note]); added++;
    } else if (ev.kind === 'depart') {
      if (open) {
        if (String(lastRow[1]) === ts) { skipped++; continue; }
        sh.getRange(last, 2).setValue(ts);
        var memo = String(lastRow[2] || ''); sh.getRange(last, 3).setValue(memo ? memo + ' / 출발 ' + note : '출발 ' + note); closed++;
      } else { sh.appendRow(['', ts, '출발만 기록(도착 행 없음) ' + note]); added++; }
    } else skipped++;
  }
  return _json({ ok: true, mode: 'presence', added: added, closed: closed, skipped: skipped });
}

// ── 원격 명령('명령' 탭) ───────────────────────────────────────
// 사람이 A열에 "도착 내일 16:00" / "난방" / "동파" / "취침" / "상태" / "점검" 을 적으면
// Pi 가 매분 cmd_pull 로 가져가 실행하고 cmd_ack 로 B 상태·C 처리시각·D 결과를 채운다.
var CMD_HEADER = ['명령', '상태', '처리시각', '결과', '입력시각', '출처'];
var CAL_KEYWORD = '동지집 도착';      // 캘린더 일정 제목에 이 말이 있으면 시작 시각 = 도착 시각
var CAL_DEFAULT_HHMM = '19:00';     // 종일 일정일 때 도착 시각
var CAL_DEFAULT_TEMP = 23.5;        // 제목에 '희망온도 24도' 없을 때
var CAL_LOOKAHEAD_DAYS = 7;
var CAL_CHECK_SEC = 600;            // cmd_pull 때 캘린더 검사 간격
var CMD_NOTE = '여기에 명령을 적으면 농막 Pi가 1분 안에 실행하고 B~D에 결과를 씁니다.\n' +
  '난방 | 동파 | 취침 | 도착 16:00 | 도착 내일 07:00 | 도착 2026-11-14 20:00 24 | 도착 취소 | 상태 | 점검\n' +
  '입력 후 12시간이 지나도록 Pi가 못 받으면 "만료"로 표시되고 실행하지 않습니다.';
var CMD_EXAMPLES = [
  ['명령 예시 (복사해서 A열에 붙여넣기)', '설명'],
  ['상태', '현재 모드·실내온도·히터 ON/단수·오늘 kWh·도착예약 한 줄'],
  ['난방', '도착했을 때. 1층 기준 ON 23 / OFF 23.5'],
  ['난방 25', '임시 목표 25°C (OFF는 +0.5)'],
  ['동파', '떠날 때. 3 / 5°C 유지, 재실기록에 출발 기록'],
  ['취침', '다락 센서 기준 20 / 21°C'],
  ['취침 19', '다락 기준 ON 19°C'],
  ['도착 16:00', '오늘 16:00에 23°C가 되도록 자동 시작 (지났으면 내일)'],
  ['도착 내일 07:00', '내일 07:00 도착'],
  ['도착 모레 20:00', '모레 20:00 도착'],
  ['도착 2026-11-14 20:00', '날짜 지정'],
  ['도착 16:00 24', '목표 24°C로 도착'],
  ['도착 취소', '도착 예약 해제'],
  ['점검', '히터 자가점검(플러그 5초 ON → 전력·단수 확인). 체류 중 사용'],
  ['(캘린더)', '구글 캘린더에 "동지집 도착, 희망온도 24도" 일정을 만들면 시작 시각으로 도착 명령이 자동 기입됨(종일이면 19:00, 온도 생략 시 23.5)']
];
function _cmdSheet() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(CMD_SHEET);
  if (!sh) {
    sh = ss.insertSheet(CMD_SHEET);
    sh.getRange(1, 1, 1, CMD_HEADER.length).setValues([CMD_HEADER]).setFontWeight('bold');
    sh.setFrozenRows(1);
    sh.setColumnWidth(1, 220); sh.setColumnWidth(2, 60); sh.setColumnWidth(3, 140); sh.setColumnWidth(4, 520); sh.setColumnWidth(5, 140);
    sh.getRange(1, 1).setNote(CMD_NOTE);
    sh.getRange('C:C').setNumberFormat('MM-dd HH:mm'); sh.getRange('E:E').setNumberFormat('MM-dd HH:mm');
    sh.getRange('D:D').setWrap(true);
    // G:H 명령 예시(복사해서 A열에 붙여넣기)
    sh.getRange(1, 7, CMD_EXAMPLES.length, 2).setValues(CMD_EXAMPLES);
    sh.getRange(1, 7, 1, 2).setFontWeight('bold');
    sh.getRange(2, 7, CMD_EXAMPLES.length - 1, 1).setBackground('#f2f2f2').setFontFamily('Roboto Mono');
    sh.setColumnWidth(6, 70); sh.setColumnWidth(7, 200); sh.setColumnWidth(8, 420);
  }
  return sh;
}
// ── 캘린더 연동 ────────────────────────────────────────────────
// 기본 캘린더에서 제목에 CAL_KEYWORD 가 든 가장 이른 일정(시작이 지금-3h ~ +7일)을 찾아
// '도착 YYYY-MM-DD HH:MM T' 를 '명령' 탭에 자동 기입. 같은 내용이면 다시 쓰지 않음(PropertiesService).
// 일정이 사라지면(삭제·지남) 마지막 도착 명령이 캘린더 출처일 때만 '도착 취소' 기입(사람이 직접 쓴 도착은 건드리지 않음).
function _calDesired() {
  var now = new Date();
  var from = new Date(now.getTime() - 3 * 3600 * 1000);
  var to = new Date(now.getTime() + CAL_LOOKAHEAD_DAYS * 86400 * 1000);
  var evs = CalendarApp.getDefaultCalendar().getEvents(from, to, { search: CAL_KEYWORD });
  var best = null;
  for (var i = 0; i < evs.length; i++) {
    var ev = evs[i]; var title = String(ev.getTitle() || '');
    if (title.indexOf(CAL_KEYWORD) < 0) continue;
    var start;
    if (ev.isAllDayEvent()) {
      var d = ev.getAllDayStartDate();
      var hm = CAL_DEFAULT_HHMM.split(':');
      start = new Date(d.getFullYear(), d.getMonth(), d.getDate(), Number(hm[0]), Number(hm[1]), 0);
    } else start = ev.getStartTime();
    if (start.getTime() < from.getTime()) continue;
    var temp = CAL_DEFAULT_TEMP;
    var m = title.match(/희망온도\s*([0-9]+(?:\.[0-9]+)?)\s*도?/);
    if (m) { var t = Number(m[1]); if (t >= 10 && t <= 30) temp = t; }
    if (!best || start.getTime() < best.start.getTime()) best = { start: start, temp: temp, title: title };
  }
  if (!best) return null;
  var ymd = Utilities.formatDate(best.start, 'Asia/Seoul', 'yyyy-MM-dd HH:mm');
  return { cmd: '도착 ' + ymd + ' ' + best.temp, key: ymd + '|' + best.temp, title: best.title };
}
function _calAppend(sh, cmd) {
  // A열 기준 마지막 행(+1). sh.getLastRow()는 G:H 예시 목록까지 세므로 쓰지 않음
  var a = sh.getRange(1, 1, sh.getMaxRows(), 1).getValues(); var row = 2;
  for (var i = a.length - 1; i >= 0; i--) if (String(a[i][0] || '').trim()) { row = i + 2; break; }
  sh.getRange(row, 1, 1, 6).setValues([[cmd, '', '', '', new Date(), '캘린더']]);
}
function _calLastArrivalSource(sh) {
  var last = sh.getLastRow(); if (last < 2) return null;
  var vals = sh.getRange(2, 1, last - 1, 6).getValues();
  for (var i = vals.length - 1; i >= 0; i--) {
    var a = String(vals[i][0] || '').trim();
    if (a.indexOf('도착') === 0 || a.toLowerCase().indexOf('arrive') === 0) return String(vals[i][5] || '').trim();
  }
  return null;
}
function _calSync(sh) {
  var cache = CacheService.getScriptCache();
  if (cache.get('cal_checked')) return;
  cache.put('cal_checked', '1', CAL_CHECK_SEC);
  var props = PropertiesService.getScriptProperties();
  var prev = props.getProperty('CAL_LAST') || '';
  var want = _calDesired();
  if (want) {
    if (want.key !== prev) { _calAppend(sh, want.cmd); props.setProperty('CAL_LAST', want.key); }
  } else if (prev) {
    if (_calLastArrivalSource(sh) === '캘린더') _calAppend(sh, '도착 취소');
    props.deleteProperty('CAL_LAST');
  }
}
// Pi → 대기 중(B 빈칸) 행 목록. 입력시각(E)이 비어 있으면 지금으로 채움(onEdit 가 못 찍은 경우).
function _cmdPull(body) {
  var sh = _cmdSheet();
  var calErr = null;
  try { _calSync(sh); } catch (err) { calErr = String(err); Logger.log('calSync: ' + err); }
  var last = sh.getLastRow();
  var out = [];
  if (last >= 2) {
    var vals = sh.getRange(2, 1, last - 1, 5).getValues();
    var now = new Date();
    for (var i = 0; i < vals.length && out.length < 20; i++) {
      var cmd = String(vals[i][0] || '').trim();
      if (!cmd || String(vals[i][1] || '').trim()) continue;
      var ent = vals[i][4];
      if (!(ent instanceof Date)) { ent = now; sh.getRange(i + 2, 5).setValue(now); }
      out.push({ row: i + 2, cmd: cmd, entered_ms: ent.getTime() });
    }
  }
  return _json({ ok: true, mode: 'cmd_pull', commands: out, cal_error: calErr });
}
// Pi → 실행 결과. acks=[{row, status, result, done_ms}]
function _cmdAck(body) {
  var sh = _cmdSheet();
  var acks = body.acks || [];
  var n = 0;
  for (var i = 0; i < acks.length; i++) {
    var a = acks[i]; var row = Number(a.row);
    if (!(row >= 2)) continue;
    var done = a.done_ms ? new Date(Number(a.done_ms)) : new Date();
    sh.getRange(row, 2, 1, 3).setValues([[String(a.status || '완료'), done, String(a.result || '')]]);
    n++;
  }
  return _json({ ok: true, mode: 'cmd_ack', updated: n });
}
// 단순 트리거: '명령' 탭 A열에 새로 적으면 입력시각(E) 스탬프, 상태(B) 비움
function onEdit(e) {
  try {
    var r = e && e.range; if (!r) return;
    var sh = r.getSheet(); if (sh.getName() !== CMD_SHEET) return;
    if (r.getColumn() !== 1 || r.getRow() < 2) return;
    for (var i = 0; i < r.getNumRows(); i++) {
      var row = r.getRow() + i;
      var v = String(sh.getRange(row, 1).getValue() || '').trim();
      if (v) { sh.getRange(row, 5).setValue(new Date()); sh.getRange(row, 2, 1, 3).clearContent(); }
    }
  } catch (err) {}
}

function calTest() { Logger.log(JSON.stringify(_calDesired())); }

function doPost(e) {
  var lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    var body = JSON.parse(e.postData.contents);
    if (String(body.secret || '') !== SECRET) {
      return _json({ ok: false, error: 'bad secret' });
    }
    if (body.mode === 'current') return _postCurrent(body);
    if (body.mode === 'avg') return _postAvg(body);
    if (body.mode === 'hist') return _postHist(body);
    if (body.mode === 'batt') return _postBatt(body);
    if (body.mode === 'presence') return _postPresence(body);
    if (body.mode === 'cmd_pull') return _cmdPull(body);
    if (body.mode === 'cmd_ack') return _cmdAck(body);
    if (body.mode === 'weather') return _postWeather(body);
    var sensor = String(body.sensor || '').trim();
    var records = body.records || [];
    if (!sensor || !records.length) return _json({ ok: true, added: 0, skipped: 0, note: 'empty' });

    var sh = _sheet();
    var existing = _existingKeys(sh);
    var batchSeen = {};
    var rows = [];
    var now = new Date();

    for (var i = 0; i < records.length; i++) {
      var r = records[i];
      var ms = new Date(r.time).getTime();
      if (isNaN(ms)) continue;
      var key = _hourKey(sensor, ms);
      if (existing[key] || batchSeen[key]) continue;
      batchSeen[key] = true;
      rows.push([new Date(ms), sensor, r.maxT, r.minT, r.maxH, r.minH, r.idx, now]);
    }

    if (rows.length) {
      // 용량 확보
      var need = sh.getLastRow() + rows.length + 1;
      if (need > sh.getMaxRows()) sh.insertRowsAfter(sh.getMaxRows(), need - sh.getMaxRows());
      // 청크 기록
      var start = sh.getLastRow() + 1;
      var CH = 500;
      for (var off = 0; off < rows.length; off += CH) {
        var chunk = rows.slice(off, off + CH);
        sh.getRange(start + off, 1, chunk.length, chunk[0].length).setValues(chunk);
      }
    }
    return _json({ ok: true, sensor: sensor, added: rows.length, skipped: records.length - rows.length });
  } catch (err) {
    return _json({ ok: false, error: String(err) });
  } finally {
    lock.releaseLock();
  }
}

// 센서별 행수/마지막 시각 요약. 브라우저에서 /exec 열면 JSON, callback 파라미터가 있으면 JSONP(JS)로 반환.
// (브라우저 fetch는 CORS에 막히므로, 앱은 <script>로 JSONP를 써서 시트의 마지막 시각을 읽는다.)
// ── v11 mode:weather ──────────────────────────────────────────────
// 'YYYY-MM-DD HH:MM' (KST) → Date. 스크립트 시간대가 Asia/Seoul 이어야 한다(프로젝트 설정).
function _kst(s) {
  var m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{1,2}):(\d{2})/.exec(String(s || ''));
  if (!m) return null;
  return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5]);
}

function _fmt(d) {
  return Utilities.formatDate(d, Session.getScriptTimeZone(), 'yyyy-MM-dd HH:mm');
}

// 부여_시간별 A열에서 마지막 날짜값의 (행번호, Date). 다른 열 수식 때문에 getLastRow()는 믿지 않는다.
function _obsLast(sh) {
  var n = sh.getLastRow();
  if (n < 2) return { row: 1, date: null };
  var a = sh.getRange(2, 1, n - 1, 1).getValues();
  for (var i = a.length - 1; i >= 0; i--) {
    var v = a[i][0];
    if (v instanceof Date && !isNaN(v.getTime())) return { row: i + 2, date: v };
  }
  return { row: 1, date: null };
}

function _weatherLast() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var out = {};
  var ob = ss.getSheetByName(OBS_SHEET);
  if (ob) { var l = _obsLast(ob); if (l.date) out.obs_last = _fmt(l.date); }
  var fc = ss.getSheetByName(FC_SHEET);
  if (fc) out.vilage_desc = String(fc.getRange(1, 1).getValue());
  return out;
}

function _postWeather(body) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var out = { ok: true };
  var when = String(body.fetched_at || _fmt(new Date()));

  var vg = body.vilage;
  if (vg && vg.rows && vg.rows.length) {
    var fc = ss.getSheetByName(FC_SHEET);
    if (!fc) return _json({ ok: false, error: 'no sheet ' + FC_SHEET });
    var vals = [];
    for (var i = 0; i < FC_ROWS; i++) {
      var r = vg.rows[i];
      if (!r) { vals.push(['', '', '', '', '', '', '']); continue; }
      var d = _kst(r[0]);
      vals.push([d || r[0], r[1], r[2], r[3], r[4], r[5], r[6]]);
    }
    fc.getRange(3, 1, FC_ROWS, 7).setValues(vals);
    var b = String(vg.base || ''), bp = String(vg.base_prev || '');
    var nice = function (x) { return x.length >= 12 ? x.substr(0, 4) + '-' + x.substr(4, 2) + '-' + x.substr(6, 2) + ' ' + x.substr(8, 2) + ':' + x.substr(10, 2) : x; };
    fc.getRange(1, 1).setValue('기상청 단기예보 동지집 격자(57,99) · 발표 ' + nice(b) +
      (bp ? ' (앞 시간은 ' + nice(bp) + ' 발표)' : '') + ' · 조회 ' + when + ' KST · Pi 자동(kma_sync)');
    out.vilage = { rows: Math.min(vg.rows.length, FC_ROWS), base: b };
  }

  var obs = body.obs_hourly;
  if (obs && obs.length) {
    var sh = ss.getSheetByName(OBS_SHEET);
    if (!sh) return _json({ ok: false, error: 'no sheet ' + OBS_SHEET });
    var last = _obsLast(sh);
    var lastMs = last.date ? last.date.getTime() : 0;
    var add = [];
    for (var j = 0; j < obs.length; j++) {
      var o = obs[j], dt = _kst(o[0]);
      if (!dt || dt.getTime() <= lastMs) continue;   // 마지막 시각 이후만(정렬된 입력 전제)
      lastMs = dt.getTime();
      add.push([dt, o[1], o[2], o[3], o[4]]);
    }
    if (add.length) {
      var need = last.row + add.length;
      if (need > sh.getMaxRows()) sh.insertRowsAfter(sh.getMaxRows(), need - sh.getMaxRows());
      sh.getRange(last.row + 1, 1, add.length, 5).setValues(add);
    }
    out.obs_hourly = { added: add.length, skipped: obs.length - add.length, last: lastMs ? _fmt(new Date(lastMs)) : '' };
  }

  var g = ss.getSheetByName(GUIDE_SHEET);
  if (g) g.getRange(14, 1).setValue('마지막 조회 ' + when + ' KST (Pi 자동)');
  out.weather_last = _weatherLast();
  return _json(out);
}

function doGet(e) {
  var p = (e && e.parameter) || {};
  if (p.q === 'weather') return _json({ ok: true, version: CODE_VERSION, weather_last: _weatherLast() });
  var sh = _sheet();
  var last = sh.getLastRow();
  var summary = {};
  if (last >= 2) {
    var vals = sh.getRange(2, 1, last - 1, 2).getValues();
    for (var i = 0; i < vals.length; i++) {
      var t = vals[i][0], s = vals[i][1];
      if (!s) continue;
      if (!summary[s]) summary[s] = { count: 0, last: null };
      summary[s].count++;
      var ms = (t instanceof Date) ? t.getTime() : new Date(t).getTime();
      if (!isNaN(ms) && (summary[s].last === null || ms > summary[s].last)) summary[s].last = ms;
    }
    for (var k in summary) if (summary[k].last) summary[k].last = new Date(summary[k].last).toISOString();
  }
  var out = { ok: true, version: CODE_VERSION, site: SITE, sheet: SHEET_NAME, totalRows: Math.max(0, last - 1), bySensor: summary };
  if (p.callback) {
    return ContentService.createTextOutput(p.callback + '(' + JSON.stringify(out) + ')')
      .setMimeType(ContentService.MimeType.JAVASCRIPT);
  }
  return _json(out);
}

function _json(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
