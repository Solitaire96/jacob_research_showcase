"""
Chiu et al. (2014) toy co-culture: dFBA reference and three hybrid-automaton encodings.

  dfba(...)            dFBA reference (LP per step, Chiu uptake bounds)
  simulate_drawn(...)  the automata exactly as drawn in thesis Ch. 6
                       (live bounds, shared clock, union-of-guards composition,
                        one-mode-per-step controller with detection threshold eps)
  simulate_fixed(...)  the corrected automata: snapshot bounds, sequential product,
                       FBA-equivalent forced chain (greedy dwell in mode order)

All automaton simulators are exact event-driven integrations: inside a location the
flow is constant (Bio is frozen during a run), so every state is affine in dwell time
and each guard crossing is found by bisection to machine precision.
"""
import numpy as np
from scipy.optimize import linprog

ENV = ["A", "B", "C", "W", "Y"]
IX = {m: i for i, m in enumerate(ENV)}

# ------------------------------------------------------------------ stoichiometry (Ch. 5)
# Organism 1: v = [R1 R2 R3 R4 J1 J2 J3 J4]; growth R3; exchanges J1:A J2:C J3:Y J4:W
S1 = np.array([[-1, 0, 0, 0, -1, 0, 0, 0],    # A
               [1, -3, 0, -1, 0, 0, 0, 0],     # D
               [0, 1, -10, 1, 0, 0, 0, 0],     # Z
               [0, 2, 0, 0, 0, 0, 0, -1],      # W
               [0, 0, 0, -1, 0, -1, 0, 0],     # C
               [0, 0, 0, 1, 0, 0, -1, 0]], float)  # Y
# Organism 2: v = [R5 R6 J5 J6 J7]; growth R6; exchanges J5:A J6:B J7:C
S2 = np.array([[-1, 0, 0, -1, 0],     # B
               [1, -50, 0, 0, 0],     # X
               [1, 0, 0, 0, -1],      # C
               [-1, 0, -1, 0, 0]], float)  # A

ORG = {
    1: dict(S=S1, EX={4: "A", 5: "C", 6: "Y", 7: "W"}, GROW=2, NINT=4, name="Organism 1"),
    2: dict(S=S2, EX={2: "A", 3: "B", 4: "C"}, GROW=1, NINT=2, name="Organism 2"),
}

# Conic generators with internal R >= 0 (thesis Ch. 6): Org 1 {b1, b2*}, Org 2 {b1}.
# Order = preference order used by the forced chain (Y-producing mode first).
B1 = np.array([1, 0, .1, 1, -1, -1, 1, 0.])          # A + C -> Y  (preferred)
B2S = np.array([0, 1, -.2, -3, 0, 3, -3, 2.]) + 3 * B1  # = [3,1,.1,0,-3,0,0,2]: 3A -> 2W
G2 = np.array([1, .02, -1, -1, 1.])                  # A + B -> C
GEN = {1: [B1, B2S], 2: [G2]}
for o, gs in GEN.items():
    for g in gs:
        assert np.allclose(ORG[o]["S"] @ g, 0)

PARAMS = dict(VMAX=20.0, KM=0.05, DT=0.1, BIO0=0.01,
              X0={"A": 10.0, "B": 10.0, "C": 0.0, "W": 0.0, "Y": 0.0},
              EPS=1e-3,           # detection threshold
              BOUND_BIO="total")  # "total" (corrected; "own" = thesis figures, overdraws shared substrate)
UB = 1000.0


def T(o):
    M = np.zeros((len(ENV), ORG[o]["S"].shape[1]))
    for j, m in ORG[o]["EX"].items():
        M[IX[m], j] = 1.0
    return M


def uptake_cap(c, bio, P):
    """Chiu uptake bound magnitude: min(Vmax c/(Km+c), c/(bio dt)); 0 if c <= 0."""
    if c <= 0:
        return 0.0
    return min(P["VMAX"] * c / (P["KM"] + c), c / (bio * P["DT"]))


def bio_for_bound(o, bio, P):
    return bio[o] if P["BOUND_BIO"] == "own" else sum(bio.values())


