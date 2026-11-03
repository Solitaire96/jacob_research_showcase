"""
agents/risk_agent.py — The Skeptic ⚖️ VETO HOLDER
"""

from __future__ import annotations

import re
from typing import Optional

from agents.base_agent import BaseAgent, DebateStatement, VoteType, clean_line, fit_tail, parse_vote
from config import ENFORCE_GATE, FACT_LEDGER, GROUND_ALL_TURNS, RISK_BLOCK_ON_UNRESOLVED, SHOW_CITATION_CHECKS
from ingestion.deal_briefing import DealBriefing
from orchestrator.citations import CitationVerifier

_LOG_RE = re.compile(
    r"^(\d+)\s*[.):]?\s*\[?\s*(RESOLVED|PARTIAL|UNRESOLVED)\s*\]?\s*[|:\-–—]\s*(.*)$",
    re.IGNORECASE,
)


# Condition-dependent prompt fragments (see config.ABLATION)
_NF_TEXT = """ A NOT FOUND flag means the quoted wording is not in the
documents; the underlying fact may exist in different words, or may be invented."""
_DOCS_TEXT = """ You have the
documents — check each claim yourself, and quote the real passage if you rely on it."""
_GATE_TEXT = """
A RESOLVED or PARTIAL status without a quote that the checker finds in the documents is
automatically downgraded to UNRESOLVED."""
_LEDGER_TEXT = """DOCUMENTED CONFLICTS and failed ARITHMETIC CHECKS in the fact ledger are unreconciled facts. An objection
that depends on a conflicted figure is not RESOLVED unless the panel showed which value is right, with a quote.

"""

EVIDENCED = ("RESOLVED", "PARTIAL")   # statuses that claim the panel answered with evidence


def apply_gate(
    log: dict[int, tuple[str, str]],
    model_vote: str,
    has_evidence: dict[int, bool],
    block: bool,
) -> tuple[dict[int, tuple[str, str]], str, list[str]]:
    """
    The Risk gate as a pure function (exhaustively tested in tests/test_gate.py).

      For each objection i with status RESOLVED or PARTIAL and no verified quote:  status_i := UNRESOLVED
      final_vote = INVEST  iff  model_vote = INVEST  and  not (block and any status_i = UNRESOLVED)

    Invariant when block is on:  final_vote = INVEST  →  every objection is RESOLVED or PARTIAL
    and cites at least one quote found in the documents.
    """
    out = dict(log)
    notes: list[str] = []
    for i, (status, expl) in log.items():
        if status in EVIDENCED and not has_evidence.get(i, False):
            out[i] = ("UNRESOLVED", expl + f" ⚠ Downgraded from {status} by code: no supporting quote "
                                        "found in the documents, even after re-ask.")
            notes.append(f"Objection {i} downgraded {status} → UNRESOLVED (no verified quote after re-ask).")
    vote = model_vote
    unresolved = [i for i, (st, _) in sorted(out.items()) if st == "UNRESOLVED"]
    if block and vote == "INVEST" and unresolved:
        vote = "PASS"
        notes.append(
            f"INVEST overridden to PASS: objection(s) {', '.join(map(str, unresolved))} UNRESOLVED "
            "(veto rule: objections must be answered with documentary evidence)."
        )
    return out, vote, notes


