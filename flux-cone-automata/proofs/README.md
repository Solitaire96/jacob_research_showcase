# Proofs

`.kyx` files are KeYmaera X models (differential dynamic logic). They were proved with KeYmaera X 5.1.2 using Z3 as the arithmetic backend; proof times are seconds to about two minutes.

- `rbc/`: properties of the projected red-blood-cell automaton over all strategies (e.g. P1: urate secreted ≤ adenine consumed). `kyx_obligations.py` generates the obligations from the model's generator branches and needs the RBC working folder (not included) to run.
- `toy/`: the Chiu co-culture automaton (the model in `../reproduction/toy-coculture/controlled-automaton/model.py`). 20 entries; 19 prove and one fails as intended:
  - P1, P1x: organism 1 alone never produces Y, over all strategies (P1x with the exact exponential biomass update).
  - P2, P2x: the corrected co-culture bounds never overdraw a shared substrate. P2b, the bound as drawn in the 2021 thesis, **fails as expected**, and P2c proves the counterexample (a negative concentration).
  - P3, P3L: the co-culture can raise Y above the detection threshold within two dFBA steps (emergence); P3L justifies the interval used for exp(ct).
  - P4, P4a, P4b: one run of the automaton is one dFBA step (soundness, exact exponential update). P5, P5a, P5b, P5r: every feasible flux is realised by one run, and the successor stays admissible (completeness).
  - P6a, P6b: the forced chain of organism 1 and the controlled automaton of organism 2 select the unique growth-optimal flux.
  - P7: every run sequence is a dFBA trajectory. P8: every dFBA trajectory is realised by a run (endpoint form). P8g: the same as a hybrid game in differential game logic, step by step.
  - Run logs from 26 Oct 2026: `proofs.log` (P1–P7, one line per entry with tactic, time and step count; files without a `Tactic` block close under KeYmaera X's automatic tactic), `proofs_P8.log` and `proofs_P8_rerun.log` (P8, P8g), and `P8_negative_controls.log` (seven mutated, false versions of P8/P8g, none of which prove). `run_all.sh` re-runs everything given `keymaerax.jar` 5.1.2 and Java 17+.
- `theorem-c/`: the continuous-time greedy-vs-throttled bound. `gen_*` are the generic-cap versions (the cap is an uninterpreted monotone function), `cap_*` are concrete caps (linear, Michaelis–Menten, Hill, Haldane), `ex_*` are existence lemmas, `*_nomono` and `neg_*` are negative controls that are expected to fail or to prove a counter-strategy.