# ------------------------------------------------------------------------- dFBA reference
def fba(o, x, bio, P, irreversible=True):
    S = ORG[o]["S"]; n = S.shape[1]; g = ORG[o]["GROW"]
    bd = []
    for j in range(n):
        if j in ORG[o]["EX"]:
            cap = uptake_cap(x[IX[ORG[o]["EX"][j]]], bio_for_bound(o, bio, P), P)
            bd.append((-cap, UB))
        else:
            bd.append((0.0, UB) if irreversible else (-UB, UB))
    c = np.zeros(n); c[g] = -1
    r = linprog(c, A_eq=S, b_eq=np.zeros(S.shape[0]), bounds=bd, method="highs")
    assert r.status == 0, r.message
    mu = -r.fun
    # Chiu tie-break: minimise sum |v| at the optimum
    A = np.vstack([np.hstack([S, -S]), np.r_[np.eye(n)[g], -np.eye(n)[g]]])
    b = np.r_[np.zeros(S.shape[0]), mu]
    bd2 = [(0, max(h, 0)) for l, h in bd] + [(0, max(-l, 0)) for l, h in bd]
    r2 = linprog(np.ones(2 * n), A_eq=A, b_eq=b, bounds=bd2, method="highs")
    assert r2.status == 0, r2.message
    return r2.x[:n] - r2.x[n:]


def dfba(orgs, nsteps, P=PARAMS, irreversible=True):
    x = np.array([P["X0"][m] for m in ENV]); bio = {o: (P["BIO0"][o] if isinstance(P["BIO0"], dict) else P["BIO0"]) for o in orgs}
    out = [dict(k=0, x=x.copy(), bio=dict(bio), v=None)]
    for k in range(1, nsteps + 1):
        v = {o: fba(o, x, bio, P, irreversible) for o in orgs}
        dx = sum(P["DT"] * bio[o] * T(o) @ v[o] for o in orgs)
        x = np.maximum(x + dx, 0.0); x[np.abs(x) < 1e-12] = 0.0
        bio = {o: bio[o] * np.exp(P["DT"] * v[o][ORG[o]["GROW"]]) for o in orgs}
        out.append(dict(k=k, x=x.copy(), bio=dict(bio), v=v))
    return out


# -------------------------------------------------------- exact event-driven helpers
def first_crossing(h, dmax, tol=1e-14):
    """Smallest d in [0, dmax] with h(d) <= 0, h continuous, h(0) > 0 assumed.
    Returns None if h(dmax) > 0. Scans then bisects (h need not be monotone)."""
    if h(0.0) <= 0:
        return 0.0
    grid = np.linspace(0.0, dmax, 2001)
    prev = 0.0
    for d in grid[1:]:
        if h(d) <= 0:
            lo, hi = prev, d
            while hi - lo > tol * max(1.0, hi):
                mid = 0.5 * (lo + hi)
                if h(mid) <= 0:
                    hi = mid
                else:
                    lo = mid
            return hi
        prev = d
    return None


# -------------------------------------------------------- corrected automata (stage 1-4)
def simulate_fixed(orgs, nsteps, P=PARAMS):
    """Sequential product over organisms; each organism is a forced chain over its
    generators in preference order.  In location (o,i): dC/dt = Bio_o T_o g_i,
    dQ_o/dt = g_i.  Leave (o,i) as soon as some exchange Q_oJ reaches -cap_J(xbar)*dt
    (cap evaluated on the run-start snapshot xbar).  After the last location of the last
    organism: closing event  Bio_o := Bio_o exp(Q_o[growth]), Q := 0, xbar := C, k += 1."""
    DT = P["DT"]
    x = np.array([P["X0"][m] for m in ENV]); bio = {o: (P["BIO0"][o] if isinstance(P["BIO0"], dict) else P["BIO0"]) for o in orgs}
    out = [dict(k=0, x=x.copy(), bio=dict(bio), dwell=None, tau=0.0)]
    tau = 0.0
    for k in range(1, nsteps + 1):
        xbar = x.copy()
        Q = {o: np.zeros(ORG[o]["S"].shape[1]) for o in orgs}
        dw = {}
        for o in orgs:
            caps = {j: uptake_cap(xbar[IX[m]], bio_for_bound(o, bio, P), P) * DT
                    for j, m in ORG[o]["EX"].items()}
            for i, g in enumerate(GEN[o]):
                # dwell until the first uptake Q_J hits -cap_J (constant guard -> closed form)
                d = np.inf
                for j, cap in caps.items():
                    if g[j] < 0:
                        d = min(d, (Q[o][j] + cap) / (-g[j]))
                d = max(d, 0.0)
                assert np.isfinite(d), "mode with no uptake would dwell forever"
                x = x + d * bio[o] * (T(o) @ g)
                Q[o] = Q[o] + d * g
                dw[(o, i)] = d
                tau += d
        for o in orgs:
            bio[o] = bio[o] * np.exp(Q[o][ORG[o]["GROW"]])
        x[np.abs(x) < 1e-12] = 0.0
        out.append(dict(k=k, x=x.copy(), bio=dict(bio), dwell=dw, tau=tau,
                        v={o: Q[o] / DT for o in orgs}))
    return out


