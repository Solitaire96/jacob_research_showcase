"""
llm.py — Single place that builds LLM clients and invokes them.

Every agent (panel, Risk, Quant, Socratic, fact extractor) goes through here, so
backend selection, prompt caching, rate-limit handling and response-text
extraction behave the same everywhere.

Prompt layout for cache hits: the deal-document block is always the FIRST
part of the system prompt and is byte-identical across agents and phases.
  - Cerebras / Gemini: provider-side prefix caching applies automatically.
  - Anthropic: the document block is sent with cache_control=ephemeral.
"""

from __future__ import annotations

import os
import random
import re
import threading
import time
from typing import Any, Callable, Optional

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from config import (
    LLM_BACKEND, ACTIVE_MODEL,
    CEREBRAS_API_KEY, CEREBRAS_BASE_URL, CEREBRAS_REASONING_EFFORT,
    ANTHROPIC_API_KEY, GOOGLE_API_KEY,
    MAX_OUTPUT_TOKENS, RATE_LIMIT_MAX_RETRIES, RATE_LIMIT_MAX_WAIT_S,
)


class ContextTooLongError(RuntimeError):
    pass


# ── Token accounting (read by the report; per process = per run) ──────────────
USAGE: dict[str, int] = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}
_USAGE_LOCK = threading.Lock()


def _record_usage(response: Any) -> None:
    um = getattr(response, "usage_metadata", None) or {}
    cached = (um.get("input_token_details") or {}).get("cache_read", 0) if isinstance(um, dict) else 0
    with _USAGE_LOCK:
        USAGE["calls"] += 1
        USAGE["input_tokens"] += int(um.get("input_tokens", 0) or 0) if isinstance(um, dict) else 0
        USAGE["output_tokens"] += int(um.get("output_tokens", 0) or 0) if isinstance(um, dict) else 0
        USAGE["cached_tokens"] += int(cached or 0)


def make_llm(temperature: float, max_tokens: int = 4096) -> Any:
    if os.getenv("LLM_FAKE") == "1":          # offline smoke test of the experiment harness
        import importlib
        fake_llm: Any = importlib.import_module("tests.fake_llm")   # test-only; not type-checked
        if not fake_llm.SCENARIO:
            fake_llm.reset()
        return fake_llm.make_fake_llm(temperature, max_tokens)
    if LLM_BACKEND == "cerebras":
        if not CEREBRAS_API_KEY:
            raise ValueError("CEREBRAS_API_KEY not set. Add it to your .env file.")
        from langchain_openai import ChatOpenAI   # Cerebras is OpenAI-API compatible
        kwargs: dict[str, Any] = {}
        # Both shared Cerebras families accept reasoning_effort (gpt-oss: low/medium/high).
        if ACTIVE_MODEL.startswith(("gpt-oss", "qwen-3")):
            kwargs["reasoning_effort"] = CEREBRAS_REASONING_EFFORT
        return ChatOpenAI(  # type: ignore[call-arg]
            model=ACTIVE_MODEL,
            api_key=CEREBRAS_API_KEY,  # type: ignore[arg-type]
            base_url=CEREBRAS_BASE_URL,
            temperature=temperature,
            # Reasoning tokens count against this budget, so use the configured
            # (larger) budget rather than the per-agent visible-answer size.
            max_tokens=MAX_OUTPUT_TOKENS,
            max_retries=0,          # 429s are handled below with longer, header-aware backoff
            timeout=300,
            **kwargs,
        )
    if LLM_BACKEND == "anthropic":
        if not ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY not set. Add it to your .env file.")
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(  # type: ignore[call-arg]
            model=ACTIVE_MODEL,
            api_key=ANTHROPIC_API_KEY,  # type: ignore[arg-type]
            temperature=temperature,
            max_tokens=max_tokens,
        )
    if not GOOGLE_API_KEY:
        raise ValueError("GOOGLE_API_KEY not set. Add it to your .env file.")
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(
        model=ACTIVE_MODEL,
        google_api_key=GOOGLE_API_KEY,
        temperature=temperature,
    )


def _response_text(response: Any) -> str:
    """Extract plain text from an AIMessage (content may be a list of blocks)."""
    text = getattr(response, "text", None)
    if text is not None and not isinstance(text, str) and callable(text):
        text = text()           # langchain-core < 1.0 exposed .text() as a method
    if text is None:
        text = response.content if isinstance(response.content, str) else str(response.content)
    return _THINK_RE.sub("", str(text)).strip()


