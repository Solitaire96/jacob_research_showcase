# Checking the debaters

**The idea in three sentences.** Multi-agent LLM debate is increasingly proposed for investment decisions, with specialist agents arguing a deal in the open and the transcript serving as an audit trail. This work reports a failure that such systems hide rather than correct: agents debating from memory of the documents invent the evidence they are told to cite, and the agent holding the veto accepts it. The system here debates venture deals from their data rooms, checks every quoted piece of evidence against the parsed documents in code, shows the result to every agent before anyone else reads the statement, and measures which of those mechanisms actually matter.

**Headline result.** In 15 early runs, all 9 INVEST verdicts rested on evidence that appears nowhere in the deal documents: documents, contracts, verifications and executives that do not exist. With documents on every turn and every quote checked, none of those entities reappeared in 20 later runs across two model families. A controlled 252-ruling replay then shows that either document sight or displayed citation checks is enough to stop a judge accepting unverified resolutions, and that the code-enforced veto gate added nothing measurable on top: it changed 0 of 252 verdicts. Details and the status of every number: `RESULTS.md`.

## What is here

| Path | Contents |
|---|---|
| `RESULTS.md` | Results summary and a **status ledger**: for every quotable number, whether it is measured, heuristic, confounded or a single run |
| `code/orchestrator/citations.py` | The citation checker: tolerant of PDF extraction noise (watermarks, split numbers, hyphenation), strict on figures, names, negations and qualifiers |
| `code/orchestrator/debate_engine.py` | The debate as a plain-Python state machine: fact ledger, independent scorecards, openings (Risk Agent last), two adversarial cross-examination rounds with a document-blind Socratic referee, the Risk Agent's challenge and ruling, panel vote |
| `code/agents/` | Risk Agent (veto holder), Financial, Growth, Sentiment, Moat and Founder agents, the Quant auditor (arithmetic re-computed in code, inputs bound to verified quotes) and the Socratic referee |
| `code/orchestrator/facts.py`, `deal_math.py` | Phase 0: stated figures enter a ledger only if their quote is found and contains the value; ownership, dilution and return multiples are computed by functions, not generated |
| `code/orchestrator/arguments.py` | Structured claims and attacks, evaluated under grounded semantics with an evidence-based preference; reported, never decides the verdict |
| `code/ingestion/` | Parsing (PDF, DOCX, PPTX, XLSX, CSV, text) and deterministic local compression that only ever deletes text verbatim, so quotes stay checkable |
| `code/experiments/` | The A–E ablation harness (`run.py`, `export_labels.py`, `analyze.py`) and the QuALITY benchmark adapter (`quality.py`) |
| `code/sample_deal/` | A fictional data room, **Larkspur Soil Analytics**, with deliberate traps listed in `TRAPS.md`, its generator script, and an example report from the scripted model |
| `code/tests/` | 124 offline tests, including the full pipeline end to end against the sample deal with a scripted model |

## How this folder differs from the code behind the results

The measured runs used real, confidential data rooms; none of their documents, reports, saved debates or deal names are here. Every number in `RESULTS.md` comes from those runs except the QuALITY pilot, which used public data. Changes in porting:

- **Expert-witness lifelines removed.** In the measured runs each agent could summon one of four investor personas once per debate; their statements went through the same checker. The prompts that offered them and the referee's instructions about them are gone; the checker, gate and ledger code is unchanged by this.
- **One prompt line softened** (the rebuttal "stalemate" option), and the evidence rule's example name changed.
- **A checker bug fixed:** the rule that re-joins numbers split by a watermark compared digits only, so "$24M" matched "$2.4M". Re-scoring all stored quotes with the fix rejects exactly one more (7,428 → 7,427 of 8,406).
- **Not included:** the Phase-4 replay harness and the quote audits, which operate on the saved confidential debates.
- **Tests repointed** from the real data rooms to the sample deal. Before, 24 end-to-end tests skipped without those documents; now all 124 run.

## Honest limits

- Nothing here is peer-reviewed; the paper is a working draft.
- No investment outcome data: nothing here says whether the verdicts are good investments.
- Citation verification is lexical. It shows that quoted words exist in the documents, not that they support the claim they are attached to.
- Seven deals, two open-weight models, mostly single runs; the fabrication audit is a rule-based heuristic, and the held-out comparison is confounded by a code change.
- The design adapts the verified-quote tool of Khan et al. (2024) and the less-informed judge of Irving et al. (2018) to noisy multi-document data rooms; the closest prior systems for investment are AlphaAgents (Zhao et al., 2025) and DIALECTIC (Bae et al., 2026). What is investigated here is the transfer to noisy documents and how sight, displayed checks and code enforcement interact at the judge.
- The system and experiments were built with AI coding assistants under my direction; the status ledger in `RESULTS.md` records what was measured and how.

## Running it

Offline, no API key (scripted model):

```
cd code
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest
python -m pytest -q tests                      # 124 tests, about 25 s

LLM_FAKE=1 CEREBRAS_API_KEY=x python app.py --company "Larkspur Soil Analytics" \
    --docs-dir sample_deal/larkspur --skip-search
```

The scripted Risk Agent claims an objection is resolved by a purchase order that does not exist, insists when re-asked, is downgraded in code, and the deal is vetoed; `sample_deal/example_report_scripted.md` is that report.

With a model: copy `.env.example` to `.env`, set one of `CEREBRAS_API_KEY`, `ANTHROPIC_API_KEY` or `GOOGLE_API_KEY`, and drop the `LLM_FAKE` variable. `ABLATION=A`…`E` selects the condition (E is the full system). The QuALITY pilot: `python experiments/quality.py fetch --study q` then `run` and `analyze`.
