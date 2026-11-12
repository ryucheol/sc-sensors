// MiBeacon (Xiaomi / pvvx "Mi encrypted") decryption for Shelly Gen3 scripts.
// AES-CCM (tag 4, nonce 12, L=3, AAD 0x11) built from the firmware's AES-ECB (AES.encrypt mode ECB, fw >= 1.6.0).
// Same nonce layout as gateway/passive_gateway.py decrypt_mibeacon:
//   nonce = mac_le(sd[5:11]) + devid+cnt(sd[2:5]) + ext_cnt(sd[-7:-4]); tag = sd[-4:]
// Input sd: service data 0xFE95 as a byte string (res.service_data["fe95"]). Key: 16-byte ArrayBuffer.
// Returns {temp, humi, batt} or null (not encrypted / bad tag / unknown objects).

function mb_ecb(key, blk) {           // blk: Uint8Array(16) -> Uint8Array(16)
  return new Uint8Array(AES.encrypt(blk.buffer, key, { mode: "ECB" }));
}
function mb_hexkey(h) {
  let u = new Uint8Array(16);
  for (let i = 0; i < 16; i++) u[i] = parseInt(h.substr(i * 2, 2), 16);
  return u.buffer;
}
function mb_decrypt(sd, key) {
  let n = sd.length;
  if (n < 15) return null;
  let fc = sd.at(0) | (sd.at(1) << 8);
  if (!(fc & 0x08)) return null;                 // not encrypted
  let i = 5;
  if (fc & 0x10) i += 6;                         // MAC present
  if (fc & 0x20) { let cap = sd.at(i); i += 1; if (cap & 0x20) i += 2; }
  let clen = n - 7 - i;
  if (clen <= 0 || clen > 16) return null;       // one block is enough for sensor objects
  let nonce = new Uint8Array(12), k;
  for (k = 0; k < 6; k++) nonce[k] = sd.at(5 + k);
  for (k = 0; k < 3; k++) nonce[6 + k] = sd.at(2 + k);
  for (k = 0; k < 3; k++) nonce[9 + k] = sd.at(n - 7 + k);
  // counter blocks A_i = 0x02 | nonce | i(3 bytes)
  let a = new Uint8Array(16);
  a[0] = 0x02; for (k = 0; k < 12; k++) a[1 + k] = nonce[k];
  a[15] = 0; let s0 = mb_ecb(key, a);
  a[15] = 1; let s1 = mb_ecb(key, a);
  let p = new Uint8Array(clen);
  for (k = 0; k < clen; k++) p[k] = sd.at(i + k) ^ s1[k];
  // CBC-MAC: B0 = 0x4A | nonce | len(3), B1 = 00 01 11 00.., then plaintext block
  let x = new Uint8Array(16);
  x[0] = 0x4A; for (k = 0; k < 12; k++) x[1 + k] = nonce[k]; x[15] = clen;
  x = mb_ecb(key, x);
  x[1] ^= 0x01; x[2] ^= 0x11;
  x = mb_ecb(key, x);
  for (k = 0; k < clen; k++) x[k] ^= p[k];
  x = mb_ecb(key, x);
  for (k = 0; k < 4; k++) if (((x[k] ^ s0[k]) & 0xFF) !== sd.at(n - 4 + k)) return null;   // tag check
  // objects: id(2 LE) len(1) value
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
