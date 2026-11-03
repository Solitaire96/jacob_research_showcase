"""
sample_deal/make_sample_deal.py — Builds the fictional Larkspur data room used by the tests and demo.

Everything in it is invented: the company, the people, the investors and every figure. It exists so
the pipeline can run end to end offline, and so the traps listed in TRAPS.md have a known answer.

The PDFs are drawn line by line (not reflowed) so the extraction noise the checker has to tolerate is
deterministic: a diagonal watermark on the deck, a header repeated on every memo page, a word
hyphenated across a line break, and a revenue table that extracts as one run of numbers per row.

    pip install reportlab
    python sample_deal/make_sample_deal.py          # writes sample_deal/larkspur/
"""

from __future__ import annotations

import random
from pathlib import Path

from docx import Document
from openpyxl import Workbook
from reportlab.lib.pagesizes import landscape, letter
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent / "larkspur"
CO = "Larkspur Soil Analytics, Inc."


def _canvas(path: Path, size: tuple[float, float]) -> canvas.Canvas:
    return canvas.Canvas(str(path), pagesize=size, invariant=1)   # fixed dates: byte-identical rebuilds


def _lines(c: canvas.Canvas, lines: list[str], x: float = 72, y: float = 700, size: int = 11, lead: float = 15) -> None:
    c.setFont("Helvetica", size)
    for line in lines:
        if line.startswith("# "):
            c.setFont("Helvetica-Bold", size + 3)
            c.drawString(x, y, line[2:])
            c.setFont("Helvetica", size)
            y -= lead + 6
            continue
        c.drawString(x, y, line)
        y -= lead


def memo() -> None:
    header = "LARKSPUR SOIL ANALYTICS | INVESTMENT COMMITTEE MEMO | DRAFT"
    pages = [
        [
            "# What the company does",
            "Larkspur is an early entrant in continuous soil-carbon measurement for row-crop farms.",
            "It sells in-field probes plus a subscription dashboard reporting soil organic carbon,",
            "moisture and nitrate levels every ten minutes.",
            "",
            "Twelve farms in Iowa and Nebraska run the probes across 48,000 acres.",
            "The company has not signed any commercial supply agreements; all twelve farm",
            "deployments are unpaid pilots that convert to paid subscriptions in the 2027 season.",
            "",
            "# People",
            "Maren Halvorson (CEO) ran field trials for a regional agronomy cooperative.",
            "Tobias Okafor (CTO) wrote embedded firmware for irrigation controllers.",
            "Headcount is 14, of whom 9 are engineers.",
        ],
        [
            "# Money",
            "Seed raise: $6M",
            "Prior funding to date: $3.2M",
            "Pricing: $34M pre-money, implying $40M post-money on the $6M seed.",
            "Funding so far totals $3.2M of venture capital plus $900K of non-",
            "dilutive state grants for agricultural technology.",
            "Quillback Ventures led the pre-seed round in 2023.",
            "",
            "# Cash",
            "Management expects roughly 20 months of operating runway after the seed closes.",
            "Net cash burn averaged $210K per month in the third quarter of 2026.",
        ],
        [
            "# Size of the prize",
            "If verification bodies adopt probe data, management sees the business reaching",
            "$40M to $60M in yearly revenue at maturity.",
            "No verification body has yet accepted continuous probe data for credit issuance.",
            "",
            "# What could go wrong",
            "Probe calibration drifts in high-clay soils and requires a manual recalibration visit.",
            "The European expansion plan is unfunded under the current round.",
            "Two growers account for 41% of deployed acres.",
            "Hardware gross margin is negative at current volumes.",
        ],
    ]
    c = _canvas(OUT / "Larkspur IC memo.pdf", letter)
    for i, body in enumerate(pages, 1):
        c.setFont("Helvetica", 9)
        c.drawString(72, 750, header)
        c.drawString(500, 40, f"{i} / {len(pages)}")
        _lines(c, body)
        c.showPage()
    c.save()


