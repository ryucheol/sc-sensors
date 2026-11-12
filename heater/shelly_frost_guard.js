// Shelly frost guard v2: Pi heartbeat watchdog + standalone frost control from BLE sensors.
// Sensors: Qingping CGP22C (plain, 0xFDCD), pvvx 1F indoor and outdoor (Mi encrypted 0xFE95, AES-CCM via ECB).
// Pi alive (heartbeat < HB_TIMEOUT): do nothing, Pi controls the switch.
// Pi dead:
//   indoor T = min of fresh indoor sensors (1F pvvx, Qingping)
//   if outdoor fresh: predict indoor after LEAD_MIN with tau TAU_MIN (Newton cooling) -> start early when cold outside
//   ON if T < FROST_ON or T_pred < FROST_ON; OFF if T > FROST_OFF and T_pred >= FROST_ON
//   no indoor sensor: ON if outdoor < FROST_OFF, else OFF. No sensor at all: ON (heater has a mechanical thermostat).
// pvvx bindkeys are read from KVS "mbkey_floor1" / "mbkey_out" (hex), never stored in this file.
// KMA ultra-short forecast (6 h, hourly) fetched directly every FC_EVERY_S with the key in KVS "kma_key":
//   pageNo=5&numOfRows=6 returns only T1H (category order fixed), ~1 KB. Outdoor for prediction = min(sensor, 6 h forecast min).
// ACT=false -> dry run: decide and report only.
let CFG = {
  ACT: false,
  QP_MAC: "58:2d:34:87:2f:64",
  FLOOR1_MAC: "a4:c1:38:6a:77:be",
  OUT_MAC: "a4:c1:38:b4:5e:13",
  HB_TIMEOUT: 600,
  SENSOR_TIMEOUT: 1800,
  FROST_ON: 3.0,
  FROST_OFF: 5.0,
  TAU_MIN: 550,
  LEAD_MIN: 60,
  TICK_MS: 30000,
  NX: 57,
  NY: 99,
  FC_EVERY_S: 3600,
  FC_MAX_AGE_S: 10800
};
let S = {
  qp: { t: null, h: null, co2: null, ts: 0, n: 0, rssi: null },
  floor1: { t: null, h: null, batt: null, ts: 0, n: 0, rssi: null, bad: 0 },
  out: { t: null, h: null, batt: null, ts: 0, n: 0, rssi: null, bad: 0 }
};
let KEY = { floor1: null, out: null };
let FC = { min6: null, vals: null, base: null, ts: 0, ok: 0, err: 0, last_err: "", try_ts: 0 };
let st = { hb_ts: 0, hb_n: 0, mode: "boot", want: null, t_in: null, t_pred: null, t_out: null, t_out_src: "none", last_act: "", boot: 0 };

function now() { return Shelly.getComponentStatus("sys").unixtime; }
function u16(s, i) { return s.at(i) | (s.at(i + 1) << 8); }
function s16(s, i) { let v = u16(s, i); return v > 32767 ? v - 65536 : v; }

function mb_ecb(key, blk) { return new Uint8Array(AES.encrypt(blk.buffer, key, { mode: "ECB" })); }
function mb_hexkey(h) {
  if (!h || h.length !== 32) return null;
  let u = new Uint8Array(16);
  for (let i = 0; i < 16; i++) u[i] = parseInt(h.substr(i * 2, 2), 16);
  return u.buffer;
}
function mb_decrypt(sd, key) {
  let n = sd.length;
  if (n < 15) return null;
  let fc = sd.at(0) | (sd.at(1) << 8);
  if (!(fc & 0x08)) return null;
  let i = 5;
  if (fc & 0x10) i += 6;
  if (fc & 0x20) { let cap = sd.at(i); i += 1; if (cap & 0x20) i += 2; }
  let clen = n - 7 - i;
  if (clen <= 0 || clen > 16) return null;
  let nonce = new Uint8Array(12), k;
  for (k = 0; k < 6; k++) nonce[k] = sd.at(5 + k);
  for (k = 0; k < 3; k++) nonce[6 + k] = sd.at(2 + k);
  for (k = 0; k < 3; k++) nonce[9 + k] = sd.at(n - 7 + k);
  let a = new Uint8Array(16);
  a[0] = 0x02; for (k = 0; k < 12; k++) a[1 + k] = nonce[k];
  a[15] = 0; let s0 = mb_ecb(key, a);
  a[15] = 1; let s1 = mb_ecb(key, a);
  let p = new Uint8Array(clen);
  for (k = 0; k < clen; k++) p[k] = sd.at(i + k) ^ s1[k];
  let x = new Uint8Array(16);
  x[0] = 0x4A; for (k = 0; k < 12; k++) x[1 + k] = nonce[k]; x[15] = clen;
  x = mb_ecb(key, x);
  x[1] ^= 0x01; x[2] ^= 0x11;
  x = mb_ecb(key, x);
  for (k = 0; k < clen; k++) x[k] ^= p[k];
  x = mb_ecb(key, x);
  for (k = 0; k < 4; k++) if (((x[k] ^ s0[k]) & 0xFF) !== sd.at(n - 4 + k)) return null;
  let out = {}, got = false; i = 0;
  while (i + 3 <= clen) {
    let oid = p[i] | (p[i + 1] << 8), ln = p[i + 2];
    if (i + 3 + ln > clen) break;
    let v0 = p[i + 3], v1 = p[i + 4], v2 = p[i + 5], v3 = p[i + 6];
    if (oid === 0x1004 && ln === 2) { let t = v0 | (v1 << 8); if (t > 32767) t -= 65536; out.temp = t / 10; got = true; }
    else if (oid === 0x1006 && ln === 2) { out.humi = (v0 | (v1 << 8)) / 10; got = true; }
    else if (oid === 0x100D && ln === 4) { let t2 = v0 | (v1 << 8); if (t2 > 32767) t2 -= 65536;
      out.temp = t2 / 10; out.humi = (v2 | (v3 << 8)) / 10; got = true; }
    else if (oid === 0x100A && ln === 1) { out.batt = v0; got = true; }
    i += 3 + ln;
  }
  return got ? out : null;
}

