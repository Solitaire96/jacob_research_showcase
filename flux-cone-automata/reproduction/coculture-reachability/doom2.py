"""(c) Doom time of greedy dFBA for the requirement Y(T) >= theta.

For each greedy state at step k:
  certificate : relax() LP from (x_k, B_k) over the remaining N-k steps is infeasible
                with Y_N >= theta  ==>  NO strategy from greedy's state meets theta (doomed)
  witness     : IPOPT from (x_k, B_k), replayed as verified dFBA, reaches theta
                ==>  not yet doomed
Doom time is bracketed by the last witnessed step and the first certified step.
"""
import json, numpy as np
from model import *
from reach import solve, replay
from relax import relax

Xg, Bg, Ug = greedy(150, "takers", False)
cases = [(10, 4.0), (10, 4.9), (8, 4.9), (7, 4.9), (6, 4.5)]
out = []
for T, theta in cases:
    N = int(round(T / DT))
    # True doom is monotone along greedy (greedy's k+1 state is reachable from its k state),
    # so bisect for the last witnessed step, then scan forward for the first certified step.
    def certified(k):
        st, _ = relax(Xg[k], tuple(Bg[k]), N - k, target=theta)
        return st == "infeasible"

    def witnessed(k):
        Xi, Bi, Uv, _ = replay(Ug[k:N], "takers", False, x0=Xg[k], B0=tuple(Bg[k]))
        st2, U, _ = solve(N - k, "takers", False, init=(Xi, Bi, Uv), x0=Xg[k], B0=tuple(Bg[k]))
        Xr, _, _, _ = replay(U, "takers", False, x0=Xg[k], B0=tuple(Bg[k]))
        return Xr[-1, iY] >= theta, float(Xr[-1, iY])

    lo, hi = 0, N - 1
    ok0, y0 = witnessed(0)
    last_wit = (0, y0) if ok0 else None
    if ok0:
        while hi - lo > 1:
            mid = (lo + hi) // 2
            ok, y = witnessed(mid)
            if ok:
                lo = mid; last_wit = (mid, y)
            else:
                hi = mid
    cert = next((k for k in range((last_wit[0] + 1) if last_wit else 0, N) if certified(k)), None)
    vis = next(k for k in range(1, len(Xg)) if Xg[-1, iY] - Xg[k, iY] < 1e-6)
    r = dict(T=T, theta=theta, last_reachable=None if last_wit is None else last_wit[0] * DT,
             witness_Y=None if last_wit is None else last_wit[1],
             certified_doomed=None if cert is None else cert * DT,
             greedy_Y_at_doom=None if cert is None else float(Xg[cert, iY]),
             greedy_A_at_doom=None if cert is None else float(Xg[cert, iA]),
             greedy_plateau=vis * DT)
    out.append(r)
    print(r, flush=True)
json.dump(out, open("doom2.json", "w"), indent=1)
