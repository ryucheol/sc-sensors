import numpy as np
D=np.loadtxt('hourly.txt'); kwh,T1,T2,Tf=D.T; S=np.loadtxt('stn.txt'); Ts,cl=S.T; N=len(D)
hour=(9+np.arange(N))%24; Ta=0.6*T1+0.4*T2; clear=1-cl/10
night=(hour>=19)|(hour<=6)
# (a) 바닥외부 - 관측소, 밤
d=Tf-Ts; m=night
for lo,hi,lab in [(0,0.3,"흐림(운량8~10)"),(0.3,0.7,"반쯤"),(0.7,1.01,"맑음(0~2)")]:
    s=m&(clear>=lo)&(clear<hi); print("(a) 밤 %s: 바닥외부−관측소 %.1f°C (n=%d)"%(lab,d[s].mean(),s.sum()))
# (b) 밤 자연 냉각: 앞뒤 시간 모두 히터 0, 히터 끈 지 3시간 지난 뒤
off=kwh==0
ok=np.zeros(N,bool)
for i in range(4,N-1):
    ok[i]=night[i] and off[i-4:i+2].all()
rate=np.zeros(N); rate[1:-1]=(Ta[2:]-Ta[:-2])/2
for Tout,lab in [(Ts,"관측소"),(Tf,"바닥외부")]:
    X=np.c_[Ta-Tout, clear][ok]; y=-rate[ok]
    A=np.c_[X,np.ones(len(X))]; c,*_=np.linalg.lstsq(A,y,rcond=None)
    pred=A@c; r2=1-((y-pred)**2).sum()/((y-y.mean())**2).sum()
    c0,*_=np.linalg.lstsq(X[:,:1],y,rcond=None)
    print("(b) 냉각속도[K/h] = %.4f×(실내−%s) + %.3f×맑음 + %.3f  R²=%.2f n=%d  | 온도차만: %.4f (시정수 %.1fh)"%(c[0],lab,c[1],c[2],r2,ok.sum(),c0[0],1/c0[0]))
# (c) 난방 유지: 밤, 히터 가동, 실내 거의 일정
hold=np.zeros(N,bool)
for i in range(1,N-1):
    hold[i]=night[i] and kwh[i]>0 and kwh[i]<1.75 and abs(Ta[i+1]-Ta[i-1])<0.6 and Ta[i]>22
P=kwh*1000
for Tout,lab in [(Ts,"관측소"),(Tf,"바닥외부")]:
    dT=(Ta-Tout)[hold]; X=np.c_[dT, clear[hold]]
    c,*_=np.linalg.lstsq(X,P[hold],rcond=None); c1,*_=np.linalg.lstsq(dT[:,None],P[hold],rcond=None)
    print("(c) 유지 전력 W = %.0f×ΔT(%s) + %.0f×맑음   | ΔT만: UA %.0f W/K  n=%d"%(c[0],lab,c[1],c1[0],hold.sum()))
import datetime; t0=datetime.datetime(2026,11,8,9)
for i in np.where(hold)[0]:
    print("   %s P %4.0f Ta %.1f 관측소 %.1f 바닥외부 %.1f 운량 %g  UA(관측소) %.0f"%((t0+datetime.timedelta(hours=int(i))).strftime('%m-%d %H'),P[i],Ta[i],Ts[i],Tf[i],cl[i],P[i]/(Ta[i]-Ts[i])))