# Some open models (Qwen) emit their reasoning inline; it must not reach the citation checker.
_THINK_RE = re.compile(r"<think>.*?(</think>|$)", re.DOTALL | re.IGNORECASE)


def _status(e: Exception) -> Optional[int]:
    for attr in ("status_code", "code", "status"):
        v = getattr(e, attr, None)
        if isinstance(v, int):
            return v
    resp = getattr(e, "response", None)
    v = getattr(resp, "status_code", None)
    return v if isinstance(v, int) else None


def _is_rate_limit(e: Exception) -> bool:
    msg = str(e).lower()
    return (_status(e) == 429 or type(e).__name__ in ("RateLimitError", "ResourceExhausted")
            or "rate limit" in msg or "too many requests" in msg or "resource_exhausted" in msg
            or "tokens per minute" in msg or "token_quota_exceeded" in msg)


def _is_context_error(e: Exception) -> bool:
    if _is_rate_limit(e):
        return False
    msg = str(e).lower()
    return any(k in msg for k in ("context_length", "context length", "maximum context",
                                  "too many tokens", "prompt is too long", "reduce the length"))


def _retry_after(e: Exception) -> Optional[float]:
    resp = getattr(e, "response", None)
    headers = getattr(resp, "headers", None) or {}
    for h in ("retry-after", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens-minute"):
        try:
            v = headers.get(h)
            if v is not None:
                return float(str(v).rstrip("s"))
        except (TypeError, ValueError):
            continue
    return None


def _invoke_with_backoff(
    llm: Any,
    messages: list[BaseMessage],
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    for attempt in range(RATE_LIMIT_MAX_RETRIES + 1):
        try:
            return llm.invoke(messages)
        except Exception as e:
            if _is_context_error(e):
                raise ContextTooLongError(
                    f"Prompt exceeds the {LLM_BACKEND}/{ACTIVE_MODEL} context window. Lower MAX_DOC_CHARS "
                    f"or MAX_TRANSCRIPT_CHARS in .env (or set CEREBRAS_TIER correctly). Provider said: {e}"
                ) from e
            if not _is_rate_limit(e) or attempt == RATE_LIMIT_MAX_RETRIES:
                raise
            wait = _retry_after(e) or min(RATE_LIMIT_MAX_WAIT_S, 5 * 2 ** attempt)
            wait = min(RATE_LIMIT_MAX_WAIT_S, wait) + random.uniform(0, 2)
            print(f"  ⏳ rate limited by {LLM_BACKEND} (attempt {attempt + 1}/{RATE_LIMIT_MAX_RETRIES}); "
                  f"waiting {wait:.0f}s", flush=True)
            sleep(wait)
    raise RuntimeError("unreachable")


def _call_counted(llm: Any, messages: list[BaseMessage]) -> Any:
    response = _invoke_with_backoff(llm, messages)
    _record_usage(response)
    return response


def invoke(llm: Any, system: str, human: str, documents: Optional[str] = None) -> str:
    """
    Call the model. If `documents` is given it is placed first in the system
    prompt (cacheable prefix), followed by the role-specific system text.
    """
    if documents:
        if LLM_BACKEND == "anthropic":
            sys_msg = SystemMessage(content=[
                {"type": "text", "text": documents, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": system},
            ])
        else:
            sys_msg = SystemMessage(content=documents + "\n\n" + system)
    else:
        sys_msg = SystemMessage(content=system)
    messages = [sys_msg, HumanMessage(content=human)]
    text = _response_text(_call_counted(llm, messages))
    if not text:
        # Reasoning models can spend the whole output budget thinking and return nothing.
        retry = [sys_msg, HumanMessage(content=human + "\n\n(Your previous reply was empty. "
                                       "Answer directly and concisely in the required format.)")]
        retry_llm = (
            llm.bind(reasoning_effort="low", max_tokens=max(MAX_OUTPUT_TOKENS, 24576))
            if getattr(llm, "openai_api_base", None) and hasattr(llm, "bind")
            else llm
        )
        text = _response_text(_call_counted(retry_llm, retry))
        if not text:
            raise RuntimeError(
                f"{LLM_BACKEND}/{ACTIVE_MODEL} returned an empty answer twice. If using a reasoning model, "
                "raise MAX_OUTPUT_TOKENS or lower CEREBRAS_REASONING_EFFORT."
            )
    return text
