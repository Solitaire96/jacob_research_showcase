"""
tests/fake_llm.py — Scripted stand-in for the chat model.

Routes on prompt content and returns canned outputs so the full pipeline can
run offline. `SCENARIO` switches behaviours per test.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import Counter

from langchain_core.messages import AIMessage

# Real text from sample_deal/larkspur (verified to be found by the checker)
REAL_1 = "Larkspur is an early entrant in continuous soil-carbon measurement for row-crop farms"
REAL_2 = "Valuation Cap: $40,000,000 post-money"
REAL_3 = "Subscription Revenue $USD 410,000 2,050,000 7,380,000"
# Invented evidence of the kind models produced in early runs (a contract and a third-party validation)
FAKE_1 = "a binding multi-year purchase order from a national grain cooperative covering 1.2 million acres"
FAKE_2 = "independently validated by a land-grant university field trial"

# Realistic extractor output on the Larkspur documents: real quotes, two runway figures that
# conflict, one wrong value and one invented quote that must be rejected.
FACT_LINES = """FACT: round_size | 6000000 | seed | «Seed raise: $6M» [IC memo, p. 2]
FACT: valuation_cap | 40000000 | SAFE, post-money | «Valuation Cap: $40,000,000 post-money» [Term sheet, p. 1]
FACT: total_raised_prior | 3200000 | before seed | «Prior funding to date: $3.2M» [IC memo, p. 2]
FACT: runway_months | 20 | after the seed closes | «roughly 20 months of operating runway after the seed closes» [IC memo, p. 2]
FACT: runway_months | 14 | at today's burn, before the seed | «At today's burn, cash on hand covers 14 months of runway» [Seed deck, slide 3]
FACT: monthly_burn | 210000 | Q3 2026 average | «Net cash burn averaged $210K per month in the third quarter of 2026» [IC memo, p. 2]
FACT: revenue:2025 | 410000 | actual | «Subscription Revenue $USD 410,000 2,050,000 7,380,000» [Revenue model, p. 1]
FACT: revenue:2026 | 2050000 | projection | «Subscription Revenue $USD 410,000 2,050,000 7,380,000» [Revenue model, p. 1]
FACT: valuation_cap | 4000000 | wrong value | «Valuation Cap: $40,000,000 post-money» [Term sheet, p. 1]
FACT: cash_on_hand | 9000000 | invented | «The company holds $9M of cash on hand» [IC memo, p. 2]"""

SCENARIO: dict = {}
CALLS: Counter = Counter()
PROMPT_CHARS: list[int] = []
_LOCK = threading.Lock()
CONCURRENCY = {"active": 0, "max": 0}


def reset(**kw):
    SCENARIO.clear()
    SCENARIO.update({
        "risk_ruling": "fabricated_then_insists",   # | fabricated_then_honest | honest_invest
        "malformed_scorecard_once": {"Moat Agent"},
        "garbage_vote": {"Founder Agent"},
        "crash_in_phase3": False,
        "delay": 0.0,
    })
    SCENARIO.update(kw)
    CALLS.clear()
    PROMPT_CHARS.clear()
    CONCURRENCY.update(active=0, max=0)


def _agent(system: str) -> str:
    m = re.search(r"You are the (\w+ Agent)", system)
    return m.group(1) if m else "?"


def _scorecard(agent: str, dims: list[str]) -> str:
    core = "\n".join(
        f"{d} | {3 if agent == 'Risk Agent' and i < 3 else 7} | Rationale «{REAL_1}» [Larkspur IC memo.pdf, p. 1]"
        for i, d in enumerate(dims)
    )
    na = "N/A" if agent == "Sentiment Agent" else "6"
    vote = "PASS" if agent == "Risk Agent" else "INVEST"
    return f"""**CORE_SCORES:**
{core}

**FLEX_SCORES:**
Valuation discipline | {na} | «{REAL_2}» [Term sheet, p. 1]
Data quality | 5 | NOT IN DOCUMENTS
Execution | 6 | opinion

**INITIAL_VOTE:** {vote}

THESIS:
Strong thesis. «{REAL_1}» and also «{FAKE_2}».

