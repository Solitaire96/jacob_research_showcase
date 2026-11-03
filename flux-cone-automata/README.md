# Flux-cone automata

**Two-minute version with charts:** [solitaire96.github.io/jacob_research_showcase](https://solitaire96.github.io/jacob_research_showcase/)

**The idea in three sentences.** Dynamic flux balance analysis (dFBA) simulates a microbial culture by solving a linear program at every time step and assuming the organism picks the growth-maximising fluxes. This work gives dFBA an exact hybrid-automaton semantics: the automaton's locations are the conic generators of the organism's flux cone, dwell time in a location is integrated flux, and the automaton's states at step boundaries are exactly the states dFBA can reach when each step may use *any* feasible flux, not just the greedy one. That turns "what could this co-culture do?" into a question about all strategies at once, answerable with convex optimisation and, for the smaller models, with machine-checked proofs.

**Headline result.** In the Chiu et al. (2014) toy co-culture, greedy dFBA produces 1.47 mM of the emergent metabolite Y; the certified optimum over all strategies is 5.00 mM, a factor of 3.4. The mechanism is that organism 1 forgoes its own growth. The upper bound is an exact stoichiometric certificate and the optimum is attained by a replayed plan (see `RESULTS.md`, exactness ledger).

## What is here

| Folder | Contents |
|---|---|
| `thesis/` | LaTeX source of my thesis from graduate studies at Florida Tech (2021), *A Hybrid Automata Framework for the Representation and Analysis of Microbial Co-Cultures*, and a compiled PDF |
| `RESULTS.md` | Summary of the 2026 results and an **exactness ledger**: for every quotable number, whether it is exact, a sound bound, or exact only for a stated step rule |
| `reproduction/toy-coculture/` | `replicate.py`: rebuilds the Chiu co-culture as dFBA, computes the flux-cone generators, replays every dFBA step through the automaton and checks state equality at run boundaries, then samples non-greedy strategies to show the reachable envelope of Y. numpy + scipy only. `results.json` and the figure are its output. `controlled-automaton/` holds the paper's run: the corrected automaton matches irreversible dFBA at every step for 15 h, to at most 1.2e-14 |
| `reproduction/coculture-reachability/` | Non-myopic reachability and viability for the same co-culture: exact upper bounds, verified replays, greedy comparison (`README.md` there has the tables) |
| `proofs/toy/` | KeYmaera X proofs for the co-culture automaton: one run is one dFBA step and back (P4, P5), trajectory soundness and completeness (P7, P8, P8g), emergence (P3), and the 2021 thesis bound refuted by a proved counterexample (P2b, P2c) |
| `proofs/rbc/` | KeYmaera X models for the red-blood-cell network (iAB-RBC-283): yield and conservation properties over all strategies |
| `proofs/theorem-c/` | KeYmaera X proofs (differential dynamic logic) for the continuous-time greedy-vs-throttled theorem under linear, Michaelis–Menten, Hill and Haldane uptake caps, with negative controls that fail as expected |

## Honest limits

- Nothing here is peer-reviewed. A 2021 version received a revise-and-resubmit from *IEEE Systems Journal*; the revised manuscript and a conference version are in preparation (2026).
- The yield and conservation certificates are classical LP facts, stated here for all strategies. The continuous-time results under Michaelis–Menten, Hill and Haldane uptake caps with cone-decomposed fluxes are, to my knowledge, new; the diauxie result carries novelty risk against Basan et al. (2020) and is flagged as such in the ledger.
- Kinetic parameters in the toy models are partly assumed and are marked as such.
- Exact generators reach the red-blood-cell model (20 after FVA reduction, with cddlib) but not the genome-scale *E. coli* pair, whose results use LP and convex certificates instead.
- The 2026 reproduction and proof work was done with AI coding assistants under my direction; the exactness ledger records what was verified and how.

## Running the replay

```
cd reproduction/toy-coculture
python3 replicate.py      # writes results.json
python3 plot_figure.py    # writes fig_Y_envelope.png
cd controlled-automaton
python3 check.py > check_output.json   # automaton vs. dFBA, every step
```

## Credits

References: [`references.bib`](references.bib) (DOIs checked against Crossref), also listed at the end of the [results page](https://solitaire96.github.io/jacob_research_showcase/).

Thesis advisors: Siddhartha Bhattacharyya and Ambarish Nag. Early work supported in part by NREL's Energy Systems Integration Facility and Florida Institute of Technology. The journal manuscript is joint work with both advisors and is not included here pending submission.
