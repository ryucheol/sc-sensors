# -*- coding: utf-8 -*-
"""예열 시뮬을 실제 forecast.json 외기·하늘·풍속으로 구동.
   모델: C·dT/dt = P − UA·(1+0.05·WSD)·(T_in−T_out) + Q_solar
   Q_solar = 1300W × 하늘계수(SKY1:1.0/3:0.5/4:0.2, 강수 중 ×0.5) × 주간곡선(07~17,정오피크).
   (1300W는 11-06 실측 보정값. 11월 기준.)"""
import json, math, datetime, bisect

UA=47.0; C_J=UA*9.16*3600; P=1740.0; K_WIND=0.05; DT=600
TARGET=23.0; T_START=5.0; S_BASE=1300.0
SKYF={1:1.0,2:0.7,3:0.5,4:0.2}

fc=json.load(open("/home/ryuch/smart-cabin/forecast.json"))
H=fc["hourly"]; MS=[h["ms"] for h in H]

def interp_tmp(tm):
    i=bisect.bisect_left(MS,tm)
    if i<=0: return H[0].get("tmp")
    if i>=len(MS): return H[-1].get("tmp")
    a,b=H[i-1],H[i]
    if a.get("tmp") is None: return b.get("tmp")
    if b.get("tmp") is None: return a.get("tmp")
    f=(tm-MS[i-1])/(MS[i]-MS[i-1]); return a["tmp"]+f*(b["tmp"]-a["tmp"])

def nearest(tm,key,default):
    i=bisect.bisect_left(MS,tm); cands=[j for j in (i-1,i) if 0<=j<len(H)]
    if not cands: return default
    j=min(cands,key=lambda j:abs(MS[j]-tm)); return H[j].get(key,default)

def q_solar(t,tm):
    h=t.hour+t.minute/60
    if not (7<=h<=17): return 0.0
    f=SKYF.get(nearest(tm,"sky",4),0.3)
    if nearest(tm,"pty",0): f*=0.5
    return S_BASE*f*max(0.0,math.sin(math.pi*(h-7)/10))

def run(day, start_h, heater):
    T=T_START; t=datetime.datetime(day.year,day.month,day.day,0,0)
    end=datetime.datetime(day.year,day.month,day.day,20,0)
    kwh=0.0; cross=None; peak=T
    while t<end:
        tm=int(t.timestamp()*1000); to=interp_tmp(tm)
        wsd=nearest(tm,"wsd",0) or 0; ua=UA*(1+K_WIND*wsd); qs=q_solar(t,tm)
        hh=t.hour+t.minute/60; on=heater and hh>=start_h and T<TARGET
        dT=((P if on else 0)-ua*(T-to)+qs)*DT/C_J
        if on: kwh+=P*DT/3600/1000
        T+=dT; t+=datetime.timedelta(seconds=DT); peak=max(peak,T)
        if cross is None and on and T>=TARGET: cross=hh
    return {"T20":T,"cross":cross,"kwh":kwh,"peak":peak}

SKYNM={1:"맑음",3:"구름많",4:"흐림"}
print("실제 예보 구동 — 발표 %s, 격자 %s,%s\n"%(fc["base"],fc["nx"],fc["ny"]))
for dstr in ("2026-11-07","2026-11-08","2026-11-09"):
    day=datetime.date.fromisoformat(dstr); dk=dstr.replace("-","")
    dv=fc["daily"].get(dk,{})
    # 낮 하늘 요약(09~16시)
    skies=[nearest(int(datetime.datetime(day.year,day.month,day.day,hh).timestamp()*1000),"sky",4) for hh in range(9,17)]
    wsds=[nearest(int(datetime.datetime(day.year,day.month,day.day,hh).timestamp()*1000),"wsd",0) for hh in range(0,20)]
    dom=max(set(skies),key=skies.count)
    print("[%s] 일최저 %s / 최고 %s  낮하늘 %s  낮풍속 평균 %.1f m/s"
          %(dstr,dv.get("tmn"),dv.get("tmx"),SKYNM.get(dom,dom),sum(wsds)/len(wsds)))
    a=run(day,0,False)
    b=run(day,0,True)
    c=run(day,13,True)
    print("   [A] 햇빛만(히터off): 낮 실내피크 %.1f°C"%a["peak"])
    print("   [B] 자정부터 예열 : 23°C도달 %s, 20시 %.1f°C, 전기 %.1f kWh"
          %(("%.1fh"%b["cross"]) if b["cross"] else "미도달", b["T20"], b["kwh"]))
    print("   [C] 13시부터 예열 : 23°C도달 %s, 20시 %.1f°C, 전기 %.1f kWh"
          %(("%.1fh"%c["cross"]) if c["cross"] else "미도달", c["T20"], c["kwh"]))
    print()
