"""Finite-horizon LP RELAXATION (superset of all strategies) from any state.

Absolute fluxes p = B1 a, q = B1 c, s = B2 d per step; concentrations are linear in them.
Every constraint below holds along every true trajectory, so
   LP max Y_N >= true max Y_N,  and  LP infeasible for Y_N >= theta  ==> no strategy reaches theta.
  * x_{j+1} = x_j + DT [(-p-3q) - s, -s, -p + s, 2q, p],   x >= 0
  * p + q >= 0, s >= 0   (growth irreversible);  irrev variant: p, q >= 0
  * depletion caps (B_o / Btot <= 1):  p+3q <= A/DT, p <= C/DT, -p <= Y/DT, -2q <= W/DT,
                                       s <= A/DT, s <= B/DT
  * MM caps with biomass upper bounds Bmax_j (constants):  u <= Bmax_j * VMAX x/(KM+x),
    imposed by tangent cuts of the concave MM function (each tangent lies above it)
  * MM caps with biomass variables:  u <= VMAX * B_j  (since x/(KM+x) < 1)
  * biomass: B1_{j+1} <= B1_j + k1 * 0.01 (p+q),  B2_{j+1} <= B2_j + k2 * 0.002 s,
    chord of exp on the feasible growth range (a+c <= 20, d <= 20): exp(u) <= 1 + (e^U-1)/U u
  * Bmax_j = min(B_0 exp(growth cap * j), B1_0 + k1*0.1*(A_0+W_0)) for org 1,
    min(B2_0 exp(0.04 j), B2_0 + k2*0.02*B_0) for org 2 (resource caps via the chord inequality)
Floating-point LP (HiGHS); not exact arithmetic.
"""
import numpy as np, cvxpy as cp
from model import VMAX, KM, DT, iA, iB, iC, iW, iY

GRID = np.concatenate([[0.0], np.geomspace(1e-9, 50, 120)])
FV = VMAX * GRID / (KM + GRID); FD = VMAX * KM / (KM + GRID) ** 2
K1 = (np.exp(0.2) - 1) / 0.2; K2 = (np.exp(0.04) - 1) / 0.04


def relax(x0, B0, N, irrev=False, target=None):
    p, q, s = cp.Variable(N), cp.Variable(N), cp.Variable(N)
    X = cp.Variable((N + 1, 5)); B1 = cp.Variable(N + 1); B2 = cp.Variable(N + 1)
    # biomass upper bounds: exponential growth cap, and a resource cap from the chord inequality
    # (sum DT (p+q) <= A_0 + W_0 since A, W stay >= 0;  sum DT s <= B_0)
    cap1 = B0[0] + K1 * 0.1 * (x0[iA] + x0[iW]); cap2 = B0[1] + K2 * 0.02 * x0[iB]
    B1max = np.minimum(B0[0] * np.exp(0.2 * np.arange(N + 1)), cap1)
    B2max = np.minimum(B0[1] * np.exp(0.04 * np.arange(N + 1)), cap2)
    cons = [X[0] == x0, B1[0] == B0[0], B2[0] == B0[1], X >= 0,
            X[1:, iA] == X[:-1, iA] + DT * (-p - 3 * q - s), X[1:, iB] == X[:-1, iB] - DT * s,
            X[1:, iC] == X[:-1, iC] + DT * (-p + s), X[1:, iW] == X[:-1, iW] + 2 * DT * q,
            X[1:, iY] == X[:-1, iY] + DT * p,
            p + q >= 0, s >= 0,
            B1[1:] <= B1[:-1] + K1 * 0.01 * (p + q), B2[1:] <= B2[:-1] + K2 * 0.002 * s]
    if irrev:
        cons += [p >= 0, q >= 0]
    Xs = X[:-1]
    for u, m, Bm, Bv in [(p + 3 * q, iA, B1max, B1), (p, iC, B1max, B1), (-p, iY, B1max, B1),
                         (-2 * q, iW, B1max, B1), (s, iA, B2max, B2), (s, iB, B2max, B2)]:
        cons += [u <= Xs[:, m] / DT, u <= VMAX * Bv[:-1]]
        cons += [u[:, None] <= cp.multiply(Bm[:-1, None], FV[None, :] + cp.multiply(FD[None, :], Xs[:, m][:, None] - GRID[None, :]))]
    if target is None:
        prob = cp.Problem(cp.Maximize(X[N, iY]), cons)
    else:
        prob = cp.Problem(cp.Minimize(0), cons + [X[N, iY] >= target])
    try:
        prob.solve(solver="HIGHS")
    except cp.error.SolverError:
        return "solver_error", None
    return prob.status, (X.value[N, iY] if X.value is not None else None)


if __name__ == "__main__":
    from model import X0, BIO0
    from upper import ub
    for T in [1, 2, 3, 4, 5, 6, 7, 8]:
        N = int(round(T / DT))
        st, v = relax(X0, (BIO0, BIO0), N)
        print(f"T={T} h: LP relaxation max Y = {v:.4f} ({st}); closed-form bound {float(ub(N)):.4f}")
