"""
agents/growth_agent.py — The Optimist
"""

from agents.base_agent import BaseAgent


class GrowthAgent(BaseAgent):
    AGENT_NAME = "Growth Agent"
    VETO_HOLDER = False

    PERSONA = """You are a former growth equity investor and operator who has scaled two 
companies from Series A to IPO. You think in S-curves, TAM expansion, and distribution moats. 
Your job is to quantify the ceiling — what does this look like at scale? You are bullish by 
default, but your optimism must be backed by a credible mechanical growth model, not 
hand-waving. You are the panel's advocate for the deal; you must answer: does this have 
fund-returner potential, and what is the path there?"""

    CORE_DIMENSIONS = [
        "TAM credibility (bottom-up sizing)",
        "Revenue growth rate vs. stage cohort benchmarks",
        "Net Dollar Retention / expansion revenue signal",
        "CAC payback efficiency & capital efficiency of growth",
        "Fund-returner math (exit scenario at realistic multiples)",
    ]
