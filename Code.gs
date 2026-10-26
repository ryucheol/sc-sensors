/*
 * Code.gs — 농막/선반 센서 데이터 수집 Apps Script 웹앱 (SITE 접두사로 여러 시트 공용).
 *
 * ★★ 버전: 2026-10-27 v6  ← 붙여넣기 전에 이 줄 확인! ★★
 *   이 버전 요약: SITE 접두사 · mode current(+SSID/IP+배터리 예상일) · avg · hist(upsert) ·
 *                 batt(배터리 이력 → _배터리 탭) · 멱등 업로드.
 *   배포된 버전 확인법: 브라우저로 /exec 주소를 열면 {"version":"2026-10-27 v6", ...} 가 보임.
 *   (구버전이 배포돼 있으면 version 이 다르거나 없음 → 다시 붙여넣고 새 버전 배포)
 *
 * 변경 이력:
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

var CODE_VERSION = '2026-10-27 v6';   // ← /exec(doGet) 응답·붙여넣기 확인용. 고칠 때마다 갱신.
var SITE = '농막';                 // ← 장소 접두사. 두 번째 시트(예: 선반)는 '선반' 으로만 바꾸면 됨
var SECRET = 'CHANGE-ME';          // ← 게이트웨이의 FARMHUT_SECRET 과 동일하게
var SHEET_NAME = SITE + '_시간별';
var CURRENT_SHEET = SITE + '_현재';   // 현재값 스냅샷(센서별 1행, 덮어쓰기)
var AVG_SHEET = SITE + '_평균';       // 게이트웨이가 passive로 계산한 시간가중 평균(파생값)
var BATT_SHEET = SITE + '_배터리';    // 배터리 전압·% 이력(수명 추세 → 현재 탭 예상 소진일)
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

// 배터리 % 추세(최소제곱)로 예상 소진일을 계산해 현재 탭 H열에 기록. 임박(≤30일)하면 빨간 굵은 글씨.
function _updateBatteryForecast(battSh) {
  var DEATH_PCT = 5, WARN_DAYS = 30, MIN_PTS = 5, MIN_SPAN_D = 3, WINDOW_D = 21, DAY = 86400000;
  var last = battSh.getLastRow();
  if (last < 2) return;
  var n = Math.min(last - 1, 3000);                    // 최근 최대 3000행만
  var vals = battSh.getRange(last - n + 1, 1, n, 4).getValues();  // 시각,센서,V,%
  var now = Date.now(), cutoff = now - WINDOW_D * DAY, bySensor = {};
  for (var i = 0; i < vals.length; i++) {
    var t = vals[i][0], s = vals[i][1], pct = vals[i][3];
    if (!t || !s || pct === '' || pct == null) continue;
    var ms = (t instanceof Date) ? t.getTime() : new Date(t).getTime();
    if (isNaN(ms) || ms < cutoff) continue;
    (bySensor[s] = bySensor[s] || []).push([ms, Number(pct)]);
  }
  var cur = _currentSheet(), cl = cur.getLastRow();
  var names = cl >= 2 ? cur.getRange(2, 1, cl - 1, 1).getValues().map(function (r) { return r[0]; }) : [];
  if (cur.getRange(1, 8).getValue() !== '배터리 예상') cur.getRange(1, 8).setValue('배터리 예상');
  for (var s2 in bySensor) {
    var idx = names.indexOf(s2);
    if (idx < 0) continue;
    var pts = bySensor[s2], cell = cur.getRange(idx + 2, 8), msg = '', red = false;
    if (pts.length >= MIN_PTS) {
      var t0 = pts[0][0], spanD = (pts[pts.length - 1][0] - t0) / DAY;
      if (spanD >= MIN_SPAN_D) {
        var nP = pts.length, sx = 0, sy = 0, sxx = 0, sxy = 0;
        for (var k = 0; k < nP; k++) { var x = (pts[k][0] - t0) / DAY, y = pts[k][1]; sx += x; sy += y; sxx += x * x; sxy += x * y; }
        var denom = nP * sxx - sx * sx;
        if (denom !== 0) {
          var slope = (nP * sxy - sx * sy) / denom;          // %/일 (음수면 감소)
          var curPct = pts[nP - 1][1];
          if (slope < -0.05) {                               // 의미있게 감소 중일 때만 예측
            var daysLeft = (curPct - DEATH_PCT) / (-slope);
            if (daysLeft < 0) daysLeft = 0;
            var ds = Utilities.formatDate(new Date(now + daysLeft * DAY), Session.getScriptTimeZone(), 'yyyy-MM-dd');
            if (daysLeft <= WARN_DAYS) { msg = '⚠ ' + ds + ' 교체 (' + Math.round(daysLeft) + '일)'; red = true; }
            else { msg = '예상 ' + ds; }
          }
        }
      }
    }
    cell.setValue(msg);
    cell.setFontColor(red ? 'red' : null);
    cell.setFontWeight(red ? 'bold' : null);
  }
}

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
function doGet(e) {
  var p = (e && e.parameter) || {};
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
