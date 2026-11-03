"""
agents/base_agent.py

Abstract base class for all VC panel agents (Risk + the five panel agents).
Defines the scorecard schema, the prompt structure, output parsing, and the
debate methods every agent shares.

Grounding: every call carries the full deal-document block (see llm.invoke),
not just Phase 1. Agents must cite verbatim «quotes», which are checked in
code by orchestrator/citations.py.
"""

from __future__ import annotations

import re
from abc import ABC
from dataclasses import dataclass
from typing import Callable, Literal, Optional, TypeVar

from config import (
    FACT_LEDGER, FLEX_DIMENSIONS_PER_AGENT, GROUND_ALL_TURNS, MAX_TRANSCRIPT_CHARS, NUMBERS_IN_CODE,
    SCORE_MAX, SCORE_MIN, SHOW_CITATION_CHECKS,
)
from ingestion.deal_briefing import DealBriefing
from llm import invoke, make_llm
from orchestrator.arguments import FORMAT_INSTRUCTIONS as ARGUMENT_FORMAT
from orchestrator.citations import EVIDENCE_RULE

VoteType = Literal["INVEST", "PASS", "ABSTAIN"]   # ABSTAIN = output could not be parsed
T = TypeVar("T")


class AgentOutputParseError(RuntimeError):
    pass


# ── Prompt-size helper ─────────────────────────────────────────────────────────

def fit_tail(text: str, limit: int = MAX_TRANSCRIPT_CHARS) -> str:
    """Keep the most recent `limit` characters of a transcript, marking the cut."""
    if len(text) <= limit:
        return text
    cut = text[-limit:]
    nl = cut.find("\n")
    if 0 <= nl < 500:
        cut = cut[nl + 1:]
    return f"[… {len(text) - len(cut):,} earlier characters of transcript omitted to fit the context window …]\n{cut}"


# ── Parsing helpers ────────────────────────────────────────────────────────────

def clean_line(line: str) -> str:
    """Strip markdown decoration (bold, headers, bullets, quotes) from a line."""
    line = line.strip().replace("**", "").replace("__", "")
    return re.sub(r"^[#>\-\*\s]+", "", line).strip()


_SCORE_RE = re.compile(r"^(N/?A|\d{1,2}(?:\.\d)?)(?:\s*/\s*10)?$", re.IGNORECASE)


def parse_score(token: str) -> tuple[bool, Optional[int]]:
    """Returns (is_valid, score). score None means N/A."""
    m = _SCORE_RE.match(token.strip())
    if not m:
        return False, None
    v = m.group(1).upper().replace("/", "")
    if v == "NA":
        return True, None
    return True, max(SCORE_MIN, min(SCORE_MAX, round(float(v))))


def parse_vote(raw: str, key: str) -> Optional[str]:
    """Find `KEY: INVEST|PASS` anywhere in the text (markdown tolerated)."""
    for line in raw.splitlines():
        m = re.match(rf"^{key}\s*:\s*\[?\s*(INVEST|PASS)\b", clean_line(line), re.IGNORECASE)
        if m:
            return m.group(1).upper()
    return None


_REVISE_RE = re.compile(r"\bREVISE\s*:\s*(.+?)\s*\|\s*([^|]+?)\s*\|\s*(.+)$", re.IGNORECASE)


def parse_revisions(raw: str) -> list[tuple[str, Optional[int], str]]:
    out = []
    for line in raw.splitlines():
        m = _REVISE_RE.search(clean_line(line))   # tolerate REVISE: mid-line
        if m:
            ok, score = parse_score(m.group(2))
            if ok:
                out.append((m.group(1).strip().strip("[]"), score, m.group(3).strip()))
    return out


def parse_stance(raw: str) -> str:
    """First labelled stance in a Socratic rebuttal: DEFEND | CONCEDE | STALEMATE."""
    for line in raw.splitlines():
        c = clean_line(line).upper()
        if not c:
            continue
        for stance in ("STALEMATE", "CONCEDE", "DEFEND"):
            if c.startswith(stance):
                return stance
        break
    return "DEFEND"   # unlabelled → treated as a defence (it did not concede)