class RiskAgent(BaseAgent):
    AGENT_NAME = "Risk Agent"
    VETO_HOLDER = True
    ALLOW_NA = False   # missing information is a risk, not a blank

    PERSONA = """You are a battle-hardened risk officer with 20 years across private equity,
credit, and litigation, now specializing in early-stage venture capital. Your mandate is to
find vectors through which this investment can fail, permanently impair capital, or create
unacceptable legal liability. You understand that early-stage companies are inherently risky
and lack perfect documentation (e.g., unsigned contracts, incomplete teams are normal). You
are not reflexively negative, but you demand rigorous, credible mitigation strategies for
existential risks. Your default is PASS; INVEST represents conviction that the existential
risks are adequately mitigated for a venture scale bet.

You hold unconditional veto power. The deal does not move forward without your approval."""

    CORE_DIMENSIONS = [
        "Regulatory & legal exposure",
        "Revenue / customer concentration risk",
        "Burn rate vs. runway adequacy",
        "Cap table structure & term sheet risks",
        "Key-person & succession risk",
    ]

    def extract_top_objections(self) -> list[str]:
        """
        The Risk Agent's top 3 objections for the gauntlet: its three lowest-scored
        dimensions on its CURRENT scorecard (i.e. after any REVISE lines it issued
        during cross-examination).
        """
        if not self.scorecard:
            return []
        scored = [d for d in self.scorecard.dimensions if d.score is not None]
        sorted_dims = sorted(scored, key=lambda d: d.score or 0)
        return [f"{d.name} (score: {d.score}/10): {d.rationale}" for d in sorted_dims[:3]]

    # ── Phase 4 ruling ────────────────────────────────────────────────────────

    @staticmethod
    def _parse_ruling(raw: str, n: int) -> Optional[dict]:
        vote = parse_vote(raw, "FINAL_VOTE")
        log: dict[int, tuple[str, str]] = {}
        rationale_lines: list[str] = []
        in_rat = False
        for rawline in raw.splitlines():
            line = clean_line(rawline)
            if not line:
                continue
            m = _LOG_RE.match(line)
            if m and not in_rat:
                log[int(m.group(1))] = (m.group(2).upper(), m.group(3).strip())
                continue
            if line.upper().startswith("RATIONALE"):
                in_rat = True
                rest = line.split(":", 1)[1].strip() if ":" in line else ""
                if rest:
                    rationale_lines.append(rest)
            elif in_rat:
                rationale_lines.append(line)
        if vote is None or any(i not in log for i in range(1, n + 1)):
            return None
        return {"vote": vote, "log": log, "rationale": " ".join(rationale_lines)}

    @staticmethod
    def _unsupported_resolutions(parsed: dict, verifier: CitationVerifier) -> dict[int, list]:
        """RESOLVED/PARTIAL entries whose explanation has no quote found in the documents."""
        bad = {}
        for i, (status, expl) in parsed["log"].items():
            if status in EVIDENCED:
                checks = verifier.check_text(expl)
                if not any(c.verified for c in checks):
                    bad[i] = checks
        return bad

    def evaluate_gauntlet_responses(
        self,
        briefing: DealBriefing,
        gauntlet_responses: list[DebateStatement],
        objections: list[str],
        verifier: CitationVerifier,
    ) -> tuple[VoteType, str, list[tuple[str, str]], list[str]]:
        """
        Phase 4 final step. Returns (vote, rationale, objection_log, enforcement_notes).

        objection_log: list of (objection, "[STATUS] explanation").
        enforcement_notes: anything the code changed about the model's ruling.
        """
        self.bind(briefing)
        n = len(objections)
        responses_text = fit_tail("\n\n".join(f"--- {s.agent_name} ---\n{s.content}" for s in gauntlet_responses))
        objections_text = "\n".join(f"  {i+1}. {obj}" for i, obj in enumerate(objections))

        prompt = f"""RISK AGENT FINAL RULING — {briefing.company_name}

Your original objections:
{objections_text}

Panel responses to your objections{" (each followed by an automated citation check)" if SHOW_CITATION_CHECKS else ""}:
{responses_text}

Evaluate each objection response STRICTLY. In early-stage venture, perfect documentary
evidence may not exist. However, you must evaluate whether the panel has presented a
highly credible, logical mitigation that offsets the risk, grounded in the documents.
Vague opinions do NOT resolve a risk concern.{_NF_TEXT if SHOW_CITATION_CHECKS else ""}{_DOCS_TEXT if GROUND_ALL_TURNS else ""}

For each of your {n} objections, classify it as:
- RESOLVED: Adequately addressed for an early-stage company.
- PARTIAL: Partially addressed; material unmitigated risk remains.
- UNRESOLVED: No satisfactory mitigation or documentary evidence was provided.
RESOLVED and PARTIAL both claim the panel answered with documentary evidence, so each MUST
include at least one «verbatim quote» [File, p. N] from the documents supporting that status.{_GATE_TEXT if ENFORCE_GATE else ""}

Then issue your FINAL VERDICT.

{_LEDGER_TEXT if FACT_LEDGER else ""}
MOVEMENT RULE: You may move from PASS → INVEST only if the existential risks have been
mitigated with documentary evidence. Conditional votes are not allowed — decide on current facts alone.
{"An INVEST vote with any objection UNRESOLVED is overridden to PASS." if RISK_BLOCK_ON_UNRESOLVED else ""}

Format your response EXACTLY as (one line per objection):

OBJECTION_LOG:
{chr(10).join(f"{i}. [RESOLVED | PARTIAL | UNRESOLVED] | [brief explanation, with «quote» unless UNRESOLVED]" for i in range(1, n + 1))}

FINAL_VOTE: [INVEST | PASS]
RATIONALE: [2-3 sentences explaining your final verdict]"""

        notes: list[str] = []
        parsed, raw = self._call_parsed(
            prompt, lambda r: self._parse_ruling(r, n),
            f"need OBJECTION_LOG lines 1-{n} as 'N. STATUS | explanation' and a FINAL_VOTE line",
        )
        if parsed is None:
            notes.append("Risk ruling unparseable after retry — defaulted to PASS (veto holder default).")
            self.final_vote = "PASS"
            self.evidence_by_objection, self.model_vote = {}, None
            return "PASS", "Ruling could not be parsed.\n\n" + raw, [
                (obj, "[UNRESOLVED] (ruling unparseable)") for obj in objections
            ], notes

        # ── Enforce: RESOLVED/PARTIAL require verified documentary evidence ──
        bad = self._unsupported_resolutions(parsed, verifier) if ENFORCE_GATE else {}
        if bad:
            detail = []
            for i, checks in bad.items():
                nf = [c.quote for c in checks if not c.verified]
                detail.append(
                    f"  Objection {i} ({parsed['log'][i][0]}): " + (
                        "no «quote» given." if not checks else
                        "quotes NOT FOUND in documents: " + "; ".join(f'"{q[:120]}"' for q in nf)
                    )
                )
            followup = (
                f"{prompt}\n\n---\nYour ruling was:\n{raw}\n\n"
                "The citation checker rejected these statuses (no quote found in the documents):\n"
                + "\n".join(detail) +
                "\n\nRe-issue your complete ruling in the same format. For each of those objections, "
                "either quote the documents verbatim to support the status, or mark it UNRESOLVED. "
                "Reconsider your FINAL_VOTE accordingly."
            )
            raw2 = self._call(self.system_prompt, followup)
            parsed2 = self._parse_ruling(raw2, n)
            notes.append(
                f"Objection(s) {', '.join(map(str, bad))} claimed RESOLVED/PARTIAL without verifiable "
                f"evidence; the Risk Agent was asked to re-rule."
            )
            if parsed2 is not None:
                parsed, raw = parsed2, raw2

        has_evidence = {
            i: any(c.verified for c in verifier.check_text(expl)) for i, (_, expl) in parsed["log"].items()
        }
        if ENFORCE_GATE:
            parsed["log"], vote, gate_notes = apply_gate(
                parsed["log"], parsed["vote"], has_evidence, RISK_BLOCK_ON_UNRESOLVED,
            )
            notes.extend(gate_notes)
        else:
            vote = parsed["vote"]
        self.evidence_by_objection = has_evidence   # logged for analysis in every condition
        self.model_vote = parsed["vote"]             # before any gate override

        objection_log = [
            (objections[i - 1], f"[{st}] {expl}") for i, (st, expl) in sorted(parsed["log"].items()) if i <= n
        ]
        final_vote: VoteType = "INVEST" if vote == "INVEST" else "PASS"
        self.final_vote = final_vote
        return final_vote, str(parsed["rationale"]), objection_log, notes
