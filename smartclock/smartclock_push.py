#!/usr/bin/env python3
"""스마트 날씨 시계(ESP32-C2)에 240x240 그림을 올리는 맥용 스크립트.

시계 웹 UI(/photo)에서 찾은 API:
  GET  /photo/list                      -> {"files":[{name,size,enabled}], total, used, interval}
  POST /photo/upload   (multipart "file")
  GET  /photo/delete?name=<파일명>
  GET  /photo/toggle?name=<파일명>&state=0|1   (슬라이드쇼 포함 여부)
  GET  /photo/interval?val=<초>
  GET  /theme/list,  /theme/toggle?id=<n>&state=0|1   (2 = Photo)

이 스크립트가 올리는 파일은 모두 'sc_' 접두사를 붙이고, 새 파일을 올린 뒤
이전 sc_* 파일을 지운다. 기존 사진(girl.jpg 등)은 건드리지 않는다
(--exclusive를 주면 슬라이드쇼 체크만 끈다. 파일은 지우지 않음).

플래시 마모를 막으려고 최소 간격 5분, 그림이 직전과 똑같으면 올리지 않음(--force로 둘 다 무시).

예:
  python3 smartclock_push.py --title "농막" --line "1층 12.3°C" --line "다락 9.8°C"
  python3 smartclock_push.py --image photo.png --exclusive
  python3 smartclock_push.py --farm farm_now.json
  python3 smartclock_push.py --farm-url        # 5분마다 launchd로 실행
  python3 smartclock_push.py --list
"""
import argparse, hashlib, io, json, os, sys, time, uuid
import urllib.request, urllib.parse
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

DEFAULT_HOST = os.environ.get("SMARTCLOCK_HOST", "192.168.0.156")   # DHCP라 바뀔 수 있음
PREFIX = "sc_"
SIZE = 240
from farm_screen import font   # 한글 글꼴 찾기(맥/Pi 공용)
STATE = Path(__file__).with_name(".smartclock_state.json")
MIN_INTERVAL_S = 5 * 60


# ---------- HTTP ----------
def api(host, path, params=None, timeout=8):
    url = f"http://{host}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def list_files(host):
    return json.loads(api(host, "/photo/list"))


def upload(host, name, data, mime="image/jpeg"):
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
            f"Content-Type: {mime}\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"http://{host}/photo/upload", data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        if r.status != 200:
            raise RuntimeError(f"upload HTTP {r.status}")


