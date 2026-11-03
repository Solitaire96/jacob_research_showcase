# Chiu toy co-culture: non-myopic reachability and viability

## Model (`model.py`)
- Reconstructed from replicate.py: S1, S2, EX1, EX2; growth reactions R3 and R6; VMAX 20, KM 0.05, Δt 0.1 h, biomass 0.01 g/L each, A = B = 10 mM, C = W = Y = 0.
- Uptake bound: J ≥ −min(VMAX·x/(KM+x), x/(Btot·Δt)).
- Biomass is frozen within a step and updated exponentially at the step end.
- Greedy dFBA: each organism maximises its own growth, with ties broken by minimum Σ|v|. It reproduces the replicate.py numbers: Y = 1.5459 with reversible internal reactions (Chiu) and 1.4670 with irreversible ones (thesis).
- The exact steady-state spaces are 2-D for organism 1 (b1: A + C → Y; b2*: 3A → 2W) and 1-D for organism 2 (g2: A + B → C). Every strategy is therefore three controls (a, c, d) per step.
- **Assumptions:**
  - Growth reactions are irreversible.
  - Btot is the biomass of the organisms with an exchange for that metabolite (`takers`). The `all` variant, with Btot = B1 + B2 for every metabolite, gives identical results.
  - Main runs use reversible internals (Chiu); the irreversible variant is reported separately.

## Results
**(a) Greedy.**
- Y is first detectable at 0.2 h. A runs out at 3.6 h.
- Y plateaus at 1.546 from 4.1 h, so it is the same at 5, 10 and 15 h.
- B is never exhausted: 8.45 mM remains. W accumulates to 4.6 mM.
- C stays at or below 0.083 mM and then decays to 0.

**(b) Max Y over all strategies.**

| T (h) | greedy | best found (verified dFBA replay) | exact upper bound | status |
|---|---|---|---|---|
| 1 | 0.181 | 0.181 | 0.211 | gap 14% |
| 2 | 0.545 | 0.545 | 0.554 | gap 1.7% |
| 3 | 1.054 | 1.054 | 1.064 | gap 1.0% |
| 4 | 1.546 | 1.807 | 1.824 | gap 1.0% |
| 5 | 1.546 | 2.923 | 2.956 | gap 1.1% |
| 6 | 1.546 | 4.574 | 4.640 | gap 1.4% |
| 7, 8, 10, 15 | 1.546 | 5.000 | 5.000 | **globally optimal** |

- Lower bounds come from IPOPT (local), using 3–6 multistarts that all agree. Each plan is replayed as ordinary dFBA with exact caps, after a projection of at most 1e-9.
- Upper bounds (`upper.py`) are in closed form:
  - C enters the medium only through organism 2, so Y_N ≤ Σ_{k≤N−2} Δt·B2_k·d_k, with d ≤ VMAX·10/10.05 and exponential growth of B2.
  - There is also the stoichiometric bound Y ≤ (A + C + 1.5W)/2 = 5 mM.
- The irreversible and `all` variants give the same best values: 2.923 at 5 h and 5.000 at 10 and 15 h.
- **Mechanism.** After A runs out, Y can keep rising only if organism 1 runs at near-zero growth (c ≈ −a). In the IPOPT plan it recycles W into A and *secretes* A (4.1 mM in total), which organism 2 turns into C, which feeds organism 1's Y pathway.
  - From greedy's own 3.6 h state, this cross-feeding loop reaches Y = 5.0 by 10 h.
  - Organism 1 runs below its per-step growth optimum on 59 of 64 steps, with a shortfall of up to 1.58 /h. A per-step growth maximiser never enters this mode, because its optimum sets the A balance to zero (a + 3c = cap_A = 0).

**(c) Doom of greedy for the requirement Y(T) ≥ θ.**
- *Reversible internals (Chiu), `doom2.py`.* A finite-horizon LP relaxation (`relax.py`, HiGHS) certifies doom, and a verified IPOPT strategy from greedy's state witnesses that θ is still reachable.

  | T | θ | still reachable at | certified doomed from | greedy plateau |
  |---|---|---|---|---|
  | 10 | 4.0 | 3.6 h | 4.2 h | 4.1 h |
  | 10 | 4.9 | 3.6 h | 4.1 h | 4.1 h |
  | 8 | 4.9 | 3.6 h | 3.9 h | 4.1 h |
  | 7 | 4.9 | 3.5 h | 3.8 h | 4.1 h |
  | 6 | 4.5 | 3.3 h | 3.6 h | 4.1 h |

  Doom coincides with A exhaustion, at most about 0.5 h before the plateau. **There is no early warning in Chiu's reversible model.** Greedy's first 3.6 h are recoverable; the failure is what greedy does once A is gone.
