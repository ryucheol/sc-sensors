# How the Xiaomi sensor works, and why we built the gateway this way

A study guide for students. It explains how the cheap Xiaomi Bluetooth thermometer (model **LYWSD03MMC**, sold as *Mijia Thermometer 2*) talks, what **GATT** is, and why this project moved from "connect to the sensor" to a **hybrid** design. It is written so that a student who knows basic programming can follow it, and who then reads a bit more can understand the code in this repository.

> 한국어판: [SENSOR_GUIDE.ko.md](SENSOR_GUIDE.ko.md)

> **Honest note.** Xiaomi publishes no official documentation for this sensor. Everything about its Bluetooth protocol here comes from community reverse engineering and from testing on real devices (see the *Development memo* in [README.md](../README.md)). Parts marked **(from the code)** are things you can verify by reading a file in this repository.

**Contents**
1. [What you will learn](#1-what-you-will-learn)
2. [The sensor in one minute](#2-the-sensor-in-one-minute)
3. [Bluetooth Low Energy in five minutes](#3-bluetooth-low-energy-in-five-minutes)
4. [Way A: listen to the broadcasts (passive)](#4-way-a-listen-to-the-broadcasts-passive)
5. [Way B: connect and read the history (GATT)](#5-way-b-connect-and-read-the-history-gatt)
6. [Why we changed the structure](#6-why-we-changed-the-structure)
7. [Pros and cons](#7-pros-and-cons)
8. [Why this matters for the heater control](#8-why-this-matters-for-the-heater-control)
9. [Design lessons you can reuse](#9-design-lessons-you-can-reuse)
10. [Exercises](#10-exercises)
11. [Glossary](#11-glossary)
12. [Further reading](#12-further-reading)
13. [한국어 요약](#13-한국어-요약)

---

## 1. What you will learn
- The difference between a Bluetooth **broadcast** and a Bluetooth **connection**.
- What a **GATT service and characteristic** are, with a real example (this sensor's history buffer).
- How an **encrypted broadcast** is decoded (AES-CCM and a per-device key).
- Why a real system mixes two data paths, and what each path costs (battery, completeness, complexity).
- Engineering habits for devices that are **unattended and sometimes offline**: store-and-forward, idempotent uploads, a battery budget.

Where to look in the code:

| Topic | File |
|---|---|
| Passive decoding (no connection) | [`passive_current.py`](../passive_current.py) |
| Connect and read history | [`farmhut_sync.py`](../farmhut_sync.py) (PC), [`passive_gateway.py`](../passive_gateway.py) (Raspberry Pi) |
| The 24/7 gateway that combines both | [`passive_gateway.py`](../passive_gateway.py) |
| Operations, battery, site switching | [OPERATIONS.md](../OPERATIONS.md), [PI_SETUP.md](../PI_SETUP.md) |

---

## 2. The sensor in one minute
- A small battery-powered thermometer and hygrometer with an LCD, a Bluetooth Low Energy (BLE) radio and a coin-cell battery.
- It **measures all the time** and **stores one record per hour** in its own memory: the highest and lowest temperature and humidity of that hour. This memory is a **ring buffer**: when it is full the oldest record is overwritten. In our tests it holds **about five months** of hours **(from the README)**.
- It **broadcasts** its current temperature, humidity and battery level over BLE. With the stock (original) firmware the broadcast is **encrypted**, and the values are sent **only when they change** and **separately and rarely**; for temperature and humidity this means roughly every ten minutes when nothing changes **(from [HEATER.md](../HEATER.md))**.
- It also lets a nearby device **connect** and read the history.

So there are two doors into the same sensor: the *broadcast* door and the *connection* door. The whole design of this project is about when to use which.

---

## 3. Bluetooth Low Energy in five minutes

### 3.1 Advertising versus connection
```
 Way A: ADVERTISING (broadcast)            Way B: CONNECTION (GATT)

   sensor ))))  ))))  ))))                   sensor <───────────> gateway
        anyone nearby may listen                    a private two-way link
   one-way, the sensor does not                the sensor must keep the radio
   know who listens                            busy while connected
```
- A BLE device regularly shouts a small packet (an **advertisement**) for anyone in range. Listening costs the sensor **nothing**: it would shout anyway. This is called **passive** reception.
- To ask a question and get an answer, a **central** (our gateway) opens a **connection** to the **peripheral** (the sensor). A connection uses the sensor's radio for much longer, so it costs **battery**. A sensor can usually serve **one connection at a time**.

Everyday comparison: advertising is a **radio station** (you tune in, the station does not care); a connection is a **phone call** (both sides are busy, and a call costs energy).

### 3.2 GATT: how data is organised inside a connected device
Once connected, the device presents its data like a small file system:

```
Service            (a group of related data, named by a UUID)
 └─ Characteristic (one piece of data, also a UUID)
     • properties: read / write / notify
     • a value: just bytes
```
- **UUID**: a 128-bit identifier, e.g. `ebe0ccb0-7a0a-4b0c-8a1a-6ff2997da3a6`.
- **read**: ask once, get the bytes. **write**: send bytes to the device. **notify**: after you *subscribe*, the device **pushes** new bytes to you by itself (good for streams).
- The meaning of the bytes is defined by the device maker. For this sensor it is **not published**, which is why people reverse-engineered it.

---

## 4. Way A: listen to the broadcasts (passive)

The sensor sends its readings in **MiBeacon** packets inside BLE advertisements, in the service-data field of the 16-bit service `0xFE95`. With the stock firmware the payload is **encrypted** with **AES-CCM**, using a secret **bindkey** that is different for every sensor.

### 4.1 What the packet looks like (as reverse-engineered)
```
 [ header: 5 bytes ][ MAC? 6 ][ capability? 1-3 ][ ciphertext ... ][ counter 3 ][ MIC 4 ]
   frame control (2)                                                   extension   integrity
   product id    (2)                                                   of the      check
   frame counter (1)                                                   counter
```
- **Frame control** has flag bits: `0x08` = payload is encrypted, `0x10` = the sensor's MAC address is included, `0x20` = a capability byte follows.
- **Nonce** (a number used once) for AES-CCM is built from parts of the packet: the MAC bytes, the product id plus frame counter, and the 3-byte counter near the end. Because the counter changes in every packet, the same reading is never encrypted the same way twice.
- **MIC** (message integrity code, 4 bytes) lets the receiver detect a wrong key or a damaged packet: decryption simply fails.
- The 4-byte tag length and the fixed extra data `0x11` are part of the scheme.

**(from the code)** [`passive_current.py`](../passive_current.py) does exactly this:
```python
nonce = bytes(sd[5:11]) + bytes(sd[2:5]) + bytes(sd[-7:-4])
mic   = bytes(sd[-4:])
ct    = bytes(sd[i:-7])          # i = start of the ciphertext after optional fields
AESCCM(key, tag_length=4).decrypt(nonce, ct + mic, b"\x11")
```

### 4.2 What is inside after decryption
A list of small records, each: `type (2 bytes, little endian) | length (1 byte) | value`.

| Type | Meaning | How to decode |
|---|---|---|
| `0x1004` | temperature | signed 16-bit ÷ 10 → °C |
| `0x1006` | humidity | unsigned 16-bit ÷ 10 → % |
| `0x100D` | temperature **and** humidity | two values as above |
| `0x100A` | battery | one byte, percent |

### 4.3 Getting the bindkey
The key is stored in your Xiaomi account, not in the sensor's public data. The community tool *Xiaomi-cloud-tokens-extractor* can read it from your own account (link at the end). **Treat the bindkey as a password.** In this repository it lives in `sensor_keys.json`, which is excluded from git; never publish it.

### 4.4 What passive gives you, and what it cannot
- ✅ **Zero extra battery use** and no connection failures.
- ✅ Good for **live** values and for computing averages.
- ❌ If the gateway is off, rebooting or out of range, the readings of that time are **lost for good**: a broadcast is not stored anywhere.
- ❌ Broadcasts only say "now". They do **not** give you the sensor's own hourly **minimum and maximum**.

That last limit is the reason for Way B.

---

## 5. Way B: connect and read the history (GATT)

### 5.1 The characteristics we use
All UUIDs share the form `ebe0cc??-7a0a-4b0c-8a1a-6ff2997da3a6`; the service itself is `ebe0ccb0-…`.

| Short id | Purpose | Format |
|---|---|---|
| `ccb7` | time counter | unsigned 32-bit: **seconds since the sensor booted** |
| `ccbb` | the **latest** history record | 14 bytes, see below |
| `ccba` | **start index pointer** | write a 32-bit index; the history stream then starts from that record (non-destructive) |
| `ccbc` | **history stream** | *notify*: one 14-byte record per notification |
| `ccc1` | current values | notify: signed 16-bit ÷ 100 → °C, then one byte of humidity % |

### 5.2 One history record (14 bytes)
```
 idx (u32) | ts (u32) | maxT (i16) | maxH (u8) | minT (i16) | minH (u8)
 record no | seconds since boot | ÷10 → °C | % | ÷10 → °C | %
```
**(from the code)** in [`farmhut_sync.py`](../farmhut_sync.py):
```python
idx, ts, maxt, maxh, mint, minh = struct.unpack_from("<IIhBhB", b, 0)
```
`<` means little-endian; `I` = unsigned 32-bit, `h` = signed 16-bit, `B` = unsigned byte.

### 5.3 The sensor has no real clock: converting time
The sensor does not know the date. It only counts seconds since boot. To label a record with a real time:
```
 boot_epoch = now − counter                 # when the sensor booted, in real time
 wall_time  = now − (counter − record.ts)   # when this record ended
```
Read the counter (`ccb7`) at the moment you connect, and take `now` from the gateway's clock. This is why a **correct clock on the gateway** matters. A Raspberry Pi usually has no battery-backed clock, so after a reboot without internet its time may be wrong (see rule H in [TODO.md](../TODO.md)).

### 5.4 Incremental sync: ask only for what you do not have
Reading five months of history over BLE would keep the sensor busy for a long time. Instead:
1. Find the record index of the **last hour you already stored**. From the code:
   `idx_of_last = (counter − (now − last_time)) / 3600 − 1`
2. Start a little earlier (**overlap of 2 hours**) so a rounding error cannot cause a gap. Duplicates are removed on the receiving side.
3. **Write** that index to `ccba` (seek), **subscribe** to `ccbc`, and collect records until you reach the latest index or the stream goes quiet.

```
 gateway                                  sensor
   │── connect ──────────────────────────▶│
   │── read ccb7 (counter) ──────────────▶│   "how long have you been running?"
   │── read ccbb (latest record) ────────▶│   "what is your newest record number?"
   │── write ccba = start_idx ───────────▶│   "start from record start_idx"
   │── subscribe ccbc ───────────────────▶│
   │◀──────── record, record, record … ───│   notifications
   │── disconnect ───────────────────────▶│
```

### 5.5 What a connection gives you, and what it costs
- ✅ The sensor's **own hourly min/max**: the *authoritative* record, complete even if the gateway was off.
- ✅ **Backfills** any gap automatically (the buffer holds months).
- ❌ Uses **battery**: every connection keeps the sensor's radio busy.
- ❌ Fragile: connections fail, streams stop in the middle, only one connection at a time (details in section 6).

---

## 6. Why we changed the structure

The first version only **connected** to the sensors (a phone web page using Web Bluetooth, and scripts on a PC). It worked, but it needed a person nearby, drained the sensor battery if repeated often, and could not give live values. The final design is a **hybrid** that runs on a Raspberry Pi all day. The path there, with the commit you can read for each step:

| Date | Commit | Problem | What we did | What it cost |
|---|---|---|---|---|
| 2026-10-25 | `5b304a8` | Manual readings from Mi Home screenshots. | Phone web app and PC scripts that **connect (GATT)** and upload history to a Google Sheet. | Needs a person nearby; Windows connections often drop; each read costs battery. |
| 2026-10-25 | `f80f371` | Need **live** values and automation without connecting. | **Passive** MiBeacon decoder with the bindkey, plus a Raspberry Pi gateway. | Must manage keys; the stock firmware sends data rarely. |
| 2026-10-26 | `bd97d4e` | Passive samples have gaps, but connecting every hour wastes battery. | **Hour-weighted averages** from passive samples; connect only **at start and once a day** for the authoritative history. | Averages are *derived*, so they live in a separate tab from the sensor's own min/max. |
| 2026-10-26 | `24a2eea` … `f469043` | Writing to the sheet on every tiny change is wasteful and noisy. | Upload when a value **changes**, with a **minimum interval** (cooldown), a periodic heartbeat, wait until temperature **and** humidity are both known, and a **1 % humidity dead band**. | A little delay; a deliberate dead band. |
| 2026-10-26 | `f6404a4`, `efe5acf` | On a Raspberry Pi (BlueZ) **scanning and connecting at the same time fails** (`InProgress`). | One lock: **pause the scanner while connecting**, then resume. | No live values for a short moment during each daily sync. |
| 2026-10-26 | `c107d75` | The cabin has internet **only when someone brings a phone hotspot**. | **Store-and-forward**: save every hourly value in a local **SQLite** file and upload later, in chunks; the server removes duplicates. | One more moving part (the queue database). |
| 2026-10-26 | `539f5e9` | When offline, the gateway still read the sensors' full buffers every day, for nothing. | **Skip the connection if the sheet cannot be reached**; the sensor keeps the data until the next online visit. | History in the sheet can lag until the next visit. |
| 2026-10-26 | `784f2ac` | Passive values are *approximate*; the sensor's min/max is *exact*. | The local database is the **single source of truth**. Passive min/max is saved first (`src = p`); when the authoritative record is read later it **overwrites** it (`src = s`). | More complex data model; you must keep the two sources distinct. |
| 2026-10-27 | `1f5fa90` | Batteries die silently. | Log the battery hourly; estimate the **expected empty date** from a 21-day trend and warn in red when under 30 days. | A forecast, not a guarantee. |
| 2026-11-01 | `409c729` | A first big history download could **stop halfway and silently drop the newest records**. | Compare the received record numbers with the target, find the **holes**, `write ccba` again to **resume**, repeat until complete (limits: 3 rounds without progress, 180 s per sensor). Verified on hardware by forcing a stop. | More code, but no silent data loss. |
| 2026-11-04 | `607e064` | The heater controller must notice a **single sensor** failing. | Record the **receive time per sensor**; two indoor sensors (ground floor and attic) back each other up. | Extra checks and a tuned stale limit. |

The resulting picture:
```
            ┌─────────────── Raspberry Pi gateway ───────────────┐
 sensor ))) │ passive scanner ──▶ live values, hourly averages   │
 (stock     │        │                    │                      │
  firmware) │        ▼                    ▼                      │
            │   latest.json         SQLite queue  ◀── history ◀──┼── connect: once at start
            │ (for the heater)     (single truth)       (GATT)   │            + once a day
            └──────────────────────────│─────────────────────────┘
                                       ▼ only when online
                                  Google Sheet
```

---

## 7. Pros and cons

### 7.1 The three ways to use the sensor

| | Connect only | Passive only | **Hybrid (our choice)** |
|---|---|---|---|
| Sensor battery | high if frequent | **none extra** | low (one short connection a day) |
| Live values | no | **yes** | **yes** |
| Hourly min/max exact | yes | **no** (approximate) | **yes** (authoritative overwrite) |
| Gaps when the gateway is off | none (buffer) | **lost** | none (buffer backfills) |
| Needs the bindkey | no | yes | yes |
| Complexity | low | medium | **high** |
| Works offline | partly | live only | **yes (store-and-forward)** |

### 7.2 Other choices we made

| Choice | Advantages | Disadvantages |
|---|---|---|
| **Stock firmware** (not flashed) | No risk of bricking the sensor; behaves as shipped | Encrypted broadcasts need the bindkey; slow updates (about 10 minutes when stable); battery about a year in our setup (author's estimate) |
| **Direct upload to a Google Sheet** | No server, no broker, no database to run; the sheet is storage, view, sharing and charts | The Apps Script has limits; not a real-time system; the secret in the script must be protected |
| **Local SQLite queue** | No data lost on power cuts, reboots or offline periods | Another file to back up; a duplicate-safe server is required |
| **Daily history sync** | Saves sensor battery | History in the sheet is up to a day late |
| **Raspberry Pi gateway** | Always on, cheap, Linux and BlueZ are more stable than Windows for BLE | You must manage a small computer (updates, clock, power) |

### 7.3 Alternatives you may want to study
A custom firmware such as **pvvx/ATC** makes the sensor broadcast **plain, open-format** data (for example the **BTHome** standard) so no decryption is needed, and it can update more often. The cost is flashing risk and a change of the device's behaviour. This project kept the stock firmware on purpose; both are valid engineering choices.

---

## 8. Why this matters for the heater control
The heater controller reads the newest temperature from a small file written by the gateway. Three facts from this guide shape its behaviour:
1. **The data can be old.** A stable sensor may broadcast only about every ten minutes, and the gateway file can lag a little more. So the controller treats data older than **45 minutes** as "unknown" (the limit is `stale_temp_min` in [`heater_config.json`](../heater_config.json); the reasoning is in [HEATER.md](../HEATER.md)). In frost-protection mode an unknown temperature means "switch the heater **on**".
2. **A broadcast is a snapshot of one moment.** The control loop is built around hysteresis and a safe default, not around split-second accuracy.
3. **The history path is for records, not for control.** Control uses the live (passive) path; the connection path only completes the long-term record.

See also [TODO.md](../TODO.md) for the planned forecast-based improvements.

---

## 9. Design lessons you can reuse
1. **Separate "live" from "authoritative".** Use a cheap path for now-values and an exact path to correct the record later.
2. **Treat battery as a budget.** Every connection has a cost; schedule it, make it incremental, skip it when it is useless (offline).
3. **Assume the network is unreliable.** Save locally first, upload when possible (**store-and-forward**).
4. **Make uploads idempotent.** The same record may arrive twice; the receiver keys rows by `(sensor, hour)` and ignores duplicates.
5. **Never fail silently.** The truncation bug (`409c729`) was dangerous because it *looked* like success. Log what you expected and what you received.
6. **Respect hardware limits.** One connection at a time; scan and connect cannot overlap on BlueZ. Put a lock around shared resources.
7. **Test on the real device.** The history-resume fix was verified by forcing the stream to stop.
8. **Keep interfaces small.** The gateway and the heater controller share only one file; either can crash without taking the other down.
9. **Keep secrets out of the repository.** Keys and passwords belong in ignored files or environment variables.

---

## 10. Exercises
Choose the level that fits you.

**Beginner**
1. Draw the two data paths (broadcast and connection) for one sensor, and write one sentence about the battery cost of each.
2. A history record in hex is `34 12 00 00 | 10 0E 00 00 | E6 00 | 3C | D2 00 | 38`. Decode `idx`, `ts` (seconds), the maximum and minimum temperature and humidity. (Hint: little-endian, temperature ÷ 10.)
3. The sensor counter reads 3,600,000 s and a record has `ts = 3,595,000`. Your clock says it is 12:00:00. When did that record end?

**Intermediate**
4. Read [`passive_current.py`](../passive_current.py) and list the order of steps from the advertisement bytes to the final temperature. What happens if the key is wrong?
5. Write a short function that decodes the object list (`type | length | value`) and test it with a made-up packet.
6. Explain why the overlap of 2 hours in the incremental sync is useful, and what the server does with the duplicate hours.

**Advanced**
7. Simulate **store-and-forward** with SQLite: generate hourly values, "go offline" for a day, then upload in chunks; make the upload idempotent and prove no row is lost or duplicated.
8. Implement **hole-aware resume**: write a fake stream that stops randomly, and a loop that detects missing record numbers and re-requests only the holes.
9. Compare the stock firmware and an open broadcast format (such as BTHome): list what you gain and lose, and decide which you would choose for a village sensor network with no internet.
10. Design the same system for **your own** problem (for example a greenhouse or a rice store): what is "live", what is "authoritative", what happens when the network is down, and what is the safe state when something fails?

<details>
<summary>Answers to the beginner exercises</summary>

- **Exercise 2:** `idx` = 0x1234 = 4660; `ts` = 0x0E10 = 3600 s (one hour after boot); `maxT` = 0xE6 = 230 → **23.0 °C**; `maxH` = **60 %**; `minT` = 0xD2 = 210 → **21.0 °C**; `minH` = 0x38 = **56 %**.
- **Exercise 3:** `counter − ts` = 3,600,000 − 3,595,000 = 5,000 s = 1 h 23 min 20 s. The record ended at 12:00:00 − 1:23:20 = **10:36:40**.
</details>

*Safety:* never share a sensor's bindkey or any password in public; use your own sensors and your own accounts.

---

## 11. Glossary
- **BLE (Bluetooth Low Energy)**: the low-power form of Bluetooth used by small sensors.
- **Advertising / advertisement**: a small packet a BLE device broadcasts regularly for anyone in range.
- **Passive scan**: only listening to advertisements, without asking or connecting.
- **Central / peripheral**: the device that connects (gateway) and the device that is connected to (sensor).
- **GATT**: the way a connected BLE device organises its data into services and characteristics.
- **Service / characteristic**: a group of data / one piece of data inside a service; both are named by a **UUID**.
- **Read / write / notify**: ask once / send data / the device pushes updates after you subscribe.
- **MiBeacon**: Xiaomi's format for advertisements (service `0xFE95`).
- **Bindkey**: the secret per-device key used to decrypt MiBeacon payloads.
- **AES-CCM**: an encryption mode that also checks integrity; uses a **nonce** and produces a **MIC** (message integrity code).
- **Nonce**: a number used only once per message, so identical data is never encrypted identically.
- **Ring buffer**: fixed-size memory where new data overwrites the oldest when full.
- **Hysteresis**: using two thresholds (on and off) so a switch does not flip back and forth.
- **Dead band**: a small change that is ignored on purpose.
- **Store-and-forward**: save data locally first and send it when a connection is available.
- **Idempotent**: doing the same operation twice has the same result as doing it once.
- **BlueZ**: the Bluetooth software stack on Linux (used on the Raspberry Pi).
- **Little-endian**: multi-byte numbers are stored with the least significant byte first.

---

## 12. Further reading
- [bleak](https://bleak.readthedocs.io): the Python BLE library used in this project.
- [pvvx/ATC_MiThermometer](https://github.com/pvvx/ATC_MiThermometer): custom firmware for these sensors; its issues page is an active community.
- [BTHome](https://bthome.io): an open standard for BLE sensor broadcasts.
- [JsBergbau/MiTemperature2](https://github.com/JsBergbau/MiTemperature2): a Python reader for this sensor (passive and connected).
- [Theengs/decoder](https://github.com/theengs/decoder): open-source decoder for many BLE devices, including MiBeacon.
- [Xiaomi-cloud-tokens-extractor](https://github.com/PiotrMachowski/Xiaomi-cloud-tokens-extractor): reads your own bindkeys from your own Xiaomi account.
- In this repository: [README.md](../README.md) (overview), [OPERATIONS.md](../OPERATIONS.md) (operation), [PI_SETUP.md](../PI_SETUP.md) (Raspberry Pi setup), [HEATER.md](../HEATER.md) (heater control).

---

## 13. 한국어 요약
- 샤오미 LYWSD03MMC는 **두 개의 문**이 있다. ① **방송(advertising, passive)**: 연결 없이 엿듣기 — 배터리 소모 0, 실시간 값에 적합, 하지만 놓친 건 영영 못 받고 시간별 min/max는 없다. ② **연결(GATT)**: 센서 내부의 **시간별 min/max 이력(링버퍼, 약 5개월)** 을 읽는다 — 정확하고 공백을 메우지만 배터리를 쓰고 연결이 불안정하다.
- 그래서 **하이브리드**: 실시간·평균은 passive, 정확한 이력은 **시작 시 1회 + 하루 1회**만 연결해 **증분**(마지막 이후 + 2시간 겹침)으로 받는다. 오프라인이면 연결 자체를 생략한다.
- 인터넷이 방문 때만 되므로 **로컬 SQLite가 단일 진실원천**이고 온라인일 때만 시트로 올린다(store-and-forward, 서버는 `(센서, 정시)`로 중복 제거).
- 구조가 바뀐 순서와 이유(커밋 해시 포함)는 6장 표, 장단점은 7장, 학습용 연습문제는 10장.
