"""
orchestrator/arguments.py — Argument audit (structured claims + attack graph).

AUDIT ONLY: this never changes votes or the verdict. It records, for the report,
which claims were made, on what grounds, which were attacked, and which stand.

Agents append a structured block to openings, cross-examination and gauntlet replies:

  CLAIMS:
  C1: <claim> | GROUNDS: «quote» [File, p. N] or F3 | INFERENCE: <why the grounds support it> | DEFEATER: <what would refute it>
  ATTACKS:
  <target claim ID, e.g. GROW.R1.C2 or OBJ2> | <UNDERMINE|REBUT|UNDERCUT> | BY C1 | <reason>

  UNDERMINE = the target's grounds are wrong or unsupported
  REBUT     = the target's conclusion is false
  UNDERCUT  = the grounds may be true but do not support the conclusion (the inference fails)

Code assigns global IDs (AGENT.PHASE.Cn), checks each claim's grounds (a found «quote» or a
ledger fact ID), and computes which claims stand:

  - attack A→B is a DEFEAT unless A has no verified grounds and B does
    (an unsupported claim cannot knock out a supported one; ASPIC+-style preference)
  - grounded semantics on the defeat graph: IN = all defeaters OUT; OUT = some defeater IN;
    UNDEC = unresolved (e.g. mutual defeat). This is the most sceptical standard.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from orchestrator.citations import CitationVerifier


def clean_line(line: str) -> str:
    """Strip markdown decoration (same rule as agents.base_agent.clean_line; no import to avoid a cycle)."""
    line = line.strip().replace("**", "").replace("__", "")
    return re.sub(r"^[#>\-\*\s]+", "", line).strip()

AGENT_CODES = {
    "Risk Agent": "RISK", "Financial Agent": "FIN", "Growth Agent": "GROW",
    "Sentiment Agent": "SENT", "Moat Agent": "MOAT", "Founder Agent": "FOUND",
}
ATTACK_TYPES = ("UNDERMINE", "REBUT", "UNDERCUT")

_CLAIM_RE = re.compile(r"^C(\d+)\s*[:.)]\s*(.+)$", re.I)
_ATTACK_RE = re.compile(
    r"^\[?([A-Z]+\.[A-Z0-9]+\.C\d+|OBJ\d+)\]?\s*\|\s*(UNDERMINE|REBUT|UNDERCUT)\s*\|\s*BY\s*C(\d+)\s*\|\s*(.*)$", re.I)
_FIELD_RE = re.compile(r"\|\s*(GROUNDS|INFERENCE|DEFEATER)\s*:\s*", re.I)
_FACT_ID = re.compile(r"\bF(\d+)\b")

FORMAT_INSTRUCTIONS = """ARGUMENT STRUCTURE (append at the end of this statement, machine-read for the audit):
CLAIMS:
C1: <one-sentence claim> | GROUNDS: «verbatim quote» [File, p. N] or ledger fact ID (e.g. F3) | INFERENCE: <why the grounds support the claim> | DEFEATER: <what finding would refute it>
(1–3 claims; number them C1, C2, C3)
ATTACKS:
<target claim ID as shown in other statements, e.g. GROW.R1.C2 or OBJ2> | <UNDERMINE|REBUT|UNDERCUT> | BY C<n> | <one-line reason>
(UNDERMINE = their grounds are wrong; REBUT = their conclusion is false; UNDERCUT = their grounds do not
support their conclusion. List only real attacks; write "none" if you attack nothing.)"""


@dataclass
class Claim:
    id: str
    agent: str
    phase: str
    text: str
    grounds: str = ""
    inference: str = ""
    defeater: str = ""
    grounded: bool = False          # grounds include a found quote or a valid ledger fact ID


@dataclass
class Attack:
    src: str
    dst: str
    type: str
    reason: str
    agent: str
    phase: str
    defeat: bool = False


@dataclass
class ArgumentGraph:
    claims: dict[str, Claim] = field(default_factory=dict)
    attacks: list[Attack] = field(default_factory=list)
    dangling: list[dict] = field(default_factory=list)      # attacks on unknown targets / missing BY
    statements_without_block: list[str] = field(default_factory=list)

    # ── building ──────────────────────────────────────────────────────────────
    def add_objections(self, objections: list[str], verifier: CitationVerifier, fact_ids: set[str]) -> None:
        for i, text in enumerate(objections, 1):
            cid = f"OBJ{i}"
            self.claims[cid] = Claim(cid, "Risk Agent", "gauntlet", text, grounds=text,
                                     grounded=_grounded(text, verifier, fact_ids))

    def add_ruling(
        self,
        objection_log: list[tuple[str, str]],
        verifier: CitationVerifier,
        fact_ids: Optional[set[str]] = None,
        include_partial: bool = True,
    ) -> None:
        """Register the Risk Agent's Phase-4 ruling on OBJ1..OBJn as undercutting counter-attacks."""
        allowed = ("UNRESOLVED", "PARTIAL") if include_partial else ("UNRESOLVED",)
        fids = fact_ids or set()
        for idx, (_obj, status_line) in enumerate(objection_log, 1):
            obj_id = f"OBJ{idx}"
            if obj_id not in self.claims:
                continue
            m = re.match(r"^\[(RESOLVED|PARTIAL|UNRESOLVED)\]\s*(.*)$", status_line.strip())
            if not m:
                continue
            status, expl = m.group(1), m.group(2)
            if status not in allowed:
                continue
            cid = f"RISK.RULING.C{idx}"
            self.claims[cid] = Claim(
                cid,
                "Risk Agent",
                "risk_ruling",
                status_line,
                grounds=expl,
                grounded=_grounded(expl, verifier, fids),
            )
            for a in list(self.attacks):
                if a.dst == obj_id and ".G.C" in a.src:
                    self.attacks.append(
                        Attack(cid, a.src, "UNDERCUT", status_line, "Risk Agent", "risk_ruling")
                    )

    def add_statement(self, agent: str, phase: str, raw: str,
                      verifier: CitationVerifier, fact_ids: set[str]) -> str:
        """Parse the CLAIMS/ATTACKS block, register it, and return the text with global IDs."""
        code = AGENT_CODES.get(agent)
        if code is None:
            return raw
        tag = _phase_tag(phase)
        prefix = f"{code}.{tag}."
        local: dict[str, str] = {}
        lines = raw.splitlines()
        mode: Optional[str] = None
        found_block = False
        out_lines = []
        for line in lines:
            c = clean_line(line)
            up = c.upper()
            if up.startswith("CLAIMS"):
                mode, found_block = "claims", True
                out_lines.append(line)
                continue
            if up.startswith("ATTACKS"):
                mode = "attacks"
                out_lines.append(line)
                continue
            if mode == "claims":
                m = _CLAIM_RE.match(c)
                if m:
                    n = m.group(1)
                    gid = f"{prefix}C{n}"
                    # same agent+phase can recur (e.g. rebuttals); keep IDs unique
                    k = 2
                    while gid in self.claims:
                        gid = f"{prefix}C{n}.{k}"
                        k += 1
                    local[n] = gid
                    text, fields = _split_fields(m.group(2))
                    self.claims[gid] = Claim(
                        gid, agent, phase, text, fields.get("GROUNDS", ""), fields.get("INFERENCE", ""),
                        fields.get("DEFEATER", ""), _grounded(fields.get("GROUNDS", ""), verifier, fact_ids),
                    )
                    out_lines.append(re.sub(r"\bC" + n + r"\b", gid, line, count=1))
                    continue
            if mode == "attacks":
                m = _ATTACK_RE.match(c)
                if m:
                    dst, typ, by, reason = m.group(1).upper(), m.group(2).upper(), m.group(3), m.group(4).strip()
                    src = local.get(by)
                    if src is None or dst not in self.claims:
                        self.dangling.append({"agent": agent, "phase": phase, "target": dst, "by": f"C{by}",
                                              "reason": "unknown target" if dst not in self.claims else "BY claim missing"})
                    else:
                        self.attacks.append(Attack(src, dst, typ, reason, agent, phase))
                    out_lines.append(line.replace(f"BY C{by}", f"BY {src or 'C' + by}", 1))
                    continue
            out_lines.append(line)
        if not found_block and tag in ("OPEN", "G") or (not found_block and tag.startswith("R")):
            self.statements_without_block.append(f"{agent} [{phase}]")
        return "\n".join(out_lines)

    # ── semantics ─────────────────────────────────────────────────────────────
    def compute(self) -> dict[str, str]:
        """Grounded labelling on the defeat graph. Returns {claim id: IN|OUT|UNDEC}."""
        for a in self.attacks:
            s, t = self.claims[a.src], self.claims[a.dst]
            a.defeat = not (t.grounded and not s.grounded)
        defeaters: dict[str, set[str]] = {cid: set() for cid in self.claims}
        for a in self.attacks:
            if a.defeat and a.src != a.dst:
                defeaters[a.dst].add(a.src)
        label: dict[str, str] = {}
        changed = True
        while changed:
            changed = False
            for cid in self.claims:
                if cid in label:
                    continue
                ds = defeaters[cid]
                if all(label.get(d) == "OUT" for d in ds):
                    label[cid] = "IN"
                    changed = True
                elif any(label.get(d) == "IN" for d in ds):
                    label[cid] = "OUT"
                    changed = True
        for cid in self.claims:
            label.setdefault(cid, "UNDEC")
        return label

    def rows(self) -> list[dict]:
        label = self.compute()
        out = []
        for cid, c in self.claims.items():
            att = [a for a in self.attacks if a.dst == cid]
            out.append({
                "id": cid, "agent": c.agent, "phase": c.phase, "claim": c.text,
                "grounded": c.grounded, "status": label[cid],
                "attacked_by": [f"{a.src} ({a.type.lower()})" for a in att],
                "defeated_by": [a.src for a in att if a.defeat and label[a.src] == "IN"],
            })
        return out


def _phase_tag(phase: str) -> str:
    if phase == "opening":
        return "OPEN"
    m = re.search(r"r(\d+)", phase)
    if phase.startswith("cross_exam") and m:
        return f"R{m.group(1)}"
    if phase.startswith("gauntlet"):
        return "G"
    if phase.startswith("socratic_rebuttal") and m:
        return f"S{m.group(1)}"
    return re.sub(r"\W", "", phase.upper())[:6] or "X"


def _split_fields(rest: str) -> tuple[str, dict[str, str]]:
    parts = _FIELD_RE.split(rest)
    text = parts[0].strip(" |")
    fields = {}
    for i in range(1, len(parts) - 1, 2):
        fields[parts[i].upper()] = parts[i + 1].strip(" |")
    return text, fields


def _grounded(grounds: str, verifier: CitationVerifier, fact_ids: set[str]) -> bool:
    if any(f"F{m.group(1)}" in fact_ids for m in _FACT_ID.finditer(grounds)):
        return True
    return any(c.verified for c in verifier.check_text(grounds))