# ── Condition-dependent prompt fragments (see config.ABLATION) ─────────────────

_CHECK_RULES = """- Statements are followed by an automated [Citation check]. NOT FOUND means the exact wording is not in
  the documents: it may be a loose paraphrase of a real passage, or an invention. Check it against the
  documents above before relying on it or attacking it. Claims with no quotes are opinion.
- Scorecard dimensions marked [evidence: not found] or [evidence: none] rest on unverified support.
"""
_LEDGER_RULES = """- The FACT LEDGER after the documents lists stated figures (F1, F2, …), each verified in code against
  its quote. Cite them by ID plus quote. Where the ledger shows a CONFLICT, say which value you use and why.
- DEAL MATH figures are computed in code from ledger facts. Do not redo that arithmetic; if you disagree,
  dispute an input or an assumption marked [A], and say what value you would use instead.
"""
_CHECKED = " (each followed by its automated citation check)" if SHOW_CITATION_CHECKS else ""


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class ScorecardDimension:
    name: str
    score: Optional[int]    # 1–10; None = N/A (no information in documents)
    rationale: str
    is_flex: bool = False   # True = agent-chosen for this deal
    evidence: str = ""      # set by the engine: "found" | "not found" | "none"

    def score_text(self) -> str:
        return "N/A" if self.score is None else f"{self.score}/10"


@dataclass
class Scorecard:
    agent_name: str
    dimensions: list[ScorecardDimension]
    overall_score: float        # computed: mean of non-N/A dimensions
    thesis: str                 # one-paragraph bull or bear thesis
    initial_vote: VoteType
    key_questions: list[str]    # 3–5 questions for other agents
    citation_summary: str = ""  # filled in by the engine after citation checking

    def recompute_overall(self) -> float:
        scores = [d.score for d in self.dimensions if d.score is not None]
        self.overall_score = round(sum(scores) / len(scores), 1) if scores else 0.0
        return self.overall_score

    def find_dimension(self, name: str) -> Optional[ScorecardDimension]:
        n = name.lower().strip()
        for d in self.dimensions:
            if d.name.lower() == n:
                return d
        for d in self.dimensions:
            if n in d.name.lower() or d.name.lower() in n:
                return d
        return None

    def to_text(self) -> str:
        lines = [f"=== {self.agent_name} Scorecard ==="]
        for d in self.dimensions:
            tag = "[FLEX]" if d.is_flex else "[CORE]"
            ev = (f" [evidence: {d.evidence}]"
                  if SHOW_CITATION_CHECKS and d.evidence and d.evidence != "found" else "")
            lines.append(f"  {tag} {d.name}: {d.score_text()}{ev} — {d.rationale}")
        lines.append(f"\n  Overall: {self.overall_score:.1f}/10")
        lines.append(f"  Initial Vote: {self.initial_vote}")
        lines.append(f"\n  Thesis:\n  {self.thesis}")
        lines.append("\n  Key Questions for Panel:")
        for i, q in enumerate(self.key_questions, 1):
            lines.append(f"    {i}. {q}")
        if self.citation_summary and SHOW_CITATION_CHECKS:
            lines.append("\n  " + self.citation_summary.replace("\n", "\n  "))
        return "\n".join(lines)


@dataclass
class DebateStatement:
    agent_name: str
    phase: str          # "opening", "cross_exam_rN", "socratic_rebuttal_rN", "gauntlet_response", ...
    content: str


# ── Base agent ─────────────────────────────────────────────────────────────────