# ---------- 그림 ----------
def render_text(title, lines, bg="#101418", fg="#ffffff", accent="#ffad4d"):
    img = Image.new("RGB", (SIZE, SIZE), bg)
    d = ImageDraw.Draw(img)
    y = 18
    if title:
        d.text((SIZE // 2, y + 14), title, font=font(26), fill=accent, anchor="mm")
        y += 42
    # 줄 수에 맞춰 글자 크기 자동 조절
    n = max(len(lines), 1)
    fs = max(18, min(40, (SIZE - y - 30) // n - 6))
    for ln in lines:
        d.text((SIZE // 2, y + fs // 2 + 4), ln, font=font(fs), fill=fg, anchor="mm")
        y += fs + 8
    d.text((SIZE // 2, SIZE - 14), datetime.now().strftime("%m/%d %H:%M 갱신"),
           font=font(14, bold=False), fill="#6e7f90", anchor="mm")
    return img


def load_image(path):
    img = Image.open(path).convert("RGB")
    return ImageOps.fit(img, (SIZE, SIZE), Image.LANCZOS)   # 웹 UI처럼 가운데 정사각형 자르기


def to_jpeg(img, quality=85):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


# ---------- 온도 기록(그래프용) ----------
HIST = Path(__file__).with_name(".smartclock_hist.json")


def local_hist(d, keep_h=26):
    """웹앱이 시간별 기록을 안 줄 때: 5분마다 받은 현재값을 로컬에 쌓아 그래프에 쓴다."""
    from datetime import timedelta
    h = json.loads(HIST.read_text()) if HIST.exists() else {}
    if d["updated"]:
        for label, t, _ in d["rooms"]:
            if t is None:
                continue
            pts = h.setdefault(label, [])
            ts = d["updated"].isoformat()
            if not pts or pts[-1][0] < ts:
                pts.append([ts, t])
    cut = (datetime.now() - timedelta(hours=keep_h)).isoformat()
    h = {k: [p for p in v if p[0] >= cut] for k, v in h.items()}
    HIST.write_text(json.dumps(h, ensure_ascii=False))
    return {k: [(datetime.fromisoformat(ts), t) for ts, t in v] for k, v in h.items()}


# ---------- 메인 ----------
def push(host, img, exclusive=False, force=False, dry_run=False):
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    since = time.time() - state.get("last_push", 0)
    # launchd가 정확히 5분마다 깨우면 몇 초 모자랄 수 있어 1분 여유를 둔다
    if not force and not dry_run and since < MIN_INTERVAL_S - 60:
        print(f"{datetime.now():%m-%d %H:%M} 마지막 업로드 후 {since/60:.0f}분 — 최소 {MIN_INTERVAL_S//60}분 간격이라 생략 (--force로 무시)")
        return

    data = to_jpeg(img)
    digest = hashlib.sha1(data).hexdigest()
    if not force and not dry_run and digest == state.get("last_hash"):
        print(f"{datetime.now():%m-%d %H:%M} 변화 없음 — 업로드 생략")
        return
    name = PREFIX + datetime.now().strftime("%m%d_%H%M") + ".jpg"
    if dry_run:
        Path(name).write_bytes(data)
        print(f"[dry-run] {name} ({len(data)} B) 저장만 함")
        return

    info = list_files(host)
    free = info["total"] - info["used"]
    if len(data) > free:
        sys.exit(f"시계 저장공간 부족: 필요 {len(data)} B, 여유 {free} B")

    upload(host, name, data)
    for f in info["files"]:
        if f["name"].startswith(PREFIX) and f["name"] != name:
            api(host, "/photo/delete", {"name": f["name"]})
        elif exclusive and f.get("enabled", True) and not f["name"].startswith(PREFIX):
            api(host, "/photo/toggle", {"name": f["name"], "state": 0})
    api(host, "/photo/toggle", {"name": name, "state": 1})

    STATE.write_text(json.dumps({"last_push": time.time(), "last_name": name, "last_hash": digest}))
    print(f"{datetime.now():%m-%d %H:%M} 업로드 완료: {name} ({len(data)} B)")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--title")
    p.add_argument("--line", action="append", default=[], help="본문 줄 (여러 번 가능)")
    p.add_argument("--image", help="이미지 파일을 240x240으로 잘라 올림")
    p.add_argument("--farm-url", action="store_true", help="secrets/farm_webapp.json의 Apps Script 웹앱에서 농막 값을 읽어 화면 생성")
    p.add_argument("--farm", metavar="JSON", help="'농막_현재' 탭 값(행 리스트) JSON으로 농막 화면 생성")
    p.add_argument("--exclusive", action="store_true", help="다른 사진은 슬라이드쇼에서 빼기")
    p.add_argument("--force", action="store_true", help="최소 간격 무시")
    p.add_argument("--dry-run", action="store_true", help="업로드 없이 jpg만 저장")
    p.add_argument("--list", action="store_true", help="시계 파일 목록 출력")
    a = p.parse_args()

    if a.list:
        info = list_files(a.host)
        for f in info["files"]:
            print(f"{'✓' if f.get('enabled', True) else ' '} {f['name']:24} {f['size']:>8} B")
        print(f"사용 {info['used']}/{info['total']} B, 슬라이드 간격 {info['interval']}초")
        return
    if a.farm_url:
        import farm_screen
        cfg = json.loads(Path(__file__).with_name("secrets").joinpath("farm_webapp.json").read_text())
        # q=now: sheets/Code.gs v15 doGet 분기. 단독 웹앱(doGet 하나)은 q를 무시하므로 같이 보내도 무방
        url = cfg["url"] + "?" + urllib.parse.urlencode({"q": "now", "t": cfg["token"]})
        with urllib.request.urlopen(url, timeout=30) as r:   # 구글이 302로 넘김 → urllib가 따라감
            resp = json.loads(r.read().decode())
        # 응답: 행 리스트(v15/단독 웹앱) 또는 {"now": 행, "hist": '최근5일_시간별' 행}(v16)
        if isinstance(resp, dict) and "now" not in resp:
            sys.exit(f"웹앱 오류: {resp}")
        rows = resp["now"] if isinstance(resp, dict) else resp
        d = farm_screen.parse(rows)
        hist = local_hist(d)
        if isinstance(resp, dict) and resp.get("hist"):
            hist = farm_screen.hist_from_rows(resp["hist"])
        img = farm_screen.render(d, hist=hist)
    elif a.farm:
        import farm_screen
        img = farm_screen.render(farm_screen.parse(json.loads(Path(a.farm).read_text())))
    elif a.image:
        img = load_image(a.image)
    elif a.title or a.line:
        img = render_text(a.title, a.line)
    else:
        p.error("--farm-url, --farm, --title/--line, --image 중 하나는 필요합니다")
    push(a.host, img, exclusive=a.exclusive, force=a.force, dry_run=a.dry_run)


if __name__ == "__main__":
    main()
