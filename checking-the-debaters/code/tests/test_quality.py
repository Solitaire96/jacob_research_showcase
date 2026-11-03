"""
tests/test_quality.py — Offline tests for the QuALITY benchmark harness (`experiments/quality.py`).

Tests:
  1. Exhaustive propositional verification of `apply_quality_gate` over all input states.
  2. Arm parser (`blind`/`sighted` × `none`/`shown`/`enforced` × `@noref`).
  3. Exact vs tolerant checker comparison on story quotes.
  4. End-to-end offline execution (`fetch` -> `run` across arms -> `analyze`) using `LLM_FAKE=1`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments"))

import tests.fake_llm as fake_llm  # noqa: E402
from orchestrator.citations import CitationVerifier  # noqa: E402
from quality import (  # noqa: E402
    BASE_ARMS,
    QualityItem,
    _extract_two_option_items,
    apply_quality_gate,
    check_quotes_both,
    parse_arm,
    run_quality_item,
)


def test_quality_gate_exhaustively():
    """Exhaustive check of all 3 × 2 × 2 = 12 input combinations of apply_quality_gate."""
    for choice in ("A", "B", None):
        for has_quote in (True, False):
            for enforce in (True, False):
                final, abstained, notes = apply_quality_gate(choice, has_quote, enforce)
                # 1. Invariant: under enforcement, a non-abstained answer A/B MUST have a verified quote
                if enforce and final in ("A", "B"):
                    assert has_quote is True
                # 2. Never flips A to B or B to A
                if final in ("A", "B"):
                    assert final == choice
                # 3. Never blocks a well-evidenced valid choice
                if choice in ("A", "B") and has_quote:
                    assert final == choice and not abstained and not notes
                # 4. Consistency of abstained flag
                assert abstained == (final == "UNSUPPORTED")


def test_parse_arm_all_combinations():
    for base in BASE_ARMS:
        cfg = parse_arm(base)
        assert cfg["referee"] is True
        assert cfg["sighted"] == base.startswith("sighted")
        assert cfg["show_checks"] == (not base.endswith("none"))
        assert cfg["enforce_gate"] == base.endswith("enforced")
        cfg_nr = parse_arm(f"{base}@noref")
        assert cfg_nr["referee"] is False
    with pytest.raises(ValueError):
        parse_arm("invalid_arm")
    with pytest.raises(ValueError):
        parse_arm("blind_shown@badmod")


def test_both_checkers_on_story():
    story = "The starship landed on the red plains of Ares at dawn, carrying $24,000,000 of non-dilutive supplies."
    verifier = CitationVerifier({"Story": story})
    res = check_quotes_both(
        'Claim 1: «The starship landed on the red plains of Ares at dawn» [Story]. '
        'Claim 2: «carrying 24000000 of non\u2011dilutive supplies» [Story].',
        verifier,
        story,
    )
    assert len(res) == 2
    # First quote is exact in both
    assert res[0]["verified"] is True and res[0]["ws_exact"] is True
    # Second quote has non-breaking hyphen \u2011 and unformatted number: tolerant finds it, ws_exact rejects
    assert res[1]["verified"] is True and res[1]["ws_exact"] is False


def test_quality_debate_mechanisms_offline(monkeypatch):
    """
    Verify that:
      - In `blind_none`: Judge sees no [Citation check], picks persuasive fake quote B (wrong).
      - In `blind_shown`: Judge sees [Citation check: ... NOT FOUND] on B, switches to verified A (correct).
      - In `blind_enforced` when Judge insists on fake quote: gate overrides to UNSUPPORTED (abstained).
      - `@noref` omits the Socratic Referee call.
    """
    monkeypatch.setenv("LLM_FAKE", "1")
    item = QualityItem(
        item_id="q1",
        article_id="art1",
        title="Red Dawn on Ares",
        story="The starship landed on the red plains of Ares at dawn. The crew disembarked safely.",
        question="Where did the starship land?",
        option_a="On the red plains of Ares at dawn",
        option_b="In the Pacific Ocean at midnight",
        gold="A",
        hard=True,
    )

    # 1. blind_none: Judge does not see citation checks -> fooled by B
    fake_llm.reset()
    r_none = run_quality_item(item, "blind_none", "fake-model")
    assert r_none["final_choice"] == "B" and r_none["final_correct"] == 0
    assert fake_llm.CALLS["quality_referee"] == 1

    # 2. blind_shown: Judge sees NOT FOUND annotation on B's fake quote -> picks A
    fake_llm.reset()
    r_shown = run_quality_item(item, "blind_shown", "fake-model")
    assert r_shown["final_choice"] == "A" and r_shown["final_correct"] == 1

    # 3. blind_enforced when Judge insists on an unverified quote -> gate blocks to UNSUPPORTED
    fake_llm.reset(quality_judge_mode="always_fake")
    r_enf = run_quality_item(item, "blind_enforced", "fake-model")
    assert r_enf["model_choice"] == "B"
    assert r_enf["final_choice"] == "UNSUPPORTED" and r_enf["abstained"] == 1
    assert r_enf["gate_intervened"] == 1 and fake_llm.CALLS["quality_judge"] == 2  # initial + 1 re-ask

    # 4. @noref disables Socratic referee
    fake_llm.reset()
    r_noref = run_quality_item(item, "sighted_shown@noref", "fake-model")
    assert fake_llm.CALLS["quality_referee"] == 0
    assert not any(t["phase"] == "socratic" for t in r_noref["transcript"])


def test_quality_cli_end_to_end(tmp_path):
    """Test fetch (from local JSONL) -> run -> analyze via subprocess."""
    sample_jsonl = tmp_path / "sample_quality.jsonl"
    sample_jsonl.write_text(json.dumps({
        "article_id": "a101",
        "title": "Mission to Ares",
        "article": "The starship landed on the red plains of Ares at dawn. Captain Vance opened the hatch.",
        "questions": [
            {
                "question_unique_id": "q101",
                "question": "Where did the starship land?",
                "options": [
                    "On the red plains of Ares at dawn",
                    "In the Pacific Ocean at midnight",
                    "On the moon Europa",
                    "In New York Harbor",
                ],
                "gold_label": 1,
                "difficult": 1,
            }
        ],
    }) + "\n", encoding="utf-8")

    items = _extract_two_option_items(sample_jsonl.read_text().splitlines(), limit=5, hard_only=True, seed=1)
    assert len(items) == 1 and items[0].item_id == "q101"

    env = {
        **os.environ,
        "LLM_FAKE": "1",
        "STUDIES_DIR": str(tmp_path / "studies"),
        "CEREBRAS_API_KEY": "x",
    }
    py = lambda *args: subprocess.run([sys.executable, "experiments/quality.py", *args],  # noqa: E731
                                      cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    r1 = py("fetch", "--study", "qtest", "--input-jsonl", str(sample_jsonl), "--limit", "5")
    assert r1.returncode == 0, r1.stdout + r1.stderr

    r2 = py("run", "--study", "qtest", "--models", "m1", "--arms", "blind_none", "blind_shown", "blind_enforced")
    assert r2.returncode == 0, r2.stdout + r2.stderr

    r3 = py("analyze", "--study", "qtest")
    assert r3.returncode == 0, r3.stdout + r3.stderr
    summary_md = (tmp_path / "studies" / "qtest" / "analysis" / "quality_summary.md").read_text()
    assert "`blind_none`" in summary_md and "`blind_shown`" in summary_md and "`blind_enforced`" in summary_md
