"""
ingestion/compress.py — Local, deterministic document compression.

Runs in memory after parsing, before anything is sent to a model. Every step
removes text verbatim (nothing is paraphrased), so quotes taken from the
compressed text are still checkable against the source documents.

Steps, in order:
  1. Watermark noise   — whole lines made only of doubled characters / punctuation
                         ("LL", "aa RR", "00 66", "//"), only in files where that pattern
                         is pervasive. Numbers split by a watermark line ("48,0" / "00")
                         are re-joined.
  2. Headers/footers   — lines repeated on ≥3 different pages of the same file (kept once).
  3. Empty table rows  — rows of only "None"/blank cells from PDF table extraction.
  4. Duplicate files   — a file ≥90% identical (8-word shingles) to another file in the set.
  5. Known templates   — in LEGAL documents only: lines also seen in a different deal's
                         documents (e.g. SPV operating-agreement and subscription boilerplate). The
                         library of seen lines lives in templates/line_index.json as hashes
                         and grows with every deal you run. Short lines that state fund
                         economics (carry %, fees, minimums) are pinned back in.

Each step records what it removed in a CompressionLog for the report.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from ingestion.document_parser import DocumentChunk

def default_index_path() -> Path:
    """templates/line_index.json in the project folder (override with TEMPLATE_INDEX_PATH)."""
    env = os.getenv("TEMPLATE_INDEX_PATH")
    return Path(env) if env else Path(__file__).resolve().parents[1] / "templates" / "line_index.json"

_NOISE_TOK = re.compile(r"^(?:(\S)\1|[^\w\s]{1,2})$")      # "mm", "LL", "22", "..", "//", "@@"
_EMPTY_ROW = re.compile(r"^(?:None|\s)*$")
_MIN_NOISE_LINES = 20          # only treat files with a pervasive watermark
_MIN_TEMPLATE_LEN = 25         # shorter lines are never template-stripped
_DUP_THRESHOLD = 0.90
_PIN_RE = re.compile(r"\d+(?:\.\d+)?\s?%|\$\s?\d")
_PIN_KW = re.compile(r"carr(?:y|ied)|fee|expense|minimum|hurdle|reserve|percentage", re.I)
_PIN_MAX_LINE, _PIN_MAX_TOTAL = 300, 4000


@dataclass
class CompressionLog:
    entries: list[dict] = field(default_factory=list)     # {file, step, chars, detail}

    def add(self, file: str, step: str, chars: int, detail: str = "") -> None:
        if chars > 0 or step == "duplicate file":
            self.entries.append({"file": file, "step": step, "chars": chars, "detail": detail})

    def total(self) -> int:
        return sum(e["chars"] for e in self.entries)

    def by_step(self) -> dict[str, int]:
        out: dict[str, int] = defaultdict(int)
        for e in self.entries:
            out[e["step"]] += e["chars"]
        return dict(out)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_noise_line(s: str) -> bool:
    toks = s.split()
    return bool(toks) and len(toks) <= 4 and all(_NOISE_TOK.match(t) for t in toks)


def _line_key(s: str) -> str:
    n = re.sub(r"[\W_]+", " ", s.lower()).strip()
    return hashlib.sha1(n.encode()).hexdigest()[:16] if len(n) >= _MIN_TEMPLATE_LEN else ""


def _deal_id(company: str) -> str:
    return re.sub(r"[\W_]+", " ", company.lower()).strip()


def _shingles(text: str, n: int = 8) -> set:
    w = re.sub(r"[\W_]+", " ", text.lower()).split()
    return {tuple(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


# ── Template library ──────────────────────────────────────────────────────────

class TemplateIndex:
    """
    Hashes only, stored as JSON:
      lines: line hash → deal ids whose legal documents contained it
      files: legal-file content hash → deal id (so the same documents run under a
             different company name are recognised as the same deal, not a second one)
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else default_index_path()
        self.lines: dict[str, list[str]] = {}
        self.files: dict[str, str] = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                self.lines, self.files = data.get("lines", {}), data.get("files", {})
            except (json.JSONDecodeError, OSError):
                self.lines, self.files = {}, {}

    def aliases(self, deal: str, file_hashes: set[str]) -> set[str]:
        """Deal ids that are really this deal: same name, or any identical legal file."""
        return {deal} | {self.files[h] for h in file_hashes if h in self.files}

    def seen_elsewhere(self, key: str, same_deal: set[str] | str) -> bool:
        same = {same_deal} if isinstance(same_deal, str) else same_deal
        return any(d not in same for d in self.lines.get(key, ()))

    def record(self, keys: set[str], deal: str, file_hashes: Iterable[str] = ()) -> None:
        for k in keys:
            deals = self.lines.setdefault(k, [])
            if deal not in deals:
                deals.append(deal)
        for h in file_hashes:
            self.files.setdefault(h, deal)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 2, "lines": self.lines, "files": self.files},
                                  separators=(",", ":")))
        os.replace(tmp, self.path)

    def deals(self) -> set[str]:
        return {d for ds in self.lines.values() for d in ds}


# ── Main entry point ──────────────────────────────────────────────────────────

