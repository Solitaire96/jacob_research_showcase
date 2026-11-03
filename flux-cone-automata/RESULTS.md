# Flux-cone automata: results summary and exactness ledger

Summary of the 2026 computational results behind the flux-cone automata revision. Nothing here has been peer-reviewed; every number carries its exactness status in the ledger below.

Section numbers (§1, §5b, …) refer to the full results bundle in my working folder, which is not included here. This repo contains the toy co-culture reproduction (`reproduction/`) and the KeYmaera X proofs (`proofs/`); every other number is quoted from that bundle.

## 0. Bottom line (2026-10-26)
- **The theorem holds on real networks.** The hybrid automaton reproduces dFBA trajectories exactly when its locations are conic generators of the flux cone. Shown on RBC (§1) and, with enzyme capacity, on a deFBA-type model (§5h).
- **Use elementary flux modes (EFMs) as locations.** Conformal decomposition then keeps external concentrations ≥ 0 *inside* every step, for any location order (§5e); for shared media this needs the depletion cap. With internal metabolites in the state (uFBA), use sub-passes; that lemma is proved for general n (§5k).
- **Variance-ranked eigenpathway truncation is unsound for reachability, on two models** (RBC §1; e_coli_core §5g). Its sound replacement is column-generation pricing, which certifies a truncation exact for a given property (§5i; classical method, new use).
- **Computation doesn't need generators.** Certificates are LPs (genome-scale iJO1366 yield and elemental theorems in under 1 s, §5f). All-strategy reach and viability are convex programs, or LPs when kinetics are saturated or enzyme-linear (§5b, §5h, §5l). The automaton is the *semantics*; the computation is convex optimisation.
- **Automaton-only results** (continuous-time switching, all strategies, symbolic parameters):
  - Regulatory toy: τ\* = T − γ/cmax − θ/kY, tight; upper bound proved in KeYmaera, witness proved in Z3 (§5d).
  - First biological version: glucose→acetate diauxie in e_coli_core with growth and MM uptake; parametric bound τ\* ∈ [5.15, 5.63] h; proved in KeYmaera. **The biological framing failed independent review**: the lag used doesn't match the model's growth rate, and with default maintenance the conclusion flips (§5m). Present it as a model-class theorem.
  - **Diauxie v3 (§5p, §5y):** with Basan's growth–lag law and maintenance, greedy is certified ≥ 0.86 h slower (median 2.1 h) to any acetate-phase biomass target than a throttled strategy, on all 134 parameter corners consistent with the measured lag. The theorem is in continuous time, with a rigorous witness on every set; at parameters fitted to Basan's data the delay is 1.73–1.77 h (§5y).
- **Theory (2026-10-26).**
  - **When greedy dFBA is optimal (§5q):** dominance-order conditions (A)–(C), which are sufficient. The Bellman converse only says that *some* order exists, not that these premises are necessary. For one organism on one substrate with no maintenance and max-growth = max-yield, greedy is optimal at every horizon (confirmed on e_coli_core). The one-pathway, continuous-time case is already in Waldherr et al. (deFBA, App. C); ours extends it to network dFBA. Each of our counterexamples (RBC doom, co-culture, diauxie, overflow, maintenance, tie-breaks) breaks one named condition.
  - **Diauxie (§5r):** in a reduced model the question has an exact iff test, M²X/X_ind > z\*²/c; Z3-proved over a parameter box containing every review corner. It proves a sign, not a size; there is novelty risk against Basan.
  - **Soundness (§5s):** the box-formula KeYmaera results transfer to dFBA by trajectory equivalence. The diamond and game proofs (P5–P8g) prove the equivalence theorem's completeness direction itself, so they need no transfer. Bisimulation is a framing upgrade.
- **Strongest empirical results:**
  - **Co-culture:** greedy dFBA reaches Y = 1.467 mM, while the certified optimum is 5.00 mM (T ≥ 7 h), a factor of 3.4. This is the paper's headline: irreversible internals, Δt = 0.1 h. With reversible internals (Chiu) greedy reaches 1.546 mM. The tables in §4 and §5o use the reversible variant. The 5.00 optimum holds in both variants. The mechanism is that organism 1 forgoes growth (§4).
  - **Genome-scale consortium** (Zhang & Reed lysine/leucine auxotrophs on iJO1366): per-step selfish dFBA is degenerate (0 to ≈ 0.53 h⁻¹ depending on tie-break), while the all-strategy envelope is an LP solved globally at genome scale; a fitted release cap of ≈ 0.022–0.031 reproduces the measured 0.056 h⁻¹ (§5l).
  - **RBC viability:** myopic dFBA is doomed hours before failure is visible, exactly characterised by a closed form (a 1 mM glucose band for the max-urate policy; broad for the max-ATP policy) (§5j).
