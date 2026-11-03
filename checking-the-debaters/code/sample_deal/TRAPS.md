# Larkspur Soil Analytics: the sample data room

**Everything here is invented**: the company, the people, the investors, the counterparties and every figure. `make_sample_deal.py` builds the files (`pip install reportlab` first); the generated files are committed so the tests run without it.

| File | Kind | Noise it carries |
|---|---|---|
| `Larkspur IC memo.pdf` | memo, 3 pages | header repeated on every page; "non-" hyphenated across a line break |
| `Larkspur seed deck.pdf` | deck, 4 slides | diagonal watermark; on slide 2 its letters land inside the founder sentence ("scient**E**ist") |
| `Larkspur term sheet.pdf` | SAFE terms | none |
| `Larkspur revenue model.pdf` | revenue table | extracts as a single run of numbers per row |
| `Larkspur pilot data.xlsx` | spreadsheet | none |
| `Legal/Larkspur SAFE.docx` | legal, in a subfolder | none |
| `Legal/Larkspur SPV LPA.docx` | legal boilerplate, ~150k characters | too large for a free-tier context window: must be cut first, before the memo or term sheet |

## Traps with known answers

| Trap | Where | What a sound system does |
|---|---|---|
| **Two runway figures**: memo says about 20 months after the seed closes; deck says cash on hand covers 14 months at today's burn | memo p. 2, deck slide 3 | ledger records both and flags a conflict instead of picking one |
| **Negation**: "The company has not signed any commercial supply agreements" | memo p. 1 | a quote that drops "not" is rejected |
| **Misread figure**: "$40M to $60M in yearly revenue at maturity" is not the round size ($6M) | memo p. 3 | a ledger entry of round_size = $40M from that sentence is kept but flagged as a conflict, with its quote |
| **Venture vs grants**: "$3.2M of venture capital plus $900K of non-dilutive state grants" | memo p. 2 | two separate facts; the line-break hyphen does not hide "non" |
| **Watermark inside a sentence** | deck slide 2 | "Maren Halvorson, a soil scientist, started the company in 2022" verifies; "2021" or "Halvorsen" does not |
| **No demand evidence**: all twelve deployments are unpaid pilots; nothing in the room is a purchase order, LOI or third-party validation | memo, deck, pilot sheet | any claim of a contract or validation is an invented quote and is flagged NOT FOUND |
| **Arithmetic**: subscription revenue goes from 410,000 to 2,050,000 | revenue model | a Quant claim of 5× verifies; 4× is an arithmetic error |
| **Legal bulk**: ~150k characters of LPA boilerplate | `Legal/` | under a 120k-character budget the LPA is truncated, not the memo |

The scripted model in `tests/fake_llm.py` plays the failure: a panelist cites an invented purchase order from a grain cooperative and an invented university field trial, and the Risk Agent marks objections RESOLVED on them and insists when re-asked. `example_report_scripted.md` shows what the pipeline does with that.
