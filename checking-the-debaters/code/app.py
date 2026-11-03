"""
app.py — CLI entry point for the VC Agent Debate System.

Usage:
    python app.py --company "Acme Corp" --docs /path/to/dd.pdf /path/to/deck.pptx
    python app.py --company "Acme Corp" --docs-dir ./deal_docs/acme/
    python app.py --company "Acme Corp" --docs-dir ./deal_docs/acme/ --fresh --skip-search

--docs-dir includes subfolders. Caches and checkpoints are keyed on the
document contents, model, and prompt version; --fresh ignores them all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from config import CACHE_DIR, LLM_BACKEND, ACTIVE_MODEL

app = typer.Typer(
    name="vc-debate",
    help="Multi-agent VC investment debate system. Feed it deal docs; get a verdict.",
    add_completion=False,
)
console = Console()


@app.command()
def analyze(
    company: str = typer.Option(..., "--company", "-c", help="Company name (for report headers)"),
    docs: Optional[list[Path]] = typer.Option(
        None, "--docs", "-d",
        help="One or more document paths (PDF, DOCX, PPTX, XLSX, CSV, TXT, MD)",
        exists=True,
    ),
    docs_dir: Optional[Path] = typer.Option(
        None, "--docs-dir",
        help="Directory of deal documents — all supported files, including in subfolders",
        exists=True, file_okay=False, dir_okay=True,
    ),
    output_dir: str = typer.Option(
        "./output/reports", "--output", "-o",
        help="Directory to write Markdown + JSON reports",
    ),
    skip_search: bool = typer.Option(
        False, "--skip-search",
        help="Skip external web search for the Sentiment Agent (document-only mode)",
    ),
    fresh: bool = typer.Option(
        False, "--fresh",
        help="Ignore all caches and checkpoints and re-run every LLM call",
    ),
    no_compress: bool = typer.Option(
        False, "--no-compress",
        help="Send documents as parsed (skip local compression: watermark, headers, duplicates, templates)",
    ),
) -> None:
    """
    Run the full 6-agent VC debate on a set of deal documents.
    Requires CEREBRAS_API_KEY, ANTHROPIC_API_KEY or GOOGLE_API_KEY in your .env file.
    """
    from ingestion.deal_briefing import build_deal_briefing, fingerprint_documents, load_or_parse
    from ingestion.document_parser import SUPPORTED_EXTENSIONS, collect_documents
    from orchestrator.debate_engine import DebateEngine
    from output.report_generator import generate_report

    # ── Collect document paths ──────────────────────────────────────────────────
    document_paths: list[Path] = []
    if docs:
        for p in docs:
            if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
                console.print(f"[bold red]Error:[/bold red] unsupported file type: {p.name} "
                              f"(supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))})")
                raise typer.Exit(code=1)
        document_paths.extend(docs)
    if docs_dir:
        document_paths.extend(collect_documents(docs_dir, recursive=True))
    document_paths = sorted(set(p.resolve() for p in document_paths))

    if not document_paths:
        console.print("[bold red]Error:[/bold red] No documents provided. Use --docs or --docs-dir.")
        raise typer.Exit(code=1)

    console.print(Panel(
        f"[bold cyan]VC Agent Debate System[/bold cyan]\n"
        f"Company: [bold]{company}[/bold]\n"
        f"Model: {LLM_BACKEND} / {ACTIVE_MODEL}\n"
        f"Documents: {len(document_paths)} file(s)\n"
        f"Output: {output_dir}",
        border_style="cyan",
    ))
    for p in document_paths:
        console.print(f"  📄 {p.name}", style="dim")

    # ── Parse (cached on disk) → compress (in memory, every run) ───────────────
    fp = fingerprint_documents(document_paths)
    try:
        raw_chunks, from_cache = load_or_parse(company, document_paths, CACHE_DIR, fresh=fresh)
    except Exception as e:
        console.print(f"[bold red]Document parsing failed:[/bold red] {e}")
        raise typer.Exit(code=1)
    if from_cache:
        console.print("[dim]⚡ Parsed documents loaded from cache (contents unchanged)[/dim]")

    briefing = build_deal_briefing(
        company, document_paths, fingerprint=fp,
        compress=False if no_compress else None, raw_chunks=raw_chunks,
    )
    console.print("[green]✓ Briefing ready[/green]")
    if briefing.compression is not None:
        raw_n = sum(len(c.text) for c in briefing.raw_chunks)
        new_n = sum(len(c.text) for c in briefing.all_chunks)
        console.print(f"[green]✓ Compressed locally:[/green] {raw_n:,} → {new_n:,} characters "
                      f"({1 - new_n / max(raw_n, 1):.0%} smaller, ~{new_n // 4:,} tokens per call)")
        for entry in briefing.compression.entries:
            console.print(f"    − {entry['step']}: {entry['chars']:,} chars from {entry['file']}"
                          + (f" ({entry['detail']})" if entry["detail"] else ""), style="dim")

    # ── Run Debate ─────────────────────────────────────────────────────────────
    try:
        engine = DebateEngine(skip_search=skip_search, fresh=fresh)
        state = engine.run(briefing)
    except Exception as e:
        console.print(f"[bold red]Debate engine error:[/bold red] {e}")
        import traceback
        traceback.print_exc()
        console.print("[dim]Progress is checkpointed; re-run the same command to resume.[/dim]")
        raise typer.Exit(code=1)

    # ── Write Report ───────────────────────────────────────────────────────────
    console.print("\n[cyan]Generating report...[/cyan]")
    md_path, json_path = generate_report(state, output_dir)
    console.print(f"[green]✓ Markdown report:[/green] {md_path}")
    console.print(f"[green]✓ JSON data:      [/green] {json_path}")

    verdict = state.verdict or "UNKNOWN"
    color = "green" if verdict == "INVEST" else "red"
    emoji = {"INVEST": "✅", "PASS": "❌", "VETOED": "🚫"}.get(verdict, "❓")
    console.print(Panel(f"{emoji} [bold {color}]{verdict}[/bold {color}]",
                        title=f"Final Verdict: {company}", border_style=color))


if __name__ == "__main__":
    app()
