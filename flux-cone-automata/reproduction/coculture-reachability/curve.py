"""Max Y by T: greedy vs verified best strategy (IPOPT, local) vs exact upper bound."""
import json, numpy as np
from model import *
from reach import best
from upper import ub
rows = []
for T in [1, 2, 3, 4, 5, 6, 7, 8]:
    N = int(round(T / DT))
    Xg, out = best(N, "takers", False, nstart=3)
    lb = max(r["Y"] for r in out); u = float(ub(N))
    rows.append(dict(T=T, greedy=float(Xg[-1, iY]), lb=float(lb), ub=u))
    print(f"T={T} h  greedy {Xg[-1, iY]:.4f}  best verified {lb:.4f}  upper bound {u:.4f}  gap {(u-lb)/u*100:.2f}%")
json.dump(rows, open("curve.json", "w"), indent=1)
