"""
orchestrator/debate_engine.py

Plain-Python state machine managing the debate phases:
  Phase 1 — Independent Analysis
  Phase 2 — Opening Statements (Risk Agent last, after hearing the panel)
  Phase 3 — Adversarial Cross-Examination (N rounds) + Socratic audit/rebuttals
  Phase 4 — Risk Agent Gauntlet (ruling enforced in code against verified evidence)
  Phase 5 — Panel Vote

Independent calls within a phase run concurrently (PARALLEL_WORKERS): Phase 1, the
panel's openings, each cross-examination round (agents see only the previous round),
the panel's gauntlet responses, and the votes. Their outputs are then processed one
at a time in fixed agent order (revisions, Quant calls, citation checks), so the
transcript is deterministic. Socratic rebuttal exchanges stay sequential.
  Phase 6 — Verdict

Every statement is citation-checked in code (orchestrator/citations.py) and
annotated before any other agent sees it. REVISE lines update scorecards.
"""

from __future__ import annotations

import os
import pickle
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TypeVar

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from agents.base_agent import (
    BaseAgent, DebateStatement, Scorecard, VoteType, parse_revisions, parse_stance,
)
from agents.financial_agent import FinancialAgent
from agents.founder_agent import FounderAgent
from agents.growth_agent import GrowthAgent
from agents.moat_agent import MoatAgent
from agents.quant_agent import QuantAgent
from agents.risk_agent import RiskAgent
from agents.sentiment_agent import SentimentAgent
from agents.socratic_agent import SocraticAgent
from config import (
    CACHE_DIR, CHECKPOINT_DIR, CROSS_EXAM_ROUNDS, DEAL_ASSUMPTIONS, FACT_LEDGER, PANEL_VOTE_THRESHOLD,
    PARALLEL_WORKERS, SHOW_CITATION_CHECKS, SOCRATIC_REFEREE,
)
from ingestion.deal_briefing import DealBriefing
from orchestrator.arguments import ArgumentGraph
from orchestrator.citations import CitationCheck, CitationVerifier
from orchestrator.deal_math import DealMath, compute_deal_math
from orchestrator.facts import FactExtractor, Ledger, ledger_block
from output.report_generator import generate_intermediate_report

console = Console()
_T = TypeVar("_T")
_R = TypeVar("_R")

# Markdown-tolerant: "**CALL_QUANT:** claim"
_QUANT_RE = re.compile(r"(?mi)^[\W_]*CALL_QUANT\s*:[\s*_]*(.+?)[\s*_]*$")


