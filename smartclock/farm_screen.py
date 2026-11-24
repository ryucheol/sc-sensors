"""농막 상태 240x240 화면.

입력은 '농막_현재' 탭 값 그대로(행 리스트). 1열 라벨로 찾으므로 행 순서가 바뀌어도 된다.
Pi에서는 gspread의 ws.get_values() 결과를, 맥에서는 그 JSON 덤프를 넘기면 된다.
"""
import re
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

# 한글 글꼴 후보: (경로, 굵은 index, 보통 index). 맥 → Pi(fonts-noto-cjk / fonts-nanum) 순.
FONTS = [
    ("/System/Library/Fonts/AppleSDGothicNeo.ttc", 6, 2),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 1, 1),
    ("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", 0, 0),
]
S = 240
BG, CARD, FG, SUB = "#0f1419", "#1a222b", "#f2f5f8", "#7f8fa0"
RED, ORANGE, BLUE, CYAN, GREEN = "#ff5a5f", "#ffad4d", "#4da3ff", "#7fd6e8", "#5fd38d"


def font(size, bold=True):
    for path, b, n in FONTS:
        try:
            return ImageFont.truetype(path, size, index=b if bold else n)
        except OSError:
            continue
    raise SystemExit("한글 글꼴이 없습니다 (Pi: sudo apt install fonts-noto-cjk)")


_f = font


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def parse(rows):
    by = {}
    for r in rows:   # 같은 라벨이 대시보드에도 있으므로 처음 나온 행(센서 현재값)을 쓴다
        if r and str(r[0]).strip():
            by.setdefault(str(r[0]).strip(), r)
    def room(label, key):
        r = by.get(key, [])
        return (label, _num(r[1] if len(r) > 1 else None), _num(r[2] if len(r) > 2 else None))
    h = by.get("히터", [])
    upd = next((r[4] for r in rows[1:4] if len(r) > 4 and r[4]), "")
    m = re.search(r"(\d{4})-(\d\d)-(\d\d)\(.\)\s*(\d\d):(\d\d)", upd)
    return {
        "rooms": [room("1층", "내부1층"), room("다락", "다락"), room("바깥", "바닥외부")],
        "heater_mode": re.sub(r"^\W+|\s*\d+/\d+.*$", "", h[1]).strip() if len(h) > 1 else "",
        "heater_on": len(h) > 2 and h[2].strip().upper().startswith("ON"),
        "arrival": re.sub(r"\s*·.*$", "", h[3]).replace(".0°C", "°C") if len(h) > 3 else "",
        "updated": datetime(*map(int, m.groups())) if m else None,
        "garden_min": _num(by.get("텃밭 예상 최저", [None, None])[1]),
        "frost": re.sub(r"\s*\(.*\)", "", by.get("서리", ["", ""])[1]),
        "co2": _num(by.get("CO2(Qingping)", [None] * 4)[3]),
    }


def temp_color(t):
    if t is None: return SUB
    if t < 0: return BLUE
    if t < 10: return CYAN
    if t < 25: return GREEN
    return ORANGE


def render(d, now=None):
    now = now or datetime.now()
    img = Image.new("RGB", (S, S), BG)
    g = ImageDraw.Draw(img)

    # 머리: 제목 + 갱신 시각 (30분 넘게 밀리면 빨간 '지연')
    g.text((12, 18), "농막", font=_f(22), fill=FG, anchor="lm")
    upd = d["updated"]
    stale = upd is None or (now - upd).total_seconds() > 30 * 60
    tag = "지연 " if stale else ""
    g.text((S - 12, 18), tag + (upd.strftime("%H:%M") if upd else "--:--") + " 측정",
           font=_f(16, bold=stale), fill=RED if stale else SUB, anchor="rm")

    # 세 곳 온도·습도 카드
    y = 38
    for label, t, rh in d["rooms"]:
        g.rounded_rectangle([8, y, S - 8, y + 39], radius=8, fill=CARD)
        g.text((20, y + 20), label, font=_f(18), fill=SUB, anchor="lm")
        g.text((168, y + 20), "--" if t is None else f"{t:.1f}°", font=_f(30), fill=temp_color(t), anchor="rm")
        g.text((S - 18, y + 24), "" if rh is None else f"{rh:.0f}%", font=_f(15, bold=False), fill=SUB, anchor="rm")
        y += 44

    # 히터 상태
    y += 1
    on = d["heater_on"]
    g.ellipse([14, y + 4, 26, y + 16], fill=RED if on else "#3a4652")
    g.text((34, y + 10), f"히터 {'ON' if on else 'OFF'}", font=_f(16), fill=FG if on else SUB, anchor="lm")
    g.text((S - 12, y + 10), d["heater_mode"], font=_f(15, bold=False), fill=CYAN, anchor="rm")

    # 서리·도착 예약
    y += 23
    gm = d["garden_min"]
    if gm is not None:
        frost = d["frost"]
        g.text((12, y + 10), f"텃밭 최저 {gm:.0f}°", font=_f(15, bold=False), fill=SUB, anchor="lm")
        if frost:
            g.text((S - 12, y + 10), f"서리 {frost}", font=_f(15), fill=BLUE if "높" in frost else SUB, anchor="rm")
    y += 21
    if d["arrival"]:
        g.text((12, y + 10), "도착 " + d["arrival"], font=_f(15, bold=False), fill=ORANGE, anchor="lm")
    co2 = d.get("co2")
    if co2 is not None:   # 1000ppm 미만 초록, 2000 미만 주황(환기), 그 이상 빨강
        g.text((S - 12, y + 10), f"CO2 {co2:.0f}", font=_f(15),
               fill=GREEN if co2 < 1000 else ORANGE if co2 < 2000 else RED, anchor="rm")
    return img