- *Irreversible internals (thesis), `doom_irrev.py`.* W can never become A again. The exact, horizon-free yield bound Y + max_P min(C + P, A − P) is evaluated in rational arithmetic on the greedy states. It certifies doom while greedy's Y is still rising:

  | θ | reachable from t = 0 by | greedy doomed from | greedy Y then | plateau visible at |
  |---|---|---|---|---|
  | 2.0 | 5 h | 3.5 h | 1.39 | 3.6 h |
  | 3.0 | 10 h | 3.1 h | 1.12 | 3.6 h |
  | 4.0 | 10 h | 2.5 h | 0.78 | 3.6 h |
  | 4.5 | 10 h | 2.0 h | 0.55 | 3.6 h |

  For θ = 4.5, doom is certified 1.6 h before the plateau, when greedy has made only about a third of its final Y. The cause is that greedy diverts A into W through the b2* growth pathway, permanently starving organism 2.

**(d)** Greedy already produces Y (emergence), so non-greedy strategies aren't needed for Y to appear at all. They are needed for the amount: 1.546 versus 5.000 by 7 h, about 3.2× (reversible internals; 3.4× with the irreversible convention used on the results page). Greedy is optimal, or within 1–2% of the bound, only for T ≤ 3 h.

## What is certified and what is only numerical
- **Exact:**
  - the 5 mM yield bound;
  - global optimality at T ≥ 7 h (verified witness = 5.000 = bound);
  - the irreversible-variant doom times (rational arithmetic on the greedy states).
- **Closed-form bound with mpmath:** the finite-horizon upper bounds in `upper.py`.
- **Floating-point LP (HiGHS):** the doom certificates for the reversible variant.
- **Local only (IPOPT):** best values at T ≤ 6 h. They are bracketed within 1–14% by the exact upper bound, so they may not be globally optimal.
- **Caveat:** greedy zeroes concentrations below 1e-12 (replicate.py convention). In the reversible model a tiny C can bootstrap recovery, so doom times that are exactly at the plateau partly depend on this.

## Files
- `model.py`: model, greedy, and the reversible yield bound.
- `reach.py`: IPOPT multistart and the verified replay. Run as `python3 reach.py takers [irrev]` or `python3 reach.py all`.
- `curve.py`: horizon table.
- `band.py`: reachable-set envelope of Y over 0–8 h for the results page (irreversible internals, the thesis convention): floor 0 (zero strategy, asserted feasible), exact ceiling from `upper.py`, verified top edge (greedy and `simple_policy.py`), and min/max of the 200 sampled strategies from `../toy-coculture/env_Y.npy` (written by `replicate.py`). Output `band.json`.
- `upper.py`: closed-form upper bound.
- `relax.py`: finite-horizon LP relaxation.
- `doom.py`: horizon-free reversible bound; it never certifies doom.
- `doom2.py`: reversible doom brackets.
- `doom_irrev.py`: exact irreversible doom.
- `*.json`, `simple_policy_output.txt`: outputs.
- Requirements: casadi 3.8.1 (bundles IPOPT), cvxpy 1.9.3 with HiGHS, mpmath, numpy, scipy.

## Independent check: hand-written policy (`simple_policy.py`)
Run here unchanged. It uses no optimiser:
- Organism 1 never uses 3A → 2W (c = 0) and runs A + C → Y at its cap.
- Organism 2 converts exactly half of the initial A into C, then stops.
- Every step is checked against the closing guard.

| variant | Y at 5 h | 7 h | 10 h | 15 h |
|---|---|---|---|---|
| takers, reversible | 2.9147 | 5.0 | 5.0 | 5.0 |
| takers, irreversible | 2.9147 | 5.0 | 5.0 | 5.0 |
| all, reversible | 2.9147 | 5.0 | 5.0 | 5.0 |

This confirms the certified optimum (5 mM by 7 h) in every variant, the irreversible one included. The mechanism is that organism 1 forgoes its growth route so that A reaches organism 2. W → A recycling is not needed. At 5 h this simple policy reaches 2.915, against 2.923 from the IPOPT plan.
