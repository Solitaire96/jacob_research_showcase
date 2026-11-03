"""
tests/test_pipeline.py — Offline end-to-end and unit tests (no API calls).

Run from the project root:   python -m pytest -q tests
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests import fake_llm  # noqa: E402

DEAL = ROOT / "sample_deal" / "larkspur"          # fictional data room, see sample_deal/TRAPS.md


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def engine_env(monkeypatch, tmp_path):
    """Patch every LLM factory with the fake model and run in a temp dir."""
    import agents.base_agent
    import agents.quant_agent
    import agents.socratic_agent
    import orchestrator.facts
    for mod in (agents.base_agent, agents.quant_agent, agents.socratic_agent, orchestrator.facts):
        monkeypatch.setattr(mod, "make_llm", fake_llm.make_fake_llm)
    monkeypatch.chdir(tmp_path)
    fake_llm.reset()
    return tmp_path


_BRIEFING_CACHE: dict = {}


def _briefing():
    """Parse once per session; hand each test its own copy."""
    import copy
    from ingestion.deal_briefing import build_deal_briefing
    from ingestion.document_parser import collect_documents
    if "b" not in _BRIEFING_CACHE:
        _BRIEFING_CACHE["b"] = build_deal_briefing("Larkspur Soil Analytics", collect_documents(DEAL))
    return copy.deepcopy(_BRIEFING_CACHE["b"])


# ── Citation checker ───────────────────────────────────────────────────────────

SYNTHETIC = {"a.pdf": "The company has signed a $2.4M distribution agreement with the Tallgrass Growers Cooperative.\n"
                      "It has not signed the Corvina supply contract; the irrigaLLtion system de-\n"
                      "livers 3,400 L/h at peak to growers in the summer of 2026."}


@pytest.mark.parametrize("expected,quote", [
    (True, "signed a $2.4M distribution agreement with the Tallgrass Growers Cooperative"),
    (False, "signed a $24M distribution agreement with the Tallgrass Growers Cooperative"),          # changed figure
    (False, "It has signed the Corvina supply contract"),                          # dropped "not"
    (True, "the irrigation system delivers 3,400 L/h at peak"),                    # extraction noise
    (False, "the irrigation system delivers 6,800 L/h at peak"),
    (False, "signed a $2.4M distribution agreement with the Tallgrass Growers Cooperative and USDA"),  # appended entity
    (False, "signed a $2.4M distribution agreement with the Tallgrass Growers Council"),        # swapped entity
    (True, "has signed $2.4M distribution agreement with Tallgrass Growers Cooperative"),            # dropped filler
    (True, "It has not signed the Corvina supply contract"),
    (True, "delivers 3,400 L/h at peak ... in the summer of 2026"),                # ellipsis
    (False, "delivers 3,400 L/h at peak ... in the summer of 2027"),
])
def test_citation_checker_synthetic(expected, quote):
    from orchestrator.citations import CitationVerifier
    assert CitationVerifier(SYNTHETIC).check_quote(quote).verified is expected


@pytest.mark.parametrize("expected,quote", [
    (True, "Larkspur is an early entrant in continuous soil-carbon measurement for row-crop farms"),
    (True, "Maren Halvorson, a soil scientist, started the company in 2022"),   # deck watermark inside the span
    (False, "Maren Halvorson, a soil scientist, started the company in 2021"),
    (False, "Maren Halvorsen, a soil scientist, started the company in 2022"),
    (True, "Valuation Cap: $40,000,000 post-money"),
    (False, "Valuation Cap: $4,000,000 post-money"),
    (True, "The company has not signed any commercial supply agreements"),
    (False, "The company has signed any commercial supply agreements"),     # negation dropped
    (True, "totals $3.2M of venture capital plus $900K of non-dilutive state grants"),  # hyphen across a line
    (False, fake_llm.FAKE_1),
    (False, fake_llm.FAKE_2),
])
def test_citation_checker_real_docs(expected, quote):
    from orchestrator.citations import CitationVerifier
    assert CitationVerifier.from_briefing(_briefing()).check_quote(quote).verified is expected


# ── Parsing ────────────────────────────────────────────────────────────────────

def test_parse_helpers():
    from agents.base_agent import parse_vote, parse_stance, parse_revisions
    assert parse_vote("**FINAL_VOTE:** INVEST", "FINAL_VOTE") == "INVEST"
    assert parse_vote("FINAL_VOTE: [PASS]", "FINAL_VOTE") == "PASS"
    assert parse_vote("I lean positive", "FINAL_VOTE") is None
    assert parse_stance("DEFEND: I do not concede anything") == "DEFEND"
    assert parse_stance("**CONCEDE:** fair") == "CONCEDE"
    assert parse_stance("STALEMATE: no") == "STALEMATE"
    assert parse_revisions("- **REVISE:** Burn rate | 4/10 | reason") == [("Burn rate", 4, "reason")]


def test_socratic_parse_tolerates_markdown():
    from agents.socratic_agent import SocraticAgent
    out = SocraticAgent.parse_critiques("**AGENT: Growth Agent**\nBad.\n\nAGENT: [Moat Agent]\nWorse.\n**VERDICT:** meh")
    assert out == [("Growth Agent", "Bad."), ("Moat Agent", "Worse.")]
    assert SocraticAgent.parse_defense_ruling("**CONCEDE:** ok") == "CONCEDE"
    assert SocraticAgent.parse_defense_ruling("PRESS: no") == "PRESS"


def test_quant_machine_check():
    from agents.quant_agent import QuantAgent, safe_eval
    from orchestrator.citations import CitationVerifier
    v = CitationVerifier({"f.pdf": "Revenue was 410,000 in 2025 and 2,050,000 in 2026."})
    ok = QuantAgent.machine_check(
        "VERDICT: [VERIFIED]\na = 410000 | «Revenue was 410,000 in 2025»\n"
        "b = 2050000 | «2,050,000 in 2026»\nCALCULATION: b / a\nRESULT: 5", v)
    assert "[VERIFIED]" in ok and "✓" in ok
    bad_math = QuantAgent.machine_check(
        "VERDICT: [VERIFIED]\na = 410000 | «Revenue was 410,000 in 2025»\n"
        "b = 2050000 | «2,050,000 in 2026»\nCALCULATION: b / a\nRESULT: 4", v)
    assert "[ARITHMETIC ERROR]" in bad_math
    bad_quote = QuantAgent.machine_check(
        "VERDICT: [VERIFIED]\na = 410000 | «Revenue was 9,999,999 in 2025»\n"
        "b = 2050000 | «2,050,000 in 2026»\nCALCULATION: b / a\nRESULT: 5", v)
    assert "[UNVERIFIABLE]" in bad_quote
    with pytest.raises(ValueError):
        safe_eval("__import__('os').system('echo hi')", {})


def test_docx_and_recursive_collection():
    from ingestion.document_parser import collect_documents, parse_document
    files = collect_documents(DEAL)
    names = {f.name for f in files}
    assert any(n.endswith(".docx") for n in names), names    # subfolder .docx included
    docx_file = next(f for f in files if f.suffix == ".docx")
    chunks = parse_document(docx_file)
    assert chunks and chunks[0].source_type == "legal" and len(chunks[0].text) > 1000


def test_fingerprint_changes_with_content(tmp_path):
    from ingestion.deal_briefing import fingerprint_documents
    f = tmp_path / "a.txt"
    f.write_text("one")
    a = fingerprint_documents([f])
    f.write_text("two")
    assert fingerprint_documents([f]) != a


# ── End to end ────────────────────────────────────────────────────────────────

def test_fabricated_evidence_cannot_clear_veto(engine_env):
    """Risk Agent claims RESOLVED on invented evidence and insists after re-ask → vetoed."""
    from orchestrator.debate_engine import DebateEngine
    fake_llm.reset(risk_ruling="fabricated_then_insists")
    state = DebateEngine(skip_search=True).run(_briefing())

    assert state.verdict == "VETOED"
    assert state.risk_final_vote == "PASS"
    assert fake_llm.CALLS["risk_ruling"] == 2                       # re-asked once
    statuses = [s for _, s in state.objection_log]
    assert statuses[0].startswith("[UNRESOLVED]") and "Downgraded" in statuses[0]
    assert statuses[1].startswith("[UNRESOLVED]")
    assert statuses[2].startswith("[RESOLVED]")                     # real quote survives
    assert any("overridden to PASS" in n for n in state.risk_enforcement_notes)

    # Citation log caught the invented quotes everywhere they appeared
    missing = {r["quote"] for r in state.citation_log if not r["verified"]}
    assert fake_llm.FAKE_1 in missing and fake_llm.FAKE_2 in missing
    assert any(r["verified"] for r in state.citation_log)


def test_full_run_mechanics(engine_env):
    """Honest ruling → INVEST path; checks revisions, Quant calls, retries, ABSTAIN, annotations."""
    from orchestrator.debate_engine import DebateEngine
    fake_llm.reset(risk_ruling="honest_invest")
    state = DebateEngine(skip_search=True).run(_briefing())

    # Malformed scorecard was retried, not silently defaulted
    assert fake_llm.CALLS["score:Moat Agent"] == 2
    assert state.scorecards["Moat Agent"].initial_vote == "INVEST"
    # Overall score is computed, N/A excluded
    sent = state.scorecards["Sentiment Agent"]
    assert any(d.score is None for d in sent.dimensions)
    scored = [d.score for d in sent.dimensions if d.score is not None]
    assert sent.overall_score == round(sum(scored) / len(scored), 1)

    # REVISE applied and drove the Risk objections
    revs = {(r["agent"], r["dimension"]) for r in state.score_revisions}
    assert ("Risk Agent", "Revenue / customer concentration risk") in revs
    assert state.risk_objections[0].startswith("Revenue / customer concentration risk (score: 1/10)")
    assert ("Moat Agent", "IP, patent, & trade secret posture") in revs

    # Quant lifeline: inputs quoted, arithmetic re-computed in code
    assert len(state.quant_calls) == 2
    r1 = "\n".join(s.content for s in state.cross_exam_rounds[0])
    r2 = "\n".join(s.content for s in state.cross_exam_rounds[1])
    assert "[VERIFIED]" in r1 and "✓" in r1
    assert "[ARITHMETIC ERROR]" in r2

    # Every agent statement carries a citation annotation
    for s in state.opening_statements + state.gauntlet_responses:
        assert "[Citation check:" in s.content

    # "DEFEND: I do not concede" was not treated as a concession → Socratic evaluated it
    assert fake_llm.CALLS["rebut:Growth Agent"] >= 2

    # Unparseable vote → ABSTAIN after one retry, counted as not-INVEST
    assert fake_llm.CALLS["vote:Founder Agent"] == 2
    assert state.final_votes["Founder Agent"] == "ABSTAIN"
    assert state.verdict == "INVEST"            # Risk INVEST + 4/5 panel INVEST ≥ 3

    # Completed run leaves no resumable checkpoint
    assert not any(Path("output/checkpoints").glob("*.pkl"))


def test_resume_after_crash_restores_scorecards(engine_env):
    """Crash in Phase 3, then resume: agents must get their scorecards back (old bug: Risk had no objections)."""
    from orchestrator.debate_engine import DebateEngine
    fake_llm.reset(risk_ruling="honest_invest", crash_in_phase3=True)
    with pytest.raises(RuntimeError, match="simulated API failure"):
        DebateEngine(skip_search=True).run(_briefing())
    assert list(Path("output/checkpoints").glob("*.pkl"))

    fake_llm.SCENARIO["crash_in_phase3"] = False
    calls_before = sum(v for k, v in fake_llm.CALLS.items() if k.startswith("score:"))
    state = DebateEngine(skip_search=True).run(_briefing())
    calls_after = sum(v for k, v in fake_llm.CALLS.items() if k.startswith("score:"))
    assert calls_after == calls_before          # Phase 1 not re-run
    assert len(state.risk_objections) == 3
    assert state.verdict in ("INVEST", "PASS")


def test_report_generation(engine_env):
    from orchestrator.debate_engine import DebateEngine
    from output.report_generator import generate_report
    fake_llm.reset(risk_ruling="fabricated_then_honest")
    state = DebateEngine(skip_search=True).run(_briefing())
    md, js = generate_report(state, "out")
    text = Path(md).read_text()
    assert "Citation Audit" in text and "Quotes not found in the documents" in text
    assert "Enforced by code" in text
    import json
    data = json.loads(Path(js).read_text())
    assert data["citation_stats"] and data["document_fingerprint"]


def test_truncation_cuts_legal_boilerplate_first(monkeypatch, tmp_path):
    import ingestion.deal_briefing as db
    (tmp_path / "Fund - LPA.txt").write_text("legal " * 400)          # legal, sorts first alphabetically
    (tmp_path / "Zeta Diligence memo.txt").write_text("memo " * 100)  # narrative
    monkeypatch.setattr(db, "MAX_DOC_CHARS", 1500)
    b = db.build_deal_briefing("X", sorted(tmp_path.iterdir()))
    block = b.document_block()
    assert block.index("[FILE: Zeta Diligence memo.txt") < block.index("[FILE: Fund - LPA.txt")
    assert "memo memo" in block and "TRUNCATED" in block
    assert b.truncated_files == ["Fund - LPA.txt"]


# ── Checker fixes from the first live run (2026-10-21) ─────────────────────────

WATERMARKED = {"d.pdf": (
    "Probes logg\nEE\ned over 48,0\nww\n00 readings in 37 cTTounties during\n"
    "the pilot season. Installed 12in stainless probes. Subscription revenue $USD $410,000 "
    "$9,250,000. Hardware capex was only $1.8M, supporting $cc7M of revenue."
)}


@pytest.mark.parametrize("expected,quote", [
    (True, "over 48,000 readings in 37 counties"),             # number split by watermark
    (False, "over 84,000 readings in 37 counties"),            # …but a different number fails
    (False, "over 48,000 readings in 73 counties"),
    (True, "Installed 12‑in stainless probes"),                         # 12-in ≡ 12in, U+2011 hyphen
    (True, "Subscription revenue $USD … 9,250,000"),                    # $ adjacent to digits
    (True, "Hardware capex was only $1.8M"),
    (False, "Hardware capex was only $8.1M"),
    (False, "Hardware capex was only $1.8B"),                           # unit change fails
    (False, "Hardware capex was only $18M"),                            # decimal shift fails (bug fixed in this port)
    (False, "Subscription revenue $USD … 92,500,000"),
])
def test_checker_watermark_and_units(expected, quote):
    from orchestrator.citations import CitationVerifier
    assert CitationVerifier(WATERMARKED).check_quote(quote).verified is expected


def test_curly_quotes_with_citation_are_checked():
    from orchestrator.citations import CitationVerifier
    v = CitationVerifier(SYNTHETIC)
    text = ('It says “signed a $2.4M distribution agreement with the Tallgrass Growers Cooperative” [a.pdf, p.1] and '
            '“signed a $4.8M distribution agreement with the Tallgrass Growers Council” [a.pdf, p.1]. A “scare quote” is ignored.')
    checks = v.check_text(text)
    assert [c.verified for c in checks] == [True, False]


def test_dimension_evidence_flags(engine_env):
    from orchestrator.debate_engine import DebateEngine
    fake_llm.reset(risk_ruling="honest_invest")
    state = DebateEngine(skip_search=True).run(_briefing())
    evid = {d.evidence for sc in state.scorecards.values() for d in sc.dimensions}
    assert {"found", "none"} <= evid           # fake scorecards cite REAL_1 / "NOT IN DOCUMENTS"
    assert "[evidence: none]" in state.scorecards["Growth Agent"].to_text()


def test_truncation_keeps_small_legal_files_before_large_ones(monkeypatch, tmp_path):
    import ingestion.deal_briefing as db
    (tmp_path / "A Offering Documents.txt").write_text("boilerplate " * 500)   # big legal, sorts first
    (tmp_path / "B Term Sheet.txt").write_text("valuation cap 300M " * 10)     # small legal
    monkeypatch.setattr(db, "MAX_DOC_CHARS", 1500)
    b = db.build_deal_briefing("X", sorted(tmp_path.iterdir()), compress=False)
    block = b.document_block()
    assert "valuation cap 300M" in block
    assert b.truncated_files == ["A Offering Documents.txt"]



# ── Round 5: parallel phases, "__" markdown, single in-progress report ─────────

def _transcript_signature(state):
    """Order-sensitive summary of everything agents said."""
    parts = [s.agent_name + ":" + s.content for s in state.opening_statements]
    for rnd in state.cross_exam_rounds:
        parts += [s.agent_name + ":" + s.content for s in rnd]
    parts += [s.agent_name + ":" + s.content for s in state.gauntlet_responses]
    return parts, dict(state.final_votes), [(r["agent"], r["dimension"]) for r in state.score_revisions]


def test_parallel_phases_same_result_as_sequential(engine_env, monkeypatch):
    import orchestrator.debate_engine as de
    from orchestrator.debate_engine import DebateEngine
    results = {}
    for workers in (1, 6):
        monkeypatch.setattr(de, "PARALLEL_WORKERS", workers)
        fake_llm.reset(risk_ruling="honest_invest", delay=0.02)
        state = DebateEngine(skip_search=True, fresh=True).run(_briefing())
        results[workers] = (_transcript_signature(state), fake_llm.CONCURRENCY["max"])
    assert results[1][0] == results[6][0]          # identical transcript, order, votes, revisions
    assert results[1][1] == 1                      # sequential really is sequential
    assert results[6][1] >= 5                      # phases actually ran concurrently


def test_double_underscore_markdown_in_quotes():
    from orchestrator.citations import CitationVerifier
    v = CitationVerifier({"a.pdf": "Larkspur has more than 48,000 acres under measurement."})
    assert v.check_quote("Larkspur has __more than 48,000 acres__ under measurement").verified
    assert v.check_quote("Larkspur has _more than 48,000 acres_ under measurement").verified
    assert not v.check_quote("Larkspur has __more than 84,000 acres__ under measurement").verified


def test_single_in_progress_report_removed_after_final(engine_env):
    from orchestrator.debate_engine import DebateEngine
    from output.report_generator import generate_report
    fake_llm.reset(risk_ruling="honest_invest")
    state = DebateEngine(skip_search=True).run(_briefing())
    reports = Path("output/reports")
    assert [p.name for p in reports.glob("*_IN_PROGRESS.md")] == ["larkspur_soil_analytics_IN_PROGRESS.md"]
    assert not list(reports.glob("*_INTERMEDIATE_*"))
    generate_report(state, "output/reports")
    assert not list(reports.glob("*_IN_PROGRESS.md"))


# ── Round 6: Quant value binding, proper-noun rule ─────────────────────────────

def test_quant_input_value_must_be_in_its_quote():
    from agents.quant_agent import QuantAgent
    from orchestrator.citations import CitationVerifier
    v = CitationVerifier({"f.pdf": "Revenue was 410,000 in 2025. It received a $0.9M state innovation grant."})
    bad = QuantAgent.machine_check(
        "VERDICT: [VERIFIED]\na = 9999999 | «Revenue was 410,000 in 2025»\nCALCULATION: a * 2\nRESULT: 19999998", v)
    assert "[UNVERIFIABLE]" in bad and "not stated in their own quotes" in bad
    ok = QuantAgent.machine_check(
        "VERDICT: [VERIFIED]\na = 900000 | «received a $0.9M state innovation grant»\nCALCULATION: a * 2\nRESULT: 1800000", v)
    assert "[VERIFIED]" in ok


@pytest.mark.parametrize("expected,quote", [
    (False, "Maren Halvorsen, a soil scientist, started the company"),
    (False, "Maren Halverson, a soil scientist, started the company"),
    (True, "Maren Halvorson, a soil scientist, started the company"),   # noisy first copy, clean name elsewhere
    (False, "Quilback Ventures led the pre-seed round"),
    (True, "Quillback Ventures led the pre-seed round"),
])
def test_proper_noun_swaps_rejected(expected, quote):
    from orchestrator.citations import CitationVerifier
    v = CitationVerifier({"a.pdf": "Maren Halvccorson, a soil scientist, started the company. "
                                   "Later: Maren Halvorson, CEO. Quillback Ventures led the pre-seed round."})
    assert v.check_quote(quote).verified is expected


def test_linebreak_hyphenated_negation_compound():
    from orchestrator.citations import CitationVerifier
    v = CitationVerifier({"doc.pdf": "Funding so far totals $3.2M of venture capital plus $900K of non-\ndilutive state grants."})
    assert v.check_quote("totals $3.2M of venture capital plus $900K of non-dilutive state grants").verified
    assert not v.check_quote("totals $3.2M of venture capital plus $900K of dilutive state grants").verified


def test_sentinel_not_in_documents_excluded_from_quotes():
    from orchestrator.citations import CitationVerifier
    v = CitationVerifier({"doc.pdf": "Sample text here."})
    checks = v.check_text("«NOT IN DOCUMENTS» [doc.pdf, p. 1] and «not in documents»")
    assert len(checks) == 0


# ── Round 7: fact ledger + deal math in the pipeline ────────────────────────────

def test_ledger_phase_reaches_every_agent_and_report(engine_env):
    from orchestrator.debate_engine import DebateEngine
    from output.report_generator import generate_report
    fake_llm.reset(risk_ruling="honest_invest")
    state = DebateEngine(skip_search=True).run(_briefing())
    led = state.ledger
    assert fake_llm.CALLS["facts"] == 1
    assert len(led.facts) == 8 and len(led.rejected) == 2
    assert [c.key for c in led.conflicts] == ["runway_months"]
    assert state.deal_math.data["entries"][0]["entry"] == 40_000_000
    # every panel/risk/quant call after Phase 0 carried the ledger
    n_doc_calls = len(fake_llm.PROMPT_CHARS) - fake_llm.CALLS["facts"] - fake_llm.CALLS["socratic_round"] \
        - fake_llm.CALLS["socratic_eval"]
    assert fake_llm.CALLS["saw_ledger"] == n_doc_calls
    md, js = generate_report(state, "out")
    text = Path(md).read_text()
    assert "## 📒 Fact Ledger" in text and "## 🧮 Deal Math" in text and "CONFLICT runway_months" in text


def test_argument_audit_in_pipeline(engine_env):
    from orchestrator.debate_engine import DebateEngine
    from output.report_generator import generate_report
    fake_llm.reset(risk_ruling="honest_invest")
    state = DebateEngine(skip_search=True).run(_briefing())
    status = state.args.compute()
    assert status["GROW.OPEN.C2"] == "OUT"     # invented grounds, defeated by a supported Risk claim
    assert status["RISK.OPEN.C1"] == "IN"      # attacked only by an unsupported claim → not a defeat
    assert status["OBJ1"] == "OUT"             # supported panel rebuttal of a supported objection
    assert len(state.args.dangling) == 1       # attack on a nonexistent claim ignored and recorded
    # agents saw global IDs in prior statements
    assert any("GROW.OPEN.C1" in s.content for s in state.opening_statements)
    assert state.verdict == "INVEST"           # audit never changes the verdict
    text = Path(generate_report(state, "out")[0]).read_text()
    assert "## 🕸 Argument Audit" in text and "OBJ1: **OUT**" in text

def test_line_break_hyphen_compound_found_but_not_notable():
    """Live run, 2026-10-21: 'non-\\ndilutive' in the memo vs 'non-dilutive' in the quote."""
    from orchestrator.citations import CitationVerifier
    v = CitationVerifier({"m.pdf": "Funding so far totals $3.2M of venture capital plus $900K of non-\n"
                                   "dilutive state grants. The team is notable for speed."})
    assert v.check_quote("totals $3.2M of venture capital plus $900K of non-dilutive state grants").verified
    assert not v.check_quote("totals $3.2M of venture capital plus $900K of dilutive state grants").verified
    assert not v.check_quote("The team is not able for speed").verified


def test_not_in_documents_sentinel_is_not_a_quote():
    from orchestrator.citations import CitationVerifier
    v = CitationVerifier({"a.pdf": "Revenue was 410,000 in 2025."})
    assert v.check_text("Burn: «NOT IN DOCUMENTS». Revenue: «Revenue was 410,000 in 2025»") \
        and len(v.check_text("Burn: «NOT IN DOCUMENTS».")) == 0


QUALIFIER_AND_QUALITY_DOC = {
    "doc.pdf": (
        "The initial pilot rollout in Q3 was a partial success across the five regional test sites.\n"
        "Management projects reaching full operating profitability in 2027 only if the Series B closes.\n"
        "No commercial deployment may begin before any approval decision from the state environmental regulator.\n"
        "Annual net dollar retention was weak across the enterprise cohort.\n"
        "Self-serve bookings grew in 2023\n117\nbut collapsed in 2024 after the pricing change.\n"
        "The European subsidiary remains unprofitable under current unit economics.\n"
        "The Q4 rollout schedule is unlikely to hold without additional field testing.\n"
        "The convertible bridge notes are unsecured obligations of the parent company.\n"
        "The environmental permitting process is incomplete across two pilot counties.\n"
        '"Every member of the crew wanted to be among the boarding party"\n'
        '"each member of the crew raced to his assigned task without delay."\n'
        '"in their loyalty, and with Dennis Brooke they could and did feel safe."\n'
        'He stood sharply to attention. "Request transfer to position of Assistant Pile Driver, j.g., sir."\n'
        'If we get back to Earth," he amended. "You can\'t be Four\'s father.\n'
        '“Playing with that old faucet—” Mimicry. “‘My rad-ium pis-tol——’”\n'
        '—or, better yet, keep those people off the ships.\n'
        "The radium pistol’s weight made his wrist ache, but he clung to it\n"
        " tightly, knowing that he\n"
        " [117]\n"
        " could never cope with a Plant Man with a sword\n"
        " alone."
    )
}


@pytest.mark.parametrize("expected,quote", [
    # 5 synthetic qualifier/hedge and antonym cases
    (False, "The initial pilot rollout in Q3 was a success across the five regional test sites"),
    (False, "Management projects reaching full operating profitability in 2027 if the Series B closes"),
    (False, "No commercial deployment may begin before approval from the state environmental regulator"),
    (False, "Annual net dollar retention was strong across the enterprise cohort"),
    (False, "Self-serve bookings grew strongly in 2024 after the pricing change"),
    # 4 negative-prefix antonym cases (un-, in-, im-, ir-, il-, non-, dis-)
    (False, "The European subsidiary remains profitable under current unit economics"),
    (False, "The Q4 rollout schedule is likely to hold without additional field testing"),
    (False, "The convertible bridge notes are secured obligations of the parent company"),
    (False, "The environmental permitting process is complete across two pilot counties"),
    # 6 QuALITY false-positive cases (plus both "All members" variants from 63150)
    (False, "All members of the crew wanted to be among the boarding party"),
    (False, "All members of the crew raced to his assigned task without delay."),
    (False, "the crew's loyalty, and with Dennis Brooke they could and did feel safe."),
    (False, "He requested transfer to position of Assistant Pile Driver, j.g., sir"),
    (False, "If we get back to Earth, you can't be Four's father"),
    (False, "Playing with that old faucet—'My rad-ium pis-tol—'"),
    (False, "Or keep those people off the ships"),
    # Verbatim quotes spanning standalone page numbers ("117", "[117]") verify
    (True, "Self-serve bookings grew in 2023 but collapsed in 2024 after the pricing change"),
    (True, "The radium pistol’s weight made his wrist ache, but he clung to it tightly, knowing that he could never cope with a Plant Man with a sword alone."),
    # Exact/filler-only controls on the same passages still verify
    (True, "The initial pilot rollout in Q3 was a partial success across the five regional test sites"),
    (True, "Every member of the crew wanted to be among the boarding party"),
    (True, "Request transfer to position of Assistant Pile Driver, j.g., sir"),
])
def test_dropped_qualifiers_and_quality_paraphrases_rejected(expected, quote):
    from orchestrator.citations import CitationVerifier
    assert CitationVerifier(QUALIFIER_AND_QUALITY_DOC).check_quote(quote).verified is expected

