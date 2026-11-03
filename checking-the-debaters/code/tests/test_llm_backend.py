"""
tests/test_llm_backend.py — Backend wiring tests (offline; no API calls).

Cerebras-specific tests run in a subprocess with CEREBRAS_API_KEY set, because
config is read at import time.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _run(code: str, **env) -> str:
    e = {k: v for k, v in os.environ.items()
         if k not in ("CEREBRAS_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "LLM_BACKEND", "CEREBRAS_TIER")}
    e.setdefault("CEREBRAS_TIER", "free")
    e.update(env)
    r = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], cwd=ROOT, env=e,
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout.strip().splitlines()[-1]   # last line = the result; earlier lines are progress output


def test_backend_selection_priority():
    out = _run("import config; print(config.LLM_BACKEND, config.ACTIVE_MODEL)",
               CEREBRAS_API_KEY="k", GOOGLE_API_KEY="g")
    assert out.split() == ["cerebras", "gpt-oss-120b"]
    out = _run("import config; print(config.LLM_BACKEND)",
               CEREBRAS_API_KEY="k", GOOGLE_API_KEY="g", LLM_BACKEND="gemini")
    assert out.strip() == "gemini"


def test_cerebras_request_payload():
    out = _run("""
        import llm
        from langchain_core.messages import SystemMessage, HumanMessage
        m = llm.make_llm(0.3, 2048)
        p = m._get_request_payload([SystemMessage(content="DOCS\\n\\npersona"), HumanMessage(content="hi")])
        print(m.openai_api_base, p["model"], p["reasoning_effort"], p["max_completion_tokens"], p["messages"][0]["role"])
    """, CEREBRAS_API_KEY="k")
    assert out.split() == ["https://api.cerebras.ai/v1", "gpt-oss-120b", "medium", "8192", "system"]


def test_backoff_honours_retry_after_and_context_errors():
    out = _run("""
        import llm
        class Resp:
            def __init__(self, h): self.headers = h; self.status_code = 429
        class RateLimitError(Exception):
            def __init__(self, h): super().__init__("Too many requests"); self.response = Resp(h); self.status_code = 429
        class Flaky:
            def __init__(self): self.n = 0
            def invoke(self, msgs):
                self.n += 1
                if self.n <= 2: raise RateLimitError({"retry-after": "7"})
                from langchain_core.messages import AIMessage
                return AIMessage(content="ok")
        waits = []
        f = Flaky()
        r = llm._invoke_with_backoff(f, [], sleep=waits.append)
        ok = (r.content, f.n, all(7 <= w <= 9 for w in waits))
        class Big:
            def invoke(self, msgs): raise Exception("Error 400: context_length_exceeded: prompt has 70000 tokens")
        try:
            llm._invoke_with_backoff(Big(), [], sleep=waits.append)
        except llm.ContextTooLongError as e:
            print(*ok, "context-error", "MAX_DOC_CHARS" in str(e))
    """, CEREBRAS_API_KEY="k")
    assert out.split() == ["ok", "3", "True", "context-error", "True"]


def test_empty_reasoning_reply_is_retried():
    out = _run("""
        import llm
        from langchain_core.messages import AIMessage
        class EmptyThenOk:
            def __init__(self): self.n = 0
            def invoke(self, msgs):
                self.n += 1
                return AIMessage(content="" if self.n == 1 else "FINAL_VOTE: PASS")
        m = EmptyThenOk()
        print(llm.invoke(m, "sys", "human", documents="DOCS"), m.n)
    """, CEREBRAS_API_KEY="k")
    assert out.strip() == "FINAL_VOTE: PASS 2"


def test_full_run_fits_cerebras_free_window():
    """Sample deal (with a 160k-char LPA) under Cerebras free-tier settings: no prompt may exceed the 65k-token window."""
    out = _run("""
        import os, sys, tempfile
        root = os.getcwd(); sys.path.insert(0, root)
        from tests import fake_llm
        import agents.base_agent, agents.quant_agent, agents.socratic_agent, orchestrator.facts
        for mod in (agents.base_agent, agents.quant_agent, agents.socratic_agent, orchestrator.facts):
            mod.make_llm = fake_llm.make_fake_llm
        from ingestion.deal_briefing import build_deal_briefing
        from ingestion.document_parser import collect_documents
        from orchestrator.debate_engine import DebateEngine
        b = build_deal_briefing("Larkspur Soil Analytics", collect_documents(os.path.join(root, "sample_deal/larkspur")))
        os.chdir(tempfile.mkdtemp())
        fake_llm.reset(risk_ruling="fabricated_then_honest")
        state = DebateEngine(skip_search=True).run(b)
        largest = max(fake_llm.PROMPT_CHARS)
        print(state.verdict, largest, len(fake_llm.PROMPT_CHARS), ",".join(b.truncated_files) or "none")
    """, CEREBRAS_API_KEY="k", CEREBRAS_TIER="free")
    verdict, largest, ncalls, truncated = out.split(maxsplit=3)
    # 65,536-token window × ~3.5 chars/token, minus the 8,192-token output budget
    assert int(largest) < (65536 - 8192) * 3.5, out
    assert "LPA" in truncated                           # boilerplate cut, not the memo
    assert "memo" not in truncated and "term sheet" not in truncated
