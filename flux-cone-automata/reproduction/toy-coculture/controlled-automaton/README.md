# Controlled co-culture automaton vs. dFBA (the paper's run)

- `model.py`: the Chiu et al. (2014) toy co-culture. A dFBA reference (one LP per step) and the hybrid-automaton encodings: `simulate_drawn` (the automata as drawn in the 2021 thesis) and `simulate_fixed` (the corrected automata: step-start snapshot bounds, sequential product, greedy dwell in mode order). Simulation is exact and event-driven: flows are constant inside a location, so each guard crossing is found by bisection to machine precision.
- `check.py`: runs dFBA and both automata for 15 h (150 steps of 0.1 h) for each organism alone and the co-culture; prints JSON.
- `check_output.json`: output of `python3 check.py` (numpy, scipy).

Headline numbers (`fixed_*` vs. irreversible dFBA, every step):

| run | max concentration error | max biomass error | final Y (mM) |
|---|---|---|---|
| organism 1 alone | 1.2e-14 | 6.1e-16 | 0 |
| organism 2 alone | 1.8e-15 | 0 | 0 |
| co-culture | 3.6e-15 | 5.0e-16 | 1.4670 |

`../replicate.py` is the older, independent replication with reversible internal reactions and ± generator pairs; its co-culture replay agrees to 6e-10 (concentration) and 3e-8 (biomass).