# -------------------------------------------------------- automata exactly as drawn (Ch. 6)
def simulate_drawn(orgs, nruns, P=PARAMS, max_tau=None):
    """Thesis Ch. 6 figures (RedOrganismWithControl, BlueOrganismWithControl,
    CoCultureWithControl).  One clock shared by all organisms.
      Org 1: l1 = b2* (3A -> 2W), l2 = b1 (A + C -> Y).  Forced l1 -> l2 when C > eps.
             Leaving l2 when C < eps closes the run.
      Org 2: single location b1 (A + B -> C).
      Closing (gamma): forced as soon as ANY listed guard holds:
             Q_J < -min{Vm X/(Km+X), X/(Bio dt)} dt  with X the LIVE concentration.
             Resets every Q and updates every Bio (synchronised).
    Returns per-run records; physical time is the run index times dt (as in dFBA), the
    automaton clock tau is reported separately."""
    DT, EPS = P["DT"], P["EPS"]
    x = np.array([P["X0"][m] for m in ENV]); bio = {o: (P["BIO0"][o] if isinstance(P["BIO0"], dict) else P["BIO0"]) for o in orgs}
    loc1 = "l1"
    tau = 0.0
    out = [dict(k=0, x=x.copy(), bio=dict(bio), tau=0.0, loc=loc1)]
    k = 0
    zeno = 0
    while k < nruns:
        Q = {o: np.zeros(ORG[o]["S"].shape[1]) for o in orgs}
        # ---- one run: possibly a zero-time switch l1->l2 first
        while True:
            if 1 in orgs and loc1 == "l1" and x[IX["C"]] > EPS:
                loc1 = "l2"
            gens = {}
            if 1 in orgs:
                gens[1] = B1 if loc1 == "l2" else B2S
            if 2 in orgs:
                gens[2] = G2
            rate = sum(bio[o] * (T(o) @ gens[o]) for o in gens)

            def state(d):
                return x + d * rate, {o: Q[o] + d * gens[o] for o in gens}

            # closing guards (union over organisms), live bounds
            def h_close(d):
                xd, Qd = state(d)
                vals = []
                for o in gens:
                    for j, m in ORG[o]["EX"].items():
                        if gens[o][j] < 0:   # only uptakes listed in the figures
                            cap = uptake_cap(xd[IX[m]], bio_for_bound(o, bio, P), P) * DT
                            vals.append(Qd[o][j] + cap)   # guard: Q_J < -cap
                return min(vals)

            def h_cdrop(d):              # l2 -> l1 when C < eps
                xd, _ = state(d)
                return xd[IX["C"]] - EPS

            def h_crise(d):              # l1 -> l2 when C > eps
                xd, _ = state(d)
                return EPS - xd[IX["C"]]

            dmax = 50.0
            cands = [("close", first_crossing(h_close, dmax))]
            if 1 in orgs and loc1 == "l2":
                cands.append(("cdrop", first_crossing(h_cdrop, dmax)))
            if 1 in orgs and loc1 == "l1":
                cands.append(("crise", first_crossing(h_crise, dmax)))
            cands = [(n, d) for n, d in cands if d is not None]
            if not cands:
                return out, dict(stalled_at_run=k, tau=tau)
            name, d = min(cands, key=lambda t: t[1])
            x, Qn = state(d); Q = Qn
            x = np.maximum(x, 0.0)
            tau += d
            if name == "crise":
                loc1 = "l2"
                continue
            # close the run ('close' or 'cdrop')
            if d == 0.0:
                zeno += 1            # zero-duration run: Zeno in automaton time
            for o in gens:
                bio[o] = bio[o] * np.exp(Q[o][ORG[o]["GROW"]])
            if name == "cdrop":
                loc1 = "l1"
            else:
                loc1 = "l1"          # gamma edges of Org 1 target l1 in the figure
            k += 1
            out.append(dict(k=k, x=x.copy(), bio=dict(bio), tau=tau, loc=loc1, ev=name))
            break
        if max_tau is not None and tau > max_tau:
            break
    return out, dict(tau=tau, zero_duration_runs=zeno)
