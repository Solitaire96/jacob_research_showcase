"""
orchestrator/citations.py — Mechanical citation checking.

Agents are required to support factual claims with verbatim quotes written as
    «exact words from a document» [File name, p. N]
This module extracts every «…» quote from an agent's output and checks it
against the parsed document text. The model cannot vouch for its own
evidence; this check is done in code.

Matching:
  - Both sides are normalised (case, unicode, quotes/dashes, PDF hyphenation,
    thousands separators, punctuation, whitespace).
  - Exact normalised substring match            → VERIFIED (exact)
  - Otherwise, word-level alignment against a document window. Accepted as
    VERIFIED (approx) only if every difference is extraction noise:
      * substituted words must be character-similar (harvest ≈ harvLLest),
        never a different word (Bureau ≠ Council), never a number or negation, and
        never differ by adding or removing a negative prefix (un-, in-, im-,
        ir-, il-, non-, dis-, e.g. unprofitable ≠ profitable);
      * words only in the quote or only in the document inside the span must be
        filler (the, of, and…), never content, qualifiers or negations
        (a quote that silently drops "not", "partial", "only" or "any" fails);
      * every capitalised word in the quote (names, firms, places) must occur spelled
        exactly that way somewhere in the documents ("Halvorsen" fails when the documents
        only say "Halvorson"; a noisy copy of a correctly spelled name still matches).
  - Quotes with ellipses are split and every segment must verify.
  - Segments under 5 words must match exactly (too short to fuzzy-match).
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ingestion.deal_briefing import DealBriefing

QUOTE_RE = re.compile(
    r"«([^«»]{1,2000})»(?:\s*\[([^\[\]]{0,200})\])?"            # «quote» [optional cite]
    r"|[“\"]([^“”\"\n]{12,2000})[”\"]\s*\[([^\[\]]{0,200})\]"   # “quote” [required cite]
)
_ELLIPSIS_RE = re.compile(r"\s*(?:\.\.\.|…|\[\.\.\.\])\s*")
MIN_FUZZY_WORDS = 5
MAX_ANCHOR_HITS = 300
CHAR_SIMILARITY = 0.6
_NEGATIONS = {"not", "no", "never", "non", "none", "without", "nor", "cannot", "unsigned", "neither"}
_NEG_PREFIXES = ("un", "in", "im", "ir", "il", "non", "dis")
_FILLER = {"the", "a", "an", "and", "of", "to", "in", "on", "for", "with", "by", "at", "as",
           "is", "are", "was", "were", "be", "its", "it", "that", "this", "from", "or", "s"}
# Standalone page-number lines (e.g. "117", "[117]", "- 12 -", "Page 4 of 18") across page breaks.
_PAGE_NUM_LINE = re.compile(
    r"(?m)^[ \t]*(?:\[\s*\d{1,4}\s*\]|-\s*\d{1,4}\s*-|page\s+\d{1,4}(?:\s+of\s+\d{1,4})?|\d{1,4})[ \t]*$",
    re.I,
)
# A "number" token: optional $, digits/decimals, optional % or short unit suffix (45m, 2026, 4.1x, 30%).
# Watermark noise inside a word ("f55or") is not a number.
_NUMBER = re.compile(r"^\$?\d[\d.]*(?:%|[a-z]{1,3})?$")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    if "\n" in s:
        s = _PAGE_NUM_LINE.sub(" ", s)                   # standalone page numbers across page breaks
    s = re.sub(r"-\s*\n\s*", "", s)                     # PDF hyphenation across lines
    s = s.lower()
    s = s.translate(str.maketrans({
        "‘": "'", "’": "'", "ʼ": "'", "“": '"', "”": '"',
        "–": "-", "—": "-", "−": "-", "×": "x", " ": " ",
    }))  # type: ignore[arg-type]
    s = re.sub(r"(?<=\d),(?=\d)", "", s)                 # 1,200 → 1200
    s = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", s)            # keep decimal points only
    s = s.replace("$", " ")                              # $5.7m ≡ 5.7m
    s = re.sub(r"(\d)([a-z])", r"\1 \2", s)              # 40ft ≡ 40 ft, 5.7m ≡ 5.7 m
    s = re.sub(r"[^\w$%.]+|_+", " ", s)                  # all other punctuation (incl. _ / __ markdown) → space
    return re.sub(r"\s+", " ", s).strip()


def _critical(tok: str) -> bool:
    return bool(_NUMBER.match(tok)) or tok in _NEGATIONS


def _has_neg_prefix_diff(a: str, b: str) -> bool:
    """True if one word equals (or closely matches) the other plus a negative prefix."""
    for pfx in _NEG_PREFIXES:
        if a == pfx + b or b == pfx + a:
            return True
        if a.startswith(pfx) != b.startswith(pfx):
            stem_a = a[len(pfx):] if a.startswith(pfx) else a
            stem_b = b[len(pfx):] if b.startswith(pfx) else b
            if len(stem_a) >= 4 and len(stem_b) >= 4 and SequenceMatcher(None, stem_a, stem_b).ratio() >= 0.75:
                return True
    return False


def _noise_only_diff(q: list[str], window: list[str]) -> bool:
    """True if `q` matches `window` up to extraction noise (see module docstring)."""
    raw_ops = SequenceMatcher(None, q, window, autojunk=False).get_opcodes()
    # Merge runs of adjacent non-equal ops (delete+insert, insert+replace…) into one
    # replace, so noise-split words like "receiv EE ed" align to "received".
    ops: list[tuple[str, int, int, int, int]] = []
    for op in raw_ops:
        if ops and op[0] != "equal" and ops[-1][0] != "equal":
            p_ = ops[-1]
            i1, j1 = p_[1], p_[3]
            ops[-1] = ("replace" if (op[2] > i1 and op[4] > j1) else op[0], i1, op[2], j1, op[4])
        else:
            ops.append(op)
    # Drop padding: doc-only words before the first / after the last quote word.
    while ops and ops[0][0] == "insert":
        ops = ops[1:]
    while ops and ops[-1][0] == "insert":
        ops = ops[:-1]
    if not ops or not any(kind == "equal" for kind, *_ in ops):
        return False
    matched = sum(i2 - i1 for kind, i1, i2, _, _ in ops if kind == "equal")
    if matched / len(q) < 0.6:
        return False
    last = len(ops) - 1
    for k, (kind, i1, i2, j1, j2) in enumerate(ops):
        qa, wb = q[i1:i2], window[j1:j2]
        if kind == "equal":
            continue
        if kind == "delete":                    # words only in the quote
            if any(t not in _FILLER for t in qa):
                return False
        elif kind == "insert":                  # words only in the document
            if any(t not in _FILLER for t in wb):  # quote dropped content/qualifier/negation
                return False
        elif kind == "replace":
            # Pure tokenisation difference ("non dilutive" vs "nondilutive" from a line-break hyphen):
            # identical characters, so accept — except where joining could hide a negation
            # ("not able" vs "notable"). "non" compounds keep their meaning when joined.
            if "".join(qa) == "".join(wb) and not any(t in _NEGATIONS - {"non"} for t in qa):
                continue
            if any(t in _NEGATIONS for t in qa):
                return False
            if any(_critical(t) for t in qa):
                # A figure must match exactly — except when the document's copy was split by
                # watermark noise (e.g. 48000 extracted as "480 ww 00"): same digits in the same
                # places, and the only extra doc tokens are ≤2-char noise. Decimal points count, so
                # "24" never matches "2.4" (a 10× change).
                qd = re.sub(r"[^\d.]", "", "".join(qa))
                if not (qd and qd == re.sub(r"[^\d.]", "", "".join(wb))
                        and all(re.search(r"\d", t) or len(t) <= 2 for t in wb)
                        and all(re.search(r"\d", t) for t in qa)):
                    return False
                continue
            # A trailing/leading replace may include window padding: allow a sub-span.
            if k == last:
                spans = [wb[:e] for e in range(1, len(wb) + 1)]
            elif k == 0:
                spans = [wb[b:] for b in range(len(wb))]
            else:
                spans = [wb]
            target = "".join(qa)
            q_non = any(t.startswith("non") for t in qa)

            def _span_matches(sp: list[str]) -> bool:
                if not sp or any(t in _NEGATIONS for t in sp):
                    return False
                # a "non"-prefixed document word ("nondilutive") must not lose its "non" in the quote
                if not q_non and any(t.startswith("non") for t in sp):
                    return False
                sp_join = "".join(sp)
                if target == sp_join:
                    return True
                if _has_neg_prefix_diff(target, sp_join):
                    return False
                if len(qa) == 1:
                    return SequenceMatcher(None, target, sp_join).ratio() >= CHAR_SIMILARITY
                # Multi-word quote spans must match word-for-word (or against watermark-split doc tokens)
                if len(qa) == len(sp):
                    return all(
                        not _has_neg_prefix_diff(qt, dt)
                        and SequenceMatcher(None, qt, dt).ratio() >= CHAR_SIMILARITY
                        for qt, dt in zip(qa, sp)
                    )
                if len(sp) > len(qa):
                    q0, q_last = qa[0], qa[-1]
                    return (
                        SequenceMatcher(None, q0, sp_join[:len(q0) + 2]).ratio() >= CHAR_SIMILARITY
                        and SequenceMatcher(None, q_last, sp_join[max(0, len(sp_join) - len(q_last) - 2):]).ratio() >= CHAR_SIMILARITY
                        and SequenceMatcher(None, target, sp_join).ratio() >= CHAR_SIMILARITY
                    )
                return False

            if not any(_span_matches(sp) for sp in spans):
                return False
    return True


@dataclass
class CitationCheck:
    quote: str
    cited_source: str
    verified: bool
    method: str                 # "exact" | "approx" | "not_found"
    found_in: Optional[str]     # source name where it was found


class CitationVerifier:
    def __init__(self, sources: dict[str, str]) -> None:
        """sources: {source name (file name or 'SEARCH'): raw text}"""
        self._norm: dict[str, str] = {}
        self._tokens: dict[str, list[str]] = {}
        self._index: dict[str, dict[str, list[int]]] = {}
        self._df: dict[str, int] = defaultdict(int)
        for name, text in sources.items():
            self._norm[name] = self._norm.get(name, "") + " " + _norm(text) + " "
        for name, n in self._norm.items():
            toks = n.split()
            self._tokens[name] = toks
            idx: dict[str, list[int]] = defaultdict(list)
            for i, t in enumerate(toks):
                idx[t].append(i)
            self._index[name] = idx
            for t, pos in idx.items():
                self._df[t] += len(pos)

    @classmethod
    def from_briefing(cls, briefing: DealBriefing, extra: Optional[dict[str, str]] = None) -> "CitationVerifier":
        sources: dict[str, str] = {}
        # Compressed text (what the models saw) plus the original parse (everything).
        for c in list(briefing.all_chunks) + list(getattr(briefing, "raw_chunks", []) or []):
            sources[c.source_file] = sources.get(c.source_file, "") + "\n" + c.text
        for k, v in (extra or {}).items():
            if v:
                sources[k] = v
        return cls(sources)

    def _check_segment(self, seg: str) -> tuple[str, Optional[str]]:
        n = _norm(seg)
        if not n:
            return "not_found", None
        needle = " " + n + " "
        for name, hay in self._norm.items():
            if needle in hay:
                return "exact", name
        q = n.split()
        if len(q) < MIN_FUZZY_WORDS:
            return "not_found", None
        # Proper-noun rule: capitalised words must exist verbatim somewhere in the corpus.
        caps = {t for w in re.findall(r"\b[A-Z][\w'’.\-]*", seg) for t in _norm(w).split()}
        if any(t not in self._df for t in caps):
            return "not_found", None
        # Anchor on the rarest quote tokens; align a padded window around each hit.
        anchors = sorted({(self._df.get(t, 0), k, t) for k, t in enumerate(q) if self._df.get(t, 0) > 0})[:3]
        for name, toks in self._tokens.items():
            idx = self._index[name]
            for _, k, t in anchors:
                for p in idx.get(t, [])[:MAX_ANCHOR_HITS]:
                    lo0, hi0 = max(0, p - k), min(len(toks), p - k + len(q))
                    lo, hi = max(0, p - k - 3), min(len(toks), p - k + len(q) + 3)
                    if _noise_only_diff(q, toks[lo0:hi0]) or _noise_only_diff(q, toks[lo:hi]):
                        return "approx", name
        return "not_found", None

    def check_quote(self, quote: str, cited: str = "") -> CitationCheck:
        segments = [s for s in _ELLIPSIS_RE.split(quote) if _norm(s)]
        if not segments:
            return CitationCheck(quote, cited, False, "not_found", None)
        methods, found = [], None
        for seg in segments:
            m, where = self._check_segment(seg)
            if m == "not_found":
                return CitationCheck(quote, cited, False, "not_found", None)
            methods.append(m)
            found = found or where
        method = "exact" if all(m == "exact" for m in methods) else "approx"
        return CitationCheck(quote, cited, True, method, found)

    def check_text(self, text: str) -> list[CitationCheck]:
        out = []
        for m in QUOTE_RE.finditer(text):
            q = m.group(1) if m.group(1) is not None else m.group(3)
            cite = m.group(2) if m.group(1) is not None else m.group(4)
            if _norm(q) in {"not in documents", "not in document"}:   # the sentinel, not a quote
                continue
            out.append(self.check_quote(q.strip(), (cite or "").strip()))
        return out

    @staticmethod
    def annotation(checks: list[CitationCheck]) -> str:
        """Human/agent-readable summary appended to each statement."""
        if not checks:
            return ("[Citation check: no verbatim document quotes. "
                    "Factual claims in this statement are unsupported opinion.]")
        ok = sum(c.verified for c in checks)
        lines = [f"[Citation check: {ok}/{len(checks)} quotes found verbatim in the documents."
                 + (" NOT FOUND = exact wording absent: a loose paraphrase or an invention — check the documents."
                    if ok < len(checks) else "")]
        for c in checks:
            if not c.verified:
                q = c.quote if len(c.quote) <= 140 else c.quote[:137] + "..."
                lines.append(f'  NOT FOUND: "{q}"' + (f" (cited as {c.cited_source})" if c.cited_source else ""))
        lines[-1] += "]"
        return "\n".join(lines)


EVIDENCE_RULE_CHECKED = """EVIDENCE RULE (enforced by an automated checker):
- Support every factual claim about the company with a VERBATIM quote from the deal documents, written as:
  «exact words copied from the document» [File name, p. N]
