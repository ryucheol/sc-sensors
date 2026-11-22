import numpy as np, sys
from scipy.optimize import least_squares
D=np.loadtxt('hourly.txt'); kwh,T1,T2,Tf=D.T; Ts,cl=np.loadtxt('stn.txt').T; N=len(D)
hour=(9+np.arange(N))%24; day=(9+np.arange(N))//24; nd=day.max()+1
P=(kwh+np.loadtxt('aux.txt'))*1000.0; Ta=T1   # 메인 + 보조(11-22 01:30~ 477W)
sol=np.where((hour>=7)&(hour<=17), np.sin(np.pi*(hour-6.5)/11.0).clip(0), 0.0)
END=int(sys.argv[1]); tag=sys.argv[2]
def sim(p,n):
    Ca,Cm,Hao,Ham,Hmo=np.exp(p[:5]); s=p[5:]
    ta=tm=Ta[0]; out=np.empty(n); dt=180.0
    for i in range(n):
        S=s[day[i]]*1000*sol[i]; acc=0
        for k in range(20):
            dta=(P[i]+S-Ham*(ta-tm)-Hao*(ta-Tf[i]))/Ca; dtm=(Ham*(ta-tm)-Hmo*(tm-Tf[i]))/Cm
            ta+=dta*dt; tm+=dtm*dt; acc+=ta
        out[i]=acc/20
    return out
nde=day[END-1]+1
def res(p): return sim(np.r_[p,np.zeros(nd-nde)],END)-Ta[:END]
best=None
for init in ([0.3,0.4,45,170,10],[0.1,1.0,30,100,20]):
    p0=np.r_[np.log(np.array(init)*[3.6e6,3.6e6,1,1,1]),np.full(nde,0.5)]
    r=least_squares(res,p0,bounds=(np.r_[[9,9,0,0,0],np.zeros(nde)],np.r_[[18,19,6,8,6],np.full(nde,5)]))
    if best is None or r.cost<best.cost: best=r
np.save('p_%s.npy'%tag,best.x[:5]); print(tag,np.round(np.exp(best.x[:5])/[3.6e6,3.6e6,1,1,1],2))
