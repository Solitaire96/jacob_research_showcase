"""
agents/quant_agent.py — The Quantitative Auditor Agent

Evaluates specific numerical claims against the deal documents. It has no
opinion on the deal.

Its output is checked in code, not trusted:
  - every input must carry a verbatim «quote» from the documents (citation-checked);
  - its CALCULATION expression is re-evaluated in Python and compared to its RESULT.
A [VERIFIED] verdict survives only if both checks pass.
"""

from __future__ import annotations

import ast
import math
import operator
import re
from typing import Any, Callable, Optional

from agents.base_agent import clean_line
from config import GROUND_ALL_TURNS, NUMBERS_IN_CODE
from ingestion.deal_briefing import DealBriefing
from llm import invoke, make_llm
from orchestrator.citations import EVIDENCE_RULE, CitationVerifier

_INPUT_RE = re.compile(r"^([A-Za-z_]\w*)\s*=\s*([-+]?[\d.,]+(?:e[-+]?\d+)?)\s*(?:\|\s*(.*))?$", re.IGNORECASE)
_VERDICT_RE = re.compile(r"\[(VERIFIED|REFUTED|UNVERIFIABLE)\]", re.IGNORECASE)

_BINOPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
_UNOPS: dict[type[ast.unaryop], Callable[[Any], Any]] = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS: dict[str, Callable[..., Any]] = {
    "min": min, "max": max, "abs": abs, "round": round, "sqrt": math.sqrt, "log": math.log,
}