def deck() -> None:
    slides = [
        ["# Larkspur", "Soil carbon, measured continuously", "Seed - 2026"],
        ["# Why now", "Carbon programs pay growers per tonne of soil carbon, but measurement still",
         "relies on manual soil cores taken once a year at roughly $40 per sample."],
        ["# Traction", "Twelve pilot farms, 48,000 acres under measurement",
         "Pilot growers report the dashboard replaced two of three annual sampling visits",
         "At today's burn, cash on hand covers 14 months of runway"],
        ["# The ask", "$6M seed on a post-money SAFE",
         "Use of funds: probe manufacturing, two field engineers, verification-body approval"],
    ]
    w, h = landscape(letter)
    c = _canvas(OUT / "Larkspur seed deck.pdf", (w, h))
    for n, body in enumerate(slides):
        _lines(c, body, x=60, y=h - 90, size=18, lead=28)
        if n == 1:   # this line sits on the watermark's path, so its extraction is interleaved with it
            c.setFont("Helvetica", 17)
            c.drawString(50, h / 2 + 2, "Maren Halvorson, a soil scientist, started the company in 2022 with engineer Tobias Okafor")
        # Diagonal watermark drawn over the slide text, as data-room viewers stamp it
        c.saveState()
        c.setFont("Helvetica-Bold", 40)
        c.setFillGray(0.85)
        c.translate(w / 2, h / 2)
        c.rotate(10)
        c.drawCentredString(0, 0, "CONFIDENTIAL  LARKSPUR")
        c.restoreState()
        c.showPage()
    c.save()


def terms() -> None:
    c = _canvas(OUT / "Larkspur term sheet.pdf", letter)
    _lines(c, [
        "# Seed SAFE - indicative terms",
        f"Company: {CO} (Delaware)",
        "Instrument: post-money SAFE",
        "Amount: $6,000,000",
        "Valuation Cap: $40,000,000 post-money",
        "Discount: 0%",
        "Pro rata: for cheques of $500,000 or more",
        "Reporting: quarterly unaudited financials",
        "Indicative only; nothing here binds either party except confidentiality.",
    ])
    c.showPage()
    c.save()


def revenue_model() -> None:
    c = _canvas(OUT / "Larkspur revenue model.pdf", letter)
    _lines(c, [
        "# Revenue model (company prepared, unaudited)",
        "USD 2025A 2026E 2027E",
        "Subscription Revenue $USD 410,000 2,050,000 7,380,000",
        "Hardware Revenue $USD 120,000 380,000 1,100,000",
        "Gross Margin % 22% 41% 58%",
        "Operating Expenses $USD 1,900,000 2,800,000 4,600,000",
        "Assumes 85% of pilots convert to paid in the 2027 season.",
    ])
    c.showPage()
    c.save()


def pilot_xlsx() -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Pilots"
    ws.append(["Farm", "State", "Acres", "Probes", "Start", "Status"])
    rng = random.Random(7)
    for i in range(1, 13):
        ws.append([f"Pilot farm {i:02d}", rng.choice(["IA", "NE"]), 4000, rng.randint(18, 40), "2026-04", "unpaid pilot"])
    wb.save(OUT / "Larkspur pilot data.xlsx")


def legal_docx() -> None:
    sub = OUT / "Legal"
    sub.mkdir(exist_ok=True)
    d = Document()
    d.add_heading("Simple Agreement for Future Equity", 1)
    d.add_paragraph(f"In exchange for the investor's payment, {CO} (the \"Company\") grants the investor "
                    "the right to certain shares of the Company's capital stock on the terms below.")
    d.add_paragraph("\"Post-Money Valuation Cap\" means $40,000,000.")
    for i in range(1, 40):
        d.add_paragraph(f"Section {i}. The Company will notify the investor in writing of any Liquidity "
                        f"Event at least ten days before it closes; clause {i} survives termination.")
    d.save(sub / "Larkspur SAFE.docx")

    # A long SPV agreement: legal boilerplate that a small context window must drop first.
    lpa = Document()
    lpa.add_heading("Larkspur Seed SPV, LLC - Limited Partnership Agreement", 1)
    clauses = [
        "Limited Partners have no role in managing or controlling the Partnership.",
        "No Limited Partner may assign its interest unless the Manager consents in writing.",
        "The Manager may call capital by giving each Limited Partner ten business days' notice.",
        "Proceeds are distributed first to return contributed capital, then as carried interest.",
        "The Partnership indemnifies the Manager for losses arising from good-faith conduct.",
    ]
    for i in range(1, 1300):
        lpa.add_paragraph(f"{i // 10 + 1}.{i % 10} {clauses[i % len(clauses)]} (Article {i}, clause {i * 7 % 97}.)")
    lpa.save(sub / "Larkspur SPV LPA.docx")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    memo()
    deck()
    terms()
    revenue_model()
    pilot_xlsx()
    legal_docx()
    for p in sorted(OUT.rglob("*")):
        if p.is_file():
            print(f"{p.relative_to(OUT)}  {p.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