@dataclass
class DebateState:
    """Full mutable state of a single deal debate."""
    briefing: DealBriefing

    # Phase 0
    ledger: Optional[Ledger] = None
    deal_math: Optional[DealMath] = None

    # Phase 1
    scorecards: dict[str, Scorecard] = field(default_factory=dict)
    search_summary: Optional[str] = None
    search_text: str = ""

    # Phase 2
    opening_statements: list[DebateStatement] = field(default_factory=list)

    # Phase 3
    cross_exam_rounds: list[list[DebateStatement]] = field(default_factory=list)

    # Phase 4
    risk_objections: list[str] = field(default_factory=list)
    gauntlet_responses: list[DebateStatement] = field(default_factory=list)
    risk_final_vote: Optional[VoteType] = None
    risk_final_rationale: str = ""
    objection_log: list[tuple[str, str]] = field(default_factory=list)
    risk_enforcement_notes: list[str] = field(default_factory=list)
    risk_evidence: dict[int, bool] = field(default_factory=dict)   # objection → has a found quote
    risk_model_vote: Optional[str] = None                         # Risk's vote before the gate

    # Phase 5
    final_votes: dict[str, VoteType] = field(default_factory=dict)
    final_vote_rationales: dict[str, str] = field(default_factory=dict)

    # Quant lifeline
    quant_tokens: dict[str, int] = field(default_factory=dict)
    quant_calls: list[dict] = field(default_factory=list)

    # Socratic Agent — injections keyed by round number
    socratic_interjections: dict[int, str] = field(default_factory=dict)

    # Audit trail
    args: ArgumentGraph = field(default_factory=ArgumentGraph)  # structured claims + attacks (audit only)
    citation_log: list[dict] = field(default_factory=list)     # one row per «quote»
    score_revisions: list[dict] = field(default_factory=list)

    # Outcome
    verdict: Optional[str] = None
    killed_by_veto: bool = False
    completed: bool = False

    def full_transcript(self) -> str:
        """Assemble the full debate transcript as a string."""
        parts: list[str] = []

        parts.append("═" * 60)
        parts.append(f"DEAL DEBATE: {self.briefing.company_name}")
        parts.append("═" * 60)

        if self.scorecards:
            parts.append("\n── PHASE 1: INDEPENDENT SCORECARDS ──")
            for name, sc in self.scorecards.items():
                parts.append(sc.to_text())

        if self.opening_statements:
            parts.append("\n── PHASE 2: OPENING STATEMENTS ──")
            for s in self.opening_statements:
                parts.append(f"\n[{s.agent_name}]\n{s.content}")

        for i, rnd in enumerate(self.cross_exam_rounds, 1):
            parts.append(f"\n── PHASE 3: CROSS-EXAMINATION ROUND {i} ──")
            for s in rnd:
                parts.append(f"\n[{s.agent_name}]\n{s.content}")

        if self.score_revisions:
            parts.append("\n── SCORE REVISIONS ──")
            for r in self.score_revisions:
                parts.append(f"  {r['agent']}: {r['dimension']} {r['old']} → {r['new']} ({r['phase']}) — {r['reason']}")

        if self.risk_objections:
            parts.append("\n── PHASE 4: RISK AGENT GAUNTLET ──")
            parts.append("Risk Agent Objections:")
            for obj in self.risk_objections:
                parts.append(f"  • {obj}")
            for s in self.gauntlet_responses:
                parts.append(f"\n[{s.agent_name} response]\n{s.content}")
            if self.risk_final_vote:
                parts.append(f"\nRisk Agent ruling: {self.risk_final_vote} — {self.risk_final_rationale}")
                for n in self.risk_enforcement_notes:
                    parts.append(f"  [enforced] {n}")

        if self.final_votes:
            parts.append("\n── PHASE 5: FINAL VOTES ──")
            for name, vote in self.final_votes.items():
                parts.append(f"  {name}: {vote}")

        return "\n".join(parts)

    def lean_transcript(self) -> str:
        """
        Compact transcript for Phase 5 voting — Phase 1 condensed to one line per
        agent; last cross-exam round and the full gauntlet kept.
        """
        parts: list[str] = []

        parts.append("═" * 60)
        parts.append(f"DEAL DEBATE SUMMARY: {self.briefing.company_name}")
        parts.append("═" * 60)

        if self.scorecards:
            parts.append("\n── PHASE 1: SCORECARD SUMMARY (current, after revisions) ──")
            for name, sc in self.scorecards.items():
                parts.append(
                    f"  {name}: {sc.overall_score:.1f}/10 | "
                    f"Vote: {sc.initial_vote} | "
                    f"Thesis: {sc.thesis[:200]}"
                )

        if self.opening_statements:
            parts.append("\n── PHASE 2: OPENING STATEMENTS (condensed) ──")
            for s in self.opening_statements:
                parts.append(f"\n[{s.agent_name}]\n{s.content[:500]}")

        if self.cross_exam_rounds:
            last_round = len(self.cross_exam_rounds)
            parts.append(f"\n── PHASE 3: FINAL CROSS-EXAMINATION (Round {last_round}) ──")
            for s in self.cross_exam_rounds[-1]:
                parts.append(f"\n[{s.agent_name}]\n{s.content}")

        if self.risk_objections:
            parts.append("\n── PHASE 4: RISK AGENT GAUNTLET ──")
            parts.append("Risk Agent Objections:")
            for obj in self.risk_objections:
                parts.append(f"  • {obj}")
            for s in self.gauntlet_responses:
                parts.append(f"\n[{s.agent_name} response]\n{s.content}")
            if self.objection_log:
                parts.append("\nRisk Agent objection log:")
                for obj, status in self.objection_log:
                    parts.append(f"  • {status}")

        return "\n".join(parts)