- **Honest limits.**
  - The yield and conservation certificates are classical LP facts, stated here for all strategies. The continuous-time results under Michaelis–Menten, Hill and Haldane uptake caps with cone-decomposed fluxes are, to my knowledge, new; the diauxie result carries novelty risk against Basan et al. (2020) and is flagged as such in the ledger.
  - Kinetic parameters are partly assumed.
  - Exact generators don't scale beyond ~7 kept exchanges (§5).
- **Prior art to cite:**
  - dynamic-optimisation dFBA (Mahadevan, Edwards & Doyle 2002) and deFBA/r-deFBA;
  - production envelopes, OptKnock, SteadyCom and community FBA;
  - ecmtool (Clement et al. 2021) and Price/Barrett (eigenpathways);
  - Dantzig–Wolfe (pricing), Rockafellar (conformal decomposition), uFBA (Bordbar 2017), Zhang & Reed 2014, Basan et al. 2020.

## 0b. Exactness ledger (2026-10-26)
No result computes the full reachable set. Each computes one property over all strategies: a maximum, a minimum, or feasibility. For each quotable number, this table says whether it is exact, a sound bound, or exact for a different step rule.

**Rule for the paper:** say "optimum" only where a certified bound equals a replayed witness. Otherwise say "upper bound", "bracket" or "certified infeasible". Always state Δt and the biomass update rule.

| Result | Status | Why | § |
|---|---|---|---|
| Yield and conservation certificates (RBC P1–P4, urate theorem, iJO1366 yields) | **Exact** | One LP over the cone with no dynamics; the dual prices are the certificate (iJO1366 caveats in §5f) | 1, 2, 5f |
| RBC all-strategy viability (0.9694 / 0.9250) | **Exact** (convex) | Constant biomass makes the dynamics linear; a Michaelis–Menten cap that bounds uptake from above is concave, so the feasible set is convex. Clarabel solves the actual problem; the tangent-cut LP is a relaxation that happens to match. Values are at Δt = 0.25 h (0.9444 / 0.875 in continuous time, §5u) | 5b |
| RBC doom closed form (272 points, 86 doom times) | **Exact** (theorem) | Exact LP facts plus Z3 lemmas; the induction is assembled by hand | 5j, 5w |
| RBC doom certificates (219 states) | **Exact conclusion via a sound relaxation** | The outer tangent relaxation is exactly infeasible (rational Farkas certificate), so the exact model is infeasible too. Viability one step earlier comes from the closed form. Policy trajectories are floating point (tolerance 1e-6 covered) | 5w |
| Co-culture 5.00 mM optimum (T ≥ 7 h) | **Exact optimum** | Exact stoichiometric upper bound, attained by a replayed plan, in every dFBA scheme and Δt | 4, 5u |
| Co-culture greedy 1.467 mM (irreversible) / 1.546 (reversible) | **Exact for its scheme** | Deterministic replay at Δt = 0.1 h; moves 1–4% as Δt → 0 | 4, 5u |
| Co-culture T = 2–6 h | **Bracket** (0.2–0.8%) | SCIP on the exact nonconvex model; the dual bound is valid to 1e-6 tolerance (not exact arithmetic); the lower bound is a replayed plan | 5n |
| Diauxie certified delay (≥ 0.86 h) | **Sound bound minus a rigorous witness, continuous time** | The greedy side is an envelope-chain lower bound (loose; its LP constants are floating point). The witness has exact fluxes with interval-propagated state. **Not valid in Euler dFBA**, where the z-link fails | 5p |
| Diauxie suboptimality sign (perturb, vyield) | **Theorem** (first order) | Z3 over a parameter box; some calculus steps are by hand | 5r |
| Regulatory-mode τ\* | **Exact** (theorem, tight) | KeYmaera upper bound plus tightness; natively continuous time | 5d |
| Genome-scale consortium envelope (Zhang & Reed) | **Exact LP, but for a different step rule** | Absolute fluxes w = B·v with an **Euler biomass update** (B + Δt·w_growth), not the exponential update the equivalence theorem uses. The effective maximum rate depends on Δt (0.55 at 1 h, up to 0.91 at 0.125 h, approaching 0.98). Quote it with the step rule | 5l |
| r-deFBA self-replicator answers | **Exact on their time grid** | MILPs plus full enumeration of the 1,469 feasible schedules; uses a parameter repair (εT2 = 0.001); not continuous time | 5v |
| Greedy optimality under Michaelis–Menten caps (e_coli_core check) | **Global optimum to solver tolerance** | SCIP, tolerance about 1e-7; only the step sizes tested | 5q |
| Lázaro 2022 chemostat optima (Table 2, heatmap, best PS over D) | **Exact upper bound plus floating-point primal** (rational dual certificate; reviewed); PS\* over D certified, its location not | Absolute rates make the steady state an exact LP in (w, X) | 5x |
| iJO1366 horizon problem (scale demo) | **Relaxation only** | Tangent-cut LP; Clarabel returned `optimal_inaccurate`, so this shows scale, not a certificate | 5b |

