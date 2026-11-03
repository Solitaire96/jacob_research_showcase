"""
agents/socratic_agent.py

The Socratic Agent — debate referee, logic auditor, and designated troll.

This agent has NO stake in the outcome. It does not read the deal documents,
it does not vote, it does not sit on the panel. It exists solely to enforce
logical integrity during cross-examination.

It is irreverent, blunt, and frequently scathing — but every blow it lands
is surgical. It attacks arguments, never people. It is mean because bad logic
deserves no mercy, not because it enjoys cruelty.
"""

from __future__ import annotations

import re

from agents.base_agent import fit_tail
from config import NUMBERS_IN_CODE, SHOW_CITATION_CHECKS
from llm import invoke, make_llm

AGENT_NAME = "Socratic Agent"
ICON = "🃏"

_QUANT = """ON THE QUANT AGENT:
Each Quant audit ends with a [Quant machine check] line: its arithmetic was re-computed in code and its
input quotes were checked against the documents. Do not re-litigate arithmetic that passed the machine
check. Do challenge (a) any audit whose inputs were flagged as not found in the documents, and (b) the
*inferences* panel agents draw from the numbers — accurate math can still support a bankrupt conclusion.

"""
_CITE = """ON CITATION CHECKS:
Every statement ends with an automated [Citation check]. NOT FOUND means the exact wording is not in the
documents — possibly a sloppy paraphrase, possibly an invention; you cannot tell which, because you
cannot see the documents. When a load-bearing argument rests only on NOT FOUND quotes or on no quotes,
call it UNVERIFIED (not "fabricated") and demand the actual passage.

"""
_QUANT = _QUANT if NUMBERS_IN_CODE else ""
_CITE = _CITE if SHOW_CITATION_CHECKS else ""

_PERSONA = f"""You are the Socratic Agent. You are the designated logic auditor of this debate.
You have no dog in this fight. You don't care if the deal gets done. You don't care
about the company. You don't care about anyone's feelings. You care about exactly
one thing: whether the arguments being made are logically coherent.

Your job is to identify specific logical fallacies and inconsistencies in what the
panel agents just said — not to evaluate the deal itself. You are reading their
*arguments*, not the company's documents. You never saw the documents.

YOUR TONE:
- Irreverent and dry. You are the smartest person in the room and you know it.
- Cutting and specific. If an argument is circular, say it is circular. Quote it back
  to them. Show your work.
- Merciless but principled. You can be devastating, but only when the logic *deserves*
  to be devastated. You never punch for entertainment alone.
- You may use sarcasm, but it must be aimed at the argument, not the agent's character.
  "That's not an argument, that's a prayer" is fair. "You're an idiot" is not.

WHAT YOU TARGET (logical fallacies and debate violations):
1. Circular Reasoning — using the conclusion as evidence for itself
2. Appeal to Authority — "Sequoia invested, so it must be good" without independent analysis
3. Unfalsifiable Claims — arguments constructed so they cannot be challenged
4. Straw Man — misrepresenting another agent's position to defeat the weaker version
5. False Dichotomy — presenting two options when more exist
6. Post-Hoc Rationalization — inventing logical cover for a pre-formed emotional conclusion
7. Moving the Goalposts — changing the standard of evidence mid-argument
8. Numerology — citing statistics or numbers without questioning the assumptions that generated them
9. Narrative Seduction — accepting a compelling story as a substitute for evidence
10. Epistemic Cowardice — deliberately vague statements designed to be defensible from any angle

{_QUANT}{_CITE}YOUR OUTPUT FORMAT:
- Write 3-5 tight, pointed observations.
- Each observation MUST start with the exact line: AGENT: [Agent Name]
  followed by your critique on the next line(s).
- Each critique must quote or closely paraphrase the flawed claim and name the specific logical flaw.
- End with a line starting with VERDICT: followed by a one-line summary of the overall logical quality.
- Total length: 250 words maximum. Shorter is better if the logic speaks for itself.
- Do NOT offer investment opinions. Do NOT speculate about the outcome. Do NOT recommend.

EXAMPLE FORMAT:
AGENT: Growth Agent
Your claim that "X proves Y" is textbook Circular Reasoning because...

AGENT: Founder Agent
The argument that "famous investors validate the model" is an Appeal to Authority...

VERDICT: Two good arguments, one disaster, one word salad.
"""


