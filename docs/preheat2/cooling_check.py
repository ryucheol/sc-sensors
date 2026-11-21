import numpy as np, datetime
D=np.loadtxt('hourly.txt'); kwh,T1,T2,Tf=D.T; Ts,cl=np.loadtxt('stn.txt').T; N=len(D)
t0=datetime.datetime(2026,11,8,9); hour=(9+np.arange(N))%24
errs={3:[],6:[],12:[]}
print("밤 자연냉각: 시작(21시, 히터 꺼진 지 3h↑) → 3·6·12시간 뒤 1층, 현 모델(tau 12h, 바깥=바닥외부 실측)")
for i in range(N-12):
    if hour[i]!=21 or kwh[max(0,i-3):i+12].sum()>0: continue
    t=T1[i]; out=[]
    for h in range(12):
        t=Tf[i+h]+(t-Tf[i+h])*np.exp(-1/12.0); out.append(t)
    s=(t0+datetime.timedelta(hours=i)).strftime('%m-%d')
    print("  %s 시작 %.1f | 3h 예측 %.1f 실제 %.1f | 6h %.1f/%.1f | 12h %.1f/%.1f"%(s,T1[i],out[2],T1[i+3],out[5],T1[i+6],out[11],T1[i+12] if i+12<N else np.nan))
    for h in (3,6,12):
        if i+h<N: errs[h].append(out[h-1]-T1[i+h])
for h in errs: print("  %dh 뒤 오차 평균 %+.1f°C, 최대 %+.1f°C"%(h,np.mean(errs[h]),max(errs[h],key=abs)))
