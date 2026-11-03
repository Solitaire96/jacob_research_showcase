# Jacob Bowers — research showcase

**Two-minute read:** [solitaire96.github.io/jacob_research_showcase](https://solitaire96.github.io/jacob_research_showcase/), one tab per project, with charts.

Selected research work, with code and proofs where they exist. Everything here is mine unless a file says otherwise; nothing here is from my employer.

| Project | What it is | Status |
|---|---|---|
| [`flux-cone-automata/`](flux-cone-automata/) | A hybrid-automata semantics for dynamic flux balance analysis (microbial co-cultures), with machine-checked proofs (KeYmaera X, Z3), convex certificates that scale to genome-scale models, and reproduction code | Thesis from graduate studies (2021) and 2026 results summary live; journal manuscript in preparation |
| [`checking-the-debaters/`](checking-the-debaters/) | Multi-agent LLM debate for venture-deal diligence in which every quoted piece of evidence is checked against the documents in code; measures how agents fabricate evidence and which mechanisms stop it | Code, 124 offline tests and a fictional sample data room live; paper in draft |
| [`drl-decumulation/`](drl-decumulation/) | Deep-RL retirement decumulation: DDPG and TD3 agents choose spending and equity weight; survival-weighted CRRA utility, Gompertz mortality, block-bootstrapped real returns 1955–2026. Scored out of sample against the closed-form (Merton–Yaari) rule, with a reproduced memorisation leak and the fix | Code, data, checkpoints, tests and results live; [two-minute read](https://solitaire96.github.io/jacob_research_showcase/decumulation.html) |

## Provenance

The work is older than this repository, which was assembled in November 2026 from private working folders and published as one commit.

- `flux-cone-automata`: formulation and correspondence theorem developed as a bioinformatics researcher at NREL (summer 2019, part time through 2022), written up as my thesis in graduate studies at Florida Tech (2021) and an *IEEE Systems Journal* submission (major revision, 2021). Revived October 2026: errors in the original construction corrected, main results machine-checked in KeYmaera X, reproduction code and convex certificates added.
- `checking-the-debaters`: built April 2026 (first full runs 23 April), rewritten October 2026; working paper draft October 2026.
- `drl-decumulation`: prototype April 2026; audited, rebuilt and re-scored November 2026 (`PROTOTYPE_AUDIT.md`).

The 2026 code and proof work used AI coding assistants under my direction; each project's results file records what was checked and how.

Contact: via LinkedIn (Jacob Bowers).
