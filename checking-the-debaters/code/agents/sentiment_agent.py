"""
agents/sentiment_agent.py — The Reporter

Optionally enriches document-based analysis with Google Programmable Search
results (needs GOOGLE_API_KEY + GOOGLE_CSE_ID). Without search it scores
external-signal dimensions N/A instead of inferring them from the company's
own documents.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

from agents.base_agent import BaseAgent, Scorecard
from config import GOOGLE_API_KEY, GOOGLE_CSE_ID
from ingestion.deal_briefing import DealBriefing

_SEARCH_URL = "https://www.googleapis.com/customsearch/v1"


class SentimentAgent(BaseAgent):
    AGENT_NAME = "Sentiment Agent"
    VETO_HOLDER = False

    PERSONA = """You are an investigative journalist turned VC analyst. You don't trust
documents prepared by the company — your job is to find what the documents don't say.
When external search results are provided, you use them to build a picture of external
reality: reviews, press, competitors, hiring. You flag asymmetries between the founder's
narrative and what the market, press, and customers actually say. You are a neutral
information broker — neither bear nor bull — but you surface inconvenient truths that
other agents may miss. You never present the company's own claims as external evidence."""

    CORE_DIMENSIONS = [
        "Brand & product sentiment (reviews, forums, social)",
        "Press & media narrative alignment with internal claims",
        "Competitor activity & funding signals",
        "Hiring velocity & employee sentiment (Glassdoor, LinkedIn)",
        "Regulatory / political environment signal",
    ]

    def _build_search_queries(self, briefing: DealBriefing) -> list[str]:
        name = briefing.company_name
        return [
            f'"{name}" reviews',
            f'"{name}" funding news',
            f'"{name}" competitors',
            f'"{name}" employees glassdoor',
            f'"{name}" hiring',
        ]

    @staticmethod
    def _search(query: str, num: int = 5) -> list[dict]:
        params = urllib.parse.urlencode({"key": GOOGLE_API_KEY, "cx": GOOGLE_CSE_ID, "q": query, "num": num})
        with urllib.request.urlopen(f"{_SEARCH_URL}?{params}", timeout=20) as r:
            data = json.load(r)
        return [
            {"title": it.get("title", ""), "link": it.get("link", ""), "snippet": it.get("snippet", "")}
            for it in data.get("items", [])
        ]

    def gather_external(self, briefing: DealBriefing, skip_search: bool) -> tuple[str, str]:
        """Returns (search_text, summary). search_text is '' when no search ran."""
        if skip_search:
            return "", "External search skipped (--skip-search)."
        if not (GOOGLE_API_KEY and GOOGLE_CSE_ID):
            return "", "External search unavailable: GOOGLE_API_KEY and GOOGLE_CSE_ID must both be set."
        blocks, errors = [], []
        for q in self._build_search_queries(briefing):
            try:
                items = self._search(q)
            except Exception as e:     # report, don't swallow silently
                errors.append(f"{q}: {type(e).__name__}: {e}")
                continue
            for it in items:
                blocks.append(f"Query: {q}\nTitle: {it['title']}\nURL: {it['link']}\nSnippet: {it['snippet']}")
        summary = f"Retrieved {len(blocks)} external results."
        if errors:
            summary += f" {len(errors)} queries failed: " + " | ".join(errors)
        return "\n\n".join(blocks), summary

    def analyze_with_search(self, briefing: DealBriefing, skip_search: bool = False) -> tuple[Scorecard, str, str]:
        """Phase 1 for the Sentiment Agent. Returns (scorecard, search_summary, search_text)."""
        search_text, summary = self.gather_external(briefing, skip_search)
        if search_text:
            self.extra_context = (
                "=== EXTERNAL SEARCH RESULTS (not deal documents) ===\n"
                "Cite these as «verbatim snippet text» [SEARCH, URL]. They are checked like document quotes.\n\n"
                f"{search_text}\n=== END EXTERNAL SEARCH RESULTS ==="
            )
            extra = "\nExternal search results are provided in your system prompt; use them.\n"
        else:
            self.extra_context = (
                "NOTE: You have NO external data for this deal (no search results). "
                "Do not infer external sentiment from the company's own documents."
            )
            extra = (
                "\nYou have NO external search data. Any dimension that requires external signals "
                "(reviews, press coverage, Glassdoor/LinkedIn, competitor funding) and is not evidenced "
                "in the deal documents themselves MUST be scored N/A.\n"
            )
        scorecard = self.analyze(briefing, extra_instructions=extra)
        return scorecard, summary, search_text
