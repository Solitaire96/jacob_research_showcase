"""
Replication of the core claims in "A Hybrid Automata Framework for the Representation
and Analysis of Microbial Co-Cultures" (J. F. Bowers, MS thesis draft, FIT 2021).

What this script does
  1. Rebuilds the Chiu et al. (2014) toy co-culture as a dynamic FBA (dFBA).
  2. Computes the extreme rays (conic generators) of each organism's flux cone.
  3. Replays every dFBA step through the hybrid-automaton encoding
     (dwell time in location i = dt * c_i, where v = sum c_i g_i, c_i >= 0)
     and checks the automaton state equals the dFBA state at every run boundary.
  4. Samples non-optimal but feasible flux strategies (what the nondeterministic
     automaton admits and dFBA does not) to show the reachable envelope of Y.
  5. Counterexamples for two flaws in the draft:
       (a) an arbitrary null-space basis can force negative dwell times;
       (b) the continuous Q-bound guard rejects feasible fluxes depending on
           location order.

Assumptions not fixed by the draft (marked ASSUMPTION below) follow Chiu et al. 2014
Methods where stated there: Vmax = 20, Km = 0.05, dt = 0.1 h, initial biomass 0.01 g/L,
growth maximisation per species, ties broken by minimum total absolute flux.
Concentration update uses the draft's convention (biomass frozen within a step,
exponential biomass update at step end), which is what the automaton encodes.
"""
import numpy as np
from scipy.optimize import linprog, nnls
import itertools, json, sys, os
HERE = os.path.dirname(os.path.abspath(__file__))

rng = np.random.default_rng(0)

# ----------------------------------------------------------------------------- model
ENV = ["A", "B", "C", "W", "Y"]

# Organism 1 ("red"): fluxes [R1 R2 R3 R4 J1 J2 J3 J4]; growth = R3
S1 = np.array([[-1, 0, 0, 0, -1, 0, 0, 0],    # A
               [1, -3, 0, -1, 0, 0, 0, 0],     # D
               [0, 1, -10, 1, 0, 0, 0, 0],     # Z
               [0, 2, 0, 0, 0, 0, 0, -1],      # W
               [0, 0, 0, -1, 0, -1, 0, 0],     # C
               [0, 0, 0, 1, 0, 0, -1, 0]], float)  # Y
# exchange index -> env metabolite (J>0 = secretion into medium)
EX1 = {4: "A", 5: "C", 6: "Y", 7: "W"}
GROW1 = 2
N_INT1 = 4

# Organism 2 ("blue"): fluxes [R5 R6 J5 J6 J7]; growth = R6
S2 = np.array([[-1, 0, 0, -1, 0],   # B
               [1, -50, 0, 0, 0],   # X
               [1, 0, 0, 0, -1],    # C
               [-1, 0, -1, 0, 0]], float)  # A
EX2 = {2: "A", 3: "B", 4: "C"}
GROW2 = 1
N_INT2 = 2

VMAX, KM, DT = 20.0, 0.05, 0.1          # Chiu 2014 Methods
BIO0 = 0.01                              # Chiu 2014 Methods (g/L)
X0 = {"A": 10.0, "B": 10.0, "C": 0.0, "W": 0.0, "Y": 0.0}  # ASSUMPTION: only A,B supplied
UB = 1000.0                              # numerical stand-in for +inf
EPS_DETECT = 1e-3                        # Chiu detection threshold (mM)

ORGS = {
    1: dict(S=S1, EX=EX1, GROW=GROW1, NINT=N_INT1),
    2: dict(S=S2, EX=EX2, GROW=GROW2, NINT=N_INT2),
}


def T_matrix(org):
    o = ORGS[org]
    T = np.zeros((len(ENV), o["S"].shape[1]))
    for j, m in o["EX"].items():
        T[ENV.index(m), j] = 1.0
    return T


