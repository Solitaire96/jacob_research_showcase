"""tests/test_facts.py — fact ledger and deal math (offline)."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orchestrator.deal_math import (  # noqa: E402
    compute_deal_math, exit_value_for_moic, moic, ownership, retention, runway,
)
from orchestrator.facts import Fact, Ledger, build_ledger, parse_fact_lines  # noqa: E402
from tests import fake_llm  # noqa: E402

_CACHE: dict = {}


def _briefing(deal: str, name: str):
    from ingestion.deal_briefing import build_deal_briefing
    from ingestion.document_parser import collect_documents
    if deal not in _CACHE:
        _CACHE[deal] = build_deal_briefing(name, collect_documents(ROOT / "sample_deal" / deal))
    return _CACHE[deal]


def _verifier(b):
    from orchestrator.citations import CitationVerifier
    return CitationVerifier.from_briefing(b)


# ── Ledger on the sample data room ─────────────────────────────────────────────

DEAL = ("larkspur", "Larkspur Soil Analytics")


def test_ledger_real_quotes():
    b = _briefing(*DEAL)
    led = build_ledger(parse_fact_lines(fake_llm.FACT_LINES), _verifier(b))
    got = {(f.key, f.value) for f in led.facts}
    assert ("valuation_cap", 40_000_000) in got
    assert ("round_size", 6_000_000) in got
    assert ("monthly_burn", 210_000) in got
    assert ("revenue:2026", 2_050_000) in got
    reasons = {r["key"]: r["reason"] for r in led.rejected}
    assert reasons["valuation_cap"] == "value is not stated in its quote"      # 4M vs quote's 40M
    assert reasons["cash_on_hand"] == "quote not found in the documents"        # invented
    assert [c.key for c in led.conflicts] == ["runway_months"]                  # memo 20 vs deck 14
    assert {f.value for f in led.conflicts[0].facts} == {20, 14}


def test_ledger_and_math_misread_figure():
    b = _briefing(*DEAL)
    lines = """FACT: round_size | 6000000 | seed | «Pricing: $34M pre-money, implying $40M post-money on the $6M seed» [IC memo, p. 2]
FACT: pre_money_valuation | 34000000 | seed | «Pricing: $34M pre-money, implying $40M post-money on the $6M seed» [IC memo, p. 2]
FACT: round_size | 40000000 | misread | «$40M to $60M in yearly revenue at maturity» [IC memo, p. 3]"""
    led = build_ledger(parse_fact_lines(lines), _verifier(b))
    assert {(f.key, f.value) for f in led.facts} >= {("round_size", 6e6), ("pre_money_valuation", 3.4e7)}
    # The misread is a real quote with the number in it — the ledger keeps it and flags the conflict,
    # with the quote, so agents can see it is revenue at maturity, not the round size.
    assert [c.key for c in led.conflicts] == ["round_size"]
    dm = compute_deal_math(led, this_year=2026)
    entries = [e["entry"] for e in dm.data["entries"]]
    assert 4.0e7 in entries                                                     # 34M + 6M
    e = next(e for e in dm.data["entries"] if e["entry"] == 4.0e7)
    assert math.isclose(e["required"][10], 4.0e7 * 10 / 0.64)


def test_ledger_cap_and_discount():
    b = _briefing(*DEAL)
    lines = """FACT: valuation_cap | 40000000 | SAFE post-money cap | «Valuation Cap: $40,000,000 post-money» [Term sheet, p. 1]
FACT: discount_pct | 0 | SAFE | «Discount: 0%» [Term sheet, p. 1]
FACT: round_size | 6000000 | SAFE | «Amount: $6,000,000» [Term sheet, p. 1]
FACT: valuation_cap | 60000000 | hallucinated | «valuation cap of $60M» [Term sheet, p. 1]"""
    led = build_ledger(parse_fact_lines(lines), _verifier(b))
    assert {(f.key, f.value) for f in led.facts} == {("valuation_cap", 4e7), ("discount_pct", 0), ("round_size", 6e6)}
    assert not led.conflicts and len(led.rejected) == 1


def test_ledger_separates_venture_raised_from_grants():
    b = _briefing(*DEAL)
    lines = """FACT: total_raised_prior | 3200000 | Venture funding | «Funding so far totals $3.2M of venture capital plus $900K of non-dilutive state grants» [IC memo, p. 2]
FACT: grants_non_dilutive | 900000 | Non-dilutive grants | «Funding so far totals $3.2M of venture capital plus $900K of non-dilutive state grants» [IC memo, p. 2]"""
    led = build_ledger(parse_fact_lines(lines), _verifier(b))
    assert {(f.key, f.value) for f in led.facts} == {
        ("total_raised_prior", 3_200_000),
        ("grants_non_dilutive", 900_000),
    }
    assert not led.conflicts and not led.rejected


# ── Deal math ──────────────────────────────────────────────────────────────────

def test_math_functions():
    assert retention(0.20, 2) == pytest.approx(0.64)
    assert ownership(1e6, 40e6) == pytest.approx(1 / 40)
    assert moic(1e9, 40e6, 0.64) == pytest.approx(1e9 * 0.64 / 40e6)
    assert exit_value_for_moic(10, 40e6, 0.64) == pytest.approx(6.25e8)
    assert runway(24e6, 1e6) == 24


def _led(*facts):
    return Ledger(facts=[Fact(f"F{i + 1}", k, v, c, f"q{i}") for i, (k, v, c) in enumerate(facts)])


def test_math_consistency_checks():
    # pre + round must reconcile with post
    dm = compute_deal_math(_led(("pre_money_valuation", 100e6, ""), ("round_size", 20e6, ""),
                                ("post_money_valuation", 150e6, "")), this_year=2026)
    assert any("pre-money" in c and "post-money" in c for c in dm.checks)
    ok = compute_deal_math(_led(("pre_money_valuation", 100e6, ""), ("round_size", 20e6, ""),
                                ("post_money_valuation", 120e6, "")), this_year=2026)
    assert not ok.checks
    # cash ÷ burn vs stated runway
    dm = compute_deal_math(_led(("cash_on_hand", 10e6, ""), ("monthly_burn", 1e6, ""),
                                ("runway_months", 24, ""), ("valuation_cap", 50e6, "")), this_year=2026)
    assert any("computed runway 10.0 months" in c for c in dm.checks)


def test_math_scenarios_and_labels():
    dm = compute_deal_math(_led(("valuation_cap", 40e6, ""), ("revenue:2025", 0.41e6, "actual"),
                                ("revenue:2027", 7.38e6, "projection"), ("revenue:2030", 60e6, "projection")),
                           this_year=2026)
    text = dm.text()
    assert "COMPANY PROJECTION" in text and "[A]" in text
    s = [x for x in dm.data["entries"][0]["scenarios"] if x["year"] == 2030 and x["multiple"] == 5]
    assert s and s[0]["moic"] == pytest.approx(60e6 * 5 * 0.64 / 40e6)          # 4.8×
    assert "97.6× revenue" in text                                                # 40M ÷ 0.41M actual


def test_nothing_computable_is_said_plainly():
    dm = compute_deal_math(_led(("employees", 42, "")), this_year=2026)
    assert "not computable" in dm.text()
