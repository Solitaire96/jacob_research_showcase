"""
tests/test_gate.py — The Risk gate as propositional logic, checked over ALL inputs.

Atoms per objection i (3 objections): status_i ∈ {RESOLVED, PARTIAL, UNRESOLVED},
evidence_i ∈ {T, F} (explanation cites ≥1 quote found in the documents, after re-ask).
Plus model_vote ∈ {INVEST, PASS} and block ∈ {T, F}.  3^3 · 2^3 · 2 · 2 = 864 cases.
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.risk_agent import apply_gate  # noqa: E402

STATUSES = ("RESOLVED", "PARTIAL", "UNRESOLVED")


def _all_cases():
    for st in itertools.product(STATUSES, repeat=3):
        for ev in itertools.product((True, False), repeat=3):
            for model_vote in ("INVEST", "PASS"):
                for block in (True, False):
                    log = {i + 1: (st[i], f"expl {i + 1}") for i in range(3)}
                    has = {i + 1: ev[i] for i in range(3)}
                    yield log, has, model_vote, block


def test_gate_exhaustive():
    n = 0
    for log, has, model_vote, block in _all_cases():
        out, vote, _ = apply_gate(log, model_vote, has, block)
        n += 1
        for i, (st_in, _) in log.items():
            st_out = out[i][0]
            # statuses are never upgraded
            assert st_out in (st_in, "UNRESOLVED")
            # U_out ↔ U_in ∨ (status_in ∈ {R,P} ∧ ¬evidence)
            assert (st_out == "UNRESOLVED") == (st_in == "UNRESOLVED" or not has[i])
        # the code never turns PASS into INVEST
        assert not (vote == "INVEST" and model_vote != "INVEST")
        if block:
            # INVARIANT (the veto clause): INVEST → every objection R/P with verified evidence
            if vote == "INVEST":
                assert all(out[i][0] in ("RESOLVED", "PARTIAL") and has[i] for i in out)
            # and the gate does not block a fully evidenced INVEST
            if model_vote == "INVEST" and all(s != "UNRESOLVED" and has[i] for i, (s, _) in log.items()):
                assert vote == "INVEST"
        else:
            assert vote == model_vote          # block off: the model's vote stands
    assert n == 864


def test_former_loophole_all_partial_no_evidence_is_blocked():
    log = {i: ("PARTIAL", "risk remains") for i in (1, 2, 3)}
    out, vote, notes = apply_gate(log, "INVEST", {1: False, 2: False, 3: False}, block=True)
    assert vote == "PASS"
    assert all(s == "UNRESOLVED" for s, _ in out.values())
    assert any("overridden to PASS" in n for n in notes)