def bounds(org, x, btot):
    """Chiu-style uptake bound: J >= -min(Vmax x/(Km+x), x/(Btot dt)); internal fluxes free."""
    o = ORGS[org]
    n = o["S"].shape[1]
    b = [(-UB, UB)] * n
    for j, m in o["EX"].items():
        c = max(x[m], 0.0)
        lim = min(VMAX * c / (KM + c), c / (btot * DT)) if c > 0 else 0.0
        b[j] = (-lim, UB)
    return b


def fba(org, x, btot):
    """max growth, then min sum|v| at fixed optimum (Chiu's tie-break)."""
    o = ORGS[org]
    S, n, g = o["S"], o["S"].shape[1], o["GROW"]
    bd = bounds(org, x, btot)
    c = np.zeros(n); c[g] = -1
    r = linprog(c, A_eq=S, b_eq=np.zeros(S.shape[0]), bounds=bd, method="highs")
    assert r.status == 0, r.message
    mu = -r.fun
    # min sum |v| : v = p - q, p,q >= 0
    A_eq = np.hstack([S, -S])
    row = np.zeros(2 * n); row[g] = 1; row[n + g] = -1
    A_eq = np.vstack([A_eq, row]); b_eq = np.r_[np.zeros(S.shape[0]), mu]
    bd2 = []
    for lo, hi in bd:
        bd2.append((0, max(hi, 0)))
    for lo, hi in bd:
        bd2.append((0, max(-lo, 0)))
    r2 = linprog(np.ones(2 * n), A_eq=A_eq, b_eq=b_eq, bounds=bd2, method="highs")
    assert r2.status == 0, r2.message
    v = r2.x[:n] - r2.x[n:]
    return v, mu


def step(x, bio, v):
    """draft's update: biomass frozen within the step, exponential biomass at step end."""
    xn = dict(x)
    dx = np.zeros(len(ENV))
    for org in v:
        dx += DT * bio[org] * T_matrix(org) @ v[org]
    for i, m in enumerate(ENV):
        xn[m] = max(x[m] + dx[i], 0.0) if abs(x[m] + dx[i]) > 1e-12 else 0.0
    bn = {org: bio[org] * np.exp(DT * v[org][ORGS[org]["GROW"]]) for org in v}
    return xn, bn


def dfba(orgs, nsteps=200):
    x, bio = dict(X0), {o: BIO0 for o in orgs}
    traj = [(dict(x), dict(bio), None)]
    for k in range(nsteps):
        btot = sum(bio.values())
        v = {o: fba(o, x, btot)[0] for o in orgs}
        x, bio = step(x, bio, v)
        traj.append((dict(x), dict(bio), v))
    return traj


# ----------------------------------------------------------------- flux-cone generators
def extreme_rays_2d_check():
    """Org 1: null space is 2-D; with internal R >= 0 the cone is generated by b1, b2* (thesis)."""
    b1 = np.array([1, 0, .1, 1, -1, -1, 1, 0.])
    b2 = np.array([0, 1, -.2, -3, 0, 3, -3, 2.])
    b2s = b2 + 3 * b1
    assert np.allclose(S1 @ b1, 0) and np.allclose(S1 @ b2, 0) and np.allclose(S1 @ b2s, 0)
    g2 = np.array([1, .02, -1, -1, 1.])
    assert np.allclose(S2 @ g2, 0)
    return {1: np.array([b1, b2s]), 2: np.array([g2])}


GEN_THESIS = extreme_rays_2d_check()          # cone with internal R >= 0 (thesis)
# Chiu's model has internal R unbounded in both directions, so the steady-state set is
# the whole null space (a subspace): conic generators are +/- each basis vector.
GEN_CHIU = {o: np.vstack([G, -G]) for o, G in GEN_THESIS.items()}
GEN = GEN_CHIU


def decompose(org, v, gen=None):
    """v = sum c_i g_i with c_i >= 0 (NNLS). Returns c and residual."""
    G = (gen or GEN)[org].T
    c, res = nnls(G, v)
    return c, np.linalg.norm(G @ c - v)


