# Checking the debaters: results summary and status ledger

Results from the working paper draft (October 2026). Nothing here is peer-reviewed. The venture runs used real data rooms that are confidential, so deals appear only as **D1–D7** and only as aggregate counts: no quotes, figures, sectors or names from those documents. D1–D4 are the main deals; D5–D7 were not used while the prompts were developed.

## 0. Bottom line

- **The failure is real and specific.** When agents debated from memory of the documents, they invented the evidence the prompts demanded, and the Risk Agent, which holds the veto and could not see the documents, accepted it. In 15 early runs (original design), **all 9 INVEST verdicts rested on evidence that does not occur anywhere in the deal's documents**: documents, contracts, third-party verifications and executives that do not exist. The evidence changed from run to run on identical documents, so it was generated, not retrieved (§1).
- **Grounding every turn and checking every quote removes it.** Across 20 later runs (8 full-system runs on D1–D4; 6 full-system and 6 original-design runs on D5–D7) and two model families, none of the previously invented entities reappeared (§2). On the held-out deals, heuristic-flagged fabrications on `gpt-oss-120b` fell from 9.7% of quotes to 0.1%, confounded by a code change between the two conditions (§3).
- **A quote checker has to tolerate PDF noise or it is useless.** Over 8,406 logged quotes, raw exact matching finds 31.5% of them in the documents and whitespace-normalised exact matching 59.4%; the tolerant checker finds 88.4% while still rejecting changed figures, swapped names and dropped negations (§4).
- **Null result on the extra machinery.** In a 252-ruling replay, either document sight *or* displayed citation checks stops a judge accepting unverified resolutions. Once the judge can see the documents, neither displayed checks nor the code-enforced veto gate measurably adds anything: the gate fired on 1 of 7 debates and changed 0 of 252 verdicts (§5).
- **What verification does not do.** It establishes that quoted words exist in the documents, not that they support the claim they are attached to. In one full-system run the Risk Agent passed the gate by attaching a real quote that answered a different objection; in the replay the gate passed an objection backed by one verified and one unverified quote (§5).
- **No outcome data.** Nothing here says whether the system picks good investments.

## 0b. Status ledger

**Rule:** "measured" means counted from stored runs by code. Everything else says what it is.

| Result | Status | Why | § |
|---|---|---|---|
| 9/9 early INVEST verdicts rested on absent evidence | **Measured** (case-insensitive search of every document, including files those runs never parsed) | 15 runs, one model family, original code | 1 |
| No reappearance of invented entities in 20 full-system runs | **Measured**, for the entities in §1 only | Absence of *those* entities, not of all fabrication | 2 |
| Held-out fabrication 9.7% → 0.1% (`gpt-oss-120b`) | **Heuristic and confounded** | Rule-based classifier, not human labels; conditions A and E ran on different commits | 3 |
| Checker regime comparison (31.5% / 59.4% / 88.4%) | **Measured** | Stored quotes re-scored offline, zero API calls | 4 |
| Decimal-shift fix | **Measured for this folder** (not in the paper draft) | Re-scoring all stored quotes with the fixed checker, 2026-11-03, rejects exactly 1 more quote (7,428 → 7,427) | 4 |
| Replay: sight or displayed checks prevents blind acceptance | **Measured, small n** | 7 saved debates; repeats within a debate are not independent | 5 |
| Gate changed 0/252 verdicts | **Measured** | Single frozen commit | 5 |
| QuALITY accuracy differences | **No detectable difference** | n = 10 items per arm, one run per cell | 6 |
| Cross-model and ablation contrasts | **Single runs** | Directional only | 2 |

## 1. The failure mode

In the original design, documents were in the prompt only in Phase 1. Openings, cross-examination, the risk challenge, the Risk ruling and the votes saw only the transcript, while every prompt said "quote specific document evidence". The Quant Agent saw only the first 8,000 characters of the documents, and `.docx` files were never parsed.

