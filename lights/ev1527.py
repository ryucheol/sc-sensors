# -*- coding: utf-8 -*-
"""EV1527 433 MHz 리모컨 신호 — 부호화(보내기)·해독(받기). 하드웨어 없이 순수 함수.

한 프레임 = 24비트(앞 20비트 리모컨 주소 + 뒤 4비트 버튼) + 동기.
  기본 시간 T(리모컨마다 약 250~450 µs, 받은 신호에서 잰다)
  0 = 높음 1T, 낮음 3T          1 = 높음 3T, 낮음 1T
  동기 = 높음 1T, 낮음 31T  (rc-switch '프로토콜 1'과 같은 꼴)
리모컨은 한 번 누르면 같은 프레임을 수십 번 반복한다 → 받는 쪽은 같은 프레임이 2번 이상 나오면 인정.

펄스 표현: [(level, µs), ...]  level 1 = 송신(높음), 0 = 쉼(낮음)
"""

SYNC_LOW = 31
BITS = 24


def frame(code, t_us):
    """24비트 코드 → 한 프레임 펄스(데이터 뒤에 동기)."""
    if not 0 <= code < (1 << BITS):
        raise ValueError("코드는 24비트(0..0xFFFFFF)")
    p = []
    for i in range(BITS - 1, -1, -1):
        if (code >> i) & 1:
            p += [(1, 3 * t_us), (0, t_us)]
        else:
            p += [(1, t_us), (0, 3 * t_us)]
    p += [(1, t_us), (0, SYNC_LOW * t_us)]
    return p


def burst(code, t_us, repeats=10):
    """한 번 누른 것과 같은 묶음: 앞에 동기 한 번 + 프레임 repeats번."""
    return [(1, t_us), (0, SYNC_LOW * t_us)] + frame(code, t_us) * repeats


def _short(d, t):
    return 0.4 * t <= d <= 1.8 * t


def _long(d, t):
    return 2.0 * t <= d <= 4.6 * t


def decode(pulses, t_min=150, t_max=700):
    """펄스 열 → [(code, t_us)] (프레임마다 하나, 순서대로). 잡음은 건너뛴다.
    동기(낮음 ≈ 31T)를 찾고, 그 뒤 48개 펄스(높음·낮음 24쌍)를 비트로 읽는다."""
    out = []
    n = len(pulses)
    i = 0
    while i < n:
        lvl, d = pulses[i]
        if lvl == 0 and 31 * t_min <= d <= 31 * t_max:
            t = d / SYNC_LOW
            j = i + 1
            if j + 2 * BITS <= n:
                code, ok = 0, True
                for b in range(BITS):
                    (lh, h), (ll, l) = pulses[j + 2 * b], pulses[j + 2 * b + 1]
                    if lh != 1 or ll != 0:
                        ok = False; break
                    if _short(h, t) and _long(l, t):
                        code <<= 1
                    elif _long(h, t) and _short(l, t):
                        code = (code << 1) | 1
                    else:
                        ok = False; break
                if ok:
                    # T는 비트들의 짧은 펄스 평균(동기 길이보다 정확)
                    shorts = [min(pulses[j + 2 * b][1], pulses[j + 2 * b + 1][1]) for b in range(BITS)]
                    out.append((code, round(sum(shorts) / len(shorts))))
                    i = j + 2 * BITS
                    continue
        i += 1
    return out


def confirmed(frames, min_count=2):
    """같은 코드가 min_count번 이상 나온 것만 → [(code, 평균 T)] (나온 순서)."""
    seen, order = {}, []
    for c, t in frames:
        if c not in seen:
            seen[c] = []; order.append(c)
        seen[c].append(t)
    return [(c, round(sum(seen[c]) / len(seen[c]))) for c in order if len(seen[c]) >= min_count]


def split(code):
    """코드 → (주소 20비트, 버튼 4비트)."""
    return code >> 4, code & 0xF


def fmt(code):
    a, b = split(code)
    return "%06X (주소 %05X · 버튼 %X)" % (code, a, b)