# ------------------------------------------------------------- automaton replay (Alg. 3, fixed)
def automaton_replay(traj, orgs, gen=None):
    gen = gen or GEN
    """
    Co-culture automaton built as ONE cycle over the stacked generators of all organisms
    (sequential product; valid because biomass is frozen within a run, so the order of
    locations inside a run does not change the run-end state).
    Each location l_(org,i): dC/dt = Bio_org * T_org g_i, dQ_org/dt = g_i.
    Run end (closing transition): Bio_org := Bio_org * exp(Q_org[growth]); Q := 0;
    bound snapshot xbar := C.  Guards are checked at the closing transition only.
    """
    x = np.array([X0[m] for m in ENV]); bio = {o: BIO0 for o in orgs}
    tau_total = 0.0
    max_err, max_bio_err, min_c, n_outside = 0.0, 0.0, np.inf, 0
    for k in range(1, len(traj)):
        xk, biok, v = traj[k]
        Q = {o: np.zeros(ORGS[o]["S"].shape[1]) for o in orgs}
        for o in orgs:
            c, res = decompose(o, v[o], gen)
            if res > 1e-8:
                n_outside += 1
            min_c = min(min_c, c.min())
            for i, ci in enumerate(c):
                dwell = DT * ci                      # dwell time in location (o,i)
                x = x + dwell * bio[o] * (T_matrix(o) @ gen[o][i])
                Q[o] = Q[o] + dwell * gen[o][i]
                tau_total += dwell
        for o in orgs:                               # closing transition
            bio[o] = bio[o] * np.exp(Q[o][ORGS[o]["GROW"]])
        x = np.where(np.abs(x) < 1e-12, 0.0, x)
        ref = np.array([xk[m] for m in ENV])
        max_err = max(max_err, np.max(np.abs(x - ref)))
        max_bio_err = max(max_bio_err, max(abs(bio[o] - biok[o]) for o in orgs))
    return dict(steps_with_flux_outside_generator_cone=n_outside, max_conc_err=max_err, max_bio_err=max_bio_err,
                min_coefficient=min_c, automaton_time=tau_total,
                physical_time=DT * (len(traj) - 1))


# ---------------------------------------------- nondeterministic (non-optimal) strategies
def random_feasible(org, x, btot):
    """Uniform-ish random point of F_t = {Sv=0, bounds} via random objective + random scaling."""
    o = ORGS[org]; S = o["S"]; n = S.shape[1]
    bd = bounds(org, x, btot)
    # restrict internal R >= 0 so the toy cone matches the thesis' generators
    bd = [(max(lo, 0.0), hi) if j < o["NINT"] else (lo, hi) for j, (lo, hi) in enumerate(bd)]
    # cap secretion so the LP is bounded
    bd = [(lo, min(hi, 50.0)) for lo, hi in bd]
    cobj = rng.normal(size=n)
    r = linprog(cobj, A_eq=S, b_eq=np.zeros(S.shape[0]), bounds=bd, method="highs")
    if r.status != 0:
        return np.zeros(n)
    return r.x * rng.uniform(0, 1)       # 0 is feasible, F_t convex -> segment is feasible


def envelope(orgs, nsamp=300, nsteps=200):
    Ys = np.zeros((nsamp, nsteps + 1))
    for s in range(nsamp):
        x, bio = dict(X0), {o: BIO0 for o in orgs}
        for k in range(nsteps):
            btot = sum(bio.values())
            v = {o: random_feasible(o, x, btot) for o in orgs}
            x, bio = step(x, bio, v)
            Ys[s, k + 1] = x["Y"]
    return Ys


# ------------------------------------------------------------------ counterexamples
def counterexample_basis():
    """One metabolite M, fluxes v1 (in), v2, v3 (out), all irreversible: S = [1 -1 -1].
    Valid null-space basis {(1,1,0), (0,1,-1)}; feasible v = (1,0,1) needs coefficient -1."""
    S = np.array([[1., -1, -1]])
    B = np.array([[1, 1, 0], [0, 1, -1.]]).T
    assert np.allclose(S @ B, 0)
    v = np.array([1., 0, 1])
    c = np.linalg.lstsq(B, v, rcond=None)[0]
    return dict(basis=B.T.tolist(), v=v.tolist(), coeffs=c.round(6).tolist())