Across 15 runs on four deals and a test deal, 9 verdicts were INVEST. In every one of them the Risk Agent marked objections RESOLVED on evidence that appears 0 times in the deal's documents, searched case-insensitively across every file, including files those runs never parsed. On one deal the cited evidence changed between runs on identical documents.

## 2. Full-system runs (`medium` reasoning effort)

| | D1 gpt-oss | D1 qwen | D2 gpt-oss | D2 qwen | D3 gpt-oss | D3 qwen | D4 gpt-oss | D4 qwen |
|---|---|---|---|---|---|---|---|---|
| Verdict | PASS | INVEST | VETOED | PASS | VETOED | VETOED | VETOED | VETOED |
| Risk ruling (resolved/partial/unresolved) | 0/3/0 | 0/3/0 | 0/3/0 | 0/3/0 | 2/1/0 | 0/2/1 | 1/2/0 | 0/2/1 |
| Gate intervention | re-ask (1) | none | none | none | re-ask (2) | none | none | none |
| Quotes verified | 187/255 (73.3%) | 370/402 (92.0%) | 238/241 (98.8%) | 385/386 (99.7%) | 202/239 (84.5%) | 325/359 (90.5%) | 225/234 (96.2%) | 435/436 (99.8%) |
| Ledger facts / rejected / conflicts | 10/12/1 | 13/2/2 | 5/1/0 | 3/0/0 | 0/5/0 | 20/1/2 | 7/0/1 | 6/0/1 |
| Claims (grounded) | 61 (42) | 57 (53) | 60 (51) | 69 (67) | 57 (45) | 52 (48) | 58 (50) | 67 (67) |
| Mean panel score (1–10) | 5.95 | 5.77 | 5.82 | 5.48 | 5.55 | 3.90 | 6.58 | 5.55 |

- **Models:** `qwen-3.8-27b` verified more quotes on every deal (1,515/1,583, 95.7%, vs 852/969, 87.9%) and scored deals lower on average (5.18 vs 5.98).
- **The gate is not a blanket block:** in three of the eight runs the Risk Agent answered every objection with verified quotes and the deal reached the panel vote; one cleared it.
- **Single-run ablations** (`gpt-oss-120b`): `low` reasoning effort cut verified quotes from 84.5% to 67.4% (D3); a 120k-character document budget truncated a key document and flipped D1 from a panel vote to a veto; disabling local compression on D4 cut verified quotes from 96.2% to 91.7% and doubled run time.

## 3. Held-out deals (D5–D7, conditions A and E)

Condition A is the original design (documents in Phase 1 only); E is the full system. All `NOT FOUND` quotes were bucketed by a rule-based classifier (close paraphrase, multi-span synthesis, quoting another agent, or **ungrounded fabrication**: a proper noun or number absent from the documents).

| Model | Condition A fabrications | Condition E fabrications |
|---|---|---|
| `gpt-oss-120b` | 56/580 quotes (9.7%) | 1/694 (0.1%) |
| `qwen-3.8-27b` | 1/986 (0.1%) | 5/1,437 (0.3%) |

A and E ran under different commits, so the drop is not attributable to the condition alone. `qwen-3.8-27b` barely fabricated even without the mechanisms.

## 4. The citation checker

Every `«quote»` is checked in code against the parsed documents and annotated before any other agent or the judge reads it.

| Regime (27 runs, 8,406 quotes) | Found in documents |
|---|---|
| Raw exact substring | 2,647 (31.5%) |
| Whitespace-normalised exact | 4,994 (59.4%) |
| Tolerant checker, as used in the stored runs | 7,606 (90.5%) |
| Tolerant checker, tightened post hoc (interior deletions limited to filler words, negative-prefix swaps rejected, page-number lines stripped) | 7,428 (88.4%) |
| Tightened, plus the decimal-point fix in this folder | 7,427 (88.4%) |
| QuALITY stories, tightened checker (60 runs) | 1,000/1,095 (91.3%) |

Accepted as noise: watermark characters interleaved in words, numbers split by a watermark line, hyphenation across lines, currency and unit spacing, filler words. Rejected: any changed figure (including a moved decimal point), a word that is not character-similar, a capitalised name not spelled exactly that way somewhere in the documents, a dropped negation or qualifier, an added or removed negative prefix. The regression suite pins each case.

