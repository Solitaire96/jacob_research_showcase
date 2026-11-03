"""Task 5: machine-check the hand lemma behind the time-bounded urate theorem.

f(C) = min(VMAX*C/(KM+C), C/(Bio*DT)) for C > 0, f(0) = 0
g(C) = C - DT*Bio*f(C)            (the greedy single-step adenine map)

Each claim is checked as validity: the negation is asserted and Z3 must answer unsat.
Divisions are removed with auxiliary variables (a*(KM+C) = VMAX*C), so every query
is quantifier-free nonlinear real arithmetic (QF_NRA, decided by nlsat).
"""
import z3

R = z3.Real


def f_and_g(C, VMAX, KM, DT, Bio, tag):
    """Return (constraints, fval, gval) defining f(C), g(C) for C >= 0."""
    a = R(f"a_{tag}")          # MM term VMAX*C/(KM+C)
    b = R(f"b_{tag}")          # depletion term C/(Bio*DT)
    fv = R(f"f_{tag}")
    cons = [a * (KM + C) == VMAX * C, b * (Bio * DT) == C,
            fv == z3.If(a <= b, a, b)]       # min; equals 0 at C = 0 since a = b = 0
    return cons, fv, C - DT * Bio * fv


def valid(name, hyps, concl, timeout_ms=600000):
    s = z3.SolverFor("QF_NRA"); s.set("timeout", timeout_ms)
    s.add(*hyps); s.add(z3.Not(concl))
    r = s.check()
    status = "PROVED" if r == z3.unsat else "COUNTEREXAMPLE" if r == z3.sat else "UNKNOWN"
    print(f"[{status}] {name}")
    if r == z3.sat:
        m = s.model()
        print("    model:", {str(d): m[d] for d in m.decls() if not str(d).startswith(("a_", "b_", "f_"))})
    return r, (s.model() if r == z3.sat else None)


x, y = R("x"), R("y")
VMAX, KM, DT, Bio = R("VMAX"), R("KM"), R("DT"), R("Bio")
pos = [VMAX > 0, KM > 0, DT > 0, Bio > 0]

# (i) monotonicity for the parameters actually used
fixed = [VMAX == 1, KM == z3.RealVal("1/2"), DT == z3.RealVal("1/4"), Bio == 1]
cx, _, gx = f_and_g(x, VMAX, KM, DT, Bio, "x")
cy, _, gy = f_and_g(y, VMAX, KM, DT, Bio, "y")
valid("(i)  g nondecreasing on C>=0, VMAX=1 KM=1/2 DT=1/4 Bio=1",
      fixed + pos + cx + cy + [0 <= x, x <= y], gx <= gy)

# (ii-a) the hand proof's sufficient condition DT*Bio*VMAX <= KM
valid("(ii-a) g nondecreasing whenever DT*Bio*VMAX <= KM (all params > 0)",
      pos + [DT * Bio * VMAX <= KM] + cx + cy + [0 <= x, x <= y], gx <= gy)

# (ii-b) search for a failure when DT*Bio*VMAX > KM
valid("(ii-b) g nondecreasing for ALL positive params (no side condition)",
      pos + cx + cy + [0 <= x, x <= y], gx <= gy)

# (ii-c) the MM branch alone (depletion cap removed) does fail when DT*Bio*VMAX > KM
ax, ay = R("ax"), R("ay")
hx, hy = x - DT * Bio * ax, y - DT * Bio * ay
r, m = valid("(ii-c) MM-only map h(C)=C-DT*Bio*VMAX*C/(KM+C) nondecreasing (expect counterexample)",
             pos + [DT * Bio * VMAX > KM, ax * (KM + x) == VMAX * x, ay * (KM + y) == VMAX * y,
                    0 <= x, x <= y], hx <= hy)
valid("(ii-d) MM-only map nondecreasing when DT*Bio*VMAX <= KM (boundary is exactly k = KM)",
      pos + [DT * Bio * VMAX <= KM, ax * (KM + x) == VMAX * x, ay * (KM + y) == VMAX * y,
             0 <= x, x <= y], hx <= hy)

# (iii) induction step, under the closing guard, for all positive params
#   hypotheses: A_k >= 0, ade_k >= A_k, step uptake u in [0, f(ade_k)], ade_{k+1} = ade_k - DT*Bio*u
#   (u >= f is excluded by the guard; secretion of adenine is impossible: no producing generator)
Ak, adek, u, adek1 = R("Ak"), R("adek"), R("u"), R("adek1")
cA, _, gA = f_and_g(Ak, VMAX, KM, DT, Bio, "A")
cD, fD, _ = f_and_g(adek, VMAX, KM, DT, Bio, "D")
step = [Ak >= 0, adek >= Ak, 0 <= u, u <= fD, adek1 == adek - DT * Bio * u]
valid("(iii-a) induction step: ade_{k+1} >= g(A_k) = A_{k+1}", pos + cA + cD + step, adek1 >= gA)
valid("(iii-b) A_{k+1} = g(A_k) >= 0 (keeps the recursion in C>=0)", pos + cA + [Ak >= 0], gA >= 0)
valid("(iii-c) guard keeps ade_{k+1} >= 0", pos + cA + cD + step, adek1 >= 0)

# (iii-d) combine with P1: urate_n - urate_0 <= ade_0 - ade_n  and  ade_n >= A_n
u0, un, a0, an, An = R("u0"), R("un"), R("a0"), R("an"), R("An")
valid("(iii-d) P1 + (ade_n >= A_n)  ==>  urate_n - urate_0 <= ade_0 - A_n",
      [un - u0 <= a0 - an, an >= An], un - u0 <= a0 - An)

# (iii-e) sanity: 3 unrolled steps from A_0 = ade_0 with the fixed parameters
hyps = fixed + pos; A = [R("A0")]; D = [R("D0")]; hyps += [A[0] == D[0], A[0] >= 0]
for k in range(3):
    c1, _, gk = f_and_g(A[k], VMAX, KM, DT, Bio, f"Au{k}")
    c2, fk, _ = f_and_g(D[k], VMAX, KM, DT, Bio, f"Du{k}")
    uk = R(f"uu{k}"); A.append(R(f"A{k+1}")); D.append(R(f"D{k+1}"))
    hyps += c1 + c2 + [A[k + 1] == gk, 0 <= uk, uk <= fk, D[k + 1] == D[k] - DT * Bio * uk]
valid("(iii-e) 3-step unrolling: ade_3 >= A_3 for every guarded strategy", hyps, D[3] >= A[3])
print("z3", z3.get_version_string())

# (ii-e) boundary is sharp for the MM-only map: for EVERY params with DT*Bio*VMAX > KM,
#        y = (DT*Bio*VMAX - KM)/2 gives h(y) < h(0) = 0.
yy, ayy = R("yy"), R("ayy")
valid("(ii-e) MM-only map fails for every DT*Bio*VMAX > KM (witness y=(k-KM)/2)",
      pos + [DT * Bio * VMAX > KM, yy == (DT * Bio * VMAX - KM) / 2, ayy * (KM + yy) == VMAX * yy],
      yy - DT * Bio * ayy < 0)
