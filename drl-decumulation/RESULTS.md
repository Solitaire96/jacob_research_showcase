# Results and how far to trust them

All numbers come from `python3 evaluate.py` on the shipped checkpoints, with fixed seeds; `results/results.json` holds every one of them and `results/results_tables.md` has the full tables. Nothing here is peer-reviewed.

**Score.** Certainty-equivalent consumption (CEC): the constant real monthly spending that a γ = 4 CRRA retiree values the same as the policy's uncertain, survival-weighted consumption stream. Higher is better. Differences between two policies are paired (same 5,000 lifetimes, same death dates) with a 95% bootstrap interval over lifetimes.

**Lifetimes.** 12-month block bootstraps of real monthly returns. *In-sample:* blocks from 1955-07 to 1999-12 (the training window; fresh draws, not the training paths). *Held out:* blocks from 2000-01 to 2026-09, never seen in training or rule tuning. *Held out, 60-month blocks:* the same window with 5-year blocks, a robustness check (§3).

## 0. Bottom line

- **The agent finds the closed-form policy's shape, not its level.** Never shown the rule, it spends a rising share of wealth with age, as the Merton–Yaari annuity rule does, but more conservatively: at 90, 12–14% of wealth a year against the rule's 20%; at 100, 30–35% against 45%. From 65 to 90 it holds 75–91% equity against the rule's 100%. Both stay high in equities because $2,000/month of guaranteed income works like a bond holding.
- **Its score is within about 1–3% of that rule's, and it does not robustly beat it.** Five seeds each, CEC minus the tuned annuitized rule: DDPG −$63 in-sample, **+$49 held out**, −$70 on 60-month blocks; TD3 −$188, **+$30**, −$199.
- **It clearly beats the rules people use.** Held out, DDPG's mean CEC is $6,129 a month against $5,077 for the 4% rule (60/40) and $5,810 for the best constant-percentage rule.
- **An earlier version looked like a win and was a leak** (§2). With macro features lagged along source history, the agent memorised which block of history came next: far ahead of the rule in-sample, behind it held out.
- **The prototype's "U-shaped glide path" did not reproduce.** The agents hold 75–91% equity from 65 to 90 (§4).

## 1. Main comparison, held out (2000–2026, 5,000 lifetimes)

| Policy | CEC $/mo | vs best annuitized | P(run out before death) | Spend at 85, P10 | Median bequest |
|---|---:|---:|---:|---:|---:|
| 4% rule, 60/40 | 5,077 | -1,003 | 3.9% | 5,333 | 1,080,755 |
| Best constant mix (100% eq, 7.5%/yr) | 5,810 | -270 | 0.0% | 3,631 | 742,874 |
| 110-minus-age, annuitized | 5,446 | -634 | 0.5% | 4,324 | 237,235 |
| Best annuitized (100% eq, r=1%) | 6,080 |  | 0.5% | 4,357 | 461,496 |
| **DDPG agent**, mean of 5 seeds (range 6,066–6,177) | **6,129** | +49 (4 of 5 seeds significantly above, 1 below; 95% paired CIs) | 0.0% | 4,496 | 590,479 |
| **TD3 agent**, mean of 5 seeds (range 6,029–6,154) | **6,111** | +30 (4 of 5 seeds significantly above, 1 below; 95% paired CIs) | 0.0% | 4,733 | 659,729 |

Full tables, including in-sample and every seed: `results/results_tables.md`.

## 2. The leak

At each annual decision, the macro-feature agent saw last month's Fed funds rate, term spread and trailing inflation. Blocks of real history are fixed sequences, and that triplet is close to unique per calendar month, so lagging it along the *source* history told the network which 12 months of history it was about to live through. Two DDPG seeds trained that way (`train.py --leaky`), on the same lifetimes as everything else, scored against the tuned annuitized rule:

| Agent | In-sample (1955–99) | Held out (2000–26) |
|---|---:|---:|
| DDPG, macro lagged along source history (the leak) | +969 (+1,058, +879) | -227 (+17, -471) |
| DDPG, wealth and age only (shipped) | -63 (-29, -142, -107, -27, -12) | +49 (-14, +40, +97, +31, +91) |
| DDPG + macro, lagged along the path (fixed) | -147 (-100, -195) | -95 (-87, -103) |
| TD3 + macro, lagged along the path (fixed) | -237 (-238, -237) | -44 (+36, -123) |

Each cell: mean of seeds (each seed), CEC minus the tuned annuitized rule, real $/month.

A large in-sample gain that turns negative out of sample is the signature of memorisation, not skill. The fix lags macro state along the *simulated* path, so at a block boundary the agent sees the previous, independent block (test: `test_macro_is_lagged_along_the_path_not_the_source`). With the fix, macro features add nothing at annual decisions; the "+ macro" ablation agents trail the wealth-and-age agents (above). The default agent therefore observes only wealth and age.

The same exposure existed in the April prototype, which observed macro state every month while stepping through 12-month blocks; its notebook's claim that block bootstrapping "absolutely cannot be explicitly memorized" holds across blocks, not within one.

## 3. Robustness: 60-month blocks

