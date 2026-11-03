"""tests/test_arguments.py — argument audit: parsing, preferences, grounded semantics."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orchestrator.arguments import ArgumentGraph, Attack, Claim  # noqa: E402
from orchestrator.citations import CitationVerifier  # noqa: E402

V = CitationVerifier({"a.pdf": "The valuation cap is $40,000,000 post-money for this SAFE round."})


def _g(claims: dict[str, bool], attacks: list[tuple[str, str]]) -> dict[str, str]:
    g = ArgumentGraph()
    for cid, grounded in claims.items():
        g.claims[cid] = Claim(cid, "Risk Agent", "x", cid, grounded=grounded)
    for s, t in attacks:
        g.attacks.append(Attack(s, t, "REBUT", "", "Risk Agent", "x"))
    return g.compute()


def test_grounded_semantics_basics():
    assert _g({"A": True, "B": True}, [("A", "B")]) == {"A": "IN", "B": "OUT"}
    # reinstatement: C defeats A, so B stands again
    assert _g({"A": True, "B": True, "C": True}, [("A", "B"), ("C", "A")]) == {"A": "OUT", "B": "IN", "C": "IN"}
    # mutual attack between equally supported claims: undecided (sceptical semantics)
    assert _g({"A": True, "B": True}, [("A", "B"), ("B", "A")]) == {"A": "UNDEC", "B": "UNDEC"}
    # odd cycle
    assert set(_g({"A": True, "B": True, "C": True}, [("A", "B"), ("B", "C"), ("C", "A")]).values()) == {"UNDEC"}


def test_preference_unsupported_cannot_defeat_supported():
    assert _g({"A": False, "B": True}, [("A", "B")]) == {"A": "IN", "B": "IN"}      # attack, not defeat
    assert _g({"A": True, "B": False}, [("A", "B")]) == {"A": "IN", "B": "OUT"}
    assert _g({"A": False, "B": False}, [("A", "B")]) == {"A": "IN", "B": "OUT"}    # equal standing


def test_parse_assign_ids_and_rewrite():
    g = ArgumentGraph()
    text = g.add_statement("Growth Agent", "opening", (
        "Prose here.\n**CLAIMS:**\n"
        "C1: Cap is $40M | GROUNDS: «The valuation cap is $40,000,000 post-money» | INFERENCE: stated | DEFEATER: other cap\n"
        "C2: Insured | GROUNDS: «a grain cooperative purchase order» | INFERENCE: x | DEFEATER: y\nATTACKS:\nnone"), V, set())
    assert "GROW.OPEN.C1: Cap is $40M" in text and "GROW.OPEN.C2:" in text
    assert g.claims["GROW.OPEN.C1"].grounded and not g.claims["GROW.OPEN.C2"].grounded
    t2 = g.add_statement("Risk Agent", "cross_exam_r1", (
        "CLAIMS:\nC1: No insurance exists | GROUNDS: F2 | INFERENCE: ledger | DEFEATER: a policy\n"
        "ATTACKS:\nGROW.OPEN.C2 | UNDERMINE | BY C1 | quote not found\nNOPE.R1.C9 | REBUT | BY C1 | bad target"),
        V, {"F2"})
    assert "BY RISK.R1.C1" in t2
    assert g.claims["RISK.R1.C1"].grounded                       # ledger fact ID counts as grounds
    assert len(g.attacks) == 1 and len(g.dangling) == 1
    assert g.compute()["GROW.OPEN.C2"] == "OUT"


def test_missing_block_recorded_only_where_requested():
    g = ArgumentGraph()
    g.add_statement("Moat Agent", "cross_exam_r2", "just prose", V, set())
    g.add_statement("Moat Agent", "socratic_rebuttal_r2", "DEFEND: prose", V, set())
    assert g.statements_without_block == ["Moat Agent [cross_exam_r2]"]


def test_add_ruling_respects_evidence_preference_and_skips_unparseable():
    g = ArgumentGraph()
    g.add_objections(
        [
            "Burn risk «The valuation cap is $40,000,000 post-money»",
            "Moat risk",
            "Key-person risk",
        ],
        V,
        set(),
    )
    g.add_statement(
        "Financial Agent",
        "gauntlet_response",
        (
            "CLAIMS:\n"
            "C1: Burn is fine | GROUNDS: «The valuation cap is $40,000,000 post-money» | INFERENCE: ok | DEFEATER: none\n"
            "C2: Moat is fine | GROUNDS: «The valuation cap is $40,000,000 post-money» | INFERENCE: ok | DEFEATER: none\n"
            "ATTACKS:\n"
            "OBJ1 | REBUT | BY C1 | answered\n"
            "OBJ2 | REBUT | BY C2 | answered"
        ),
        V,
        set(),
    )
    # Before ruling: OBJ1 and OBJ2 are defeated by grounded gauntlet claims
    assert g.compute()["OBJ1"] == "OUT"
    assert g.compute()["OBJ2"] == "OUT"

    # After ruling:
    # - OBJ1 has a grounded [PARTIAL] ruling -> defeats FIN.G.C1, reinstating OBJ1 to IN
    # - OBJ2 has an unquoted [UNRESOLVED] ruling -> grounded=False, cannot defeat grounded FIN.G.C2, OBJ2 stays OUT
    # - OBJ3 has an unparseable line -> skipped (not added to g.claims)
    g.add_ruling(
        [
            ("Burn risk", "[PARTIAL] «The valuation cap is $40,000,000 post-money» does not disclose burn."),
            ("Moat risk", "[UNRESOLVED] Cap does not prove moat."),
            ("Key-person risk", "malformed line without bracketed status"),
        ],
        V,
        set(),
    )
    assert g.claims["RISK.RULING.C1"].grounded is True
    assert g.claims["RISK.RULING.C2"].grounded is False
    assert "RISK.RULING.C3" not in g.claims
    lbl = g.compute()
    assert lbl["OBJ1"] == "IN"
    assert lbl["FIN.G.C1"] == "OUT"
    assert lbl["OBJ2"] == "OUT"
    assert lbl["FIN.G.C2"] == "IN"

