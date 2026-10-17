import numpy as np
from scipy.stats import norm

def _fb(x,mu,sd,A,pi):
    T=len(x); K=len(mu)
    B=norm.pdf(x[:,None],mu[None,:],sd[None,:])+1e-300
    al=np.zeros((T,K)); c=np.zeros(T)
    a=pi*B[0]; c[0]=a.sum(); al[0]=a/c[0]
    for t in range(1,T):
        a=(al[t-1]@A)*B[t]; c[t]=a.sum(); al[t]=a/c[t]
    be=np.ones((T,K))
    for t in range(T-2,-1,-1): be[t]=(A@(B[t+1]*be[t+1]))/c[t+1]
    return B,al,be,c

def fit_hmm(x,K,n_init=8,iters=200,seed=0,tol=1e-6):
    x=np.asarray(x,float); T=len(x); rng=np.random.default_rng(seed); best=None
    for _ in range(n_init):
        mu=rng.normal(x.mean(),x.std()*0.5,K); sd=x.std()*rng.uniform(0.4,1.8,K)
        A=np.full((K,K),0.05/(K-1)); np.fill_diagonal(A,0.95); pi=np.full(K,1/K); old=-np.inf
        for _it in range(iters):
            B,al,be,c=_fb(x,mu,sd,A,pi); ll=np.log(c).sum()
            g=al*be; g/=g.sum(1,keepdims=True)
            xi=(al[:-1,:,None]*A[None]*(B[1:]*be[1:])[:,None,:]/c[1:,None,None]).sum(0)
            pi=g[0]; A=xi/xi.sum(1,keepdims=True); A=np.clip(A,1e-6,None); A/=A.sum(1,keepdims=True)
            w=g.sum(0); mu=(g*x[:,None]).sum(0)/w; sd=np.sqrt((g*(x[:,None]-mu)**2).sum(0)/w); sd=np.clip(sd,1e-3,None)
            if abs(ll-old)<tol: break
            old=ll
        if best is None or ll>best["ll"]: best=dict(ll=ll,mu=mu.copy(),sd=sd.copy(),A=A.copy(),pi=pi.copy())
    k=2*K+K*(K-1)+(K-1); best["bic"]=-2*best["ll"]+k*np.log(T); best["K"]=K
    return best

def filtered_smoothed(x,m):
    x=np.asarray(x,float); B,al,be,c=_fb(x,m["mu"],m["sd"],m["A"],m["pi"])
    sm=al*be; sm/=sm.sum(1,keepdims=True); return al,sm
