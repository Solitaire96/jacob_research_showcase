"""tests/test_compress.py — local document compression (offline)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ingestion.compress import TemplateIndex, compress_chunks  # noqa: E402
from ingestion.document_parser import DocumentChunk  # noqa: E402

WM = "\nLL\naa RR\nkk SS\n..\n00 66\n"          # doubled-character watermark fragment, as pdfplumber extracts one


def _pdf(name, pages, kind="narrative"):
    return [DocumentChunk(name, kind, i + 1, t) for i, t in enumerate(pages)]


def test_watermark_header_and_number_rejoin(tmp_path):
    header = "ACME | IC MEMO | DRAFT"
    pages = [f"{header}{WM}Page {i} body text about the company and its plans.{WM}" for i in range(6)]
    pages[2] = f"{header}\nProbes logged over 48,0\nww\n00 readings.{WM}"
    out, log = compress_chunks(_pdf("memo.pdf", pages), "Acme", TemplateIndex(tmp_path / "i.json"))
    text = "\n".join(c.text for c in out)
    assert "aa RR" not in text and "\nLL\n" not in text
    assert text.count(header) == 1                                 # kept once
    assert "48,000 readings" in text                   # re-joined
    steps = log.by_step()
    assert steps["watermark noise"] > 0 and steps["repeated header/footer"] > 0


def test_watermark_rule_only_when_pervasive(tmp_path):
    # A normal document with a couple of short lines like "AA" must be left alone.
    chunks = _pdf("ratings.pdf", ["Credit rating:\nAA\nOutlook stable."])
    out, log = compress_chunks(chunks, "X", TemplateIndex(tmp_path / "i.json"))
    assert "AA" in out[0].text and not log.entries


def test_duplicate_file_keeps_plain_name(tmp_path):
    body = " ".join(f"word{i}" for i in range(400))
    chunks = _pdf("Report-2.pdf", [body + " extra"]) + _pdf("Report.pdf", [body])
    out, log = compress_chunks(chunks, "X", TemplateIndex(tmp_path / "i.json"))
    by = {c.source_file: c.text for c in out}
    assert by["Report.pdf"] == body
    assert by["Report-2.pdf"].startswith("[Omitted: this file is a near-duplicate of Report.pdf")
    assert log.by_step()["duplicate file"] > 0


def test_template_learning_across_deals(tmp_path):
    idx = TemplateIndex(tmp_path / "i.json")
    boiler = [f"Section {i}. Limited Partners have no role in managing the Fund." for i in range(50)]
    fee = '"Manager Carry Share" means twenty percent (20.0%).'
    deal_a = boiler + [fee, '"Portfolio Company" means Alpha Inc., a Delaware corporation.']
    deal_b = boiler + [fee, '"Portfolio Company" means Beta Corp., a Nevada corporation.']

    out_a, log_a = compress_chunks([DocumentChunk("A - LPA.docx", "legal", None, "\n".join(deal_a))], "Alpha", idx)
    assert not log_a.entries                     # first deal: nothing known yet, just learned
    out_b, log_b = compress_chunks([DocumentChunk("B - LPA.docx", "legal", None, "\n".join(deal_b))], "Beta", idx)
    text_b = "\n".join(c.text for c in out_b)
    assert "Beta Corp., a Nevada corporation" in text_b       # deal-specific kept
    assert fee in text_b                                         # fund economics pinned
    assert "no role in managing" not in text_b    # template removed
    assert "lines of standard template text omitted" in text_b
    # Re-running deal A now strips its template too (Beta is known)
    out_a2, _ = compress_chunks([DocumentChunk("A - LPA.docx", "legal", None, "\n".join(deal_a))], "Alpha", idx)
    assert "Alpha Inc." in "\n".join(c.text for c in out_a2)
    assert "no role in managing" not in "\n".join(c.text for c in out_a2)
    # Same company re-run under the same name does not count as a second deal
    out_b2, _ = compress_chunks([DocumentChunk("B - LPA.docx", "legal", None, "\n".join(deal_b))], "Beta", idx)
    # Non-legal documents are never template-stripped
    memo = [DocumentChunk("B memo.pdf", "narrative", 1, "\n".join(boiler))]
    out_m, log_m = compress_chunks(memo, "Beta", idx)
    assert out_m[0].text == "\n".join(boiler) and not log_m.entries
    assert TemplateIndex(tmp_path / "i.json").deals() == {"alpha", "beta"}   # persisted
    # Same documents under a different company name are the same deal, not a template source:
    out_r, _ = compress_chunks([DocumentChunk("B - LPA.docx", "legal", None, "\n".join(deal_b))], "Beta Corp", idx)
    text_r = "\n".join(c.text for c in out_r)
    assert "Beta Corp., a Nevada corporation" in text_r