KEY_QUESTIONS:
1. Q1?
2. Q2?
3. Q3?"""


class FakeLLM:
    def __init__(self, temperature: float = 0.0, max_tokens: int = 0):
        self.temperature = temperature

    def invoke(self, messages):
        if os.getenv("FAKE_402") == "1":                # harness test: provider out of credit
            raise RuntimeError("Error code: 402 - {'message': 'Payment required', 'code': 'payment_required'}")
        system = messages[0].content if isinstance(messages[0].content, str) else "".join(
            b["text"] for b in messages[0].content)
        human = messages[1].content
        with _LOCK:
            PROMPT_CHARS.append(len(system) + len(human))
            CONCURRENCY["active"] += 1
            if "=== FACT LEDGER" in system:
                CALLS["saw_ledger"] += 1
            if os.getenv("FAKE_PROMPT_LOG"):          # harness tests inspect what each condition sends
                with open(os.environ["FAKE_PROMPT_LOG"], "a", encoding="utf-8") as f:
                    f.write(json.dumps({"system": system, "human": human}) + "\n")
            CONCURRENCY["max"] = max(CONCURRENCY["max"], CONCURRENCY["active"])
        try:
            if SCENARIO.get("delay"):
                time.sleep(SCENARIO["delay"])
            with _LOCK:      # routing mutates shared counters
                out = self._route(system, human)
            return AIMessage(content=out)
        finally:
            with _LOCK:
                CONCURRENCY["active"] -= 1

    def _route(self, system: str, human: str) -> str:
        agent = _agent(system)

        # ── Phase 0 fact extraction ─────────────────────────────────────────
        if "You are the Fact Extractor" in system:
            CALLS["facts"] += 1
            return FACT_LINES

        # ── Quant ───────────────────────────────────────────────────────────
        if "You are the Quant Agent" in system:
            CALLS["quant"] += 1
            stated = "5" if CALLS["quant"] == 1 else "4"   # second audit has bad arithmetic
            return f"""VERDICT: [VERIFIED]
INPUTS:
rev_2025 = 410000 | «{REAL_3}» [Revenue model, p. 1]
rev_2026 = 2050000 | «{REAL_3}» [Revenue model, p. 1]
CALCULATION: rev_2026 / rev_2025
RESULT: {stated}
EXPLANATION: Growth multiple."""

        # ── Socratic ────────────────────────────────────────────────────────
        if system.startswith("You are the Socratic Agent"):
            if "CROSS-EXAMINATION ROUND" in human:
                CALLS["socratic_round"] += 1
                if "Fake documents" in human:
                    raise AssertionError("Socratic should be document-blind")
                return """**AGENT: Growth Agent**
Circular reasoning: "it will win because it is winning".

AGENT: [Moat Agent]
Appeal to authority.

