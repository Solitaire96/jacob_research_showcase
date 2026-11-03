import numpy as np, json, time
from model import *
N=150
res={}
for name,orgs in [("mono1",[1]),("mono2",[2]),("co",[1,2])]:
    for irr in (True,False):
        d=dfba(orgs,N,irreversible=irr)
        res[f"dfba_{name}_{'irr' if irr else 'free'}"]=dict(Y=d[-1]["x"][IX["Y"]], bio={o:b for o,b in d[-1]["bio"].items()})
    f=simulate_fixed(orgs,N)
    d=dfba(orgs,N,irreversible=True)
    err=max(np.max(np.abs(a["x"]-b["x"])) for a,b in zip(f,d))
    berr=max(max(abs(a["bio"][o]-b["bio"][o]) for o in orgs) for a,b in zip(f,d))
    res[f"fixed_{name}"]=dict(Y=f[-1]["x"][IX["Y"]], max_err_vs_dfba_irr=err, max_bio_err=berr, tau=f[-1]["tau"])
    t=time.time(); dr,info=simulate_drawn(orgs,N)
    res[f"drawn_{name}"]=dict(Y=dr[-1]["x"][IX["Y"]], runs=len(dr)-1, info=info, bio=dr[-1]["bio"], secs=time.time()-t)
print(json.dumps(res,indent=1,default=float))
