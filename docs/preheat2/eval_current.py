import numpy as np, math, json, sys
sys.path.insert(0,'../../heater'); import heater_control as hc
cfg=json.load(open('../../heater_config.json'))
D=np.loadtxt('hourly.txt'); kwh,T1,T2,Tf=D.T; Ts,cl=np.loadtxt('stn.txt').T
def lead(t_in,t_out,P,tgt,margin=1.1):
    c=json.loads(json.dumps(cfg)); c["preheat"]["target_temp"]=tgt; c["preheat"]["margin"]=margin
    return hc.preheat_lead_hours(c,t_in,t_out,P)
# (이름, 시작 idx, 시작 1층, 목표, P, 실제h)
E=[("11-13 17시 (2단)",128,19.7,23,1102,2.5),("11-15 20시",179,21.1,23,1700,0.6),("11-16 06시 (아침)",189,17.5,23,1750,1.7),
   ("11-20 00시 (보조 꺼짐)",279,7.6,23,1820,10.7),("11-21 06시 (아침)",309,19.6,23,1780,2.0),("11-22 01:30 (보조 켜짐)",328,14.1,25,1830+477,6.5)]
print("%-24s %5s %6s | %8s %8s | %6s"%("예열","시작","바깥최저","예측(×1.1)","예측(그대로)","실제"))
for n,i,t0,tg,P,act in E:
    to=Ts[i:i+max(2,int(act)+1)].min()
    L=lead(t0,to,P,tg); L0=lead(t0,to,P,tg,1.0)
    teq=to+cfg["preheat"]["heat_eff"]*P/cfg["preheat"]["ua_w_per_k"]
    flag=" ←도달불가 판정→상한" if teq<=tg+1 else ""
    print("%-24s %5.1f %6.1f | %7.1fh %7.1fh | %5.1fh  (평형 %.1f°C)%s"%(n,t0,to,L,L0,act,teq,flag))
