"""(b) Max Y reachable by time T over ALL feasible flux strategies of both organisms.

Nonconvex optimal control (biomass multiplies flux; exponential biomass update),
solved with IPOPT (CasADi) by direct multiple shooting, multistart from the greedy
trajectory and random perturbations. IPOPT gives a LOCAL optimum only. Every
reported value is a verified LOWER bound: the controls are replayed as ordinary
dFBA steps (exact caps, closing-guard check), after shrinking any step whose caps
are violated by solver tolerance (all constraints are homogeneous in (a, c, d)
with nonnegative right-hand sides, so scaling the step down restores them).
Upper bound: yield_bound (exact, any horizon) = 5 mM from the initial medium.
"""
import sys, json
import numpy as np, casadi as ca
from model import *


def solve(N, mode="takers", irrev=False, init=None, seed=None, target=None, maxit=3000, x0=None, B0=None):
    x0 = X0 if x0 is None else x0; B0 = (BIO0, BIO0) if B0 is None else B0
    opti = ca.Opti()
    X = opti.variable(5, N + 1); B = opti.variable(2, N + 1); U = opti.variable(3, N)
    opti.subject_to(X[:, 0] == x0); opti.subject_to(B[0, 0] == B0[0]); opti.subject_to(B[1, 0] == B0[1])
    for k in range(N):
        a, c, d = U[0, k], U[1, k], U[2, k]
        B1, B2 = B[0, k], B[1, k]
        x = X[:, k]
        dx = DT * (B1 * ca.vertcat(-a - 3 * c, 0, -a, 2 * c, a) + B2 * ca.vertcat(-d, -d, d, 0, 0))
        opti.subject_to(X[:, k + 1] == x + dx)
        opti.subject_to(B[0, k + 1] == B1 * ca.exp(DT * 0.1 * (a + c)))
        opti.subject_to(B[1, k + 1] == B2 * ca.exp(DT * 0.02 * d))
        bt = {m: (B1 + B2 if (mode == "all" or len(TAKERS[m]) == 2) else (B1 if TAKERS[m] == (1,) else B2))
              for m in range(5)}

        def le_cap(u, m):   # u <= min(VMAX x/(KM+x), x/(Btot DT))  (smooth, equivalent)
            opti.subject_to(u * (KM + x[m]) <= VMAX * x[m])
            opti.subject_to(u * bt[m] * DT <= x[m])
        le_cap(a + 3 * c, iA); le_cap(a, iC); le_cap(-a, iY); le_cap(-2 * c, iW)
        le_cap(d, iA); le_cap(d, iB)
        opti.subject_to(a + c >= 0); opti.subject_to(d >= 0)
        if irrev:
            opti.subject_to(a >= 0); opti.subject_to(c >= 0)
    opti.subject_to(opti.bounded(-UB, ca.vec(U), UB))
    opti.subject_to(ca.vec(X) >= 0)
    if target is None:
        opti.minimize(-X[iY, N])
    else:                                    # feasibility of reaching a threshold
        opti.subject_to(X[iY, N] >= target); opti.minimize(0)
    if init is not None:
        Xi, Bi, Ui = init
        opti.set_initial(X, Xi.T); opti.set_initial(B, Bi.T); opti.set_initial(U, Ui.T)
    opti.solver("ipopt", {"print_time": False}, {"print_level": 0, "max_iter": maxit, "tol": 1e-9,
                                                  "constr_viol_tol": 1e-10, "sb": "yes"})
    try:
        sol = opti.solve()
        return "ok", np.array(sol.value(U)).reshape(3, N).T, float(sol.value(X[iY, N]))
    except RuntimeError:
        st = opti.stats()["return_status"]
        return st, np.array(opti.debug.value(U)).reshape(3, N).T, float(opti.debug.value(X[iY, N]))


def replay(U, mode="takers", irrev=False, x0=None, B0=None):
    """Verified forward replay as dFBA; returns trajectory and max projection change."""
    x = (X0 if x0 is None else x0).copy(); B1, B2 = (BIO0, BIO0) if B0 is None else B0
    Xs = [x.copy()]; Bs = [(B1, B2)]; smin = 0.0; Uv = []
    for a, c, d in U:
        k = caps(x, B1, B2, mode)
        a0, c0, d0 = a, c, d
        # project onto the step's feasible set (removes solver slack; never relaxes a bound)
        a = min(max(a, -k[iY], 0.0 if irrev else -np.inf), k[iC])
        lo_c = max(-k[iW] / 2, -a, 0.0 if irrev else -np.inf)
        hi_c = (k[iA] - a) / 3
        c = min(max(c, lo_c), hi_c)
        if c < lo_c:                         # empty interval: fall back to the zero-growth-safe point
            a, c = 0.0, min(max(0.0, -k[iW] / 2), k[iA] / 3) if not irrev else 0.0
        d = min(max(d, 0.0), k[iA], k[iB])
        smin = max(smin, abs(a - a0) + abs(c - c0) + abs(d - d0))  # max projection change
        assert feasible(x, B1, B2, a, c, d, mode, irrev, tol=1e-12)
        x, B1, B2 = step(x, B1, B2, a, c, d)
        assert x.min() > -1e-9, x          # floating-point round-off only; clipped below
        x = np.maximum(x, 0.0)
        Xs.append(x.copy()); Bs.append((B1, B2)); Uv.append((a, c, d))
    return np.array(Xs), np.array(Bs), np.array(Uv), smin


def best(N, mode="takers", irrev=False, nstart=6):
    Xg, Bg, Ug = greedy(N, mode, irrev)
    rng = np.random.default_rng(0)
    out = []
    inits = [(Xg, Bg, Ug)]
    for s in range(nstart - 1):           # perturbed starts: scale controls randomly, re-simulate
        Up = Ug * rng.uniform(0, 1, size=Ug.shape)
        Xp, Bp, Up2, _ = replay(Up, mode, irrev)
        inits.append((Xp, Bp, Up2))
    for i, ini in enumerate(inits):
        st, U, Yip = solve(N, mode, irrev, init=ini)
        Xr, Br, Uv, smin = replay(U, mode, irrev)
        out.append(dict(start=i, status=st, Y=Xr[-1, iY], Yip=Yip, smin=smin, U=Uv, X=Xr, B=Br))
    return Xg, out


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "takers"
    irrev = len(sys.argv) > 2 and sys.argv[2] == "irrev"
    res = {}
    for T in [5, 10, 15]:
        N = int(round(T / DT))
        Xg, out = best(N, mode, irrev)
        o = max(out, key=lambda r: r["Y"])
        Ys = sorted(round(r["Y"], 4) for r in out)
        print(f"[{mode}{' irrev' if irrev else ''}] T={T:2d} h: greedy Y={Xg[-1, iY]:.4f} | best verified Y={o['Y']:.4f} "
              f"(start {o['start']}, ipopt {o['status']}, max projection {o['smin']:.1e}, ipopt Y {o['Yip']:.4f}) | all starts {Ys} | "
              f"upper bound {yield_bound(X0):.1f}")
        np.savez(f"reach_{mode}{'_irrev' if irrev else ''}_T{T}.npz", X=o["X"], B=o["B"], U=o["U"], Xg=Xg)
        res[T] = dict(greedy=Xg[-1, iY], best=o["Y"], starts=Ys)
    json.dump(res, open(f"reach_{mode}{'_irrev' if irrev else ''}.json", "w"), indent=1)