Twelve-month blocks keep the cross-section of each year intact but cut multi-year regimes. Fed funds jumps a median 3.1 points at a block boundary against a typical monthly move of 0.1, and high-inflation spells, which historically last 21 months on average and up to 66, are cut into fragments. Rescoring the held-out window with 60-month blocks (agents unchanged, trained on 12):

| Agent | Held out (2000–26) | Held out, 60-month blocks |
|---|---:|---:|
| DDPG | +49 (-14, +40, +97, +31, +91) | -70 (+0, -142, -114, -112, +17) |
| TD3 | +30 (-51, +51, +74, +34, +44) | -199 (-154, -268, -233, -89, -254) |

Each cell: mean of seeds (each seed), CEC minus the tuned annuitized rule on the same lifetimes.

The agents' small held-out edge does not survive: with regimes intact, the tuned annuitized rule is ahead. Agents that observe macro state are excluded from this set, because with 5-year blocks their annual decisions fall mid-block, where macro state identifies the month (the §2 leak).

## 4. What the agents learned

Held-out medians by age, mean over seeds (figure: `results/fig_policy.png`):

| Age | DDPG equity | TD3 equity | Rule equity | DDPG spend | TD3 spend | Rule spend |
|---:|---:|---:|---:|---:|---:|---:|
| 65 | 89% | 88% | 100% | 6.6% | 5.7% | 5.6% |
| 70 | 89% | 81% | 100% | 6.7% | 6.0% | 6.8% |
| 75 | 90% | 82% | 100% | 7.3% | 6.9% | 8.4% |
| 80 | 79% | 80% | 100% | 8.5% | 8.4% | 10.8% |
| 85 | 88% | 77% | 100% | 11.0% | 10.2% | 14.4% |
| 90 | 86% | 75% | 100% | 14.0% | 12.1% | 19.9% |
| 95 | 87% | 73% | 100% | 18.9% | 17.2% | 28.7% |

Equity is the weight on stocks; spend is the withdrawal rate, % of wealth per year.

Spending rises with age for both, as the annuity rule does, but more slowly after 80: the agents keep more for very late life than the rule. Equity is high throughout; TD3 de-risks gently with age, DDPG stays flat apart from a dip around 80. There is no U-shape.

Inference, not tested: the late-life gap may be under-training rather than preference. Rewards after 85 carry survival weights below one half, and after 95 below a tenth, so the critic gets little signal there; a longer run or a survival-conditional reward would tell.

## 5. Historical cohorts (actual sequences, no bootstrap)

Median seed of each algorithm, not the best. Real dollars.

| Retired at 65 in | Policy | Wealth at end | Lowest monthly spend | Mean monthly spend |
|---|---|---:|---:|---:|
| 2000-01 (to 2026-09) | 4% rule, 60/40 | $616,402 | $5,333 | $5,333 |
| 2000-01 (to 2026-09) | Best annuitized | $216,176 | $3,790 | $5,642 |
| 2000-01 (to 2026-09) | DDPG (seed1) | $233,348 | $4,100 | $5,601 |
| 2000-01 (to 2026-09) | TD3 (seed4) | $223,368 | $4,018 | $5,255 |
| 2007-01 (to 2026-09) | 4% rule, 60/40 | $1,255,882 | $5,333 | $5,333 |
| 2007-01 (to 2026-09) | Best annuitized | $816,432 | $4,344 | $8,160 |
| 2007-01 (to 2026-09) | DDPG (seed1) | $888,571 | $5,116 | $7,679 |
| 2007-01 (to 2026-09) | TD3 (seed4) | $819,581 | $3,789 | $6,847 |

The 4% rule ends richest in both cohorts because it spends least: a flat $5,333 against adaptive rules that spend more on average and less after crashes. That is the tradeoff CEC prices.

## 6. Ledger

| Claim | Status | Why |
|---|---|---|
| Agents vs annuitized rule, held out | **Measured**, ±$5–21 per seed (paired 95% CI; up to ±$29 on 60-month blocks) | 5,000 lifetimes; but one historical window, and the sign flips on 60-month blocks (§3) |
| Agents beat the 4% rule | **Robust** | Every set, every seed: by $952 to $1,701/month |
| Agents beat the best constant-mix rule | **Not in every set** | Margin per seed and set ranges -25 to +367/month |
| Leak mechanism (§2) | **Reproduced** | Two seeds with the bug on purpose; same lifetimes as the main agents |
| Policy shape (§4) | **Descriptive** | Medians over held-out lifetimes; seeds differ in detail |
| Cohort replays (§5) | **One path each** | Illustrative, not statistical |
| Tuned rules prefer 100% equity | **Window-specific** | 1955–1999 had a 5.6-point real equity premium over the 10-year bond; 2000–2026 had 4.6 |
| Mortality, utility, γ = 4, $2,000 income | **Assumptions** | Changing them changes the numbers; the evaluation has no sensitivity sweep yet |

## Reproduce

```
python3 data/build_dataset.py   # optional: rebuilds data/market_monthly.csv from data/raw/
./run_all.sh                    # ≈ 75 min, every checkpoint
python3 evaluate.py             # ≈ 8 min, writes results/
python3 -m pytest -q tests
```
