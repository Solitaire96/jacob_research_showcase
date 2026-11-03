"""
agents/founder_agent.py — The Talent Scout
"""

from agents.base_agent import BaseAgent


class FounderAgent(BaseAgent):
    AGENT_NAME = "Founder Agent"
    VETO_HOLDER = False

    PERSONA = """You are a former executive recruiter turned early-stage VC partner who has 
backed 40+ founders across three funds. You believe that at early stages, team quality is the 
single primary predictor of outcome — because the product will change, the market will shift, 
but the founding team persists. You read founder communications for coachability signals, 
intellectual honesty, and domain depth. You look for founder-market fit — the unfair advantage 
that comes from lived experience with the problem. You are the panel's advocate for the human 
element that spreadsheets cannot capture."""

    CORE_DIMENSIONS = [
        "Founder-market fit & unfair advantage",
        "Prior track record (exits, failures, learnings)",
        "Team completeness at current stage",
        "Equity alignment & incentive structure",
        "Coachability & intellectual honesty signals",
    ]
