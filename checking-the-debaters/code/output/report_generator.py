"""
output/report_generator.py

Generates a structured Markdown + JSON report from a completed DebateState.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from orchestrator.debate_engine import DebateState

from config import OUTPUT_DIR


VERDICT_EMOJI = {
    "INVEST": "✅",
    "PASS": "❌",
    "VETOED": "🚫",
}

VOTE_ICON = {"INVEST": "✅", "PASS": "❌", "ABSTAIN": "⚠️"}

AGENT_EMOJI = {
    "Risk Agent": "🔴",
    "Financial Agent": "📊",
    "Growth Agent": "🚀",
    "Sentiment Agent": "🌐",
    "Moat Agent": "🏰",
    "Founder Agent": "🧬",
}


def intermediate_report_path(state: DebateState, output_dir: str = OUTPUT_DIR) -> str:
    safe_name = state.briefing.company_name.replace(" ", "_").lower()
    return os.path.join(output_dir, f"{safe_name}_IN_PROGRESS.md")


def generate_intermediate_report(state: DebateState, phase_name: str, output_dir: str = OUTPUT_DIR) -> str:
    """
    Generate an intermediate Markdown report during debate rounds.
    """
    os.makedirs(output_dir, exist_ok=True)
    md_path = intermediate_report_path(state, output_dir)   # one file, overwritten each phase

    lines: list[str] = []
    company = state.briefing.company_name
    date = datetime.now().strftime("%B %d, %Y %H:%M:%S")

    lines += [
        f"# ⏳ {company} — INTERMEDIATE DEBATE STATE ({phase_name})",
        f"**Date:** {date}  ",
        f"**Documents Analyzed:** {', '.join(state.briefing.documents_loaded)}",
        "",
        "---",
        "",
        "> **NOTE:** This is an intermediate artifact. The debate is still ongoing and final votes have not been tallied.",
        "",
        "## Current Transcript",
        "",
        "```text",
        state.full_transcript(),
        "```",
    ]

    md_content = "\n".join(lines)
    Path(md_path).write_text(md_content, encoding="utf-8")
    return md_path


def generate_report(state: DebateState, output_dir: str = OUTPUT_DIR) -> tuple[str, str]:
    """
    Generate a Markdown report and JSON data file.
    Returns (markdown_path, json_path).
    """
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = state.briefing.company_name.replace(" ", "_").lower()
    md_path = os.path.join(output_dir, f"{safe_name}_{timestamp}.md")
    json_path = os.path.join(output_dir, f"{safe_name}_{timestamp}.json")

    md_content = _build_markdown(state)
    json_content = _build_json(state)

    Path(md_path).write_text(md_content, encoding="utf-8")
    Path(json_path).write_text(json.dumps(json_content, indent=2), encoding="utf-8")

    # The in-progress snapshot is superseded by the final report.
    in_progress = Path(intermediate_report_path(state))
    if in_progress.exists():
        in_progress.unlink()

    return md_path, json_path


def _build_markdown(state: DebateState) -> str:
    lines: list[str] = []
    company = state.briefing.company_name
    verdict = state.verdict or "UNKNOWN"
    emoji = VERDICT_EMOJI.get(verdict, "❓")
    date = datetime.now().strftime("%B %d, %Y")

    lines += [
        f"# {emoji} {company} — VC Investment Analysis",
        f"**Date:** {date}  ",
        f"**Documents Analyzed:** {', '.join(state.briefing.documents_loaded)}",
        "",
        "---",
        "",
    ]

    # ── Verdict Banner ──
    if state.killed_by_veto:
        lines += [
            "## 🚫 DEAL VETOED BY RISK AGENT",
            "",
            f"> **Risk Agent Final Ruling:** {state.risk_final_rationale}",
            "",
            "> The Risk Agent holds unconditional veto power. This deal does not proceed.",
            "",
        ]
    else:
        risk_vote = state.risk_final_vote or "N/A"
        panel_pos = sum(
            1 for n, v in state.final_votes.items()
            if n != "Risk Agent" and v == "INVEST"
        )
        panel_total = len(state.final_votes) - 1

        lines += [
            f"## {emoji} Final Verdict: {verdict}",
            "",
            "```",
            f"{'═' * 50}",
            f"⚖️  RISK AGENT GATE: {risk_vote}   ← PASS = immediate kill",
            f"{'═' * 50}",
            f"Panel: {panel_pos}/{panel_total} positive votes",
            "```",
            "",
        ]

    # ── Fact ledger & deal math ──
    lines += _ledger_section(state)

    # ── Scorecards ──
    lines += ["## Agent Scorecards", ""]
    for agent_name, sc in state.scorecards.items():
        emo = AGENT_EMOJI.get(agent_name, "🤖")
        veto = " ⚖️ VETO" if agent_name == "Risk Agent" else ""
        lines += [
            f"### {emo} {agent_name}{veto}",
            f"**Overall (current, after revisions):** {sc.overall_score:.1f}/10 | **Initial Vote:** {sc.initial_vote}",
            "",
            "| Type | Dimension | Score | Evidence | Rationale |",
            "|------|-----------|-------|----------|-----------|",
        ]
        for d in sc.dimensions:
            tag = "FLEX" if d.is_flex else "CORE"
            rationale = d.rationale.replace("|", "\\|")
            ev = {"found": "✓ found", "not found": "⚠ not found", "none": "— none"}.get(getattr(d, "evidence", ""), "")
            lines.append(f"| {tag} | {d.name} | {d.score_text()} | {ev} | {rationale} |")
        lines += [
            "",
            f"**Thesis:** {sc.thesis}",
            "",
            "**Key Questions Raised:**",
        ]
        for q in sc.key_questions:
            lines.append(f"- {q}")
        lines.append("")

    # ── Risk Agent Objection Log ──
    if state.objection_log:
        lines += ["## ⚖️ Risk Agent Objection Log", ""]
        lines += ["| Objection | Status |", "|-----------|--------|"]
        for obj, status in state.objection_log:
            lines.append(f"| {_cell(obj)} | {_cell(status)} |")
        lines.append("")
    if state.risk_enforcement_notes:
        lines += ["**Enforced by code (not the model):**", ""]
        lines += [f"- {n}" for n in state.risk_enforcement_notes]
        lines.append("")

    # ── Score revisions ──
    if state.score_revisions:
        lines += ["## ✎ Score Revisions During Debate", ""]
        lines += ["| Agent | Dimension | Old | New | Phase | Reason |", "|---|---|---|---|---|---|"]
        for r in state.score_revisions:
            lines.append(f"| {r['agent']} | {_cell(r['dimension'])} | {r['old']} | {r['new']} | {r['phase']} | {_cell(r['reason'])} |")
        lines.append("")

    # ── Document compression ──
    comp = getattr(state.briefing, "compression", None)
    if comp is not None:
        raw_n = sum(len(c.text) for c in state.briefing.raw_chunks)
        new_n = sum(len(c.text) for c in state.briefing.all_chunks)
        lines += ["## 🗜 Document Compression (local, before sending)", "",
                  f"{raw_n:,} → {new_n:,} characters ({1 - new_n / max(raw_n, 1):.0%} removed). "
                  "Removed text is verbatim-deleted, never paraphrased; quotes are still checked "
                  "against the original documents.", "",
                  "| File | Removed | Characters | Detail |", "|---|---|---|---|"]
        for e in comp.entries:
            lines.append(f"| {_cell(e['file'])} | {e['step']} | {e['chars']:,} | {_cell(e['detail'])} |")
        lines.append("")
    elif state.briefing.raw_chunks is not None and getattr(state.briefing, "raw_chunks", None):
        lines += ["## 🗜 Document Compression", "", "_Disabled for this run (--no-compress)._", ""]

    # ── Scores without verified evidence ──
    unsupported = [
        (n, d) for n, sc in state.scorecards.items() for d in sc.dimensions
        if d.score is not None and getattr(d, "evidence", "found") in ("not found", "none")
    ]
    if unsupported:
        lines += ["## ⚠ Scores Without Verified Evidence", "",
                  "These scored dimensions cite no quote that was found verbatim in the documents.", "",
                  "| Agent | Dimension | Score | Evidence |", "|---|---|---|---|"]
        for n, d in unsupported:
            lines.append(f"| {n} | {_cell(d.name)} | {d.score_text()} | {d.evidence} |")
        lines.append("")

    # ── Argument audit ──
    lines += _argument_section(state)

    # ── Citation audit ──
    lines += _citation_section(state)

    # ── Final Votes ──
    if state.final_votes:
        lines += ["## Final Votes", ""]
        for name, vote in state.final_votes.items():
            emo = AGENT_EMOJI.get(name, "🤖")
            veto = " ⚖️" if name == "Risk Agent" else ""
            icon = VOTE_ICON.get(vote, "❓")
            lines.append(f"- {emo} **{name}**{veto}: {icon} {vote}")
        lines.append("")


    # ── Full Transcript ──
    lines += [
        "---",
        "",
        "## Full Debate Transcript",
        "",
        "```",
        state.full_transcript(),
        "```",
    ]

    return "\n".join(lines)


def _ledger_section(state: DebateState) -> list[str]:
    led = getattr(state, "ledger", None)
    if led is None:
        return []
    from orchestrator.facts import fmt_value
    out = ["## 📒 Fact Ledger", "",
           f"{len(led.facts)} facts verified in code (quote found in the documents and value stated in the quote); "
           f"{len(led.rejected)} extracted values rejected.", ""]
    if led.facts:
        out += ["| ID | Fact | Value | Context | Quote |", "|---|---|---|---|---|"]
        for f in led.facts:
            out.append(f"| {f.id} | {f.key} | {fmt_value(f.key, f.value)} | {_cell(f.context)} | {_cell(f.quote)} |")
        out.append("")
    if led.conflicts:
        out += ["**⚠ Documented conflicts** (same fact, different stated values; may be real or an artefact "
                "such as a rubric — check the quotes):", ""]
        out += [f"- {c.summary()}" for c in led.conflicts]
        out.append("")
    if led.rejected:
        out += ["<details><summary>Rejected extractions</summary>", ""]
        out += [f"- {r['key']} = {r['value']} — {r['reason']}: \"{_cell(r['quote'])[:160]}\"" for r in led.rejected]
        out += ["", "</details>", ""]
    dm = getattr(state, "deal_math", None)
    if dm is not None and (dm.lines or dm.checks):
        out += ["## 🧮 Deal Math (computed in code)", "", "```", dm.text(), "```", ""]
    return out


def _argument_section(state: DebateState) -> list[str]:
    g = getattr(state, "args", None)
    if g is None or not g.claims:
        return []
    rows = g.rows()
    cnt = {s: sum(r["status"] == s for r in rows) for s in ("IN", "OUT", "UNDEC")}
    grounded = sum(r["grounded"] for r in rows)
    defeats = sum(a.defeat for a in g.attacks)
    out = ["## 🕸 Argument Audit (does not affect the verdict)", "",
           f"{len(rows)} claims ({grounded} with verified grounds), {len(g.attacks)} attacks "
           f"({defeats} count as defeats). Standing under grounded semantics: "
           f"**{cnt['IN']} IN**, {cnt['OUT']} OUT, {cnt['UNDEC']} undecided.", "",
           "_An attack from a claim without verified grounds cannot defeat a claim with verified grounds. "
           "IN = every defeater is itself defeated; OUT = defeated by a claim that stands; "
           "UNDEC = unresolved (e.g. mutual attack)._", ""]
    objs = [r for r in rows if r["id"].startswith("OBJ")]
    if objs:
        out += ["**Risk objections in the argument graph** (compare with the Risk Agent's ruling above):", ""]
        out += [f"- {r['id']}: **{r['status']}**"
                + (f" — defeated by {', '.join(r['defeated_by'])}" if r["defeated_by"] else "")
                + (f" — attacked by {', '.join(r['attacked_by'])}" if r["attacked_by"] and not r["defeated_by"] else "")
                for r in objs]
        out.append("")
    out += ["| Claim | Agent | Status | Grounds | Claim text | Attacked by |", "|---|---|---|---|---|---|"]
    for r in rows:
        if r["id"].startswith("OBJ"):
            continue
        out.append(f"| {r['id']} | {r['agent']} | {r['status']} | {'✓' if r['grounded'] else '✗'} | "
                   f"{_cell(r['claim'])[:200]} | {_cell(', '.join(r['attacked_by'])) or '—'} |")
    out.append("")
    if g.dangling:
        out += [f"_{len(g.dangling)} attack(s) ignored (unknown target or missing BY claim)._", ""]
    if g.statements_without_block:
        out += [f"_No structured claims in: {', '.join(g.statements_without_block)}._", ""]
    return out


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _citation_stats(state: DebateState) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    for r in state.citation_log:
        s = stats.setdefault(r["agent"], {"quotes": 0, "verified": 0, "not_found": 0})
        s["quotes"] += 1
        s["verified" if r["verified"] else "not_found"] += 1
    return stats


def _citation_section(state: DebateState) -> list[str]:
    if not state.citation_log:
        return ["## 🔎 Citation Audit", "", "_No «quotes» were made._", ""]
    lines = ["## 🔎 Citation Audit", "",
             "Every «quote» was checked in code against the parsed documents.", "",
             "| Agent | Quotes | Found | Not found |", "|---|---|---|---|"]
    for agent, s in _citation_stats(state).items():
        lines.append(f"| {agent} | {s['quotes']} | {s['verified']} | {s['not_found']} |")
    missing = [r for r in state.citation_log if not r["verified"]]
    if missing:
        lines += ["", "**Quotes not found in the documents:**", ""]
        for r in missing:
            q = r["quote"] if len(r["quote"]) <= 200 else r["quote"][:197] + "..."
            cited = f" (cited as {r['cited_as']})" if r["cited_as"] else ""
            lines.append(f"- {r['agent']} [{r['phase']}]: \"{q}\"{cited}")
    lines.append("")
    return lines


def _run_meta() -> dict:
    import config
    import llm
    return {
        "ablation": config.ABLATION, "run_id": config.RUN_ID, "backend": config.LLM_BACKEND,
        "model": config.ACTIVE_MODEL, "prompt_version": config.PROMPT_VERSION,
        "flags": {k: getattr(config, k) for k in (
            "GROUND_ALL_TURNS", "SHOW_CITATION_CHECKS", "ENFORCE_GATE", "NUMBERS_IN_CODE",
            "FACT_LEDGER", "RISK_BLOCK_ON_UNRESOLVED", "SOCRATIC_REFEREE")},
        "usage": dict(llm.USAGE),
    }


def _build_json(state: DebateState) -> dict:
    return {
        "company": state.briefing.company_name,
        "date": datetime.now().isoformat(),
        "run": _run_meta(),
        "verdict": state.verdict,
        "killed_by_veto": state.killed_by_veto,
        "risk_gate": {
            "vote": state.risk_final_vote,
            "rationale": state.risk_final_rationale,
            "objection_log": [
                {"objection": obj, "status": status}
                for obj, status in state.objection_log
            ],
            "enforcement_notes": state.risk_enforcement_notes,
            "model_vote": getattr(state, "risk_model_vote", None),
            "has_verified_quote": {str(k): v for k, v in (getattr(state, "risk_evidence", None) or {}).items()},
        },
        "scorecards": {
            name: {
                "overall_score": sc.overall_score,
                "initial_vote": sc.initial_vote,
                "thesis": sc.thesis,
                "dimensions": [
                    {
                        "name": d.name,
                        "score": d.score,
                        "rationale": d.rationale,
                        "is_flex": d.is_flex,
                        "evidence": getattr(d, "evidence", ""),
                    }
                    for d in sc.dimensions
                ],
                "key_questions": sc.key_questions,
            }
            for name, sc in state.scorecards.items()
        },
        "fact_ledger": (None if state.ledger is None else {
            "facts": [vars(f) for f in state.ledger.facts],
            "conflicts": [c.summary() for c in state.ledger.conflicts],
            "rejected": state.ledger.rejected,
        }),
        "deal_math": (None if state.deal_math is None else {
            "lines": state.deal_math.lines, "checks": state.deal_math.checks,
        }),
        "argument_audit": (None if not state.args or not state.args.claims else {
            "claims": state.args.rows(),
            "attacks": [vars(a) for a in state.args.attacks],
            "dangling": state.args.dangling,
            "statements_without_block": state.args.statements_without_block,
        }),
        "final_votes": state.final_votes,
        "final_vote_rationales": state.final_vote_rationales,
        "score_revisions": state.score_revisions,
        "citation_stats": _citation_stats(state),
        "citation_log": state.citation_log,
        # Statements as the other agents saw them (citation annotations included where the condition shows them).
        "transcript": [
            {"agent": s.agent_name, "phase": s.phase, "content": s.content}
            for s in (list(state.opening_statements)
                      + [x for rnd in state.cross_exam_rounds for x in rnd]
                      + list(state.gauntlet_responses))
        ],
        "quant_calls": state.quant_calls,
        "search_summary": state.search_summary,
        "documents": state.briefing.documents_loaded,
        "documents_truncated": state.briefing.truncated_files,
        "compression": (state.briefing.compression.entries
                        if state.briefing.compression is not None else None),
        "document_fingerprint": state.briefing.fingerprint,
    }
