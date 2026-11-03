# What changed from the April 2026 prototype, and why

The first version of this project was a research notebook plus a Streamlit dashboard (April 2026). Rebuilding it for this repository meant checking each claim in that notebook against its own code. This file records what failed the check and what replaced it. The rebuild was done with AI coding assistants under my direction.

| # | Prototype claim or behaviour | What the code actually did | Now |
|---|---|---|---|
| 1 | Objective: minimise the CVaR of terminal wealth | Stated as the objective in the design notes; the trained environment used a flat ruin penalty instead (−500, later −1000), and CVaR was never computed | No separate ruin penalty: the retiree keeps living on guaranteed income, so the cost of running out is priced by the same utility function |
| 2 | "$4,000/month living-expense floor makes ruin possible" | The trained environment's default floor was $100 (notebook) or $500 (repo); the $4,000 figure appeared only in a demo cell | Withdrawals are a share of wealth, with $2,000/month of guaranteed income underneath. Fixed-dollar rules (the 4% rule) can still run out, and are measured for it |
| 3 | "Gompertz–Makeham mortality from SSA anchors" / "IRS 2026 statutory tables" | A linear interpolation of 12 hand-picked anchor points; no Makeham term, no SSA or IRS table | Closed-form Gompertz with Milevsky's m = 88, b = 10, stated as an illustrative law (median age at death 85.7) |
| 4 | Death sampled every month | One coin flip per month from the global RNG, so training reward was high-variance and runs were not reproducible | Survival enters as a weight S(t) on each month's utility: the exact expectation over the death date. Sampled deaths are used only to report depletion and bequests |
| 5 | "24 years of data, 2000–2024, includes the dot-com crash" | AGG starts in Sep 2003, so the joined sample was 2003-10 to 2023-12 (243 months): no dot-com crash, no inflationary decade | 1955–2026 from CRSP (via the French library) and FRED, frozen in the repo |
| 6 | Data loaded at import | `yf.download` and FRED calls ran on every import, so results moved with every run | `data/build_dataset.py` runs once; everything else reads a CSV |
| 7 | Agent sees the macro regime | Observation at month *t* included that month's average Fed funds rate and TIPS yield, then the agent invested for that same month: a small look-ahead | Macro features are lagged one month; a test pins it |
| 8 | "Navigates inflationary shocks" | All amounts nominal, so a fixed-dollar floor eroded silently and utility was of nominal dollars | All returns, consumption and wealth are real (CPI-deflated) |
| 9 | Epstein–Zin utility module | A per-period power utility minus a mean-variance penalty on realised return times wealth. Not Epstein–Zin (EZ is recursive and is not a sum of per-step rewards) | Removed. CRRA only, the one utility the evaluation is built around |
| 10 | "The agent discovers a U-shaped glide path" | Evidence was one deterministic evaluation episode of one 10,000-step training run (≈45 episodes), with nothing to compare against | Five seeds, 5,000 held-out lifetimes, glide path reported as median and P10–P90 per age, compared with tuned rules (see `RESULTS.md`) |
| 11 | Benchmarks | None | 4% rule; best constant mix and best annuitised rule, both grid-searched on training data; 110-minus-age |
| 12 | Train/test separation | Training and evaluation drew from the same 2003–2023 history | Train on 1955–1999, test on 2000–2026, with macro z-scores fitted on train only |
| 13 | Block bootstrap | Blocks wrapped from Dec 2023 back to Oct 2003 | Blocks never wrap |
| 14 | Spending action | Withdrawal rate capped at 0.1–1% per month (notebook) or an absolute $0–30,000 (repo) | Annualised share of wealth, 1–60% on a log scale, so late-life annuity-like spending is reachable and typical rates sit mid-range |
| 15 | Monthly decisions | 480+ decisions per lifetime; with the budget available here DDPG never learned a coherent spending path (first rebuild attempt: held-out CEC ≈ $3,900–4,000 vs $6,100 for the tuned rule) | The agent decides once a year; returns, rebalancing and withdrawals stay monthly inside the year. The objective is unchanged (a test checks the env's reward stream equals the evaluator's objective) |

The prototype's two architectural fixes — normalising the observation and adding exploration noise to DDPG — were correct and are kept.
