"""Chiu et al. (2014) toy co-culture, reconstructed from the 2021 thesis.

Organism 1 fluxes [R1 R2 R3 R4 J1 J2 J3 J4], growth R3; organism 2 [R5 R6 J5 J6 J7],
growth R6. Exchanges J>0 = secretion. Parameters: VMAX 20, KM 0.05, DT 0.1 h,
biomass 0.01 g/L each, A = B = 10 mM, C = W = Y = 0.

Steady-state flux spaces (exact):  Null(S1) = span{b1, b2*},  Null(S2) = span{g2}
  b1  = A + C -> Y + 0.1 growth             (R4 path)
  b2* = 3A -> 2W + 0.1 growth               (R2 path)
  g2  = A + B -> C + 0.02 growth
so v1 = a b1 + c b2*,  v2 = d g2  with three scalar controls (a, c, d) per step.

Bounds (closing guard): uptake J >= -min(VMAX x/(KM+x), x/(Btot DT)).
BTOT = "takers": Btot = total biomass of organisms with an exchange for x
       (A, C: both; B: org 2; W, Y: org 1)  -- the revised thesis text
BTOT = "all"   : Btot = B1 + B2 for every metabolite (replicate.py convention)
Internal reactions reversible (Chiu) unless IRREV; growth reactions >= 0 always
(ASSUMPTION: negative growth is not allowed).
"""
import numpy as np
from scipy.optimize import linprog

ENV = ["A", "B", "C", "W", "Y"]
iA, iB, iC, iW, iY = range(5)
S1 = np.array([[-1, 0, 0, 0, -1, 0, 0, 0], [1, -3, 0, -1, 0, 0, 0, 0], [0, 1, -10, 1, 0, 0, 0, 0],
               [0, 2, 0, 0, 0, 0, 0, -1], [0, 0, 0, -1, 0, -1, 0, 0], [0, 0, 0, 1, 0, 0, -1, 0]], float)
S2 = np.array([[-1, 0, 0, -1, 0], [1, -50, 0, 0, 0], [1, 0, 0, 0, -1], [-1, 0, -1, 0, 0]], float)
EX1 = {4: iA, 5: iC, 6: iY, 7: iW}
EX2 = {2: iA, 3: iB, 4: iC}
G1, G2 = 2, 1
VMAX, KM, DT, BIO0, UB = 20.0, 0.05, 0.1, 0.01, 1000.0
X0 = np.array([10.0, 10.0, 0.0, 0.0, 0.0])
b1 = np.array([1, 0, .1, 1, -1, -1, 1, 0.])
b2 = np.array([3, 1, .1, 0, -3, 0, 0, 2.])
g2 = np.array([1, .02, -1, -1, 1.])
TAKERS = {iA: (1, 2), iB: (2,), iC: (1, 2), iW: (1,), iY: (1,)}


def btot(m, B1, B2, mode):
    if mode == "all":
        return B1 + B2
    return sum(B for o, B in ((1, B1), (2, B2)) if o in TAKERS[m])


def cap(x, m, B1, B2, mode):
    c = max(x[m], 0.0)
    if c <= 0:
        return 0.0
    return min(VMAX * c / (KM + c), c / (btot(m, B1, B2, mode) * DT))


def caps(x, B1, B2, mode):
    return {m: cap(x, m, B1, B2, mode) for m in range(5)}


def fba(org, x, B1, B2, mode, irrev=False):
    S, EX, g = (S1, EX1, G1) if org == 1 else (S2, EX2, G2)
    n = S.shape[1]
    bd = []
    for j in range(n):
        if j in EX:
            bd.append((-cap(x, EX[j], B1, B2, mode), UB))
        elif j == g:
            bd.append((0.0, UB))
        else:
            bd.append((0.0, UB) if irrev else (-UB, UB))
    c = np.zeros(n); c[g] = -1
    r = linprog(c, A_eq=S, b_eq=np.zeros(S.shape[0]), bounds=bd, method="highs")
    assert r.status == 0, r.message
    mu = -r.fun
    A = np.vstack([np.hstack([S, -S]), np.r_[np.eye(n)[g], -np.eye(n)[g]]])
    bd2 = [(0, max(h, 0)) for l, h in bd] + [(0, max(-l, 0)) for l, h in bd]
    r2 = linprog(np.ones(2 * n), A_eq=A, b_eq=np.r_[np.zeros(S.shape[0]), mu], bounds=bd2, method="highs")
    assert r2.status == 0, r2.message
    return r2.x[:n] - r2.x[n:]


def to_controls(v1, v2):
    """exact coordinates: v1 = a b1 + c b2*, v2 = d g2."""
    a, c = np.linalg.lstsq(np.c_[b1, b2], v1, rcond=None)[0]
    d = v2[0]
    return a, c, d


def step(x, B1, B2, a, c, d):
    dx = DT * (B1 * np.array([-a - 3 * c, 0, -a, 2 * c, a]) + B2 * np.array([-d, -d, d, 0, 0]))
    return x + dx, B1 * np.exp(DT * 0.1 * (a + c)), B2 * np.exp(DT * 0.02 * d)


def feasible(x, B1, B2, a, c, d, mode, irrev=False, tol=1e-9):
    """closing-guard check for controls (a,c,d) at state (x, B1, B2)."""
    k = caps(x, B1, B2, mode)
    ok = [a + 3 * c <= k[iA] + tol, a <= k[iC] + tol, -a <= k[iY] + tol, -2 * c <= k[iW] + tol,
          a + c >= -tol, d >= -tol, d <= k[iA] + tol, d <= k[iB] + tol]
    if irrev:
        ok += [a >= -tol, c >= -tol]
    return all(ok)


def greedy(N, mode, irrev=False):
    x, B1, B2 = X0.copy(), BIO0, BIO0
    X, Bs, U = [x.copy()], [(B1, B2)], []
    for _ in range(N):
        v1 = fba(1, x, B1, B2, mode, irrev); v2 = fba(2, x, B1, B2, mode, irrev)
        a, c, d = to_controls(v1, v2)
        assert np.allclose(a * b1 + c * b2, v1, atol=1e-7)
        x, B1, B2 = step(x, B1, B2, a, c, d)
        x = np.where(np.abs(x) < 1e-12, 0.0, x)
        X.append(x.copy()); Bs.append((B1, B2)); U.append((a, c, d))
    return np.array(X), np.array(Bs), np.array(U)


def yield_bound(x):
    """Exact all-horizon bound on further Y from state x (any strategy, any time).
    Integrated conversions: P = org2 (A+B->C), Ya = org1 b1 (A+C->Y), Cc = org1 b2*.
    Final concentrations >= 0 give  Ya <= C + P,  P <= B,  Ya + 3Cc + P <= A,  Cc >= -W/2.
    Max Ya over P in [0, B]:  min(C + B, A + 1.5W,  (A + 1.5W + C)/2)."""
    A, B, C, W = x[iA], x[iB], x[iC], x[iW]
    E = A + 1.5 * W
    P = np.clip((E - C) / 2, 0, B)
    return max(0.0, min(C + P, E - P))