class DebateEngine:
    """Orchestrates the debate between all agents."""

    def __init__(self, skip_search: bool = False, fresh: bool = False) -> None:
        self.skip_search = skip_search
        self.fresh = fresh

        self.risk_agent = RiskAgent()
        self.financial_agent = FinancialAgent()
        self.growth_agent = GrowthAgent()
        self.sentiment_agent = SentimentAgent()
        self.moat_agent = MoatAgent()
        self.founder_agent = FounderAgent()
        self.socratic_agent = SocraticAgent()
        self.quant_agent = QuantAgent()

        self.panel_agents: list[BaseAgent] = [
            self.financial_agent,
            self.growth_agent,
            self.sentiment_agent,
            self.moat_agent,
            self.founder_agent,
        ]
        self.all_agents: list[BaseAgent] = [self.risk_agent] + self.panel_agents
        self._by_name: dict[str, BaseAgent] = {a.AGENT_NAME: a for a in self.all_agents}

        self.verifier: CitationVerifier = CitationVerifier({})

    # ── Concurrency ─────────────────────────────────────────────────────────────

    @staticmethod
    def _run_parallel(fn: Callable[[_T], _R], items: list[_T]) -> list[_R]:
        """fn(item) for each item, concurrently up to PARALLEL_WORKERS; results in input order."""
        if PARALLEL_WORKERS <= 1 or len(items) <= 1:
            return [fn(x) for x in items]
        with ThreadPoolExecutor(max_workers=min(PARALLEL_WORKERS, len(items))) as pool:
            return list(pool.map(fn, items))

    # ── Persistence ─────────────────────────────────────────────────────────────

    def _save_checkpoint(self, state: DebateState, checkpoint_path: str, phase_name: str) -> None:
        with open(checkpoint_path, "wb") as f:
            pickle.dump(state, f)
        generate_intermediate_report(state, phase_name)

    @staticmethod
    def _cache_path(briefing: DealBriefing, suffix: str) -> str:
        os.makedirs(CACHE_DIR, exist_ok=True)
        return os.path.join(CACHE_DIR, f"{briefing.cache_key}_{suffix}.pkl")

    def _load_cache(self, briefing: DealBriefing, suffix: str) -> Any:
        path = self._cache_path(briefing, suffix)
        if self.fresh or not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return pickle.load(f)

    def _save_cache(self, briefing: DealBriefing, suffix: str, obj: Any) -> None:
        with open(self._cache_path(briefing, suffix), "wb") as f:
            pickle.dump(obj, f)

    # ── Citation checking, annotation, score revisions ─────────────────────────

    def _log_citations(self, state: DebateState, agent: str, phase: str, text: str) -> list[CitationCheck]:
        checks = self.verifier.check_text(text)
        for c in checks:
            state.citation_log.append({
                "agent": agent, "phase": phase, "quote": c.quote, "cited_as": c.cited_source,
                "verified": c.verified, "method": c.method, "found_in": c.found_in,
            })
        return checks

    def _finalize(self, state: DebateState, agent_name: str, phase: str, content: str) -> str:
        """Citation-check a statement, log it, and append the annotation."""
        checks = self._log_citations(state, agent_name, phase, content)   # logged in every condition
        if not SHOW_CITATION_CHECKS:
            return content
        return f"{content}\n\n{CitationVerifier.annotation(checks)}"

    def _apply_revisions(self, state: DebateState, agent: BaseAgent, phase: str, raw: str) -> None:
        if agent.scorecard is None:
            return
        for dim_name, new, reason in parse_revisions(raw):
            dim = agent.scorecard.find_dimension(dim_name)
            if dim is None:
                console.print(f"  [yellow]⚠ {agent.AGENT_NAME} tried to revise unknown dimension '{dim_name}'[/yellow]")
                continue
            old = dim.score
            if old == new:
                continue
            dim.score = new
            agent.scorecard.recompute_overall()
            state.scorecards[agent.AGENT_NAME] = agent.scorecard
            state.score_revisions.append({
                "agent": agent.AGENT_NAME, "phase": phase, "dimension": dim.name,
                "old": dim.score_text() if old is None else old, "new": "N/A" if new is None else new,
                "reason": reason,
            })
            console.print(f"  [magenta]✎ {agent.AGENT_NAME} revises {dim.name}: {old} → {new}[/magenta]")

    def _fact_ids(self, state: DebateState) -> set[str]:
        return {f.id for f in state.ledger.facts} if state.ledger else set()

    def _process_statement(self, state: DebateState, agent: BaseAgent, stmt: DebateStatement) -> DebateStatement:
        """Revisions → argument IDs → Quant lifeline → citation check. Returns the final annotated statement."""
        self._apply_revisions(state, agent, stmt.phase, stmt.content)
        content = state.args.add_statement(agent.AGENT_NAME, stmt.phase, stmt.content,
                                           self.verifier, self._fact_ids(state))
        content, _ = self._handle_quant_call(agent.AGENT_NAME, content, state)
        content = self._finalize(state, agent.AGENT_NAME, stmt.phase, content)
        console.print(content)
        return DebateStatement(agent_name=agent.AGENT_NAME, phase=stmt.phase, content=content)

    # ── Lifelines ──────────────────────────────────────────────────────────────

    @staticmethod
    def _splice(raw: str, match: re.Match, header: str, body: str, agent_name: str) -> str:
        before, after = raw[:match.start()].strip(), raw[match.end():].strip()
        out = f"{before}\n\n" if before else ""
        out += f"{header}\n{body}"
        if after:
            out += f"\n\n[{agent_name} continues]:\n{after}"
        return out

    def _handle_quant_call(self, agent_name: str, raw_content: str, state: DebateState) -> tuple[str, bool]:
        if state.quant_tokens.get(agent_name, 0) <= 0:
            return raw_content, False
        match = _QUANT_RE.search(raw_content)
        if not match:
            return raw_content, False
        claim = match.group(1).strip()
        state.quant_tokens[agent_name] -= 1
        console.print(Panel(
            f"[bold cyan]🧮 {agent_name} calls the Quant Agent to verify the math![/bold cyan]\nClaim: {claim}",
            title="QUANT AUDITOR", border_style="cyan",
        ))
        verdict = self.quant_agent.verify_claim(
            briefing=state.briefing,
            debate_context=state.full_transcript(),
            specific_claim=claim,
            calling_agent=agent_name,
            verifier=self.verifier,
        )
        state.quant_calls.append({"calling_agent": agent_name, "claim": claim})
        return self._splice(
            raw_content, match,
            f"[{agent_name} requested a quantitative audit]\n\n🧮 **Quant Agent** (summoned by {agent_name}):",
            verdict, agent_name,
        ), True

    # ── Run ────────────────────────────────────────────────────────────────────

    def run(self, briefing: DealBriefing) -> DebateState:
        os.makedirs(CHECKPOINT_DIR, exist_ok=True)
        checkpoint_path = os.path.join(CHECKPOINT_DIR, f"{briefing.cache_key}_state.pkl")

        state: Optional[DebateState] = None
        if os.path.exists(checkpoint_path) and not self.fresh:
            with open(checkpoint_path, "rb") as f:
                loaded = pickle.load(f)
            if getattr(loaded, "completed", False):
                console.print("[dim]Previous checkpoint is a completed run — starting a new debate.[/dim]")
            else:
                console.print(f"[bold green]Resuming from checkpoint:[/bold green] {checkpoint_path}")
                state = loaded
                briefing = state.briefing
        if state is None:
            state = DebateState(briefing=briefing)

        for agent in self.all_agents:
            agent.bind(briefing)
        briefing.document_block()   # build once; records any truncation
        if briefing.truncated_files:
            console.print(f"[bold yellow]⚠ Documents truncated to fit MAX_DOC_CHARS. Omitted: {', '.join(briefing.truncated_files)}[/bold yellow]")

        if not state.quant_tokens:
            for agent in self.all_agents:
                state.quant_tokens[agent.AGENT_NAME] = 2

        console.rule(f"[bold cyan]VC DEBATE: {briefing.company_name}[/bold cyan]")
        console.print(briefing.summary_stats(), style="dim")

        self.verifier = CitationVerifier.from_briefing(briefing)
        self._phase0_ledger(state, checkpoint_path)
        self._phase1_analyze(state, checkpoint_path)
        self._sync_agents(state)
        self._phase2_opening(state, checkpoint_path)
        self._phase3_cross_exam(state, checkpoint_path)
        self._phase4_gauntlet(state, checkpoint_path)

        if state.killed_by_veto:
            console.print(Panel(
                "[bold red]⚖️  RISK AGENT VETO — DEAL KILLED[/bold red]\n"
                f"Rationale: {state.risk_final_rationale}",
                title="VETO EXERCISED", border_style="red",
            ))
            state.verdict = "VETOED"
        else:
            self._phase5_vote(state)
            self._phase6_verdict(state)

        state.completed = True
        if os.path.exists(checkpoint_path):
            os.remove(checkpoint_path)   # a finished run is not resumable; reruns start fresh
        return state

    # ── Phase 0 ────────────────────────────────────────────────────────────────

    def _phase0_ledger(self, state: DebateState, checkpoint_path: str) -> None:
        if not FACT_LEDGER:
            return
        if state.ledger is None:
            cached = self._load_cache(state.briefing, "phase0")
            if cached:
                console.print("[dim]⚡ Fact ledger loaded from cache[/dim]")
                state.ledger = cached
            else:
                console.rule("[yellow]Phase 0 — Fact Ledger[/yellow]")
                state.ledger = FactExtractor().extract(state.briefing, self.verifier)
                self._save_cache(state.briefing, "phase0", state.ledger)
        state.deal_math = compute_deal_math(state.ledger, DEAL_ASSUMPTIONS)
        state.briefing.ledger_block = ledger_block(state.ledger, state.deal_math.text())
        led = state.ledger
        console.print(f"[green]✓ Fact ledger:[/green] {len(led.facts)} verified facts, "
                      f"{len(led.rejected)} rejected, {len(led.conflicts)} conflicts, "
                      f"{len(state.deal_math.checks)} failed arithmetic checks")
        for c in led.conflicts:
            console.print(f"  [yellow]⚠ {c.summary()}[/yellow]")
        for c in state.deal_math.checks:
            console.print(f"  [yellow]⚠ {c}[/yellow]")
        self._save_checkpoint(state, checkpoint_path, "Phase 0")

    def _sync_agents(self, state: DebateState) -> None:
        """After Phase 1 (fresh, cached, or resumed), give every agent its scorecard and context."""
        for agent in self.all_agents:
            if agent.AGENT_NAME in state.scorecards:
                agent.scorecard = state.scorecards[agent.AGENT_NAME]
        if state.search_text:
            self.sentiment_agent.extra_context = (
                "=== EXTERNAL SEARCH RESULTS (not deal documents) ===\n"
                "Cite these as «verbatim snippet text» [SEARCH, URL].\n\n"
                f"{state.search_text}\n=== END EXTERNAL SEARCH RESULTS ==="
            )
        self.verifier = CitationVerifier.from_briefing(state.briefing, extra={"SEARCH": state.search_text})
        # Citation-check Phase 1 scorecards (idempotent)
        state.citation_log = [r for r in state.citation_log if r["phase"] != "phase1"]
        for name, sc in state.scorecards.items():
            for d in sc.dimensions:
                dchecks = self.verifier.check_text(d.rationale)
                d.evidence = ("found" if any(c.verified for c in dchecks)
                              else "not found" if dchecks else "none")
            text = "\n".join(d.rationale for d in sc.dimensions) + "\n" + sc.thesis
            checks = self._log_citations(state, name, "phase1", text)
            sc.citation_summary = CitationVerifier.annotation(checks)

    def _print_scores(self, state: DebateState) -> None:
        for name, sc in state.scorecards.items():
            veto_tag = " ⚖️" if name == self.risk_agent.AGENT_NAME else ""
            vote_color = "red" if sc.initial_vote == "PASS" else "green"
            console.print(
                f"  {name}{veto_tag} | Score: [bold]{sc.overall_score:.1f}/10[/bold]"
                f" | Vote: [{vote_color}]{sc.initial_vote}[/{vote_color}]"
            )

    # ── Phase 1 ────────────────────────────────────────────────────────────────

    def _phase1_analyze(self, state: DebateState, checkpoint_path: str) -> None:
        if len(state.scorecards) == len(self.all_agents):
            console.print("[dim]Skipping Phase 1 (already completed in checkpoint)[/dim]")
            self._print_scores(state)
            return

        cached = self._load_cache(state.briefing, "phase1")
        if cached:
            console.print("[dim]⚡ Phase 1 loaded from cache (same documents, model, and prompt version)[/dim]")
            state.scorecards = cached["scorecards"]
            state.search_summary = cached.get("search_summary")
            state.search_text = cached.get("search_text", "")
            self._print_scores(state)
            return

        console.rule("[yellow]Phase 1 — Independent Analysis[/yellow]")
        todo = [a for a in self.all_agents if a.AGENT_NAME not in state.scorecards]

        def work(agent: BaseAgent) -> tuple[BaseAgent, Scorecard, Optional[tuple[Optional[str], str]]]:
            if isinstance(agent, SentimentAgent):
                sc, summary, text = agent.analyze_with_search(state.briefing, skip_search=self.skip_search)
                return agent, sc, (summary, text)
            return agent, agent.analyze(state.briefing), None

        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
            tasks = {a.AGENT_NAME: progress.add_task(f"[cyan]{a.AGENT_NAME}[/cyan] analyzing...", total=1) for a in todo}
            with ThreadPoolExecutor(max_workers=max(1, PARALLEL_WORKERS)) as pool:
                futures = [pool.submit(work, a) for a in todo]
                for fut in as_completed(futures):
                    agent, sc, search = fut.result()
                    state.scorecards[agent.AGENT_NAME] = sc
                    if search is not None:
                        state.search_summary, state.search_text = search
                        console.print(f"[dim]Sentiment search: {state.search_summary}[/dim]")
                    progress.update(tasks[agent.AGENT_NAME], completed=1)
                    self._save_checkpoint(state, checkpoint_path, "Phase 1")

        # Keep a stable agent order regardless of completion order
        state.scorecards = {a.AGENT_NAME: state.scorecards[a.AGENT_NAME] for a in self.all_agents}
        self._save_cache(state.briefing, "phase1", {
            "scorecards": state.scorecards,
            "search_summary": state.search_summary,
            "search_text": state.search_text,
        })
        console.print("[dim]💾 Phase 1 cached for future runs (use --fresh to bypass).[/dim]")
        self._print_scores(state)

    # ── Phase 2 ────────────────────────────────────────────────────────────────

    def _phase2_opening(self, state: DebateState, checkpoint_path: str) -> None:
        if len(state.opening_statements) == len(self.all_agents):
            console.print("[dim]Skipping Phase 2 (already completed in checkpoint)[/dim]")
            return

        cached = self._load_cache(state.briefing, "phase2")
        if isinstance(cached, dict):
            console.print("[dim]⚡ Phase 2 loaded from cache[/dim]")
            state.opening_statements = cached["openings"]
            state.args = cached["args"]          # opening claims, already registered with global IDs
            for s in state.opening_statements:
                self._log_citations(state, s.agent_name, s.phase, s.content)
            return

        console.rule("[yellow]Phase 2 — Opening Statements[/yellow]")
        todo = [a for a in self.panel_agents
                if not any(s.agent_name == a.AGENT_NAME for s in state.opening_statements)]
        raw = self._run_parallel(lambda a: a.opening_statement(state.briefing), todo)
        for agent, stmt in zip(todo, raw):
            console.print(f"\n[bold cyan]{agent.AGENT_NAME}:[/bold cyan]")
            state.opening_statements.append(self._process_statement(state, agent, stmt))
        if todo:
            self._save_checkpoint(state, checkpoint_path, "Phase 2")

        # Risk agent last — and it actually receives the panel's openings.
        if not any(s.agent_name == self.risk_agent.AGENT_NAME for s in state.opening_statements):
            console.print("\n[bold red]⚖️  Risk Agent (VETO HOLDER):[/bold red]")
            panel_openings = [s for s in state.opening_statements if s.agent_name != self.risk_agent.AGENT_NAME]
            stmt = self.risk_agent.opening_statement(state.briefing, prior_statements=panel_openings)
            state.opening_statements.append(self._process_statement(state, self.risk_agent, stmt))

        self._save_cache(state.briefing, "phase2", {"openings": state.opening_statements, "args": state.args})
        self._save_checkpoint(state, checkpoint_path, "Phase 2 - Complete")

    # ── Phase 3 ────────────────────────────────────────────────────────────────

    def _resolve_agent(self, target_name: str) -> Optional[BaseAgent]:
        t = target_name.lower().strip()
        for name, a in self._by_name.items():
            if name.lower() == t:
                return a
        for name, a in self._by_name.items():
            if t and (t in name.lower() or name.lower().split()[0] == t.split()[0]):
                return a
        return None

    def _phase3_cross_exam(self, state: DebateState, checkpoint_path: str) -> None:
        for round_num in range(1, CROSS_EXAM_ROUNDS + 1):
            if len(state.cross_exam_rounds) >= round_num:
                console.print(f"[dim]Skipping Phase 3 Round {round_num} (already completed in checkpoint)[/dim]")
                continue

            console.rule(f"[yellow]Phase 3 — Cross-Examination Round {round_num}[/yellow]")
            round_statements: list[DebateStatement] = []

            prior_statements = state.opening_statements if round_num == 1 else state.cross_exam_rounds[-1]

            def examine(agent: BaseAgent) -> DebateStatement:
                other_stmts = [s for s in prior_statements if s.agent_name != agent.AGENT_NAME]
                other_cards = [sc for name, sc in state.scorecards.items() if name != agent.AGENT_NAME]
                return agent.cross_examine(state.briefing, round_num, other_stmts, other_cards)

            raw_round = self._run_parallel(examine, self.all_agents)
            for agent, stmt in zip(self.all_agents, raw_round):
                console.print(f"\n[bold cyan]{agent.AGENT_NAME}:[/bold cyan]")
                round_statements.append(self._process_statement(state, agent, stmt))

            state.cross_exam_rounds.append(round_statements)

            if not SOCRATIC_REFEREE:        # ablation: no referee audit, no rebuttal exchanges
                self._save_checkpoint(state, checkpoint_path, f"Phase 3 Round {round_num}")
                continue

            # ── Socratic audit ──────────────────────────────────────────────────
            console.print(f"\n[dim]🃏 Socratic Agent reviewing Round {round_num}...[/dim]")
            critique = self.socratic_agent.evaluate_round(
                round_num=round_num,
                statements=round_statements,
            )
            state.socratic_interjections[round_num] = critique
            console.print(Panel(critique, title=f"🃏 Socratic Agent — Round {round_num} Audit", border_style="yellow"))
            round_statements.append(DebateStatement(
                agent_name=self.socratic_agent.AGENT_NAME, phase=f"socratic_audit_r{round_num}", content=critique,
            ))

            # ── Rebuttal loop ───────────────────────────────────────────────────
            for target_name, critique_text in SocraticAgent.parse_critiques(critique):
                target_agent = self._resolve_agent(target_name)
                if target_agent is None:
                    console.print(f"  [dim yellow]⚠ Socratic targeted unknown agent '{target_name}' — skipping[/dim yellow]")
                    continue

                current = critique_text
                for depth in range(1, 4):
                    console.print(Panel(
                        f"[bold yellow]🃏 → {target_agent.AGENT_NAME}:[/bold yellow] {current}",
                        title="ACCUSATION" if depth == 1 else f"Socratic Agent Presses (Turn {depth})",
                        border_style="yellow",
                    ))
                    raw = target_agent.rebut_socratic(current, round_num, depth=depth)
                    stance = parse_stance(raw.content)
                    had_lifeline = bool(_QUANT_RE.search(raw.content))
                    rebuttal = self._process_statement(state, target_agent, raw)
                    round_statements.append(rebuttal)

                    if stance == "STALEMATE":
                        console.print("\n[bold red]💥 STALEMATE REACHED! Neither side will yield.[/bold red]")
                        break
                    # A plain concession ends the exchange; a concession plus a Quant call
                    # (conceding the foul, defending the thesis) goes to the Socratic Agent.
                    if stance == "CONCEDE" and not had_lifeline:
                        break
                    if depth == 3:
                        break

                    console.print("\n[dim]🃏 Socratic Agent evaluating defense...[/dim]")
                    ruling = self.socratic_agent.evaluate_defense(current, rebuttal.content)
                    round_statements.append(DebateStatement(
                        agent_name=self.socratic_agent.AGENT_NAME,
                        phase=f"socratic_eval_r{round_num}_d{depth}",
                        content=ruling,
                    ))
                    if SocraticAgent.parse_defense_ruling(ruling) == "CONCEDE":
                        console.print("\n[bold green]🃏 Socratic Agent Concedes![/bold green]")
                        console.print(ruling)
                        break
                    current = re.sub(r"(?i)^[\W_]*PRESS\W*", "", ruling.strip()).strip()

            state.cross_exam_rounds[-1] = round_statements
            self._save_checkpoint(state, checkpoint_path, f"Phase 3 Round {round_num}")

    # ── Phase 4 ────────────────────────────────────────────────────────────────

    def _phase4_gauntlet(self, state: DebateState, checkpoint_path: str) -> None:
        if state.risk_final_vote is not None:
            console.print("[dim]Skipping Phase 4 (already completed in checkpoint)[/dim]")
            return

        console.rule("[bold red]Phase 4 — Risk Agent Gauntlet[/bold red]")
        # Objections come from the Risk Agent's CURRENT scorecard (after debate revisions).
        objections = self.risk_agent.extract_top_objections()
        if not objections:
            raise RuntimeError("Risk Agent produced no objections — its scorecard is missing or empty.")
        state.risk_objections = objections

        console.print("\n[bold red]Risk Agent's Top Objections:[/bold red]")
        for i, obj in enumerate(objections, 1):
            console.print(f"  {i}. {obj}", style="red")

        state.args.add_objections(objections, self.verifier, self._fact_ids(state))

        console.print("\n[bold cyan]Panel Response:[/bold cyan]")
        state.gauntlet_responses = []
        raw = self._run_parallel(lambda a: a.risk_gauntlet_challenge(state.briefing, objections), self.panel_agents)
        for agent, resp in zip(self.panel_agents, raw):
            console.print(f"\n[bold]{agent.AGENT_NAME}:[/bold]")
            state.gauntlet_responses.append(self._process_statement(state, agent, resp))

        console.print("\n[bold red]⚖️  Risk Agent Final Ruling:[/bold red]")
        vote, rationale, obj_log, notes = self.risk_agent.evaluate_gauntlet_responses(
            state.briefing, state.gauntlet_responses, objections, self.verifier,
        )
        state.risk_final_vote = vote
        state.risk_final_rationale = rationale
        state.objection_log = obj_log
        state.risk_enforcement_notes = notes
        state.risk_evidence = getattr(self.risk_agent, "evidence_by_objection", {})
        state.risk_model_vote = getattr(self.risk_agent, "model_vote", None)
        state.final_votes[self.risk_agent.AGENT_NAME] = vote
        state.args.add_ruling(obj_log, self.verifier, self._fact_ids(state))
        self._log_citations(state, self.risk_agent.AGENT_NAME, "risk_ruling",
                            "\n".join(s for _, s in obj_log))

        vote_color = "red" if vote != "INVEST" else "green"
        console.print(f"\nRisk Agent Final Vote: [{vote_color}]{vote}[/{vote_color}]")
        console.print(rationale)
        for n in notes:
            console.print(f"  [bold yellow]⚙ enforced:[/bold yellow] {n}")

        if vote != "INVEST":
            state.killed_by_veto = True

        self._save_checkpoint(state, checkpoint_path, "Phase 4 - Complete")

    # ── Phase 5 ────────────────────────────────────────────────────────────────

    def _phase5_vote(self, state: DebateState) -> None:
        console.rule("[yellow]Phase 5 — Final Panel Vote[/yellow]")
        transcript = state.lean_transcript()
        results = self._run_parallel(lambda a: a.cast_final_vote(state.briefing, transcript), self.panel_agents)
        for agent, (vote, rationale) in zip(self.panel_agents, results):
            state.final_votes[agent.AGENT_NAME] = vote
            state.final_vote_rationales[agent.AGENT_NAME] = rationale
            color = {"INVEST": "green", "PASS": "red"}.get(vote, "yellow")
            suffix = " (output unparseable — counted as not INVEST)" if vote == "ABSTAIN" else ""
            console.print(f"  {agent.AGENT_NAME}: [{color}]{vote}[/{color}]{suffix}")

    # ── Phase 6 ────────────────────────────────────────────────────────────────

    def _phase6_verdict(self, state: DebateState) -> None:
        panel_votes = [v for name, v in state.final_votes.items() if name != self.risk_agent.AGENT_NAME]
        positive_votes = sum(1 for v in panel_votes if v == "INVEST")

        risk_vote = state.risk_final_vote or "PASS"
        passes_gate = risk_vote == "INVEST"
        passes_panel = positive_votes >= PANEL_VOTE_THRESHOLD
        state.verdict = "INVEST" if (passes_gate and passes_panel) else "PASS"

        console.rule("[bold]Phase 6 — Final Verdict[/bold]")
        color = "green" if state.verdict == "INVEST" else "red"
        console.print(Panel(
            f"[bold {color}]{state.verdict}[/bold {color}]\n\n"
            f"Risk Gate: [bold]{'✅ CLEARED' if passes_gate else '❌ BLOCKED'}[/bold] ({risk_vote})\n"
            f"Panel: [bold]{positive_votes}/{len(panel_votes)}[/bold] positive votes "
            f"(threshold: {PANEL_VOTE_THRESHOLD})",
            title=f"VERDICT: {state.briefing.company_name}",
            border_style=color,
        ))