def counterexample_guard():
    """Overflow toy: glucose G, acetate Ac exchanges.
    mode1: G -> Ac (acetate secreted, +1);  mode2: Ac -> biomass (acetate taken up, -1).
    Ac absent from medium -> lower bound on J_Ac is 0 (no net uptake).
    v = mode1 + mode2 has J_Ac = 0: feasible.  Continuous guard Q_Ac < vmin*dt = 0
    fires as soon as mode2 is entered first, so the run is cut before mode1 is reached."""
    g = {"mode1": np.array([+1.0]), "mode2": np.array([-1.0])}   # J_Ac component only
    out = {}
    for order in (("mode1", "mode2"), ("mode2", "mode1")):
        Q, ok, t = 0.0, True, 0.0
        for m in order:
            # integrate dQ/dt = g_m for dwell = dt*1 with fine resolution, check guard
            for _ in range(1000):
                Q += g[m][0] * DT / 1000
                if Q < -1e-15:        # guard Q_Ac < vmin*dt (=0)
                    ok = False
                    break
            if not ok:
                break
        out[" -> ".join(order)] = "reachable" if ok else "cut by guard (feasible flux lost)"
    return out


# ----------------------------------------------------------------------------- main
if __name__ == "__main__":
    out = {}
    nsteps = int(sys.argv[1]) if len(sys.argv) > 1 else 150   # 15 h at dt = 0.1
    runs = {"mono1": [1], "mono2": [2], "co": [1, 2]}
    trajs = {k: dfba(o, nsteps) for k, o in runs.items()}
    for k, tr in trajs.items():
        Y = np.array([t[0]["Y"] for t in tr])
        C = np.array([t[0]["C"] for t in tr])
        out[k] = dict(Y_final=float(Y[-1]), Y_max=float(Y.max()), C_max=float(C.max()),
                      t_Y_detect=(float(DT * np.argmax(Y > EPS_DETECT)) if (Y > EPS_DETECT).any() else None),
                      bio_final={o: float(b) for o, b in tr[-1][1].items()},
                      A_final=float(tr[-1][0]["A"]), B_final=float(tr[-1][0]["B"]))
        # internal-flux sign check (thesis assumed R >= 0)
        negR = 0.0
        for t in tr[1:]:
            for o, vo in t[2].items():
                negR = min(negR, vo[:ORGS[o]["NINT"]].min())
        out[k]["min_internal_flux"] = float(negR)
        out[k]["automaton_replay_thesis_generators_Rnonneg"] = automaton_replay(tr, runs[k], GEN_THESIS)
        out[k]["automaton_replay_chiu_generators_pm"] = automaton_replay(tr, runs[k], GEN_CHIU)
    out["counterexample_nullspace_basis"] = counterexample_basis()
    out["counterexample_bound_guard"] = counterexample_guard()
    np.save(os.path.join(HERE, "co_traj_Y.npy"), np.array([t[0]["Y"] for t in trajs["co"]]))
    Ys = envelope([1, 2], nsamp=200, nsteps=nsteps)
    np.save(os.path.join(HERE, "env_Y.npy"), Ys)
    Ym = envelope([1], nsamp=50, nsteps=nsteps)
    out["envelope"] = dict(co_Y_final_min=float(Ys[:, -1].min()), co_Y_final_max=float(Ys[:, -1].max()),
                           co_Y_dfba_final=out["co"]["Y_final"],
                           mono1_Y_max_over_all_samples=float(Ym.max()))
    print(json.dumps(out, indent=1, default=float))
    json.dump(out, open(os.path.join(HERE, "results.json"), "w"), indent=1, default=float)
