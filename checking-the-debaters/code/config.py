"""
config.py — Central configuration for the VC Agent Debate System.

LLM backend selection:
  1. LLM_BACKEND env var, if set: "cerebras" | "anthropic" | "gemini"
  2. otherwise the first key present: CEREBRAS_API_KEY → ANTHROPIC_API_KEY → GOOGLE_API_KEY
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── LLM Backend ────────────────────────────────────────────────────────────────
CEREBRAS_API_KEY: str = os.getenv("CEREBRAS_API_KEY", "")
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")
GOOGLE_CSE_ID: str = os.getenv("GOOGLE_CSE_ID", "")

_explicit_backend = os.getenv("LLM_BACKEND", "").strip().lower()
if _explicit_backend and _explicit_backend not in ("cerebras", "anthropic", "gemini"):
    raise ValueError(f"LLM_BACKEND must be cerebras, anthropic or gemini (got {_explicit_backend!r})")
LLM_BACKEND: str = _explicit_backend or (
    "cerebras" if CEREBRAS_API_KEY else "anthropic" if ANTHROPIC_API_KEY else "gemini"
)

# Model names — override via env if needed
CEREBRAS_MODEL: str = os.getenv("CEREBRAS_MODEL", "gpt-oss-120b")
ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")
ACTIVE_MODEL: str = {
    "cerebras": CEREBRAS_MODEL, "anthropic": ANTHROPIC_MODEL, "gemini": GEMINI_MODEL,
}[LLM_BACKEND]

# ── Cerebras specifics ─────────────────────────────────────────────────────────
# Shared-inference limits per Cerebras docs (checked 2026-10-21):
#   gpt-oss-120b context: 65k tokens (free trial) / 131k (Developer, pay-as-you-go)
#   free trial: 5 req/min, 30k uncached tokens/min, 1M tokens/day
#   developer:  1k req/min, 1M uncached tokens/min, no daily cap
CEREBRAS_BASE_URL: str = os.getenv("CEREBRAS_BASE_URL", "https://api.cerebras.ai/v1")
CEREBRAS_TIER: str = os.getenv("CEREBRAS_TIER", "free").strip().lower()   # free | paid
if CEREBRAS_TIER not in ("free", "paid"):
    raise ValueError(f"CEREBRAS_TIER must be free or paid (got {CEREBRAS_TIER!r})")
# gpt-oss is a reasoning model; reasoning tokens count against the output budget.
CEREBRAS_REASONING_EFFORT: str = os.getenv("CEREBRAS_REASONING_EFFORT", "medium")  # low | medium | high

_free = LLM_BACKEND == "cerebras" and CEREBRAS_TIER == "free"
_paid = LLM_BACKEND == "cerebras" and CEREBRAS_TIER == "paid"

# Output token budget per call (includes gpt-oss reasoning tokens on Cerebras).
MAX_OUTPUT_TOKENS: int = int(os.getenv("MAX_OUTPUT_TOKENS", "8192" if _free else "12288" if _paid else "4096"))

# Rate-limit handling: on HTTP 429 wait (Retry-After if given, else exponential backoff) and retry.
RATE_LIMIT_MAX_RETRIES: int = int(os.getenv("RATE_LIMIT_MAX_RETRIES", "10"))
RATE_LIMIT_MAX_WAIT_S: float = float(os.getenv("RATE_LIMIT_MAX_WAIT_S", "90"))

# ── Ablation conditions (experiments) ──────────────────────────────────────────
# Each condition adds one mechanism to the previous one. Held constant across all of
# them: the «quote» format request, document compression, the argument-structure block.
#   A  documents in Phase 1 only (original design); the Quant Agent sees an 8,000-char prefix
#   B  + documents on every turn
#   C  + citation checks shown to agents (annotations, NOT FOUND flags, evidence marks)
#   D  + code-enforced veto gate (re-ask, downgrade, override)
#   E  + fact ledger, deal math, Quant arithmetic/value checks          (= full system)
ABLATION: str = os.getenv("ABLATION", "E").strip().upper()
if ABLATION not in ("A", "B", "C", "D", "E"):
    raise ValueError(f"ABLATION must be one of A-E (got {ABLATION!r})")
_lvl = "ABCDE".index(ABLATION)
GROUND_ALL_TURNS: bool = _lvl >= 1
SHOW_CITATION_CHECKS: bool = _lvl >= 2
ENFORCE_GATE: bool = _lvl >= 3
NUMBERS_IN_CODE: bool = _lvl >= 4
# Ablation of the document-blind Socratic referee (Phase 3 audit + rebuttal exchanges), independent of
# A–E. 1 = on (default). Run as a variant: --variant noref:SOCRATIC_REFEREE=0.
SOCRATIC_REFEREE: bool = os.getenv("SOCRATIC_REFEREE", "1") == "1"
# Repeated runs of the same deal/model/condition need separate caches.
RUN_ID: str = os.getenv("RUN_ID", "")

# Bump when prompts/parsing change so stale Phase 1/2 caches are not reused.
PROMPT_VERSION: str = "2026-10-21.4"

# ── Documents ──────────────────────────────────────────────────────────────────
# Every LLM call carries the full document set. If the set exceeds this many
# characters it is truncated (with a loud warning listing what was cut).
# Documents are ordered memo/deck/financials first and legal boilerplate last,
# so truncation cuts boilerplate first. Budgets assume ~3.5 chars/token (PDF noise
# makes text token-dense) and leave room for persona, transcript and output:
#   Cerebras free (65k window):   120k chars docs + 40k transcript + 8k output
#   Cerebras paid (131k window):  280k chars docs + 80k transcript + 12k output
#   Claude (200k window):         560k chars docs
#   Gemini 2.5 Pro (1M window):   1.6M chars docs
_DEFAULT_MAX_DOC_CHARS = (
    "120000" if _free else "280000" if _paid else
    "560000" if LLM_BACKEND == "anthropic" else "1600000"
)
MAX_DOC_CHARS: int = int(os.getenv("MAX_DOC_CHARS", _DEFAULT_MAX_DOC_CHARS))

# Local compression before sending (ingestion/compress.py): watermark noise, repeated
# headers/footers, empty table rows, duplicate files, known template text. --no-compress disables.
DOC_COMPRESSION: bool = os.getenv("DOC_COMPRESSION", "1") == "1"

# Debate transcript passed into a single prompt is capped (most recent text kept).
MAX_TRANSCRIPT_CHARS: int = int(os.getenv(
    "MAX_TRANSCRIPT_CHARS", "40000" if _free else "80000" if _paid else "400000"))

# ── Fact ledger & deal math (Phase 0) ─────────────────────────────────────────
FACT_LEDGER: bool = os.getenv("FACT_LEDGER", "1") == "1" and NUMBERS_IN_CODE
# Assumptions used by orchestrator/deal_math.py; shown to agents and in the report as [A].
DEAL_ASSUMPTIONS: dict = {
    "dilution_per_round": float(os.getenv("DILUTION_PER_ROUND", "0.20")),
    "future_rounds": int(os.getenv("FUTURE_ROUNDS", "2")),
    "exit_revenue_multiples": tuple(float(x) for x in os.getenv("EXIT_REVENUE_MULTIPLES", "3,5,10").split(",")),
    "moic_targets": tuple(float(x) for x in os.getenv("MOIC_TARGETS", "10,30,100").split(",")),
}

# ── Debate Protocol ─────────────────────────────────────────────────────────────
CROSS_EXAM_ROUNDS: int = 2
PANEL_VOTE_THRESHOLD: int = 3   # out of 5 non-Risk agents
# Concurrent independent model calls within a phase (Phase 1 analyses, Phase 2 panel openings,
# each cross-examination round, Phase 4 panel responses, Phase 5 votes). 1 = sequential.
# Cerebras free tier allows 5 req/min, so it defaults to 1 there.
PARALLEL_WORKERS: int = int(os.getenv("PARALLEL_WORKERS", os.getenv("PHASE1_MAX_WORKERS", "1" if _free else "6")))
PHASE1_MAX_WORKERS: int = PARALLEL_WORKERS   # backward-compatible name

# Risk gate enforcement (applied in code, not left to the model):
#  - A RESOLVED or PARTIAL objection must cite at least one verbatim document quote
#    that the citation checker finds in the documents; otherwise (after one re-ask)
#    it is downgraded to UNRESOLVED.
#  - If any objection is UNRESOLVED after that check, a Risk INVEST is
#    overridden to PASS. This is the existing veto rule ("only if your
#    objections are answered with documentary evidence"), enforced mechanically.
RISK_BLOCK_ON_UNRESOLVED: bool = os.getenv("RISK_BLOCK_ON_UNRESOLVED", "1") == "1" and ENFORCE_GATE

# ── Scoring ─────────────────────────────────────────────────────────────────────
SCORE_MIN: int = 1
SCORE_MAX: int = 10
FLEX_DIMENSIONS_PER_AGENT: int = 3

# ── Output ──────────────────────────────────────────────────────────────────────
OUTPUT_DIR: str = os.getenv("OUTPUT_DIR", "./output/reports")
CHECKPOINT_DIR: str = os.getenv("CHECKPOINT_DIR", "./output/checkpoints")
CACHE_DIR: str = os.getenv("CACHE_DIR", "./output/cache")