def safe_eval(expr: str, names: dict[str, float]) -> float:
    """Evaluate an arithmetic expression over named inputs. No attribute access, no builtins."""
    def ev(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in names:
                raise ValueError(f"unknown input '{node.id}'")
            return names[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 100:
                raise ValueError("exponent too large")
            return float(_BINOPS[type(node.op)](left, right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNOPS:
            return float(_UNOPS[type(node.op)](ev(node.operand)))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in _FUNCS and not node.keywords):
            return float(_FUNCS[node.func.id](*[ev(a) for a in node.args]))
        raise ValueError(f"disallowed expression element: {type(node).__name__}")
    return float(ev(ast.parse(expr.strip(), mode="eval")))


_QUOTE_NUM = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s*(%|percent|k|thousand|mm|m|million|bn|b|billion|tn|trillion)?(?![a-z])",
    re.IGNORECASE,
)
_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6,
          "b": 1e9, "bn": 1e9, "billion": 1e9, "tn": 1e12, "trillion": 1e12}


def quote_values(text: str) -> list[float]:
    """Every value a number in `text` can denote: as written, with its unit applied, and % as a fraction."""
    vals: list[float] = []
    for m in _QUOTE_NUM.finditer(text):
        try:
            base = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        unit = (m.group(2) or "").lower()
        vals.append(base)
        if unit in _SCALE:
            vals.append(base * _SCALE[unit])
        elif unit in ("%", "percent"):
            vals.append(base / 100)
    return vals


def value_in_quote(value: float, quote_text: str) -> bool:
    return any(math.isclose(value, v, rel_tol=5e-3, abs_tol=1e-9) for v in quote_values(quote_text))


def _num(s: str) -> Optional[float]:
    try:
        return float(s.replace(",", "").replace("$", "").replace("%", "").strip())
    except ValueError:
        return None



def support_documents(briefing: DealBriefing) -> str:
    """Documents shown to the Quant Agent. Condition A: an 8,000-char prioritized prefix
    (the pre-intervention design); B+: the same full block the panel sees."""
    if GROUND_ALL_TURNS:
        return briefing.context_block()
    return "DEAL DOCUMENTS (excerpt):\n" + briefing.full_text(max_chars=8000, prioritized=True)


class QuantAgent:
    """Emotionless numerical auditor whose math is re-checked in code."""

    AGENT_NAME: str = "Quant Agent"

    def __init__(self) -> None:
        self.llm = make_llm(temperature=0.0, max_tokens=2048)

    @property
    def system_prompt(self) -> str:
        return f"""You are the {self.AGENT_NAME}, an emotionless, highly rigorous financial and quantitative auditor.

Your only purpose is to evaluate specific numerical claims made by other agents against the deal documents above.
You have NO OPINION on whether the company is a good or bad investment. You only see math.

{EVIDENCE_RULE}

OUTPUT FORMAT (machine-checked — follow exactly):
VERDICT: [VERIFIED] or [REFUTED] or [UNVERIFIABLE]
INPUTS:
name_one = 1234567 | «verbatim quote containing the figure» [File, p. N]
name_two = 0.25 | «verbatim quote» [File, p. N]
CALCULATION: an arithmetic expression using only the input names, numbers, + - * / ** ( ) and min/max/abs/round
RESULT: the numeric value of CALCULATION
EXPLANATION: 2-4 sentences comparing RESULT to the claim.

RULES:
- Input values are plain numbers in base units (no $, %, K, M, B suffixes; write 45000000 not $45M; 0.25 not 25%).
- Every input needs a verbatim quote, and the input's value must be a number stated in that quote
  (unit conversion is fine: «$1.25M» supports 1250000; «25%» supports 0.25). If a needed number
  is not in the documents, the verdict is [UNVERIFIABLE].
{"- Your CALCULATION is re-computed in code. If it does not equal your RESULT, your verdict is replaced with [ARITHMETIC ERROR]." + chr(10) if NUMBERS_IN_CODE else ""}- Use terms like "accurate", "inaccurate", "unsupported". Keep it under 300 words."""

    def verify_claim(
        self,
        briefing: DealBriefing,
        debate_context: str,
        specific_claim: str,
        calling_agent: str,
        verifier: Optional[CitationVerifier] = None,
    ) -> str:
        prompt = f"""You have been summoned by the {calling_agent} to audit a quantitative claim regarding {briefing.company_name}.

THE NUMERICAL CLAIM YOU MUST AUDIT:
"{specific_claim}"

RECENT DEBATE CONTEXT:
{debate_context[-4000:]}

Deliver your audit in the required format now."""
        raw = invoke(self.llm, self.system_prompt, prompt, documents=support_documents(briefing))
        return self.machine_check(raw, verifier) if NUMBERS_IN_CODE else raw

    @staticmethod
    def machine_check(raw: str, verifier: Optional[CitationVerifier]) -> str:
        """Re-verify the Quant output in code; returns output with corrected verdict + check note."""
        m = _VERDICT_RE.search(raw)
        claimed = m.group(1).upper() if m else "UNVERIFIABLE"
        names: dict[str, float] = {}
        quote_problems: list[str] = []
        value_problems: list[str] = []
        calc: Optional[str] = None
        result: Optional[float] = None

        for rawline in raw.splitlines():
            line = clean_line(rawline)
            im = _INPUT_RE.match(line)
            if im:
                val = _num(im.group(2))
                if val is not None:
                    names[im.group(1)] = val
                    if verifier is not None:
                        checks = verifier.check_text(im.group(3) or "")
                        if not any(c.verified for c in checks):
                            quote_problems.append(im.group(1))
                    # The value must be a number stated in its own (found) quote, not just any number.
                    quoted = " ".join(c.quote for c in (verifier.check_text(im.group(3) or "") if verifier else []))
                    if not value_in_quote(val, quoted or (im.group(3) or "")):
                        value_problems.append(f"{im.group(1)}={val:,.6g}")
                continue
            up = line.upper()
            if up.startswith("CALCULATION:"):
                calc = line.split(":", 1)[1].strip().strip("`")
            elif up.startswith("RESULT:"):
                result = _num(line.split(":", 1)[1])

        notes: list[str] = []
        final = claimed
        if calc is None or result is None:
            notes.append("no CALCULATION/RESULT given — arithmetic not machine-checked")
            if claimed == "VERIFIED":
                final = "UNVERIFIABLE"
        else:
            try:
                computed = safe_eval(calc, names)
                if math.isclose(computed, result, rel_tol=5e-3, abs_tol=1e-9):
                    notes.append(f"arithmetic re-computed in code: {calc} = {computed:,.6g} ✓")
                else:
                    notes.append(f"arithmetic re-computed in code: {calc} = {computed:,.6g}, but Quant stated {result:,.6g} ✗")
                    final = "ARITHMETIC ERROR"
            except (ValueError, ZeroDivisionError, SyntaxError, TypeError, OverflowError) as e:
                notes.append(f"CALCULATION could not be evaluated ({e})")
                if claimed == "VERIFIED":
                    final = "UNVERIFIABLE"
        if quote_problems:
            notes.append("inputs without a quote found in the documents: " + ", ".join(quote_problems))
            if final == "VERIFIED":
                final = "UNVERIFIABLE"
        if value_problems:
            notes.append("input values not stated in their own quotes: " + ", ".join(value_problems))
            if final == "VERIFIED":
                final = "UNVERIFIABLE"
        if final != claimed:
            notes.append(f"verdict changed by code: [{claimed}] → [{final}]")
            raw = _VERDICT_RE.sub(f"[{final}]", raw, count=1) if m else f"[{final}]\n{raw}"
        return raw + "\n\n[Quant machine check: " + "; ".join(notes) + "]"