**VERDICT:** Two fouls."""
            CALLS["socratic_eval"] += 1
            return "**PRESS:** Still circular." if CALLS["socratic_eval"] == 1 else "CONCEDE: Fine, logic holds."

        # ── QuALITY benchmark (experiments/quality.py) ─────────────────────
        if "QUALITY_DEBATER" in system:
            CALLS["quality_debater"] += 1
            side = "A" if "ASSIGNED OPTION: A" in system else "B"
            real_q = SCENARIO.get("quality_real_quote", "The starship landed on the red plains of Ares at dawn")
            fake_q = SCENARIO.get("quality_fake_quote", "The starship splashed down in the Pacific Ocean at midnight")
            if side == SCENARIO.get("quality_correct_side", "A"):
                return f"Option {side} is right because «{real_q}» [Story]."
            return f"Option {side} is right because «{fake_q}» [Story]."

        if "QUALITY_REFEREE" in system:
            CALLS["quality_referee"] += 1
            if "=== SOURCE STORY" in system or "=== SOURCE STORY" in human:
                raise AssertionError("QuALITY Socratic referee must be story-blind")
            return "AUDIT: Debater B relies on an unverified or circular assertion; Debater A cites direct text."

        if "QUALITY_JUDGE" in system:
            CALLS["quality_judge"] += 1
            reask = "citation checker rejected" in human
            real_q = SCENARIO.get("quality_real_quote", "The starship landed on the red plains of Ares at dawn")
            fake_q = SCENARIO.get("quality_fake_quote", "The starship splashed down in the Pacific Ocean at midnight")
            mode = SCENARIO.get("quality_judge_mode", "persuaded_by_fake_unless_checked")
            if mode == "always_fake":
                return f"ANSWER: B\nEVIDENCE: «{fake_q}» [Story]\nRATIONALE: B sounded convincing."
            if reask or "NOT FOUND" in human:
                return f"ANSWER: A\nEVIDENCE: «{real_q}» [Story]\nRATIONALE: Only A has verified quote support."
            return f"ANSWER: B\nEVIDENCE: «{fake_q}» [Story]\nRATIONALE: B sounded convincing."

        # ── Panel / Risk ────────────────────────────────────────────────────
        from config import GROUND_ALL_TURNS
        is_scorecard = "CORE_SCORES" in human
        if "=== DEAL DOCUMENTS" not in system and (GROUND_ALL_TURNS or is_scorecard):
            raise AssertionError(f"{agent} called WITHOUT the document block")
        if "=== DEAL DOCUMENTS" in system and not GROUND_ALL_TURNS and not is_scorecard:
            raise AssertionError(f"{agent} saw documents outside Phase 1 in condition A")

        # (checked first: the vote prompt embeds the transcript, which contains other phase headers)
        if "cast your final vote" in human:
            CALLS[f"vote:{agent}"] += 1
            if agent in SCENARIO["garbage_vote"]:
                return "I lean positive overall."
            return "**FINAL_VOTE:** INVEST\nRATIONALE: good.\nDISSENT: None."

        if "YOUR SCORING TASK" in human:
            CALLS[f"score:{agent}"] += 1
            if agent in SCENARIO["malformed_scorecard_once"] and CALLS[f"score:{agent}"] == 1:
                return "I think it's a good deal, 8/10."
            dims = re.findall(r"^  - (.+)$", human, re.M)
            return _scorecard(agent, dims)

        if "deliver your opening statement" in human:
            if agent == "Risk Agent":
                assert "Opening statements already delivered by the panel" in human, "Risk opened blind"
            return (f"Opening. «{REAL_2}» [Terms, p.1] and «{FAKE_1}» [Diligence, p. 4]\n\n"
                    f"CLAIMS:\nC1: The cap is $40M | GROUNDS: «{REAL_2}» [Terms, p.1] | INFERENCE: stated in the terms"
                    f" | DEFEATER: a different cap in the documents\n"
                    f"C2: Demand is contracted | GROUNDS: «{FAKE_1}» [Diligence, p. 4] | INFERENCE: a purchase order"
                    f" secures demand | DEFEATER: no order exists\nATTACKS:\nnone")

        if "Cross-examination round" in human:
            if SCENARIO["crash_in_phase3"]:
                raise RuntimeError("simulated API failure")
            rnd = int(re.search(r"round (\d+)", human).group(1))
            out = f"I dispute Growth. «{REAL_1}» [Diligence, p.1]"
            if agent == "Risk Agent" and rnd == 1:
                out += ("\n**REVISE:** Revenue / customer concentration risk | 1 | worse than thought «" + REAL_3 + "»")
            if agent == "Financial Agent" and rnd == 1:
                out += "\nCALL_QUANT: subscription revenue grows 5x from 2025 to 2026"
            if agent == "Financial Agent" and rnd == 2:
                out += "\nCALL_QUANT: subscription revenue grows 4x from 2025 to 2026"
            out += (f"\n\n**CLAIMS:**\nC1: Larkspur is an early entrant | GROUNDS: «{REAL_1}» | INFERENCE: stated |"
                    f" DEFEATER: incumbents exist\nC2: The team will win | GROUNDS: opinion | INFERENCE: track record |"
                    f" DEFEATER: execution misses\nATTACKS:\n")
            if agent == "Risk Agent" and rnd == 1:
                out += "GROW.OPEN.C2 | UNDERMINE | BY C1 | the purchase-order quote is not in the documents"
            elif agent == "Growth Agent" and rnd == 1:
                out += "RISK.OPEN.C1 | REBUT | BY C2 | the cap is irrelevant"
            elif agent == "Moat Agent" and rnd == 1:
                out += "XXX.R9.C1 | REBUT | BY C1 | nonexistent target"
            else:
                out += "none"
            return out

        if "publicly accused you" in human or "rejected your defense" in human:
            CALLS[f"rebut:{agent}"] += 1
            if agent == "Growth Agent":
                return "DEFEND: I do not concede. My argument is not circular."
            return "**CONCEDE:** Fair point. REVISE: IP, patent, & trade secret posture | 5 | conceded"

        if "RISK AGENT GAUNTLET" in human:
            out = f"Objection 1 is mitigated: «{FAKE_1}» [Diligence p.9]. Objection 2: «{REAL_2}»."
            out += (f"\n\nCLAIMS:\nC1: The cap limits entry price | GROUNDS: «{REAL_2}» | INFERENCE: x | DEFEATER: y"
                    f"\nATTACKS:\n")
            out += "OBJ1 | REBUT | BY C1 | addressed by the terms" if agent == "Financial Agent" else "none"
            return out

        if "RISK AGENT FINAL RULING" in human:
            CALLS["risk_ruling"] += 1
            sc = SCENARIO["risk_ruling"]
            reask = "citation checker rejected" in human
            fab = f"""OBJECTION_LOG:
1. **[RESOLVED]** | Contracted. «{FAKE_1}» [Diligence p.9]
2. RESOLVED | Verified. «{FAKE_2}»
3. [RESOLVED] | Fine. «{REAL_2}»

**FINAL_VOTE:** INVEST
RATIONALE: All resolved."""
            if sc == "honest_invest":
                return f"""OBJECTION_LOG:
1. RESOLVED | «{REAL_1}»
2. PARTIAL | some risk remains «{REAL_3}»
3. RESOLVED | «{REAL_2}»

FINAL_VOTE: INVEST
RATIONALE: Adequately mitigated."""
            if not reask:
                return fab
            if sc == "fabricated_then_honest":
                return f"""OBJECTION_LOG:
1. UNRESOLVED | Nothing in the documents.
2. PARTIAL | Unclear.
3. RESOLVED | «{REAL_2}»

FINAL_VOTE: PASS
RATIONALE: Evidence not in documents."""
            return fab   # insists

        raise AssertionError(f"unrouted prompt for {agent}: {human[:200]}")


def make_fake_llm(temperature: float = 0.0, max_tokens: int = 0):
    return FakeLLM(temperature, max_tokens)
