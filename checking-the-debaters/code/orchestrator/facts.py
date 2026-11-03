"""
orchestrator/facts.py — The fact ledger (Phase 0).

One model call lists every value the documents STATE for a fixed set of deal facts,
each with a verbatim quote. Code then decides what enters the ledger:
  - the quote must be found in the documents (CitationVerifier), and
  - the value must be a number stated in that quote (units applied: $1.25M = 1250000).
The model can miss facts; it cannot add one.

Formal rule (functional dependency): each fact key has one value. If verified entries
for the same key disagree beyond tolerance, that is a CONFLICT. A conflict means
"the documents state different values, with quotes" — it can be a real inconsistency or
an artefact (e.g. a scoring rubric listing alternatives); agents must reconcile it.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ingestion.deal_briefing import DealBriefing

from agents.base_agent import clean_line
from agents.quant_agent import value_in_quote
from llm import invoke, make_llm
from orchestrator.citations import CitationVerifier

# key → (unit, description). "revenue:YYYY" is a pattern key.
FACT_SCHEMA: dict[str, tuple[str, str]] = {
    "round_size": ("USD", "total size of the current round being raised"),
    "pre_money_valuation": ("USD", "pre-money valuation of the current round"),
    "post_money_valuation": ("USD", "post-money valuation of the current round"),
    "valuation_cap": ("USD", "SAFE/note valuation cap for the current round"),
    "discount_pct": ("%", "SAFE/note discount, as a percent number (20 for 20%)"),
    "runway_months": ("months", "months of runway the company/document asserts"),
    "monthly_burn": ("USD", "monthly net burn"),
    "cash_on_hand": ("USD", "cash on hand before the round"),
    "total_raised_prior": ("USD", "total equity/venture funding raised before the current round (excluding non-dilutive grants)"),
    "grants_non_dilutive": ("USD", "non-dilutive grants or SBIR/STTR awards received prior to the current round"),
    "employees": ("count", "headcount"),
    "founded_year": ("year", "year founded"),
    "revenue:YYYY": ("USD", "revenue for calendar/fiscal year YYYY; say 'actual' or 'projection' in context"),
}
_KEY_RE = re.compile(r"^(?:%s|revenue:\d{4})$" % "|".join(k for k in FACT_SCHEMA if ":" not in k))
_FACT_LINE = re.compile(r"^FACT\s*:\s*([a-z_]+(?::\d{4})?)\s*\|\s*([-+\d.,eE$%]+)\s*\|\s*(.*?)\s*\|\s*(.+)$", re.I)
CONFLICT_REL_TOL = 0.02


@dataclass
class Fact:
    id: str
    key: str
    value: float
    context: str
    quote: str
    cited: str = ""
    sources: int = 1


@dataclass
class Conflict:
    key: str
    facts: list[Fact]

    def summary(self) -> str:
        vals = " vs ".join(f"{f.id}={fmt_value(f.key, f.value)}" for f in self.facts)
        return f"CONFLICT {self.key}: {vals}"


@dataclass
class Ledger:
    facts: list[Fact] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)      # {key, value, quote, reason}
    raw: str = ""

    def by_key(self, key: str) -> list[Fact]:
        return [f for f in self.facts if f.key == key]

    def keys(self) -> list[str]:
        return list(dict.fromkeys(f.key for f in self.facts))


def unit_of(key: str) -> str:
    return FACT_SCHEMA["revenue:YYYY"][0] if key.startswith("revenue:") else FACT_SCHEMA.get(key, ("", ""))[0]


def fmt_value(key: str, v: float) -> str:
    u = unit_of(key)
    if u == "USD":
        per = "/month" if key == "monthly_burn" else ""
        for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
            if abs(v) >= div:
                return f"${v / div:,.4g}{suf}{per}"
        return f"${v:,.0f}{per}"
    if u == "%":
        return f"{v:g}%"
    if u == "months":
        return f"{v:g} months"
    return f"{v:g}"


def _num(s: str) -> Optional[float]:
    try:
        return float(s.replace(",", "").replace("$", "").replace("%", ""))
    except ValueError:
        return None


# ── Parsing & verification ─────────────────────────────────────────────────────

def parse_fact_lines(raw: str) -> list[dict]:
    out = []
    for line in raw.splitlines():
        m = _FACT_LINE.match(clean_line(line))
        if not m:
            continue
        key = m.group(1).lower()
        val = _num(m.group(2))
        rest = m.group(4)
        qm = re.search(
            r"«([^«»]+)»\s*(?:\[([^\[\]]*)\])?|[“\"]([^“”\"\n]+)[”\"]\s*(?:\[([^\[\]]*)\])?",
            rest,
        )
        quote = (qm.group(1) or qm.group(3) or "").strip() if qm else ""
        cited = (qm.group(2) or qm.group(4) or "").strip() if qm else ""
        out.append({"key": key, "value": val, "context": m.group(3).strip(),
                    "quote": quote, "cited": cited})
    return out


def build_ledger(candidates: list[dict], verifier: CitationVerifier, raw: str = "") -> Ledger:
    led = Ledger(raw=raw)
    for c in candidates:
        reason = None
        if not _KEY_RE.match(c["key"]):
            reason = "unknown fact key"
        elif c["value"] is None or not math.isfinite(c["value"]):
            reason = "value is not a number"
        elif not c["quote"]:
            reason = "no «quote»"
        elif not verifier.check_quote(c["quote"], c["cited"]).verified:
            reason = "quote not found in the documents"
        elif not value_in_quote(c["value"], c["quote"]):
            reason = "value is not stated in its quote"
        if reason:
            led.rejected.append({**c, "reason": reason})
            continue
        # merge duplicates (same key, same value)
        dup = next((f for f in led.facts if f.key == c["key"]
                    and math.isclose(f.value, c["value"], rel_tol=CONFLICT_REL_TOL, abs_tol=1e-9)), None)
        if dup:
            dup.sources += 1
            continue
        led.facts.append(Fact(f"F{len(led.facts) + 1}", c["key"], c["value"], c["context"], c["quote"], c["cited"]))
    for key in led.keys():
        fs = led.by_key(key)
        if len(fs) > 1:
            led.conflicts.append(Conflict(key, fs))
    return led


def ledger_block(led: Ledger, math_text: str = "") -> str:
    if not led.facts and not math_text:
        return ""
    lines = ["=== FACT LEDGER (values stated in the documents; each checked in code against its quote) ==="]
    for f in led.facts:
        ctx = f" ({f.context})" if f.context else ""
        lines.append(f"{f.id} {f.key} = {fmt_value(f.key, f.value)}{ctx} «{f.quote}»"
                     + (f" [{f.cited}]" if f.cited else ""))
    if led.conflicts:
        lines.append("")
        lines.append("DOCUMENTED CONFLICTS — the documents state different values for the same fact. Some may be")
        lines.append("artefacts (a rubric listing alternatives, a different date or basis); any argument relying on")
        lines.append("one of these facts must say which value it uses and why:")
        for c in led.conflicts:
            lines.append(f"  ⚠ {c.summary()}")
    if math_text:
        lines += ["", math_text]
    lines.append("=== END FACT LEDGER ===")
    return "\n".join(lines)


# ── Extraction (one model call) ────────────────────────────────────────────────

class FactExtractor:
    NAME = "Fact Extractor"

    def __init__(self) -> None:
        self.llm = make_llm(temperature=0.0, max_tokens=4096)

    @property
    def system_prompt(self) -> str:
        keys = "\n".join(f"  {k} ({u}): {d}" for k, (u, d) in FACT_SCHEMA.items())
        return f"""You are the {self.NAME}. You copy numbers out of the deal documents above. You never compute,