function handleAdv(addr, sdmap, rssi) {
  if (!sdmap) return;
  if (addr === CFG.QP_MAC) {
    let p = sdmap["fdcd"];
    if (!p) return;
    let i = 8, got = false;
    while (i + 2 <= p.length) {
      let t = p.at(i), ln = p.at(i + 1);
      if (t === 0x01 && ln === 4) { S.qp.t = s16(p, i + 2) / 10; S.qp.h = u16(p, i + 4) / 10; got = true; }
      else if (t === 0x13 && ln === 2) { S.qp.co2 = u16(p, i + 2); got = true; }
      i += 2 + ln;
    }
    if (got) { S.qp.ts = now(); S.qp.n++; S.qp.rssi = rssi; }
    return;
  }
  let name = addr === CFG.FLOOR1_MAC ? "floor1" : (addr === CFG.OUT_MAC ? "out" : null);
  if (name === null || KEY[name] === null) return;
  let sd = sdmap["fe95"];
  if (!sd) return;
  let r = mb_decrypt(sd, KEY[name]);
  let e = S[name];
  if (r === null) { e.bad++; return; }
  if (r.temp !== undefined) e.t = r.temp;
  if (r.humi !== undefined && r.humi > 0) e.h = r.humi;
  if (r.batt !== undefined && r.batt > 0) e.batt = r.batt;
  if (r.temp !== undefined) { e.ts = now(); e.n++; }
  e.rssi = rssi;
}

function onScan(ev, res) {
  if (ev !== BLE.Scanner.SCAN_RESULT) return;
  handleAdv(res.addr, res.service_data, res.rssi);
}

function p2(n) { return (n < 10 ? "0" : "") + JSON.stringify(n); }
function ymd(days) { let z = days + 719468, era = Math.floor(z / 146097), doe = z - era * 146097;
  let yoe = Math.floor((doe - Math.floor(doe / 1460) + Math.floor(doe / 36524) - Math.floor(doe / 146096)) / 365);
  let y = yoe + era * 400, doy = doe - (365 * yoe + Math.floor(yoe / 4) - Math.floor(yoe / 100)), mp = Math.floor((5 * doy + 2) / 153);
  let d = doy - Math.floor((153 * mp + 2) / 5) + 1, m = mp < 10 ? mp + 3 : mp - 9; if (m <= 2) y++;
  return JSON.stringify(y) + p2(m) + p2(d); }
function fcFetch() {
  FC.try_ts = now();
  Shelly.call("KVS.Get", { key: "kma_key" }, function (r) {
    if (!r || !r.value) { FC.last_err = "no key"; return; }
    let k = now() + 32400 - 45 * 60, days = Math.floor(k / 86400), hh = Math.floor((k % 86400) / 3600);
    let bd = ymd(days), bt = p2(hh) + "30";
    let url = "https://apihub.kma.go.kr/api/typ02/openApi/VilageFcstInfoService_2.0/getUltraSrtFcst?pageNo=5&numOfRows=6&dataType=JSON"
      + "&base_date=" + bd + "&base_time=" + bt + "&nx=" + JSON.stringify(CFG.NX) + "&ny=" + JSON.stringify(CFG.NY) + "&authKey=" + r.value;
    Shelly.call("HTTP.GET", { url: url, timeout: 20 }, function (res, ec, em) {
      if (ec !== 0 || !res || res.code !== 200) { FC.err++; FC.last_err = "http " + JSON.stringify(ec) + " " + (res ? JSON.stringify(res.code) : ""); return; }
      try {
        let it = JSON.parse(res.body).response.body.items.item, v = [], mn = null;
        for (let i = 0; i < it.length; i++) {
          if (it[i].category !== "T1H") continue;
          let t = Number(it[i].fcstValue); v.push(t); if (mn === null || t < mn) mn = t;
        }
        if (mn === null) { FC.err++; FC.last_err = "no T1H"; return; }
        FC.min6 = mn; FC.vals = v; FC.base = bd + " " + bt; FC.ts = now(); FC.ok++; FC.last_err = "";
      } catch (e) { FC.err++; FC.last_err = "parse"; }
    });
  });
}