**Bug found while porting.** The rule that re-joins numbers split by a watermark compared digits only, so a quote saying "$24M" matched a document saying "$2.4M". Fixed here (decimal points must match); it affected 1 of 8,406 stored quotes.

## 5. Phase-4 replay: sight vs displayed checks vs code gate

One saved debate per deal (7), the panel's answers held fixed, and only the Risk ruling replayed: judge **blind** (no documents in Phase 4) or **sighted**, crossed with verification **none**, **shown** (citation checks appended to the panel's answers) or **enforced** (shown, plus the code gate). Two models, three repeats, one frozen commit: 252 rulings, 42 per arm.

| Judge | Verification | Debates with an unverified resolution (runs) | Gate fired (debates) | INVEST |
|---|---|---|---|---|
| Blind | none | 3/7 (10/42) | – | 13/42 |
| Blind | shown | 0/7 (0/42) | – | 10/42 |
| Blind | enforced | 0/7 (0/42) | 0/7 | 9/42 |
| Sighted | none | 1/7 (1/42) | – | 15/42 |
| Sighted | shown | 4/7 (4/42) | – | 12/42 |
| Sighted | enforced | 0/7 after the gate (1/7 before) | 1/7 | 13/42 |

- **Blind and unchecked**, the judge copied a panelist's fabricated quotes into its ruling and voted INVEST in 3 of 6 repeats on one deal. Showing the checks flipped all 6 to PASS.
- **Sighted judges quote the documents directly** (4.5–5.4 verified quotes per ruling vs 3.4) and so produce occasional unverified quotes of their own, which is why "sighted, shown" is worse than "sighted, none".
- **The gate's two interventions**, both on one debate: it downgraded a real sentence with one pronoun replaced by the company name (a false positive), and passed an objection that paired one verified quote with a second unverified one whose number changed between repeats (the gate requires *at least one* verified quote). It changed no verdict.

## 6. QuALITY HARD pilot (public data)

Same protocol on the public QuALITY reading-comprehension benchmark (Pang et al., 2022), used by Khan et al. (2024): two debaters argue options A and B over two rounds, a story-blind referee audits round 1, a judge picks an answer with a supporting quote. 10 HARD questions per arm, one run per cell, 60 runs on `gpt-oss-120b`, 50% chance.

| Judge | Verification | Correct | Unverified rulings | Quotes (tolerant / exact) |
|---|---|---|---|---|
| Blind | none | 6/10 | 2/10 | 90.2% / 58.6% |
| Blind | shown | 5/10 | 0/10 | 91.7% / 62.2% |
| Blind | enforced | 6/10 | 0/10 | 98.3% / 64.6% |
| Sighted | none | 6/10 | 2/10 | 91.0% / 53.5% |
| Sighted | shown | 7/10 | 1/10 | 94.6% / 59.7% |
| Sighted | enforced | 7/10 | 0/10 | 94.0% / 72.5% |

No detectable accuracy difference (Fisher's exact p = 0.65 for 7/10 vs 5/10). The gate never fired: whenever the prompt said unverified rulings would be rejected, the judge supplied a verified quote first time. This slice checks the plumbing; an accuracy effect of 10 points would need roughly 200+ items per arm. Even on clean Project Gutenberg text, whitespace-normalised exact matching rejects 23–41% of quotes the tolerant checker verifies.

## 7. Limits

- Verification is lexical. A real quote attached to the wrong claim passes.
- 7 deals, 2 open-weight models, mostly single runs; no human labels for fabrication; no investment outcomes.
- The fabrication audit is a rule-based heuristic, and the held-out comparison is confounded by a code change.
- The structured argument audit (ASPIC+-style grounded semantics) is reported but never decides the verdict; in the stored runs it omitted the Risk ruling from the graph, fixed afterwards.
- This folder's code differs from the code that produced these numbers in the ways listed in `README.md` ("How this folder differs").
