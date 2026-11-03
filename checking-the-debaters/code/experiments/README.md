# Experiments

## Ablation conditions (`ABLATION` in `config.py`)

Each condition adds one mechanism to the previous one:

| | Documents | Citation checks shown | Veto gate in code | Ledger, deal math, Quant checks |
|---|---|---|---|---|
| A | Phase 1 only (original design); the Quant Agent gets an 8,000-char excerpt | – | – | – |
| B | every call | – | – | – |
| C | every call | ✓ | – | – |
| D | every call | ✓ | ✓ | – |
| E | every call | ✓ | ✓ | ✓ (full system) |

Held constant: the «quote» format request, compression (frozen per study), the argument blocks, temperatures, the document cap. Quotes are logged and checked in **every** condition; in A and B the agents never see the results. `SOCRATIC_REFEREE=0` removes the document-blind referee independently of A–E.

## The grid runner

```bash
python experiments/run.py models                       # what the key can call
python experiments/run.py ping --models gpt-oss-120b qwen-3.8-27b
python experiments/run.py prepare --study main --tier paid
python experiments/run.py run --study main --models gpt-oss-120b qwen-3.8-27b --conditions A E --runs 3
python experiments/export_labels.py --study main --labellers 2    # blinded labelling sheets
python experiments/analyze.py --study main                        # summary and contrasts
```

- `prepare` parses every deal in `deals.csv`, learns the compression templates over all of them, then freezes the parsed documents and template index for the study, so every run sees identical documents.
- `run` is resumable, records the code hash, and refuses to continue a study on changed pipeline code unless told to (`--allow-code-change`).
- It stops the grid on HTTP 402 (out of credit) instead of marking every remaining run failed.
- `--variant name:SETTING=value` adds an arm (for example `nocompress:DOC_COMPRESSION=0`).
- `analyze.py --outcomes outcomes.csv` computes ROC AUC against fund decisions or outcomes when you have them.

`deals.csv` lists only the sample deal. Add rows (`deal_id,company,docs_dir,tuned`) for your own data rooms; set `tuned=1` for deals used while developing prompts; `prepare` leaves them out unless given `--include-tuned`.

## QuALITY

```bash
python experiments/quality.py fetch   --study q --limit 30
python experiments/quality.py run     --study q --models gpt-oss-120b --arms blind_none blind_shown blind_enforced sighted_none sighted_shown sighted_enforced
python experiments/quality.py analyze --study q
```

Two debaters argue the gold answer and a distractor; a story-blind referee audits round 1; the judge is blind (transcript only, as in Khan et al., 2024) or sighted (story plus transcript), crossed with verification none, shown or enforced. Every quote is scored by both whitespace-normalised exact match and the tolerant checker.
