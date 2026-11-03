"""
ingestion/deal_briefing.py

Constructs a unified DealBriefing object from parsed document chunks.
This is the single shared context object passed to all agents.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config import ABLATION, ACTIVE_MODEL, DOC_COMPRESSION, LLM_BACKEND, MAX_DOC_CHARS, PROMPT_VERSION, RUN_ID
from ingestion.compress import CompressionLog, TemplateIndex, compress_chunks
from ingestion.document_parser import DocumentChunk, parse_documents


_TYPE_PRIORITY = {"narrative": 0, "presentation": 1, "financial": 2, "data": 3, "unknown": 4, "legal": 5}


def content_fingerprint(paths: Sequence[str | Path]) -> str:
    """Hash of the document contents only (keys the parse cache, shared across models/conditions)."""
    h = hashlib.sha256()
    for p in sorted(Path(x) for x in paths):
        h.update(p.name.encode())
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def load_or_parse(company: str, paths: Sequence[str | Path], cache_dir: str | Path,
                  fresh: bool = False) -> tuple[list[DocumentChunk], bool]:
    """Parsed chunks, from the on-disk cache when the file contents are unchanged.
    Keyed on contents only, so every model/condition/run of a deal shares one parse.
    Returns (chunks, loaded_from_cache)."""
    import pickle
    cache = Path(cache_dir) / f"{company.replace(' ', '_').lower()}_{content_fingerprint(paths)[:12]}_parsed.pkl"
    if cache.exists() and not fresh:
        with open(cache, "rb") as f:
            return pickle.load(f), True
    chunks = parse_documents(paths)
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(f".{os.getpid()}.tmp")
    with open(tmp, "wb") as f:
        pickle.dump(chunks, f)
    os.replace(tmp, cache)        # atomic: parallel runs never read a half-written pickle
    return chunks, False


def fingerprint_documents(paths: Sequence[str | Path]) -> str:
    """
    Content hash of the document set plus everything that changes LLM output
    (backend, model, prompt version). Used to key caches and checkpoints so a
    changed document set never reuses stale results.
    """
    h = hashlib.sha256()
    h.update(f"{LLM_BACKEND}|{ACTIVE_MODEL}|{PROMPT_VERSION}|{ABLATION}|{RUN_ID}".encode())
    for p in sorted(Path(x) for x in paths):
        h.update(p.name.encode())
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


@dataclass
class DealBriefing:
    """Unified deal context consumed by all agents."""
    company_name: str
    documents_loaded: list[str]                      # filenames
    all_chunks: list[DocumentChunk]                  # chunks the models see (compressed if enabled)
    fingerprint: str = ""                            # documents + model + prompts + compressed text
    raw_chunks: list[DocumentChunk] = field(default_factory=list)   # as parsed, before compression
    compression: Optional[CompressionLog] = None
    ledger_block: str = ""          # fact ledger + deal math (set by the engine in Phase 0)

    # Pre-segmented views for agent convenience
    financial_chunks: list[DocumentChunk] = field(default_factory=list)
    narrative_chunks: list[DocumentChunk] = field(default_factory=list)
    presentation_chunks: list[DocumentChunk] = field(default_factory=list)
    data_chunks: list[DocumentChunk] = field(default_factory=list)
    legal_chunks: list[DocumentChunk] = field(default_factory=list)

    # Populated lazily by document_block()
    _doc_block: Optional[str] = field(default=None, repr=False)
    truncated_files: list[str] = field(default_factory=list)

    @property
    def cache_key(self) -> str:
        safe = self.company_name.replace(" ", "_").lower()
        return f"{safe}_{self.fingerprint[:12]}" if self.fingerprint else safe

    def full_text(self, max_chars: Optional[int] = None, prioritized: bool = False) -> str:
        """
        All document content as one string, optionally truncated. With
        prioritized=True, files are ordered by type (memo/deck/financials before
        legal boilerplate) so that truncation removes the least decision-relevant text.
        """
        chunks = self.all_chunks
        if prioritized:
            # By type (memo/deck/financials before legal), then smaller files first within a
            # type, so a 2-page term sheet is never cut to make room for a 300-page offering pack.
            size: dict[str, int] = {}
            for c in chunks:
                size[c.source_file] = size.get(c.source_file, 0) + len(c.text)
            chunks = sorted(chunks, key=lambda c: (_TYPE_PRIORITY.get(c.source_type, 9), size[c.source_file]))
        parts = []
        for chunk in chunks:
            header = f"\n\n[FILE: {chunk.source_file}"
            if chunk.page_or_slide is not None:
                header += f" | Page/Slide {chunk.page_or_slide}"
            header += f" | Type: {chunk.source_type}]\n"
            parts.append(header + chunk.text)
        full = "".join(parts)
        return full[:max_chars] if max_chars else full

    def document_block(self) -> str:
        """
        The full document set, framed as the only admissible evidence.
        Byte-identical on every call (so providers can cache it). Truncated to
        MAX_DOC_CHARS if necessary, recording which files were cut.
        """
        if self._doc_block is not None:
            return self._doc_block
        text = self.full_text(prioritized=True)
        note = ""
        if len(text) > MAX_DOC_CHARS:
            text = text[:MAX_DOC_CHARS]
            kept_whole = {c.source_file for c in self.all_chunks}
            # A file is (partly) omitted if any of its text falls past the cut.
            pos = 0
            size: dict[str, int] = {}
            for c in self.all_chunks:
                size[c.source_file] = size.get(c.source_file, 0) + len(c.text)
            for c in sorted(self.all_chunks,
                            key=lambda c: (_TYPE_PRIORITY.get(c.source_type, 9), size[c.source_file])):
                header = f"\n\n[FILE: {c.source_file}" + (
                    f" | Page/Slide {c.page_or_slide}" if c.page_or_slide is not None else "") + \
                    f" | Type: {c.source_type}]\n"
                pos += len(header) + len(c.text)
                if pos > MAX_DOC_CHARS:
                    kept_whole.discard(c.source_file)
            self.truncated_files = sorted({c.source_file for c in self.all_chunks} - kept_whole)
            note = (
                f"\n\n[TRUNCATED at {MAX_DOC_CHARS:,} characters. Files partly or wholly "
                f"omitted: {', '.join(self.truncated_files)}]"
            )
        self._doc_block = (
            f"=== DEAL DOCUMENTS: {self.company_name} ===\n"
            "These documents are the ONLY admissible evidence about the company.\n"
            f"Files: {', '.join(self.documents_loaded)}"
            f"{text}{note}\n"
            "=== END DEAL DOCUMENTS ==="
        )
        return self._doc_block

    def context_block(self) -> str:
        """What every agent receives first: the documents, then the fact ledger and deal math."""
        block = self.document_block()
        return f"{block}\n\n{self.ledger_block}" if self.ledger_block else block

    def financial_text(self) -> str:
        """Financial and data chunks only."""
        chunks = [c for c in self.all_chunks if c.source_type in ("financial", "data")]
        return "\n\n".join(
            f"[{c.source_file} | {c.source_type}]\n{c.text}" for c in chunks
        )

    def narrative_text(self) -> str:
        """Narrative & presentation chunks."""
        chunks = [c for c in self.all_chunks if c.source_type in ("narrative", "presentation", "unknown")]
        return "\n\n".join(
            f"[{c.source_file} | {c.source_type}]\n{c.text}" for c in chunks
        )

    def summary_stats(self) -> str:
        """Quick stats about loaded documents for CLI display."""
        type_counts: dict[str, int] = {}
        for c in self.all_chunks:
            type_counts[c.source_type] = type_counts.get(c.source_type, 0) + 1
        total = len(self.full_text())
        lines = [f"  Company: {self.company_name}"]
        if self.compression is not None:
            raw = sum(len(c.text) for c in self.raw_chunks)
            lines += [f"  Compression: {raw:,} → {sum(len(c.text) for c in self.all_chunks):,} chars "
                      f"({self.compression.total():,} removed: "
                      + ", ".join(f"{k} {v:,}" for k, v in self.compression.by_step().items()) + ")"]
        lines += [f"  Documents loaded: {len(self.documents_loaded)}"]
        lines += [f"    • {n}: {k} chunks" for k, n in type_counts.items()]
        lines += [f"  Total chunks: {len(self.all_chunks)}"]
        lines += [f"  Total characters: {total:,} (~{total // 4:,} tokens per LLM call)"]
        if total > MAX_DOC_CHARS:
            lines += [f"  ⚠ Exceeds MAX_DOC_CHARS ({MAX_DOC_CHARS:,}) — documents will be truncated"]
        return "\n".join(lines)


def build_deal_briefing(
    company_name: str,
    document_paths: Sequence[str | Path],
    fingerprint: str = "",
    compress: Optional[bool] = None,
    raw_chunks: Optional[list[DocumentChunk]] = None,
    template_index: Optional[TemplateIndex] = None,
) -> DealBriefing:
    """
    Parse (or reuse `raw_chunks`), compress in memory, and assemble a DealBriefing.

    Args:
        company_name: Human-readable company name for report headers.
        document_paths: Paths to deal documents (PDF, DOCX, PPTX, CSV, etc.)
        fingerprint: Optional precomputed fingerprint_documents() value.
        compress: Run ingestion/compress.py (default: DOC_COMPRESSION from config).
        raw_chunks: Previously parsed chunks for these paths (skips parsing).
        template_index: Template library to use/learn (default: templates/line_index.json).
    """
    paths = [Path(p) for p in document_paths]
    raw = raw_chunks if raw_chunks is not None else parse_documents(paths)
    compress = DOC_COMPRESSION if compress is None else compress

    log: Optional[CompressionLog] = None
    chunks = raw
    if compress:
        # TEMPLATE_LEARN=0 freezes the template index (experiments: every run sees the same compression).
        chunks, log = compress_chunks(raw, company_name, template_index or TemplateIndex(),
                                      learn=os.getenv("TEMPLATE_LEARN", "1") == "1")

    base_fp = fingerprint or fingerprint_documents(paths)
    seen = hashlib.sha256(base_fp.encode())
    for c in chunks:                       # what the models actually see
        seen.update(c.source_file.encode())
        seen.update(c.text.encode())

    briefing = DealBriefing(
        company_name=company_name,
        documents_loaded=[p.name for p in paths],
        all_chunks=chunks,
        fingerprint=seen.hexdigest(),
        raw_chunks=raw,
        compression=log,
    )

    for chunk in chunks:
        if chunk.source_type == "financial":
            briefing.financial_chunks.append(chunk)
        elif chunk.source_type == "narrative":
            briefing.narrative_chunks.append(chunk)
        elif chunk.source_type == "presentation":
            briefing.presentation_chunks.append(chunk)
        elif chunk.source_type == "data":
            briefing.data_chunks.append(chunk)
        elif chunk.source_type == "legal":
            briefing.legal_chunks.append(chunk)

    return briefing
