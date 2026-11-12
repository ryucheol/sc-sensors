// Shelly frost guard: Pi heartbeat watchdog + Qingping BLE temperature -> standalone frost hysteresis
// Pi alive (heartbeat < HB_TIMEOUT): do nothing, Pi controls the switch.
// Pi dead: ON below FROST_ON, OFF above FROST_OFF using Qingping temp.
// Sensor stale too (> SENSOR_TIMEOUT): force ON (heater has its own mechanical thermostat).
// ACT=false -> dry run: decide and report only, never switch.
let CFG = {
  ACT: false,
  QP_MAC: "58:2d:34:87:2f:64",
  HB_TIMEOUT: 600,
  SENSOR_TIMEOUT: 1800,
  FROST_ON: 3.0,
  FROST_OFF: 5.0,
  TICK_MS: 30000
};
let st = { hb_ts: 0, hb_n: 0, t: null, h: null, co2: null, s_ts: 0, s_n: 0, rssi: null,
           mode: "boot", want: null, last_act: "", boot: 0 };

function now() { return Shelly.getComponentStatus("sys").unixtime; }
function u16(s, i) { return s.at(i) | (s.at(i + 1) << 8); }
function s16(s, i) { let v = u16(s, i); return v > 32767 ? v - 65536 : v; }

function onScan(ev, res) {
  if (ev !== BLE.Scanner.SCAN_RESULT || res.addr !== CFG.QP_MAC || !res.service_data) return;
  let p = res.service_data["fdcd"];
  if (!p) return;
  let i = 8, got = false;
  while (i + 2 <= p.length) {
    let t = p.at(i), ln = p.at(i + 1);
    if (t === 0x01 && ln === 4) { st.t = s16(p, i + 2) / 10; st.h = u16(p, i + 4) / 10; got = true; }
    else if (t === 0x13 && ln === 2) { st.co2 = u16(p, i + 2); got = true; }
    i += 2 + ln;
  }
  if (got) { st.s_ts = now(); st.s_n++; st.rssi = res.rssi; }
}

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
  if (n - st.hb_ts < CFG.HB_TIMEOUT) { st.mode = "pi"; st.want = null; return; }
  if (st.s_ts === 0 || n - st.s_ts > CFG.SENSOR_TIMEOUT) { st.mode = "blind"; setSwitch(true, "sensor-stale"); return; }
  st.mode = "frost";
  if (st.t < CFG.FROST_ON) setSwitch(true, "t=" + JSON.stringify(st.t));
  else if (st.t > CFG.FROST_OFF) setSwitch(false, "t=" + JSON.stringify(st.t));
}

// GET /script/<id>/hb   -> Pi heartbeat (memory only, no flash write)
HTTPServer.registerEndpoint("hb", function (req, res) {
  st.hb_ts = now(); st.hb_n++;
  res.code = 200; res.body = JSON.stringify({ ok: true, mode: st.mode, t: st.t, s_age: st.s_ts ? now() - st.s_ts : null });
  res.send();
});
// GET /script/<id>/status -> full state for Pi / humans
HTTPServer.registerEndpoint("status", function (req, res) {
  let n = now();
  res.code = 200;
  res.body = JSON.stringify({ act: CFG.ACT, mode: st.mode, want: st.want, t: st.t, h: st.h, co2: st.co2, rssi: st.rssi,
    s_age: st.s_ts ? n - st.s_ts : null, s_n: st.s_n, hb_age: st.hb_ts ? n - st.hb_ts : null, hb_n: st.hb_n,
    last_act: st.last_act, up: n - st.boot, cfg: CFG });
  res.send();
});

st.boot = now();
BLE.Scanner.Subscribe(onScan);
if (!BLE.Scanner.isRunning()) BLE.Scanner.Start({ duration_ms: BLE.Scanner.INFINITE_SCAN, active: false });
Timer.set(CFG.TICK_MS, true, tick);
print("frost guard started, ACT=" + JSON.stringify(CFG.ACT));
