"""
orchestrator/deal_math.py — Deal arithmetic computed in code, not by a model.

Inputs are verified ledger facts (by ID) plus explicit, labelled assumptions.
Every output line shows its formula and inputs, so agents dispute assumptions,
not arithmetic.

Computes, when the needed facts exist:
  - entry valuation (post-money; else pre-money + round; else valuation cap)
  - ownership per $1M invested
  - ownership retained after future dilution          [assumption]
  - MOIC at exit = exit value × retention ÷ entry valuation
  - exit scenarios: revenue (actual or company projection) × exit multiples [assumption]
  - exit value required for target MOICs
  - entry valuation ÷ latest actual revenue
  - revenue CAGR across stated years
Consistency checks (formal; reported like ledger conflicts):
  - pre-money + round size ≈ post-money
  - cash on hand ÷ monthly burn ≈ stated runway
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any

from orchestrator.facts import Fact, Ledger, fmt_value

DEFAULT_ASSUMPTIONS = {
    "dilution_per_round": 0.20,          # typical venture round dilution
    "future_rounds": 2,                  # rounds between entry and exit
    "exit_revenue_multiples": (3, 5, 10),
    "moic_targets": (10, 30, 100),
}
CHECK_REL_TOL = 0.10        # pre + round vs post
RUNWAY_REL_TOL = 0.25       # cash/burn vs stated runway


# ── Pure functions ─────────────────────────────────────────────────────────────

def ownership(investment: float, entry_valuation: float) -> float:
    return investment / entry_valuation


def retention(dilution_per_round: float, rounds: int) -> float:
    return (1 - dilution_per_round) ** rounds


def moic(exit_value: float, entry_valuation: float, retained: float) -> float:
    return exit_value * retained / entry_valuation


def exit_value_for_moic(target: float, entry_valuation: float, retained: float) -> float:
    return target * entry_valuation / retained


def runway(cash: float, monthly_burn: float) -> float:
    return cash / monthly_burn


def cagr(v0: float, v1: float, years: int) -> float:
    return (v1 / v0) ** (1 / years) - 1


# ── Assembly from the ledger ───────────────────────────────────────────────────

@dataclass
class DealMath:
    lines: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)       # inconsistencies found by arithmetic
    data: dict = field(default_factory=dict)

    def text(self) -> str:
        if not self.lines and not self.checks:
            return ""
        out = ["=== DEAL MATH (computed in code from ledger facts; assumptions marked [A]) ==="]
        out += self.lines
        if self.checks:
            out += ["", "ARITHMETIC CHECKS FAILED — stated figures do not reconcile:"]
            out += [f"  ⚠ {c}" for c in self.checks]
        return "\n".join(out)


def _entry_valuations(led: Ledger) -> list[tuple[float, str]]:
    """(value, provenance) candidates for the entry valuation, best basis first."""
    posts = led.by_key("post_money_valuation")
    if posts:
        return [(f.value, f"{f.id} post-money") for f in posts]
    pres, rounds = led.by_key("pre_money_valuation"), led.by_key("round_size")
    if pres and rounds:
        return [(p.value + r.value, f"{p.id} pre-money + {r.id} round size") for p in pres for r in rounds]
    caps = led.by_key("valuation_cap")
    if caps:
        return [(f.value, f"{f.id} valuation cap (SAFE; treated as post-money entry)") for f in caps]
    return []


def _revenues(led: Ledger, this_year: int) -> list[tuple[int, Fact, str]]:
    out = []
    for f in led.facts:
        if f.key.startswith("revenue:"):
            year = int(f.key.split(":")[1])
            ctx = f.context.lower()
            kind = ("projection" if ("project" in ctx or "forecast" in ctx or "plan" in ctx or year > this_year)
                    else "actual")
            out.append((year, f, kind))
    return sorted(out, key=lambda t: t[0])


def compute_deal_math(led: Ledger, assumptions: dict | None = None, this_year: int | None = None) -> DealMath:
    a = {**DEFAULT_ASSUMPTIONS, **(assumptions or {})}
    this_year = this_year or _dt.date.today().year
    dm = DealMath()
    L, chk = dm.lines, dm.checks

    # ── Consistency: pre + round ≈ post ──
    for p in led.by_key("pre_money_valuation"):
        for r in led.by_key("round_size"):
            for q in led.by_key("post_money_valuation"):
                implied = p.value + r.value
                if abs(implied - q.value) / q.value > CHECK_REL_TOL:
                    chk.append(f"{p.id} pre-money {fmt_value(p.key, p.value)} + {r.id} round "
                               f"{fmt_value(r.key, r.value)} = {fmt_value(q.key, implied)}, but {q.id} states "
                               f"post-money {fmt_value(q.key, q.value)}")

    # ── Runway: cash ÷ burn vs stated ──
    for c in led.by_key("cash_on_hand"):
        for b in led.by_key("monthly_burn"):
            m = runway(c.value, b.value)
            L.append(f"Runway (computed) = {c.id} cash {fmt_value(c.key, c.value)} ÷ {b.id} burn "
                     f"{fmt_value(b.key, b.value)} = {m:.1f} months")
            for s in led.by_key("runway_months"):
                if abs(m - s.value) / s.value > RUNWAY_REL_TOL:
                    chk.append(f"computed runway {m:.1f} months ({c.id}÷{b.id}) vs {s.id} stated {s.value:g} months")

    # ── Entry valuation & ownership ──
    entries = _entry_valuations(led)[:3]
    if not entries:
        L.append("Entry valuation: not computable (no post-money, pre-money + round, or valuation cap in the ledger).")
        return dm
    keep = retention(a["dilution_per_round"], a["future_rounds"])
    L.append(f"[A] Dilution: {a['dilution_per_round']:.0%} per round × {a['future_rounds']} future rounds "
             f"→ retention = (1 − {a['dilution_per_round']:g})^{a['future_rounds']} = {keep:.3f}")
    revs = _revenues(led, this_year)
    dm.data = {"entries": [], "retention": keep, "assumptions": a}

    for entry, prov in entries:
        scenarios: list[dict[str, Any]] = []
        e: dict[str, Any] = {"entry": entry, "basis": prov, "scenarios": scenarios, "required": {}}
        L.append("")
        L.append(f"Entry valuation = {fmt_value('round_size', entry)} ({prov})")
        L.append(f"  Ownership per $1M invested = $1M ÷ {fmt_value('round_size', entry)} = "
                 f"{ownership(1e6, entry):.4%} at entry → {ownership(1e6, entry) * keep:.4%} after dilution [A]")
        req = ", ".join(f"{t}× needs exit ≥ {fmt_value('round_size', exit_value_for_moic(t, entry, keep))}"
                        for t in a["moic_targets"])
        L.append(f"  Exit value required (MOIC = exit × {keep:.3f} ÷ entry): {req}")
        e["required"] = {t: exit_value_for_moic(t, entry, keep) for t in a["moic_targets"]}
        actuals = [t for t in revs if t[2] == "actual"]
        if actuals:
            y, f, _ = actuals[-1]
            L.append(f"  Entry ÷ latest actual revenue ({f.id} {y}: {fmt_value(f.key, f.value)}) = "
                     f"{entry / f.value:,.1f}× revenue")
        # exit scenarios on the latest (actual or projected) revenue and one mid-point year
        pick = [revs[-1]] if revs else []
        if len(revs) >= 3:
            pick = [revs[len(revs) // 2], revs[-1]]
        for y, f, kind in pick:
            parts = []
            for mult in a["exit_revenue_multiples"]:
                ev = f.value * mult
                x = moic(ev, entry, keep)
                parts.append(f"{mult}× → exit {fmt_value(f.key, ev)} → MOIC {x:.2f}×")
                scenarios.append({"year": y, "fact": f.id, "kind": kind, "multiple": mult,
                                  "exit": ev, "moic": x})
            label = "COMPANY PROJECTION" if kind == "projection" else "actual"
            L.append(f"  [A] Exit at revenue multiple on {f.id} {y} revenue {fmt_value(f.key, f.value)} "
                     f"({label}): " + "; ".join(parts))
        dm.data["entries"].append(e)

    if len(revs) >= 2:
        (y0, f0, _), (y1, f1, k1) = revs[0], revs[-1]
        if y1 > y0 and f0.value > 0:
            L.append("")
            L.append(f"Revenue CAGR {y0}→{y1} ({f0.id}→{f1.id}{', ends in a projection' if k1 == 'projection' else ''})"
                     f" = ({fmt_value(f1.key, f1.value)} ÷ {fmt_value(f0.key, f0.value)})^(1/{y1 - y0}) − 1 = "
                     f"{cagr(f0.value, f1.value, y1 - y0):.0%}")
    return dm