- Copy quotes exactly — at least 5 consecutive words where possible. Do not paraphrase inside «».
- ONE continuous passage per «». For two passages, write two «» quotes. Do not stitch separate
  sentences together with "…", and do not substitute words (e.g. "Acme" for "the company").
- Use «» only for evidence — not “ ” or " ".
- Every «» quote is checked against the documents in code. A quote whose exact wording is not in the
  documents is flagged publicly as NOT FOUND, and claims without quotes are treated as unsupported opinion.
- If the documents do not contain the evidence you need, say "NOT IN DOCUMENTS". Never invent
  documents, contracts, LOIs, people, figures, or third-party validations."""

# Ablation conditions A/B: same quote format requested, no claim that quotes are checked.
EVIDENCE_RULE_UNCHECKED = """EVIDENCE RULE:
- Support every factual claim about the company with a VERBATIM quote from the deal documents, written as:
  «exact words copied from the document» [File name, p. N]
- Copy quotes exactly — at least 5 consecutive words where possible. Do not paraphrase inside «».
- ONE continuous passage per «». For two passages, write two «» quotes.
- Use «» only for evidence — not “ ” or " ".
- If the documents do not contain the evidence you need, say "NOT IN DOCUMENTS". Never invent
  documents, contracts, LOIs, people, figures, or third-party validations."""


def _evidence_rule() -> str:
    from config import SHOW_CITATION_CHECKS
    return EVIDENCE_RULE_CHECKED if SHOW_CITATION_CHECKS else EVIDENCE_RULE_UNCHECKED


EVIDENCE_RULE = _evidence_rule()
