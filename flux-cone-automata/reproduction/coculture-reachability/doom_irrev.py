"""(c') Irreversible-internal variant (thesis convention: R >= 0, so a, c >= 0).
W can then never be turned back into A, and the exact all-horizon yield bound from a
state x is  max over P in [0, B] of  min(C + P, A - P)   (P = future org-2 conversion).
Greedy is DOOMED for theta at step k if  Y_k + bound(x_k) < theta  -- exact rational
arithmetic, valid for every strategy of both organisms and every horizon.
"""
import json
from fractions import Fraction as Fr
import numpy as np
from model import *


def yb_irrev(x):
    A, B, C = (Fr(float(v)) for v in (x[iA], x[iB], x[iC]))
    P = min(max((A - C) / 2, Fr(0)), B)
    return max(Fr(0), min(C + P, A - P))


X, Bs, U = greedy(150, "takers", True)
reach = json.load(open("reach_takers_irrev.json"))
cert = [Fr(float(X[k, iY])) + yb_irrev(X[k]) for k in range(len(X))]
A_out = next(k for k in range(1, len(X)) if X[k, iA] <= 1e-9)
plateau = next(k for k in range(1, len(X)) if X[-1, iY] - X[k, iY] < 1e-6)
print(f"greedy (irrev): Y plateau {X[-1, iY]:.4f}; A exhausted {A_out*DT:.1f} h; plateau reached {plateau*DT:.1f} h")
print("best verified Y (irrev):", {T: round(v['best'], 4) for T, v in reach.items()})
rows = []
for theta in [2.0, 2.5, 3.0, 3.5, 4.0, 4.5]:
    k = next(k for k in range(len(X)) if cert[k] < theta)
    margin = float(Fr(theta) - cert[k])
    reachable_by = min((int(T) for T, v in reach.items() if v["best"] >= theta), default=None)
    rows.append(dict(theta=theta, doomed_from_h=k * DT, greedy_Y_then=float(X[k, iY]), margin=margin,
                     reachable_by_T=reachable_by, warning_before_plateau_h=(plateau - k) * DT))
    print(f"theta={theta}: reachable from t=0 by T={reachable_by} h; greedy doomed (exact) from t={k*DT:.1f} h "
          f"(greedy Y then {X[k, iY]:.3f}, still rising until {plateau*DT:.1f} h; margin {margin:.3f})")
json.dump(rows, open("doom_irrev.json", "w"), indent=1)
