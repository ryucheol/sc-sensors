"""농막 상태 240x240 화면.

입력은 '농막_현재' 탭 값 그대로(행 리스트). 1열 라벨로 찾으므로 행 순서가 바뀌어도 된다.
Pi에서는 gspread의 ws.get_values() 결과를, 맥에서는 그 JSON 덤프를 넘기면 된다.
"""
import re
from datetime import datetime, timedelta
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
        "rooms": [room("다락", "다락"), room("1층", "내부1층"), room("바깥", "바닥외부")],   # 위→아래: 건물 높이 순
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


HIST_LABELS = {"내부1층": "1층", "다락": "다락", "바닥외부": "바깥"}
HIST_HOURS = 24


def hist_from_rows(rows, now=None):
    """'최근5일_시간별' 행(머리 2줄 포함 가능) → {"1층": [(datetime, 온도), ...], ...}.
    시각 칸 형식 '11/24(화) 00시'. 연도는 now 기준(12월→1월 넘어가면 작년으로)."""
    now = now or datetime.now()
    cols = {}
    out = {v: [] for v in HIST_LABELS.values()}
    for r in rows:
        if not r:
            continue
        if str(r[0]).strip() == "시각":
            cols = {i: HIST_LABELS[c.strip()] for i, c in enumerate(r[:4]) if c.strip() in HIST_LABELS}
            continue
        m = re.match(r"(\d+)/(\d+)\(.\)\s*(\d+)시", str(r[0]))
        if not (m and cols):
            continue
        mo, dd, hh = map(int, m.groups())
        ts = datetime(now.year, mo, dd, hh)
        if ts > now + timedelta(days=1):
            ts = ts.replace(year=now.year - 1)
        for i, lab in cols.items():
            t = _num(r[i]) if i < len(r) else None
            if t is not None:
                out[lab].append((ts, t))
    return out


def _spark(g, pts, box, color, now, hours=HIST_HOURS):
    """box=(x0,y0,x1,y1) 안에 최근 hours 시간 꺾은선. x는 시각 비례, y는 자체 최소~최대(최소 폭 2°C)."""
    x0, y0, x1, y1 = box
    start = now - timedelta(hours=hours)
    pts = [(ts, t) for ts, t in pts if start <= ts <= now + timedelta(minutes=10)]
    if len(pts) < 2 or x1 - x0 < 16:
        return
    lo, hi = min(t for _, t in pts), max(t for _, t in pts)
    if hi - lo < 2:
        mid = (hi + lo) / 2
        lo, hi = mid - 1, mid + 1
    span = hours * 3600
    xy = [(x0 + (x1 - x0) * min(1, max(0, (ts - start).total_seconds() / span)),
           y1 - (y1 - y0) * (t - lo) / (hi - lo)) for ts, t in pts]
    g.line(xy, fill=color, width=2, joint="curve")
    ex, ey = xy[-1]
    g.ellipse([ex - 2.5, ey - 2.5, ex + 2.5, ey + 2.5], fill=color)


def render(d, now=None, hist=None, hours=HIST_HOURS):
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
    # 온도는 습도 바로 왼쪽에 붙인다(습도 칸 폭은 '88%' 기준 고정)
    temp_r = S - 18 - g.textlength("88%", font=_f(15, bold=False)) - 5
    tfmt = lambda t: "--" if t is None else f"{t:.1f}°"
    # 그래프 칸은 세 카드 모두 같은 폭(가장 긴 온도 글자 기준) → 시간축이 위아래로 맞아 비교 가능
    spark_r = temp_r - max(g.textlength(tfmt(t), font=_f(30)) for _, t, _ in d["rooms"]) - 8
    for label, t, rh in d["rooms"]:
        g.rounded_rectangle([8, y, S - 8, y + 39], radius=8, fill=CARD)
        g.text((20, y + 20), label, font=_f(18), fill=SUB, anchor="lm")
        ttxt = tfmt(t)
        g.text((temp_r, y + 20), ttxt, font=_f(30), fill=temp_color(t), anchor="rm")
        if hist and hist.get(label):   # 라벨과 온도 사이 빈칸에 최근 hours시간 꺾은선
            pts = list(hist[label])
            if t is not None and d["updated"]:
                pts.append((d["updated"], t))
            _spark(g, sorted(pts), (58, y + 9, spark_r, y + 31), temp_color(t), now, hours)
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
