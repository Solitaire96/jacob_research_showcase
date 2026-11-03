"""Task 4: discharge the proof obligations of rbc_props.kyx independently with Z3.

Projected automaton (for x = urate (P1) or hxan (P2)):
  loop { choose (ga, gx) among the branches;  ade' = Bio*ga, x' = Bio*gx  for any t >= 0 }
Claim: Bio >= 0 & ade = A0 & x = X0  ->  [loop*]  x - X0 <= A0 - ade.
Loop invariant J := x - X0 <= A0 - ade & Bio >= 0.
  (a) J preserved by every branch's ODE flow for all t >= 0 (closed-form solution),
      and by differential invariance (dI: derivative of A0 - ade - (x - X0) >= 0)
  (b) init -> J         (c) J -> post
Branch values are recomputed from the exact GMP generators of the RBC cone
(imports properties.py from the RBC working folder; not included here, so this script is for reference).
"""
import sys, os
from fractions import Fraction
import z3

sys.path.insert(0, os.environ.get("RBC_DIR", ".")); os.chdir(os.environ.get("RBC_DIR", "."))
import contextlib, io
with contextlib.redirect_stdout(io.StringIO()):
    from properties import rays, lins

R = z3.Real


def branches(var):
    raw = [(g.get("ade[e]", Fraction(0)), g.get(var, Fraction(0))) for g in rays]
    raw += [(s * g.get("ade[e]", Fraction(0)), s * g.get(var, Fraction(0))) for g in lins for s in (1, -1)]
    return raw


def prove(name, hyps, concl):
    s = z3.Solver(); s.add(*hyps); s.add(z3.Not(concl)); r = s.check()
    print(f"  [{'PROVED' if r == z3.unsat else 'FAILED ' + str(r)}] {name}")
    return r == z3.unsat


kyx_pairs = {(-3, 0), (-1, 0), (-1, 1), (0, 0)}       # as written in rbc_props_auto.kyx
ok_all = True
for prop, var in [("P1", "urate[e]"), ("P2", "hxan[e]")]:
    raw = branches(var)
    # normalise each pair by a positive scalar (dwell time absorbs scaling)
    def norm(p):
        a, b = p; m = max(abs(a), abs(b))
        return (a / m, b / m) if m else (Fraction(0), Fraction(0))
    distinct = sorted({p for p in raw})
    print(f"{prop} ({var}): {len(raw)} generator branches (50 rays + 2x2 lineality), "
          f"{len(distinct)} distinct (ga,gx) pairs: {[(str(a), str(b)) for a, b in distinct]}")
    print(f"  pairs in .kyx match exact generators: {set((int(a), int(b)) for a, b in distinct) == kyx_pairs and all(a.denominator == b.denominator == 1 for a, b in distinct)}")
    print(f"  every branch has ga + gx <= 0: {all(a + b <= 0 for a, b in raw)}  "
          f"(max ga+gx = {max(a + b for a, b in raw)}); lineality branches have ga = gx = 0: "
          f"{all(a == 0 and b == 0 for a, b in raw[len(rays):])}")
    ade, x, Bio, A0, X0, t = R("ade"), R("x"), R("Bio"), R("A0"), R("X0"), R("t")
    J = z3.And(x - X0 <= A0 - ade, Bio >= 0)
    for ga, gx in distinct:
        ga_, gx_ = z3.RealVal(str(ga)), z3.RealVal(str(gx))
        ade_t, x_t = ade + Bio * ga_ * t, x + Bio * gx_ * t          # solution of the ODE
        ok_all &= prove(f"(a) solve: branch ({ga},{gx}) preserves J for all t>=0",
                        [J, t >= 0], z3.And(x_t - X0 <= A0 - ade_t, Bio >= 0))
        ok_all &= prove(f"(a) dI:    branch ({ga},{gx}) derivative of (A0-ade)-(x-X0) >= 0",
                        [Bio >= 0], -Bio * ga_ - Bio * gx_ >= 0)
    ok_all &= prove("(b) init -> J", [Bio >= 0, ade == A0, x == X0], J)
    ok_all &= prove("(c) J -> post", [J], x - X0 <= A0 - ade)
print("\nall obligations proved:", ok_all, "| z3", z3.get_version_string())
