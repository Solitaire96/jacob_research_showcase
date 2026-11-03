"""
ingestion/document_parser.py

Parses deal documents (PDF, DOCX, PPTX, CSV, XLSX, TXT, MD) into structured
text chunks, each tagged with a source type. The output feeds the
DealBriefing object consumed by all agents.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SourceType = Literal["financial", "narrative", "presentation", "data", "legal", "unknown"]

_PAGE_NUM_LINE = re.compile(
    r"^\s*(?:\[\s*\d{1,4}\s*\]|-\s*\d{1,4}\s*-|page\s+\d{1,4}(?:\s+of\s+\d{1,4})?|\d{1,4})\s*$",
    re.I,
)


def strip_page_numbers(text: str, boundary_only: bool = False) -> str:
    """Remove standalone page-number lines (either at page top/bottom or between paragraphs)."""
    lines = text.splitlines()
    if not lines:
        return text
    if boundary_only:
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if len(lines) > 1 and _PAGE_NUM_LINE.match(lines[0]):
            lines.pop(0)
        if len(lines) > 1 and _PAGE_NUM_LINE.match(lines[-1]):
            lines.pop()
        return "\n".join(lines)
    return "\n".join(ln for ln in lines if not _PAGE_NUM_LINE.match(ln))


@dataclass
class DocumentChunk:
    source_file: str
    source_type: SourceType
    page_or_slide: int | None
    text: str


def _infer_source_type(filename: str) -> SourceType:
    lower = filename.lower()
    if any(k in lower for k in ("financ", "p&l", "income", "balance", "cash", "model", "projection")):
        return "financial"
    if any(k in lower for k in ("deck", "pitch", "present", "slide")):
        return "presentation"
    if any(k in lower for k in (
        "legal", "term", "sha", "safe", "contract", "agree", "lpa", "ppm",
        "subscription", "offering", "cap-table", "cap table", "cap_table",
    )):
        return "legal"
    if any(k in lower for k in ("data", "cohort", "metric", "kpi")):
        return "data"
    if any(k in lower for k in ("memo", "dd", "diligence", "brief", "report", "note")):
        return "narrative"
    return "unknown"


def parse_pdf(path: Path) -> list[DocumentChunk]:
    try:
        import pdfplumber
    except ImportError:
        raise ImportError("pdfplumber is required: pip install pdfplumber")

    chunks: list[DocumentChunk] = []
    source_type = _infer_source_type(path.name)
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            text = strip_page_numbers(page.extract_text() or "", boundary_only=True)
            # Also extract tables as CSV-like text
            for table in page.extract_tables():
                rows = ["\t".join(str(cell) for cell in row) for row in table if row]
                text += "\n[TABLE]\n" + "\n".join(rows)
            if text.strip():
                chunks.append(DocumentChunk(
                    source_file=path.name,
                    source_type=source_type,
                    page_or_slide=i,
                    text=text.strip(),
                ))
    return chunks


def parse_docx(path: Path) -> list[DocumentChunk]:
    try:
        import docx  # python-docx
    except ImportError:
        raise ImportError("python-docx is required: pip install python-docx")

    d = docx.Document(str(path))
    parts: list[str] = [p.text for p in d.paragraphs if p.text.strip()]
    for t_idx, table in enumerate(d.tables, 1):
        rows = []
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                rows.append("\t".join(cells))
        if rows:
            parts.append(f"[TABLE {t_idx}]\n" + "\n".join(rows))
    text = "\n".join(parts).strip()
    if not text:
        return []
    return [DocumentChunk(
        source_file=path.name,
        source_type=_infer_source_type(path.name),
        page_or_slide=None,
        text=text,
    )]


def parse_pptx(path: Path) -> list[DocumentChunk]:
    try:
        from pptx import Presentation
    except ImportError:
        raise ImportError("python-pptx is required: pip install python-pptx")

    chunks: list[DocumentChunk] = []
    prs = Presentation(str(path))
    for i, slide in enumerate(prs.slides, 1):
        texts = []
        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text.strip():
                texts.append(shape.text.strip())
        if texts:
            chunks.append(DocumentChunk(
                source_file=path.name,
                source_type="presentation",
                page_or_slide=i,
                text="\n".join(texts),
            ))
    return chunks


def parse_spreadsheet(path: Path) -> list[DocumentChunk]:
    try:
        import openpyxl
    except ImportError:
        raise ImportError("openpyxl is required: pip install openpyxl")

    chunks: list[DocumentChunk] = []
    wb = openpyxl.load_workbook(str(path), data_only=True)
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows = []
        for row in ws.iter_rows(values_only=True):
            if any(cell is not None for cell in row):
                rows.append("\t".join(str(c) if c is not None else "" for c in row))
        if rows:
            chunks.append(DocumentChunk(
                source_file=path.name,
                source_type="financial",
                page_or_slide=None,
                text=f"[Sheet: {sheet_name}]\n" + "\n".join(rows),
            ))
    return chunks


def parse_csv(path: Path) -> list[DocumentChunk]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        rows = ["\t".join(row) for row in reader]
    return [DocumentChunk(
        source_file=path.name,
        source_type="data",
        page_or_slide=None,
        text="\n".join(rows),
    )]


def parse_text(path: Path) -> list[DocumentChunk]:
    text = strip_page_numbers(path.read_text(encoding="utf-8", errors="replace")).strip()
    source_type = _infer_source_type(path.name)
    return [DocumentChunk(
        source_file=path.name,
        source_type=source_type,
        page_or_slide=None,
        text=text,
    )]


PARSERS = {
    ".pdf": parse_pdf,
    ".docx": parse_docx,
    ".pptx": parse_pptx,
    ".xlsx": parse_spreadsheet,   # .xls (legacy binary) is not readable by openpyxl
    ".csv": parse_csv,
    ".txt": parse_text,
    ".md": parse_text,
}


def parse_document(path: str | Path) -> list[DocumentChunk]:
    """Parse a single document into a list of chunks."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Document not found: {path}")
    suffix = path.suffix.lower()
    parser = PARSERS.get(suffix)
    if parser is None:
        raise ValueError(f"Unsupported file type: {suffix}. Supported: {list(PARSERS)}")
    return parser(path)


SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(PARSERS)


def collect_documents(directory: str | Path, recursive: bool = True) -> list[Path]:
    """All supported files under `directory` (subfolders included by default), sorted."""
    directory = Path(directory)
    walker = directory.rglob("*") if recursive else directory.iterdir()
    return sorted(
        p for p in walker
        if p.is_file()
        and p.suffix.lower() in SUPPORTED_EXTENSIONS
        and not p.name.startswith(("~$", "."))
    )


def parse_documents(paths: Sequence[str | Path]) -> list[DocumentChunk]:
    """Parse multiple documents and return a combined flat list of chunks."""
    all_chunks: list[DocumentChunk] = []
    for p in paths:
        all_chunks.extend(parse_document(p))
    return all_chunks
