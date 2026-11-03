"""
tests/test_ablation.py — Each ablation condition sends what it should, and only that.

Runs app.py end to end in a subprocess (flags are read at import time) with the
scripted fake model, and inspects every prompt sent.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEAL = ROOT / "sample_deal" / "larkspur"


def _run(cond: str, tmp: Path) -> tuple[list[str], dict]:
    log = tmp / f"prompts_{cond}.jsonl"
    env = {**os.environ, "LLM_FAKE": "1", "LLM_BACKEND": "cerebras", "CEREBRAS_API_KEY": "x",
           "ABLATION": cond, "RUN_ID": f"test-{cond}", "CACHE_DIR": str(tmp / "cache"),
           "CHECKPOINT_DIR": str(tmp / "ck"), "TEMPLATE_LEARN": "0", "FAKE_PROMPT_LOG": str(log)}
    out = tmp / f"out_{cond}"
    r = subprocess.run([sys.executable, "app.py", "--company", "Larkspur Soil Analytics", "--docs-dir", str(DEAL),
                        "--skip-search", "--output", str(out)],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    prompts = [(lambda d: d["system"] + "\n" + d["human"])(json.loads(line)) for line in log.open()]
    report = json.loads(next(out.glob("*.json")).read_text())
    return prompts, report


def _n(prompts: list[str], pattern: str) -> int:
    return sum(bool(re.search(pattern, p)) for p in prompts)


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ablation")
    return {c: _run(c, tmp) for c in "ABCDE"}


CHECKER = r"(?i)citation check|automated checker|NOT FOUND"


def test_condition_A_documents_only_in_phase1(runs):
    prompts, rep = runs["A"]
    with_docs = [p for p in prompts if "=== DEAL DOCUMENTS" in p]
    assert with_docs and all("CORE_SCORES" in p for p in with_docs)
    assert any(p.startswith("DEAL DOCUMENTS (excerpt)") for p in prompts)       # Quant/expert prefix
    assert _n(prompts, CHECKER) == 0 and _n(prompts, "FACT LEDGER") == 0
    assert rep["run"]["ablation"] == "A" and rep["fact_ledger"] is None
    assert rep["citation_log"], "quotes are still logged for analysis in every condition"


def test_condition_B_documents_every_turn_no_checker(runs):
    prompts, _ = runs["B"]
    panel = [p for p in prompts if "You are the Socratic Agent" not in p]
    assert all("DEAL DOCUMENTS" in p for p in panel)
    assert _n(prompts, CHECKER) == 0


def test_condition_C_checker_visible_no_gate(runs):
    prompts, rep = runs["C"]
    assert _n(prompts, r"\[Citation check") > 0
    assert _n(prompts, "automatically downgraded") == 0
    # Scripted Risk Agent lifts its veto on an invented quote; nothing stops it in C.
    assert rep["risk_gate"]["model_vote"] == "INVEST" and rep["verdict"] == "INVEST"
    assert rep["risk_gate"]["enforcement_notes"] == []


def test_condition_D_gate_without_ledger(runs):
    prompts, rep = runs["D"]
    assert _n(prompts, "automatically downgraded") > 0
    assert _n(prompts, "FACT LEDGER") == 0 and _n(prompts, "re-computed in code") == 0
    assert rep["risk_gate"]["model_vote"] == "INVEST" and rep["verdict"] == "VETOED"
    assert rep["risk_gate"]["enforcement_notes"], "the gate must record why it overrode the vote"


def test_condition_E_full_system(runs):
    prompts, rep = runs["E"]
    assert _n(prompts, "FACT LEDGER") > 0 and _n(prompts, "re-computed in code") > 0
    assert rep["fact_ledger"] is not None and rep["verdict"] == "VETOED"
    assert all(k in rep["run"]["usage"] for k in ("calls", "input_tokens", "output_tokens"))


def test_held_constant_across_conditions(runs):
    """Quote format and argument blocks are requested in every condition."""
    for c, (prompts, _) in runs.items():
        assert _n(prompts, "EVIDENCE RULE") > 0, c
        assert _n(prompts, "CLAIMS:") > 0, c


def test_think_tags_stripped():
    from types import SimpleNamespace

    from llm import _response_text
    msg = SimpleNamespace(text="<think>secret «invented quote»</think>VERDICT: PASS", content="")
    assert _response_text(msg) == "VERDICT: PASS"
    assert _response_text(SimpleNamespace(text="<think>never closed", content="")) == ""


def test_usage_counted():
    from types import SimpleNamespace

    import llm
    before = dict(llm.USAGE)
    llm._record_usage(SimpleNamespace(usage_metadata={
        "input_tokens": 1000, "output_tokens": 50, "input_token_details": {"cache_read": 800}}))
    llm._record_usage(SimpleNamespace(usage_metadata=None))
    assert llm.USAGE["calls"] - before["calls"] == 2
    assert llm.USAGE["input_tokens"] - before["input_tokens"] == 1000
    assert llm.USAGE["cached_tokens"] - before["cached_tokens"] == 800


def test_label_text_is_blinded():
    sys.path.insert(0, str(ROOT / "experiments"))
    from export_labels import assertion_sentences, objection_statuses, scrub
    s = scrub("Runway is 24 months per F3. [evidence: not found]\n\n[Citation check: 0/1 quotes found.\n  NOT FOUND: \"x\"]")
    assert s == "Runway is 24 months." and "F3" not in s
    rep = {"risk_gate": {"objection_log": [{"objection": "o", "status": "[UNRESOLVED] Insured. «x y z» [D, p. 1] "
                                            "⚠ Downgraded from RESOLVED by code: no supporting quote"}]}}
    ob = objection_statuses(rep)[0]
    assert (ob["model_status"], ob["final_status"]) == ("RESOLVED", "UNRESOLVED") and "Downgraded" not in ob["text"]
    sents = assertion_sentences("Management confirmed a signed LOI with a national grain cooperative worth $4M (F2).\n"
                                "We like it a lot overall.\nCLAIMS:\nC1: Revenue was 5M in 2025 | GROUNDS: x")
    assert sents == ["Management confirmed a signed LOI with a national grain cooperative worth $4M."]


def test_harness_end_to_end(tmp_path):
    """prepare → run (resumable) → export labels → analyze, offline."""
    deals = tmp_path / "deals.csv"
    deals.write_text("deal_id,company,docs_dir,tuned\nlarkspur,Larkspur Soil Analytics,sample_deal/larkspur,0\n")
    env = {**os.environ, "LLM_FAKE": "1", "CEREBRAS_API_KEY": "x", "STUDIES_DIR": str(tmp_path / "studies"),
           "CEREBRAS_TIER": "free", "LLM_BACKEND": "cerebras"}
    py = lambda *args: subprocess.run([sys.executable, *args], cwd=ROOT, env=env,  # noqa: E731
                                      capture_output=True, text=True, timeout=900)
    r = py("experiments/run.py", "prepare", "--study", "t", "--deals", str(deals))
    assert r.returncode == 0, r.stdout + r.stderr
    args = ("experiments/run.py", "run", "--study", "t", "--models", "m1", "--conditions", "A", "D",
            "--runs", "2", "--jobs", "2", "--variant", "nocompress:DOC_COMPRESSION=0", "--variant-conditions", "D")
    r = py(*args)
    assert r.returncode == 0 and "6/6 done" in r.stdout, r.stdout + r.stderr
    r = py(*args)
    assert "6 already done, 0 to run" in r.stdout
    meta = json.loads(next((tmp_path / "studies" / "t" / "runs").glob("*/*/D@nocompress/r1/run_meta.json")).read_text())
    assert meta["settings"]["DOC_COMPRESSION"] == "0"
    assert meta["settings"]["CEREBRAS_TIER"] == "free" and meta["settings"]["MAX_OUTPUT_TOKENS"] == "8192"
    assert meta["workers"] == 1                     # free-tier default
    rep = json.loads(next((tmp_path / "studies" / "t" / "runs").glob("*/*/D@nocompress/r1/result.json")).read_text())
    assert rep["compression"] is None and rep["run"]["ablation"] == "D"
    # Redefining a variant, or continuing on changed code, is refused.
    r = py("experiments/run.py", "run", "--study", "t", "--models", "m1", "--conditions", "A",
           "--variant", "nocompress:DOC_COMPRESSION=1")
    assert r.returncode != 0 and "already defined" in (r.stdout + r.stderr)
    code = tmp_path / "studies" / "t" / "code.json"
    code.write_text(json.dumps([{"code_hash": "0000", "commit": "old"}]))
    r = py(*args)
    assert r.returncode != 0 and "Pipeline code changed" in (r.stdout + r.stderr)
    r = py(*args, "--allow-code-change")
    assert r.returncode == 0 and len(json.loads(code.read_text())) == 2
    r = py("experiments/export_labels.py", "--study", "t", "--labellers", "2")
    assert r.returncode == 0, r.stdout + r.stderr
    lab = tmp_path / "studies" / "t" / "labels"
    assert "condition" not in (lab / "labels_1.csv").read_text().splitlines()[0]
    r = py("experiments/analyze.py", "--study", "t", "--boot", "50")
    assert r.returncode == 0, r.stdout + r.stderr
    summary = (tmp_path / "studies" / "t" / "analysis" / "summary.md").read_text()
    assert "| m1 | A | 2 |" in summary and "| m1 | D | 2 |" in summary and "| m1 | D@nocompress | 2 |" in summary
    assert "D→D@nocompress" in summary


def test_referee_switch(tmp_path):
    """SOCRATIC_REFEREE=0 removes the Socratic audit and rebuttal exchanges, and nothing else."""
    out = {}
    for ref in ("1", "0"):
        log = tmp_path / f"p{ref}.jsonl"
        env = {**os.environ, "LLM_FAKE": "1", "LLM_BACKEND": "cerebras", "CEREBRAS_API_KEY": "x",
               "ABLATION": "E", "SOCRATIC_REFEREE": ref, "RUN_ID": f"ref{ref}", "CACHE_DIR": str(tmp_path / "c"),
               "CHECKPOINT_DIR": str(tmp_path / f"ck{ref}"), "TEMPLATE_LEARN": "0", "FAKE_PROMPT_LOG": str(log)}
        r = subprocess.run([sys.executable, "app.py", "--company", "Larkspur Soil Analytics", "--docs-dir", str(DEAL),
                            "--skip-search", "--output", str(tmp_path / f"o{ref}")],
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
        assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
        rep = json.loads(next((tmp_path / f"o{ref}").glob("*.json")).read_text())
        prompts = [json.loads(line)["system"] for line in log.open()]
        out[ref] = (rep, sum(p.startswith("You are the Socratic Agent") for p in prompts))
    (on, n_on), (off, n_off) = out["1"], out["0"]
    assert n_on > 0 and n_off == 0
    assert any(s["phase"].startswith("socratic") for s in on["transcript"])
    assert not any(s["phase"].startswith("socratic") for s in off["transcript"])
    assert off["run"]["flags"]["SOCRATIC_REFEREE"] is False and on["run"]["flags"]["SOCRATIC_REFEREE"] is True
    assert {s["phase"] for s in off["transcript"]} >= {"opening", "gauntlet_response"}



def test_contrasts_use_only_shared_deals(tmp_path):
    """An arm run on fewer deals must be compared on those deals only (regression, 2026-10-22)."""
    study = tmp_path / "studies" / "s"

    def put(deal, cond, verdict):
        d = study / "runs" / deal / "m1" / cond / "r1"
        d.mkdir(parents=True)
        (d / "result.json").write_text(json.dumps({
            "verdict": verdict, "run": {"model": "m1", "usage": {}},
            "risk_gate": {"vote": "PASS", "model_vote": "PASS", "objection_log": [], "has_verified_quote": {}},
            "citation_log": [], "transcript": [], "scorecards": {}}))

    put("d1", "D", "INVEST")
    put("d1", "E", "INVEST")
    put("d2", "E", "PASS")            # E also ran on d2; D did not
    env = {**os.environ, "STUDIES_DIR": str(tmp_path / "studies")}
    r = subprocess.run([sys.executable, "experiments/analyze.py", "--study", "s", "--boot", "50"],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    rows = list(__import__("csv").DictReader((study / "analysis" / "contrasts.csv").open()))
    inv = [x for x in rows if x["from"] == "D" and x["to"] == "E" and x["metric"] == "invest_rate"]
    assert inv and inv[0]["deals"] == "1" and float(inv[0]["diff"]) == 0.0


def test_runner_halts_on_payment_required(tmp_path):
    """HTTP 402 stops the grid instead of burning through every remaining run."""
    deals = tmp_path / "deals.csv"
    deals.write_text("deal_id,company,docs_dir,tuned\nlarkspur,Larkspur Soil Analytics,sample_deal/larkspur,0\n")
    env = {**os.environ, "LLM_FAKE": "1", "CEREBRAS_API_KEY": "x", "STUDIES_DIR": str(tmp_path / "studies"),
           "CEREBRAS_TIER": "free", "LLM_BACKEND": "cerebras"}
    r = subprocess.run([sys.executable, "experiments/run.py", "prepare", "--study", "q", "--deals", str(deals)],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr
    r = subprocess.run([sys.executable, "experiments/run.py", "run", "--study", "q", "--models", "m1",
                        "--conditions", "A", "E", "--runs", "2", "--jobs", "1", "--retries", "0"],
                       cwd=ROOT, env={**env, "FAKE_402": "1"}, capture_output=True, text=True, timeout=600)
    assert "[quota]" in r.stdout, r.stdout[-3000:] + r.stderr[-2000:]
    rows = list(__import__("csv").DictReader((tmp_path / "studies" / "q" / "manifest.csv").open()))
    assert rows and all(x["status"] == "pending" for x in rows)   # nothing marked failed, nothing done


def test_outcomes_auc_and_backend_parse(tmp_path):
    """analyze.py computes rank-based ROC AUC against fund_decision/outcomes; run.py parses backend:model."""
    sys.path.insert(0, str(ROOT / "experiments"))
    from analyze import roc_auc
    from run import _model_backend
    assert _model_backend("anthropic:claude-sonnet-4-5") == ("anthropic", "claude-sonnet-4-5")
    assert _model_backend("gemini:gemini-2.5-pro") == ("gemini", "gemini-2.5-pro")
    assert _model_backend("gpt-oss-120b", "cerebras") == ("cerebras", "gpt-oss-120b")
    assert roc_auc([8.0, 7.0, 5.0, 4.0], [True, True, False, False]) == 1.0
    assert roc_auc([6.0, 6.0], [True, False]) == 0.5
    assert roc_auc([6.0, 7.0], [True, True]) is None

    study = tmp_path / "studies" / "s_out"
    for deal, score, verdict in [("d1", 8.0, "INVEST"), ("d2", 6.0, "PASS"), ("d3", 4.0, "VETOED")]:
        d = study / "runs" / deal / "m1" / "E" / "r1"
        d.mkdir(parents=True)
        (d / "result.json").write_text(json.dumps({
            "verdict": verdict, "run": {"model": "m1", "usage": {}},
            "risk_gate": {"vote": "PASS", "model_vote": "PASS", "objection_log": [], "has_verified_quote": {}},
            "citation_log": [], "transcript": [],
            "scorecards": {"A": {"overall_score": score}}}))
    oc = tmp_path / "outcomes.csv"
    oc.write_text("deal_id,fund_decision,outcome\nd1,INVEST,positive\nd2,PASS,negative\nd3,PASS,negative\n")
    env = {**os.environ, "STUDIES_DIR": str(tmp_path / "studies")}
    r = subprocess.run([sys.executable, "experiments/analyze.py", "--study", "s_out", "--outcomes", str(oc), "--boot", "20"],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    summary = (study / "analysis" / "summary.md").read_text()
    assert "| m1 | E | 3 | 3/3 | 1.00 | 1/1 | 0/2 | 1.00 |" in summary