function fresh(e, n) { return e.ts > 0 && n - e.ts <= CFG.SENSOR_TIMEOUT && e.t !== null; }

function setSwitch(on, why) {
  st.want = on;
  let out = Shelly.getComponentStatus("switch:0").output;
  if (out === on) return;
  st.last_act = (CFG.ACT ? "" : "DRY ") + (on ? "ON " : "OFF ") + why + " @" + JSON.stringify(now());
  print(st.last_act);
  if (CFG.ACT) Shelly.call("Switch.Set", { id: 0, on: on });
}

function tick() {
  let n = now();
  st.t_in = null; st.t_pred = null;
  if (fresh(S.floor1, n)) st.t_in = S.floor1.t;
  if (fresh(S.qp, n) && (st.t_in === null || S.qp.t < st.t_in)) st.t_in = S.qp.t;
  if (n - FC.try_ts >= CFG.FC_EVERY_S) fcFetch();
  let tout = fresh(S.out, n) ? S.out.t : null;
  let tfc = (FC.ts > 0 && n - FC.ts <= CFG.FC_MAX_AGE_S) ? FC.min6 : null;
  st.t_out_src = tout === null ? (tfc === null ? "none" : "forecast") : (tfc !== null && tfc < tout ? "forecast" : "sensor");
  if (tfc !== null && (tout === null || tfc < tout)) tout = tfc;
  st.t_out = tout;
  if (st.t_in !== null) {
    st.t_pred = st.t_in;
    if (tout !== null) st.t_pred = tout + (st.t_in - tout) * Math.exp(-CFG.LEAD_MIN / CFG.TAU_MIN);
    st.t_pred = Math.round(st.t_pred * 10) / 10;
  }
  if (n - st.hb_ts < CFG.HB_TIMEOUT) { st.mode = "pi"; st.want = null; return; }
  if (st.t_in === null && tout === null) { st.mode = "blind"; setSwitch(true, "no-sensor"); return; }
  if (st.t_in === null) {
    st.mode = "out-only";
    if (tout < CFG.FROST_OFF) setSwitch(true, "out=" + JSON.stringify(tout));
    else setSwitch(false, "out=" + JSON.stringify(tout));
    return;
  }
  st.mode = tout === null ? "frost" : "frost+out";
  let why = "in=" + JSON.stringify(st.t_in) + " pred=" + JSON.stringify(st.t_pred);
  if (st.t_in < CFG.FROST_ON || st.t_pred < CFG.FROST_ON) setSwitch(true, why);
  else if (st.t_in > CFG.FROST_OFF && st.t_pred >= CFG.FROST_ON) setSwitch(false, why);
}

function loadKeys() {
  Shelly.call("KVS.Get", { key: "mbkey_floor1" }, function (r) { if (r && r.value) KEY.floor1 = mb_hexkey(r.value); });
  Shelly.call("KVS.Get", { key: "mbkey_out" }, function (r) { if (r && r.value) KEY.out = mb_hexkey(r.value); });
}

function sens(e, n) {
  return { t: e.t, h: e.h, batt: e.batt, co2: e.co2, age: e.ts ? n - e.ts : null, n: e.n, rssi: e.rssi, bad: e.bad };
}

HTTPServer.registerEndpoint("hb", function (req, res) {
  st.hb_ts = now(); st.hb_n++;
  res.code = 200;
  res.body = JSON.stringify({ ok: true, mode: st.mode, t_in: st.t_in, t_pred: st.t_pred });
  res.send();
});
HTTPServer.registerEndpoint("status", function (req, res) {
  let n = now();
  res.code = 200;
  res.body = JSON.stringify({ act: CFG.ACT, mode: st.mode, want: st.want, t_in: st.t_in, t_pred: st.t_pred,
    t_out: st.t_out, t_out_src: st.t_out_src,
    qp: sens(S.qp, n), floor1: sens(S.floor1, n), out: sens(S.out, n),
    fc: { min6: FC.min6, vals: FC.vals, base: FC.base, age: FC.ts ? n - FC.ts : null, ok: FC.ok, err: FC.err, last_err: FC.last_err },
    keys: { floor1: KEY.floor1 !== null, out: KEY.out !== null },
    hb_age: st.hb_ts ? n - st.hb_ts : null, hb_n: st.hb_n, last_act: st.last_act, up: n - st.boot, cfg: CFG });
  res.send();
});
HTTPServer.registerEndpoint("reload", function (req, res) {
  loadKeys(); res.code = 200; res.body = "{\"ok\":true}"; res.send();
});

st.boot = now();
loadKeys();
BLE.Scanner.Subscribe(onScan);
if (!BLE.Scanner.isRunning()) BLE.Scanner.Start({ duration_ms: BLE.Scanner.INFINITE_SCAN, active: false });
Timer.set(CFG.TICK_MS, true, tick);
print("frost guard v2 started, ACT=" + JSON.stringify(CFG.ACT));
