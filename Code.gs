/*
 * Code.gs — 농막 샤오미 센서 이력을 '농막_시간별' 탭에 증분 기록하는 Apps Script 웹앱.
 *
 * 배포:
 *   1) 대상 구글시트에서  확장 프로그램 > Apps Script  열기
 *   2) 이 파일 내용 전체 붙여넣기, 아래 SECRET 를 원하는 값으로 변경
 *   3) 배포 > 새 배포 > 유형: 웹 앱
 *        - 실행 계정: 나
 *        - 액세스 권한: 모든 사용자
 *   4) 생성된 /exec URL 을 index.html 설정의 "Apps Script 웹앱 URL"에 입력
 *
 * 특징:
 *   - 중복 제거를 여기(서버)서 처리 → 페이지는 시트를 읽을 필요 없음, 업로드가 멱등(idempotent).
 *   - 중복 기준: (센서, 시각의 '                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    정시 버킷'). 페이지가 이미 정시로 반올림해 보냄.
 *   - 시각은 실제 날짜/시간 값으로 저장 → 나중에 MAXIFS 등으로 일별 집계 가능.
 */

var SECRET = 'CHANGE-ME';          // ← index.html 의 '공유 시크릿'과 동일하게
var SHEET_NAME = '농막_시간별';
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

function doPost(e) {
  var lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    var body = JSON.parse(e.postData.contents);
    if (String(body.secret || '') !== SECRET) {
      return _json({ ok: false, error: 'bad secret' });
    }
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
  var out = { ok: true, sheet: SHEET_NAME, totalRows: Math.max(0, last - 1), bySensor: summary };
  if (p.callback) {
    return ContentService.createTextOutput(p.callback + '(' + JSON.stringify(out) + ')')
      .setMimeType(ContentService.MimeType.JAVASCRIPT);
  }
  return _json(out);
}

function _json(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
