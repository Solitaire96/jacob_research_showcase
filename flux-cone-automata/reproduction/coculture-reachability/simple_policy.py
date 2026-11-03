"""Independent check of the co-culture Y = 5 result with a hand-written policy (no optimiser).
Organism 1 never uses the 3A -> 2W growth pathway (c = 0) and runs A + C -> Y at its cap;
organism 2 converts exactly half of the initial A into C (A + B -> C), then stops.
Every step is checked against the model's closing guard. Run from cocult/: python3 simple_policy.py
Folded into this folder unchanged apart from this docstring."""
import numpy as np
from model import *
for mode, irrev in [("takers", False), ("takers", True), ("all", False)]:
    x, B1, B2 = X0.copy(), BIO0, BIO0; P = 0.0; out = {}
    for n in range(150):
        k = caps(x, B1, B2, mode)
        d = min(k[iA], k[iB], max(0.0, (5.0 - P) / (DT * B2)))
        a, c = min(k[iC], k[iA]), 0.0
        assert feasible(x, B1, B2, a, c, d, mode, irrev, tol=1e-12)
        P += DT * B2 * d
        x, B1, B2 = step(x, B1, B2, a, c, d); assert x.min() > -1e-9; x = np.maximum(x, 0)
        if n + 1 in (50, 70, 100, 150): out[(n + 1) / 10] = round(float(x[iY]), 4)
    print(mode, "irrev" if irrev else "rev", out)
