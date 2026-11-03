"""Reachable-set envelope of Y over time (results page, "The reachable set" chart).

Every admissible strategy keeps Y(t) inside [0, ub(t)]:
  - floor 0: the all-zero controls (a, c, d) = 0 are feasible at every step (asserted below);
  - ceiling ub(t): the exact finite-horizon bound of upper.py.
The top edge is attained by verified strategies: pointwise max of greedy dFBA and the
hand-written policy of simple_policy.py (every step checked against the closing guard).
Sampled strategies: ../toy-coculture/env_Y.npy, written by replicate.py (200 random
feasible strategies); summarised as min/max per step if the file is present.
Irreversible internals (the thesis convention; greedy plateaus at 1.467 mM), "takers"
convention, DT = 0.1 h, 0-8 h. The samples also use irreversible internals (replicate.py
restricts internal fluxes to R >= 0). Run: python3 band.py
"""
import json, os
import numpy as np
from model import *
from upper import ub

HERE = os.path.dirname(os.path.abspath(__file__))
mode, irrev, N = "takers", True, 80

Xg, _, _ = greedy(N, mode, irrev)

x, B1, B2 = X0.copy(), BIO0, BIO0; P = 0.0; Ys = [0.0]
for n in range(N):                                   # simple_policy.py, verbatim logic
    k = caps(x, B1, B2, mode)
    d = min(k[iA], k[iB], max(0.0, (5.0 - P) / (DT * B2))); a, c = min(k[iC], k[iA]), 0.0
    assert feasible(x, B1, B2, a, c, d, mode, irrev, tol=1e-12)
    P += DT * B2 * d; x, B1, B2 = step(x, B1, B2, a, c, d); assert x.min() > -1e-9
    x = np.maximum(x, 0); Ys.append(float(x[iY]))

x, B1, B2 = X0.copy(), BIO0, BIO0                    # zero strategy: Y stays 0
for n in range(N):
    assert feasible(x, B1, B2, 0, 0, 0, mode, irrev, tol=0); x, B1, B2 = step(x, B1, B2, 0, 0, 0)
assert x[iY] == 0

env = os.path.join(HERE, "..", "toy-coculture", "env_Y.npy")
S = np.load(env)[:, :N + 1] if os.path.exists(env) else None

rows = []
for k in range(N + 1):
    u = float(ub(k)) if k >= 1 else 0.0
    g, s = float(Xg[k, iY]), Ys[k]
    assert g <= u + 1e-9 and s <= u + 1e-9
    r = dict(t=round(k * DT, 1), greedy=g, simple_policy=s, attained=max(g, s), upper_bound=u)
    if S is not None:
        assert S[:, k].max() <= u + 1e-9
        r.update(sampled_min=float(S[:, k].min()), sampled_max=float(S[:, k].max()))
    rows.append(r)
json.dump(rows, open(os.path.join(HERE, "band.json"), "w"), indent=1)
gap = max(r["upper_bound"] - r["attained"] for r in rows)
print(f"max gap between bound and attained: {gap:.4f} mM; samples present: {S is not None}")