class SocraticAgent:
    """
    A non-voting, document-blind logic auditor that injects after each
    cross-examination round to savage logical inconsistencies.
    """

    AGENT_NAME: str = AGENT_NAME
    ICON: str = ICON

    def __init__(self) -> None:
        self.llm = make_llm(temperature=0.6, max_tokens=2048)

    def _call(self, human: str) -> str:
        # Document-blind by design: no documents passed.
        return invoke(self.llm, _PERSONA, human)

    def evaluate_round(
        self,
        round_num: int,
        statements: list,           # list[DebateStatement], typed loosely to avoid circular import
    ) -> str:
        """
        Audit the logical quality of a completed cross-examination round.
        Returns the Socratic Agent's critique as a string.
        """
        # Build the transcript of this round for the agent to read
        transcript_parts = []
        for stmt in statements:
            transcript_parts.append(
                f"[{stmt.agent_name}]:\n{stmt.content}"
            )

        prompt = f"""CROSS-EXAMINATION ROUND {round_num} TRANSCRIPT:
{'=' * 60}
{fit_tail(chr(10).join(transcript_parts))}
{'=' * 60}

Audit the above arguments for logical consistency. Find the worst offenders.
Remember: you are judging the *logic of the arguments*, not the investment.
"""
        return self._call(prompt)

    @staticmethod
    def parse_critiques(raw_critique: str) -> list[tuple[str, str]]:
        """
        Parse the structured Socratic output into (agent_name, critique_text) pairs.
        Tolerates markdown around the 'AGENT:' delimiter (e.g. '**AGENT: Growth Agent**').
        """
        critiques: list[tuple[str, str]] = []
        text = raw_critique.replace("**", "").replace("__", "")
        parts = re.split(r"(?mi)^[#>\-\*\s]*AGENT\s*:\s*", text)
        for part in parts[1:]:
            part = part.strip()
            lines = part.split("\n", 1)
            agent_name = lines[0].strip().strip("[]").rstrip(":").strip()
            critique_text = lines[1].strip() if len(lines) > 1 else ""
            m = re.search(r"(?mi)^[#>\-\*\s]*VERDICT\s*:", critique_text)
            if m:
                critique_text = critique_text[:m.start()].strip()
            if agent_name and critique_text:
                critiques.append((agent_name, critique_text))
        return critiques

    @staticmethod
    def parse_defense_ruling(text: str) -> str:
        """'CONCEDE' or 'PRESS' from evaluate_defense output (markdown tolerated)."""
        first = re.sub(r"^[#>\-\*\s]+", "", text.strip().replace("**", "")).upper()
        if first.startswith("CONCEDE"):
            return "CONCEDE"
        return "PRESS"

    def evaluate_defense(self, original_accusation: str, defense_text: str) -> str:
        """
        Evaluate an agent's defense against a logical accusation.
        Returns either a CONCEDE or a PRESS followed by the reasoning.
        """
        prompt = f"""You accused an agent of a logical fallacy. They have responded and defended their argument.

YOUR ORIGINAL ACCUSATION:
{original_accusation}

THEIR DEFENSE:
{defense_text}

Evaluate their defense. Has their logic held up, or are they still committing a foul?
You have two options:
1. CONCEDE: If their defense is logically sound, admit it. Output exactly: 'CONCEDE: [1 sentence admitting their logic holds]'
2. PRESS: If their defense relies on further fallacies or misses the point, absolutely eviscerate them. Output exactly: 'PRESS: [2-3 sentences dismantling their defense]'

Do not be polite. Be precise and devastating."""
        return self._call(prompt)
