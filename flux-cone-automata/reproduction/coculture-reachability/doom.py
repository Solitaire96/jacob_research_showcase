"""(c) Viability / doom of the greedy co-culture for the requirement  Y(T) >= theta.

Certificate (exact, horizon-free): from state x, no strategy of either organism can
ever add more than yield_bound(x) to Y (stoichiometric yield LP with final
concentrations >= 0; it ignores kinetics, growth and time, so it is a relaxation of
every finite-horizon problem). Hence greedy is DOOMED at step k for theta if
    Y_k + yield_bound(x_k) < theta.
The bound is evaluated in exact rational arithmetic on the (floating-point) greedy
states, so the certificate is exact for those states.
Reachability of theta by T from the initial medium is shown by a verified witness
strategy from reach.py.
"""
import json
from fractions import Fraction as Fr
import numpy as np
from model import *


def yb_exact(x):
    A, B, C, W = (Fr(float(v)) for v in x[:4])
    E = A + Fr(3, 2) * W
    P = min(max((E - C) / 2, Fr(0)), B)
    return max(Fr(0), min(C + P, E - P))


if __name__ == "__main__":
    out = {}
    for mode, irrev in [("takers", False), ("takers", True)]:
        X, Bs, U = greedy(150, mode, irrev)
        tag = mode + ("_irrev" if irrev else "")
        reach = json.load(open(f"reach_{tag}.json"))
        cert = np.array([float(Fr(float(X[k, iY])) + yb_exact(X[k])) for k in range(len(X))])
        plateau = next(k for k in range(1, len(X)) if X[-1, iY] - X[k, iY] < 1e-6)
        A_out = next(k for k in range(1, len(X)) if X[k, iA] <= 1e-9)
        print(f"\n[{tag}] greedy: Y(15h)={X[-1, iY]:.4f}; A exhausted at {A_out*DT:.1f} h; Y within 1e-6 of plateau from {plateau*DT:.1f} h")
        print("   t(h)   Y_greedy  Y+bound (max Y ever reachable from greedy state)")
        for t in [0, 0.5, 1, 1.5, 2, 2.5, 3, 3.6, 5]:
            k = int(round(t / DT)); print(f"   {t:4.1f}   {X[k, iY]:.4f}    {cert[k]:.4f}")
        rows = []
        for T in ["5", "10", "15"]:
            best = reach[T]["best"]
            for theta in [2.0, 2.5, best - 1e-3]:
                if theta > best:
                    continue
                k = next((k for k in range(len(X)) if cert[k] < theta), None)
                rows.append((T, theta, k))
                print(f"   theta={theta:.3f} reachable by T={T} h (witness {best:.4f}); greedy doomed from "
                      + (f"t={k*DT:.1f} h (Y_greedy then {X[k, iY]:.3f}; certificate margin {theta - cert[k]:.3f})" if k is not None else "never"))
        out[tag] = dict(cert=cert.tolist(), rows=rows)
    json.dump(out, open("doom.json", "w"), indent=1)