def compress_chunks(
    chunks: list[DocumentChunk],
    company: str,
    index: TemplateIndex | None = None,
    learn: bool = True,
) -> tuple[list[DocumentChunk], CompressionLog]:
    """Return (compressed chunks, log). Input chunks are not modified."""
    log = CompressionLog()
    deal = _deal_id(company)
    by_file: dict[str, list[DocumentChunk]] = defaultdict(list)
    for c in chunks:
        by_file[c.source_file].append(c)

    out_by_file: dict[str, list[DocumentChunk]] = {}
    legal_keys: set[str] = set()
    legal_file_hashes = {
        hashlib.sha1(re.sub(r"[\W_]+", " ", "\n".join(c.text for c in fc).lower()).encode()).hexdigest()[:16]
        for fc in by_file.values() if fc and fc[0].source_type == "legal"
    }
    same_deal = index.aliases(deal, legal_file_hashes) if index is not None else {deal}

    for fname, fchunks in by_file.items():
        # Step 1 setup: is the watermark pervasive in this file?
        noise_lines = sum(_is_noise_line(ln.strip()) for c in fchunks for ln in c.text.splitlines())
        strip_noise = noise_lines >= _MIN_NOISE_LINES
        # Step 2 setup: lines repeated on ≥3 distinct pages
        pages_for: dict[str, set] = defaultdict(set)
        for c in fchunks:
            for ln in c.text.splitlines():
                s = ln.strip()
                if len(s) > 8:
                    pages_for[s].add(c.page_or_slide)
        repeated = {s for s, pages in pages_for.items() if len(pages) >= 3}
        kept_repeated: set[str] = set()

        removed: Counter[str] = Counter()
        new_chunks: list[DocumentChunk] = []
        for c in fchunks:
            kept: list[str] = []
            after_noise = False
            for line in c.text.splitlines():
                s = line.strip()
                if not s:
                    continue
                if strip_noise and _is_noise_line(s):
                    removed["watermark noise"] += len(line) + 1
                    after_noise = True
                    continue
                was_after_noise, after_noise = after_noise, False
                if s in repeated:
                    if s in kept_repeated:
                        removed["repeated header/footer"] += len(line) + 1
                        continue
                    kept_repeated.add(s)
                if _EMPTY_ROW.match(s) and "None" in s:
                    removed["empty table rows"] += len(line) + 1
                    continue
                # Re-join a number split by a removed watermark line: "48,0" + "00 acres…"
                if kept and was_after_noise:
                    m_tail = re.search(r",(\d{1,2})$", kept[-1])
                    m_head = re.match(r"^(\d{1,2})\b", s)
                    if m_tail and m_head and len(m_tail.group(1)) + len(m_head.group(1)) == 3:
                        kept[-1] = kept[-1] + s
                        continue
                kept.append(line.rstrip())
            text = "\n".join(kept).strip()
            if text:
                new_chunks.append(DocumentChunk(c.source_file, c.source_type, c.page_or_slide, text))
        for step, n in removed.items():
            log.add(fname, step, n)

        # Step 5: template lines (legal documents only)
        if index is not None and new_chunks and new_chunks[0].source_type == "legal":
            pinned_total, tmpl_removed, n_lines = 0, 0, 0
            stripped: list[DocumentChunk] = []
            for c in new_chunks:
                kept = []
                for line in c.text.splitlines():
                    k = _line_key(line)
                    if k:
                        legal_keys.add(k)
                    if k and index.seen_elsewhere(k, same_deal):
                        s = line.strip()
                        if (len(s) <= _PIN_MAX_LINE and _PIN_RE.search(s) and _PIN_KW.search(s)
                                and pinned_total + len(s) <= _PIN_MAX_TOTAL):
                            kept.append(line)          # fund economics: keep verbatim
                            pinned_total += len(s)
                            continue
                        tmpl_removed += len(line) + 1
                        n_lines += 1
                        continue
                    kept.append(line)
                text = "\n".join(kept).strip()
                if text:
                    stripped.append(DocumentChunk(c.source_file, c.source_type, c.page_or_slide, text))
            if tmpl_removed:
                note = DocumentChunk(
                    fname, "legal", None,
                    f"[{n_lines:,} lines of standard template text omitted (identical in other deals' "
                    f"documents); deal-specific lines and fund-economics lines kept verbatim.]",
                )
                stripped = [note] + stripped
                log.add(fname, "known template text", tmpl_removed,
                        f"{n_lines:,} lines; {pinned_total:,} chars of fee/carry terms kept")
            new_chunks = stripped
        out_by_file[fname] = new_chunks

    # Step 4: duplicate files (keep the shorter/plainer filename)
    order = sorted(out_by_file, key=lambda f: (len(f), f))
    kept_files: list[tuple[str, set]] = []
    for fname in order:
        sh = _shingles("\n".join(c.text for c in out_by_file[fname]))
        dup_of = None
        for other, osh in kept_files:
            if sh and osh and len(sh & osh) / len(sh | osh) >= _DUP_THRESHOLD:
                dup_of = other
                break
        if dup_of:
            n = sum(len(c.text) for c in out_by_file[fname])
            log.add(fname, "duplicate file", n, f"≥{_DUP_THRESHOLD:.0%} identical to {dup_of}")
            out_by_file[fname] = [DocumentChunk(
                fname, out_by_file[fname][0].source_type if out_by_file[fname] else "unknown", None,
                f"[Omitted: this file is a near-duplicate of {dup_of}.]")]
        else:
            kept_files.append((fname, sh))

    if index is not None and learn and legal_keys:
        index.record(legal_keys, deal, legal_file_hashes)
        index.save()

    # Preserve original chunk order
    result: list[DocumentChunk] = []
    for fname in by_file:
        result.extend(out_by_file[fname])
    return result, log