estimate or infer a value — only values the documents state about THIS company and THIS deal.

FACT KEYS:
{keys}

RULES:
- List every value the documents state for each key. If the documents state DIFFERENT values for the
  same fact in different places, list each one — conflicts matter.
- Skip text that is not a claim about this company: scoring rubrics or checklists that list
  alternative options, generic templates, examples, and other companies' figures.
- One line per value, EXACTLY:
  FACT: <key> | <value as a plain number in the key's unit> | <short context: basis, date, actual/projection> | «verbatim quote that contains the number» [File, p. N]
- USD values in full units (45000000, not 45M). Percent as a number (20 for 20%).
- The quote must be copied exactly from the documents and must contain the number.
- If a key is not stated anywhere, omit it. Output only FACT lines."""

    def extract(self, briefing: DealBriefing, verifier: CitationVerifier) -> Ledger:
        human = f"Extract the fact ledger for {briefing.company_name}."
        raw = invoke(self.llm, self.system_prompt, human, documents=briefing.document_block())
        cands = parse_fact_lines(raw)
        if not cands or not any(c["quote"] for c in cands):
            raw = invoke(self.llm, self.system_prompt,
                         human + "\n\nYour previous reply contained no valid FACT lines. Use exactly the FACT format.",
                         documents=briefing.document_block())
            cands = parse_fact_lines(raw)
        return build_ledger(cands, verifier, raw)
