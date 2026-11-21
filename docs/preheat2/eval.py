import numpy as np, sys
D=np.loadtxt('hourly.txt'); kwh,T1,T2,Tf=D.T; N=len(D)
tmid=np.arange(N)+0.5   # 시간 평균값 = 그 시간의 중간 시각
def T1at(t): return np.interp(t,tmid,T1)
def Tfat(t): return np.interp(t,tmid,Tf)
def run(tag):
    Ca,Cm,Hao,Ham,Hmo=np.load('p_%s.npy'%tag)*[1,1,1,1,1]; Ca,Cm=np.exp(np.load('p_%s.npy'%tag)[:2]); Hao,Ham,Hmo=np.exp(np.load('p_%s.npy'%tag)[2:5])
    def observer(t_end):
        t=0.5; tm=T1[0]
        while t<t_end:
            ta=T1at(t); tm+=(Ham*(ta-tm)-Hmo*(tm-Tfat(t)))/Cm*360; t+=0.1
        return tm
    def lead(ta,tm,t0,Pm,aux,tgt,fcmin):
        t=0.0
        while ta<tgt and t<14:
            Pw=Pm+(477 if aux and ta<tgt-1+0.7 else 0)   # 보조: 목표−1 아래에서 켜고 목표−0.3에서 끔(간단히)
            to=fcmin
            dta=(Pw-Ham*(ta-tm)-Hao*(ta-to))/Ca; dtm=(Ham*(ta-tm)-Hmo*(tm-to))/Cm
            ta+=dta*60; tm+=dtm*60; t+=1/60
        return t
    def actual(t0,tgt,tmax=N-0.5):
        t=t0
        while t<tmax:
            if T1at(t)>=tgt: return t-t0
            t+=0.05
        return None
    E=[("11-13 17:37 (2단)",128.62,23,1040,0),("11-15 20:20",179.33,23,1700,0),("11-16 06:19 (아침)",189.32,23,1750,0),
       ("11-20 00:00 (보조 꺼짐)",279.0,23,1820,0),("11-21 06:00 (아침)",309.0,23,1780,0),("11-22 01:30 (보조 켜짐)",328.5,25,1835,1)]
    print("== 모델 %s  Ca %.2f Cm %.2f kWh/K, Hao %.0f Ham %.0f Hmo %.0f W/K"%(tag,Ca/3.6e6,Cm/3.6e6,Hao,Ham,Hmo))
    for n,t0,tgt,Pm,aux in E:
        i=int(t0); ta=T1at(t0); tm=observer(t0)
        fc=min(Tf[i:i+12].min(),Tf[i])     # 바닥외부 실측 최저(앞 12시간) = 완벽한 예보 가정
        L=lead(ta,tm,t0,Pm,aux,tgt,fc); a=actual(t0,tgt)
        if tgt==25 and a is None: a_s=">6.0h(07:27에 24.6°C)"
        else: a_s="%.1fh"%a
        print("  %-22s 1층 %.1f 가구 %.1f 바깥 %.1f → %g°C | 예측 %.1fh ×1.1=%.1fh | 실제 %s"%(n,ta,tm,fc,tgt,L,L*1.1,a_s))
run(sys.argv[1])