class BaseAgent(ABC):
    """
    Abstract base for the six panel agents.
    Subclasses define their name, persona, and core dimensions.
    """

    #: Override in each subclass
    AGENT_NAME: str = "Base Agent"
    PERSONA: str = ""
    VETO_HOLDER: bool = False
    ALLOW_NA: bool = True   # may score a dimension N/A when documents are silent

    #: Core scoring dimensions — must be exactly 5 for comparability
    CORE_DIMENSIONS: list[str] = []

    def __init__(self) -> None:
        self.llm = make_llm(temperature=0.3, max_tokens=4096)
        self.scorecard: Optional[Scorecard] = None
        self.final_vote: Optional[VoteType] = None
        self.briefing: Optional[DealBriefing] = None
        self.extra_context: str = ""   # agent-private context (e.g. Sentiment search results)

    def bind(self, briefing: DealBriefing) -> None:
        self.briefing = briefing

    _phase1_call: bool = False   # set during analyze(); condition A shows documents only then

    def _call(self, system: str, human: str) -> str:
        """Invoke the LLM with the document block + system prompt + human message."""
        assert self.briefing is not None, f"{self.AGENT_NAME}: bind(briefing) before calling"
        docs = self.briefing.context_block() if (GROUND_ALL_TURNS or self._phase1_call) else None
        return invoke(self.llm, system, human, documents=docs)

    def _call_parsed(
        self,
        human: str,
        parse: Callable[[str], Optional[T]],
        what: str,
    ) -> tuple[Optional[T], str]:
        """Call, parse; on failure re-ask once with the format error. Returns (parsed|None, raw)."""
        raw = self._call(self.system_prompt, human)
        parsed = parse(raw)
        if parsed is not None:
            return parsed, raw
        retry = (
            f"{human}\n\n---\nYour previous answer could not be parsed ({what}). "
            f"Your previous answer was:\n{raw}\n\n"
            "Reply again using EXACTLY the required format, labels included."
        )
        raw2 = self._call(self.system_prompt, retry)
        return parse(raw2), raw2

    @property
    def system_prompt(self) -> str:
        veto_clause = (
            "\n\nIMPORTANT: You hold UNCONDITIONAL VETO POWER over this investment. "
            "A PASS verdict from you kills the deal regardless of other agents' views. "
            "You may only move from PASS to INVEST if — and only if — your stated objections "
            "are answered with specific documentary evidence. You cannot be coerced; only persuaded."
            if self.VETO_HOLDER else ""
        )
        na_rule = (
            "- If the documents contain no information for a dimension, score it N/A instead of guessing."
            if self.ALLOW_NA else
            "- Missing information is itself a risk: score it, do not mark it N/A."
        )
        extra = f"\n\n{self.extra_context}" if self.extra_context else ""
        return f"""You are the {self.AGENT_NAME} in a VC investment analysis panel.

{self.PERSONA}

SCORING RULES:
- Score each dimension from {SCORE_MIN} to {SCORE_MAX} (10 = exceptional, 1 = disqualifying).
- You always score exactly 5 core dimensions plus {FLEX_DIMENSIONS_PER_AGENT} flex dimensions.
- Flex dimensions: choose the {FLEX_DIMENSIONS_PER_AGENT} factors you find most material in THIS deal.
{na_rule}
- The overall score is computed automatically as the mean of your scored dimensions.
- Votes: INVEST (strong conviction) or PASS (do not invest). No conditional votes or deal term changes allowed.
{veto_clause}

{EVIDENCE_RULE}

DEBATE RULES:
- Be direct, concise, and adversarial where warranted.
{_CHECK_RULES if SHOW_CITATION_CHECKS else ""}{_LEDGER_RULES if FACT_LEDGER else ""}- If an argument changes your view of a dimension, revise it with a line in EXACTLY this form:
  REVISE: [exact dimension name] | [new score 1-10] | [reason, with a «quote» if factual]
  Revisions are applied to your scorecard and change which issues are pressed later.

QUANT AUDITOR (use during cross-examination or gauntlet phases):
- If another agent makes a dubious numerical claim, or you want to mathematically prove your own point, you can summon the Quant Agent.
- To summon the Quant Agent, include EXACTLY this line anywhere in your response:
  CALL_QUANT: [Specific numerical claim to verify]
- You have EXACTLY 2 Quant tokens per debate.
- The Quant Agent has NO opinion on the company. It quotes its inputs from the documents{" and its arithmetic is re-computed in code" if NUMBERS_IN_CODE else ""}.{extra}"""

    # ── Phase 1 ────────────────────────────────────────────────────────────────

    def analyze(self, briefing: DealBriefing, extra_instructions: str = "") -> Scorecard:
        """Phase 1: Independent analysis. Read the deal and produce a scorecard."""
        self.bind(briefing)
        core_dims_str = "\n".join(f"  - {d}" for d in self.CORE_DIMENSIONS)
        score_hint = "1-10 or N/A" if self.ALLOW_NA else "1-10"
        prompt = f"""You are analyzing the VC deal for {briefing.company_name}. The deal documents are above.
{extra_instructions}
YOUR SCORING TASK:
Score the following 5 CORE dimensions (mandatory):
{core_dims_str}

Then select {FLEX_DIMENSIONS_PER_AGENT} FLEX dimensions — the factors you find most critical in THIS specific deal that are not already covered by the core dimensions.

Each rationale must include at least one «verbatim quote» [File, p. N] or the words NOT IN DOCUMENTS.

Return your analysis in EXACTLY this format:

CORE_SCORES:
[dimension name] | [score {score_hint}] | [one sentence rationale with «quote»]
[repeat for all 5 core dimensions]

FLEX_SCORES:
[dimension name] | [score {score_hint}] | [one sentence rationale with «quote»]
[repeat for {FLEX_DIMENSIONS_PER_AGENT} flex dimensions]

INITIAL_VOTE: [INVEST | PASS]

THESIS:
[One paragraph: your core bull or bear thesis on this deal, with «quotes»]

KEY_QUESTIONS:
1. [question for other agents]
2. [question for other agents]
3. [question for other agents]
4. [question for other agents - optional]
5. [question for other agents - optional]"""

        self._phase1_call = True
        try:
            sc, raw = self._call_parsed(
                prompt, self._parse_scorecard,
                "need ≥5 'name | score | rationale' lines under CORE_SCORES and an INITIAL_VOTE line",
            )
        finally:
            self._phase1_call = False
        if sc is None:
            raise AgentOutputParseError(
                f"{self.AGENT_NAME}: scorecard unparseable after retry. Last output:\n{raw[:2000]}"
            )
        self.scorecard = sc
        return sc

    def _parse_scorecard(self, raw: str) -> Optional[Scorecard]:
        """Parse the structured scorecard text. Returns None if the format was not followed."""
        dimensions: list[ScorecardDimension] = []
        vote = parse_vote(raw, "INITIAL_VOTE")
        thesis_lines: list[str] = []
        questions: list[str] = []

        mode = None
        for rawline in raw.splitlines():
            line = clean_line(rawline)
            if not line:
                continue
            upper = line.upper()
            if upper.startswith("CORE_SCORES"):
                mode = "core"
            elif upper.startswith("FLEX_SCORES"):
                mode = "flex"
            elif upper.startswith(("OVERALL_SCORE", "INITIAL_VOTE")):
                mode = None
            elif upper.startswith("THESIS"):
                mode = "thesis"
                rest = line.split(":", 1)[1].strip() if ":" in line else ""
                if rest:
                    thesis_lines.append(rest)
            elif upper.startswith("KEY_QUESTIONS"):
                mode = "questions"
            elif mode in ("core", "flex") and "|" in line:
                parts = [p.strip() for p in line.strip("|").split("|")]
                if len(parts) >= 3:
                    ok, score = parse_score(parts[1])
                    if not ok:
                        continue   # header row / separator / malformed
                    if score is None and not self.ALLOW_NA:
                        score = SCORE_MIN
                    dimensions.append(ScorecardDimension(
                        name=parts[0].strip("[]"),
                        score=score,
                        rationale=" | ".join(parts[2:]),
                        is_flex=(mode == "flex"),
                    ))
            elif mode == "thesis":
                thesis_lines.append(line)
            elif mode == "questions" and line[0].isdigit():
                q = line.lstrip("0123456789.) ").strip()
                if q:
                    questions.append(q)

        n_core = sum(1 for d in dimensions if not d.is_flex)
        if n_core < 5 or vote is None:
            return None
        sc = Scorecard(
            agent_name=self.AGENT_NAME,
            dimensions=dimensions,
            overall_score=0.0,
            thesis=" ".join(thesis_lines),
            initial_vote=vote,  # type: ignore[arg-type]
            key_questions=questions[:5],
        )
        sc.recompute_overall()
        return sc

    # ── Phase 2 ────────────────────────────────────────────────────────────────

    def opening_statement(
        self,
        briefing: DealBriefing,
        prior_statements: Optional[list[DebateStatement]] = None,
    ) -> DebateStatement:
        """Phase 2: Deliver opening statement based on scorecard (and prior openings, if given)."""
        self.bind(briefing)
        assert self.scorecard is not None, "Call analyze() before opening_statement()"
        prior = ""
        if prior_statements:
            prior = "\n\nOpening statements already delivered by the panel:\n" + fit_tail("\n\n".join(
                f"--- {s.agent_name} ---\n{s.content}" for s in prior_statements
            )) + "\n\nRespond to the strongest of these where relevant."
        prompt = f"""Based on your scorecard for {briefing.company_name}, deliver your opening statement to the panel.

Your scorecard summary:
{self.scorecard.to_text()}{prior}

Write a compelling 2-3 paragraph opening statement that:
1. States your initial vote ({self.scorecard.initial_vote}) and overall score ({self.scorecard.overall_score}/10)
2. Presents your 2-3 most important findings, each with a «verbatim quote» [File, p. N]
3. Poses your most critical question to the panel

Be direct, confident, and grounded in the documents.

{ARGUMENT_FORMAT}"""

        content = self._call(self.system_prompt, prompt)
        return DebateStatement(agent_name=self.AGENT_NAME, phase="opening", content=content)

    # ── Phase 3 ────────────────────────────────────────────────────────────────

    def cross_examine(
        self,
        briefing: DealBriefing,
        round_num: int,
        other_statements: list[DebateStatement],
        other_scorecards: list[Scorecard],
    ) -> DebateStatement:
        """Phase 3: Challenge other agents you disagree with."""
        self.bind(briefing)
        others_text = fit_tail("\n\n".join(
            f"--- {s.agent_name} ---\n{s.content}" for s in other_statements
        ))
        prior_label = "opening statements" if round_num == 1 else f"Round {round_num - 1}"
        prompt = f"""Cross-examination round {round_num} for {briefing.company_name}.

Other agents' statements from the {prior_label}{_CHECKED}:
{others_text}

Your scorecard: {self.scorecard.to_text() if self.scorecard else 'N/A'}

Identify the 1-2 agents whose analysis you most disagree with. Challenge them with:
1. A specific claim they made that you dispute{" (claims flagged NOT FOUND are fair game)" if SHOW_CITATION_CHECKS else ""}
2. Your counter-evidence from the deal documents, as «verbatim quotes»
3. A direct question they must answer

Then briefly respond to any challenges directed at you in the {prior_label}.
If you change your view on any dimension, add a REVISE: line.
Keep your response focused and under 300 words (quotes and the structure block excluded).
Claims in other statements carry IDs like GROW.R1.C2 — use them as ATTACK targets.

{ARGUMENT_FORMAT}"""

        content = self._call(self.system_prompt, prompt)
        return DebateStatement(
            agent_name=self.AGENT_NAME,
            phase=f"cross_exam_r{round_num}",
            content=content,
        )

    def rebut_socratic(
        self,
        critique_point: str,
        round_num: int,
        depth: int = 1,
    ) -> DebateStatement:
        """
        Respond to a specific logical critique from the Socratic Agent.
        The agent must defend or concede. At depth 3, they can declare a STALEMATE.
        """
        if depth == 1:
            context_line = "The Socratic Agent — a logic auditor with no stake in this deal — has publicly accused you of a logical fallacy."
        else:
            context_line = "The Socratic Agent has rejected your defense and doubled down on their accusation. You are in a heated back-and-forth."

        stalemate_option = ""
        if depth >= 3:
            stalemate_option = "3. STALEMATE: If neither of you will yield and you've reached an impasse, say so. Be highly opinionated, dismiss their rigid logic, and forcefully reassert your thesis. Start with: 'STALEMATE: [your explosive final word]'\n"

        prompt = f"""{context_line}

THE ACCUSATION / RETURN FIRE:
{critique_point}

You must respond. Your FIRST line must begin with exactly one of these labels:
1. DEFEND: Explain why the accusation is wrong. Show that your argument does NOT commit the named fallacy. Be specific; cite «verbatim quotes» for any factual point.
2. CONCEDE: Acknowledge the flaw and restate your argument in a logically sound way.
{stalemate_option}
You may NOT ignore the accusation or change the subject.
If you concede a point that affects a score, add a REVISE: line.
Keep your response under 150 words. Be precise."""

        content = self._call(self.system_prompt, prompt)
        return DebateStatement(
            agent_name=self.AGENT_NAME,
            phase=f"socratic_rebuttal_r{round_num}",
            content=content,
        )

    # ── Phase 4 ────────────────────────────────────────────────────────────────

    def risk_gauntlet_challenge(
        self,
        briefing: DealBriefing,
        risk_objections: list[str],
    ) -> DebateStatement:
        """Phase 4 (non-Risk agents only): Jointly address the Risk Agent's top objections."""
        self.bind(briefing)
        objections_text = "\n".join(f"  {i+1}. {obj}" for i, obj in enumerate(risk_objections))
        prompt = f"""RISK AGENT GAUNTLET — {briefing.company_name}

The Risk Agent (who holds veto power) has raised these objections:
{objections_text}

From your analytical perspective as {self.AGENT_NAME}, address each objection with:
1. Specific evidence from the deal documents that refutes or mitigates the concern — as «verbatim quotes» [File, p. N]
2. Your honest assessment of whether the objection is valid, partially valid, or overblown
3. Your stance on whether the objection is factually refutable based on current docs (no future conditional clauses are allowed)

If the documents do not contain mitigating evidence, say NOT IN DOCUMENTS for that objection.
{"The Risk Agent rules only on quotes that the citation checker finds in the documents." if SHOW_CITATION_CHECKS else ""}
The objections are OBJ1, OBJ2, OBJ3 (in the order listed) — use them as ATTACK targets.

{ARGUMENT_FORMAT}"""

        content = self._call(self.system_prompt, prompt)
        return DebateStatement(
            agent_name=self.AGENT_NAME,
            phase="gauntlet_response",
            content=content,
        )

    # ── Phase 5 ────────────────────────────────────────────────────────────────

    def cast_final_vote(
        self,
        briefing: DealBriefing,
        full_debate_transcript: str,
    ) -> tuple[VoteType, str]:
        """
        Phase 5: Cast final vote after seeing the debate.
        Returns (vote, rationale). Unparseable output after one retry → ABSTAIN.
        """
        self.bind(briefing)
        prompt = f"""After the full debate on {briefing.company_name}, cast your final vote.

Debate transcript{" (with citation checks)" if SHOW_CITATION_CHECKS else ""}:
{fit_tail(full_debate_transcript)}

Your current scorecard (including any revisions made during the debate):
{self.scorecard.to_text() if self.scorecard else 'N/A'}

State:
FINAL_VOTE: [INVEST | PASS]
RATIONALE: [2-3 sentences explaining your final position and any changes from your initial view]
DISSENT: [If you are in the minority, write your formal dissent in 1-2 sentences. Otherwise write "None."]"""

        vote, raw = self._call_parsed(
            prompt, lambda r: parse_vote(r, "FINAL_VOTE"), "missing 'FINAL_VOTE: INVEST|PASS' line"
        )
        final: VoteType = vote if vote else "ABSTAIN"  # type: ignore[assignment]
        self.final_vote = final
        return final, raw
