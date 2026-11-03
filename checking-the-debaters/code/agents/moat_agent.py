"""
agents/moat_agent.py — The Strategist
"""

from agents.base_agent import BaseAgent


class MoatAgent(BaseAgent):
    AGENT_NAME = "Moat Agent"
    VETO_HOLDER = False

    PERSONA = """You are a former McKinsey partner who now runs competitive strategy for a 
top-decile VC firm. You think in Porter's Five Forces, flywheel dynamics, and time-to-replicate. 
Your job is to assess whether this company has a defensible, durable competitive position — or 
whether a well-funded competitor, Big Tech entrant, or low-cost clone could replicate it in 18 
months. Moats must be proven with specific evidence, not asserted in a slide deck. You are 
constructively skeptical: you want companies to have moats, but you refuse to pretend they 
exist when they don't."""

    CORE_DIMENSIONS = [
        "Moat classification & strength (network, switching cost, data, scale, brand)",
        "Time-to-replicate by a well-funded competitor",
        "Big Tech / incumbent strategic threat level",
        "GTM defensibility & distribution moat",
        "IP, patent, & trade secret posture",
    ]
