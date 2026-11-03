"""
experiments/export_labels.py — Blinded label sheets for the fabrication measurement.

    python experiments/export_labels.py --study S [--labellers 2]

Writes to experiments/studies/S/labels/:
    labels_1.csv (labels_2.csv …)   what labellers see: deal, text, cited source. No model,
                                    condition, agent, phase or checker result. Rows shuffled
                                    (a different order per labeller).
    key.csv                         item → every occurrence (run, condition, model, agent,
                                    phase, checker verdict, sampling weight). Do not open
                                    while labelling.
    export.json                     settings + counts, for the paper's methods section.

Two kinds of item:
  quote      text the agent put inside «» (or “…”/"…" followed by a [File, p. N] cite).
             Strata = model × condition × phase group × checker verdict. Every NOT FOUND quote is
             kept (up to --nf-cap per stratum), FOUND quotes are sampled (--found-n per stratum),
             and every quote in the Risk ruling is kept. Weights undo the sampling.
  assertion  an unquoted sentence with a number or a proper noun, from gauntlet responses and
             the Risk ruling: catches fabrication that never uses the quote format, which the
             checker cannot see and which may differ by condition.

Identical texts within a deal are labelled once and the label applies to every occurrence.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import iter_results, study_dir, write_csv  # noqa: E402

from orchestrator.citations import QUOTE_RE, _norm  # noqa: E402

PHASE_GROUP = [("phase1", "scorecard"), ("opening", "debate"), ("cross_exam", "debate"),
               ("gauntlet", "gauntlet"), ("risk_ruling", "risk_ruling")]
STATUS_RE = re.compile(r"^\[(RESOLVED|PARTIAL|UNRESOLVED)\]\s*", re.I)
DOWNGRADE_RE = re.compile(r"\s*⚠\s*Downgraded from (RESOLVED|PARTIAL)\b.*$", re.S)


def phase_group(phase: str) -> str:
    for prefix, g in PHASE_GROUP:
        if phase.startswith(prefix):
            return g
    return "other"


def scrub(text: str) -> str:
    """Remove anything that reveals the condition: checker annotations, ledger IDs, gate notes."""
    text = DOWNGRADE_RE.sub("", text)
    text = re.sub(r"\[Citation check:.*?\]\s*$", "", text, flags=re.S)
    text = re.sub(r"\[evidence:[^\]]*\]", "", text)
    text = re.sub(r"\[Quant machine check:.*?\]", "", text, flags=re.S)
    text = re.sub(r"\s*,?\s*(?:\b(?:per|see|from|in)\s+)?\(?\bF\d+\b\)?", "", text)
    text = re.sub(r"\s+([.,;:])", r"\1", text)
    text = re.sub(r"\[A\]", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def objection_statuses(rep: dict) -> list[dict]:
    """Per objection: the status the model gave (before any gate) and the status that stood."""
    out = []
    for i, row in enumerate(rep.get("risk_gate", {}).get("objection_log", []), 1):
        s = row["status"]
        m = STATUS_RE.match(s)
        final = m.group(1).upper() if m else "UNRESOLVED"
        d = DOWNGRADE_RE.search(s)
        model = d.group(1).upper() if d else final
        body = DOWNGRADE_RE.sub("", STATUS_RE.sub("", s))
        out.append({"idx": i, "model_status": model, "final_status": final, "text": body})
    return out


_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z“\"(«])")
_PROPER = re.compile(r"(?<!^)(?<![.!?]\s)\b[A-Z][a-zA-Z&'’.-]+(?:\s+[A-Z][a-zA-Z&'’.-]+)*")
_SKIP_WORDS = {"I", "We", "The", "This", "That", "Risk", "Agent", "Objection", "RESOLVED", "PARTIAL",
               "UNRESOLVED", "INVEST", "PASS", "NOT", "IN", "DOCUMENTS", "Series", "CEO", "CFO", "CTO"}


def assertion_sentences(text: str) -> list[str]:
    """Unquoted factual-looking sentences: contain a digit or a proper noun; ≥ 6 words."""
    text = scrub(text)
    text = re.split(r"^\s*CLAIMS:\s*$", text, maxsplit=1, flags=re.M)[0]   # argument block → audit only
    text = QUOTE_RE.sub("«…»", text)                                       # quotes are labelled as quotes
    text = re.sub(r"[#*_`>]+", " ", text)
    out = []
    for raw in re.split(r"\n+", text):
        for s in _SENT.split(raw.strip()):
            s = re.sub(r"\s+", " ", s).strip(" -•")
            if len(s.split()) < 6:
                continue
            words = {w for w in _PROPER.findall(s) if w.split()[0] not in _SKIP_WORDS}
            if re.search(r"\d", s) or words:
                out.append(s)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--study", required=True)
    ap.add_argument("--labellers", type=int, default=1, help="independent copies (2 recommended)")
    ap.add_argument("--nf-cap", type=int, default=60, help="max NOT FOUND quotes per stratum")
    ap.add_argument("--found-n", type=int, default=10, help="FOUND quotes sampled per stratum")
    ap.add_argument("--assert-per-run", type=int, default=6, help="gauntlet assertions sampled per run")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--force", action="store_true", help="overwrite existing label sheets")
    a = ap.parse_args()

    study = study_dir(a.study)
    out_dir = study / "labels"
    if (out_dir / "key.csv").exists() and not a.force:
        raise SystemExit(f"{out_dir} already has label sheets; --force overwrites them (and any labels in them).")
    rng = random.Random(a.seed)

    occ: list[dict] = []          # one row per occurrence (before sampling)
    for deal, model, cond, k, rep in iter_results(study):
        base = {"deal": deal, "model": model, "condition": cond, "run": k}
        rid = f"{deal}|{model}|{cond}|{k}"
        found = {r["quote"]: r for r in rep.get("citation_log", []) if r["phase"] == "risk_ruling"}
        for r in rep.get("citation_log", []):
            if r["phase"] == "risk_ruling":
                continue                       # taken per objection below
            occ.append({**base, "run_key": rid, "kind": "quote", "agent": r["agent"], "phase": r["phase"],
                        "group": phase_group(r["phase"]), "text": r["quote"], "cited_as": r.get("cited_as") or "",
                        "checker_found": int(bool(r["verified"])), "checker_method": r.get("method") or "",
                        "objection": "", "objection_model_status": ""})
        for ob in objection_statuses(rep):
            for m in QUOTE_RE.finditer(ob["text"]):
                q = (m.group(1) if m.group(1) is not None else m.group(3)).strip()
                if _norm(q) == "not in documents":
                    continue
                cite = (m.group(2) if m.group(1) is not None else m.group(4)) or ""
                fr = found.get(q)
                occ.append({**base, "run_key": rid, "kind": "quote", "agent": "Risk Agent", "phase": "risk_ruling",
                            "group": "risk_ruling", "text": q, "cited_as": cite.strip(),
                            "checker_found": int(bool(fr and fr["verified"])),
                            "checker_method": (fr or {}).get("method") or "",
                            "objection": ob["idx"], "objection_model_status": ob["model_status"]})
            for s in assertion_sentences(ob["text"]):
                occ.append({**base, "run_key": rid, "kind": "assertion", "agent": "Risk Agent",
                            "phase": "risk_ruling", "group": "risk_ruling", "text": s, "cited_as": "",
                            "checker_found": "", "checker_method": "", "objection": ob["idx"],
                            "objection_model_status": ob["model_status"]})
        for st in rep.get("transcript", []):
            if st["phase"].startswith("gauntlet"):
                for s in assertion_sentences(st["content"]):
                    occ.append({**base, "run_key": rid, "kind": "assertion", "agent": st["agent"],
                                "phase": st["phase"], "group": "gauntlet", "text": s, "cited_as": "",
                                "checker_found": "", "checker_method": "", "objection": "",
                                "objection_model_status": ""})
    if not occ:
        raise SystemExit("no completed runs with quotes/assertions found")

    # ── Sampling (weights = population / sample within each stratum) ──────────
    strata: dict[tuple, list[int]] = defaultdict(list)
    for i, o in enumerate(occ):
        if o["kind"] == "quote":
            key = ("quote", o["model"], o["condition"], o["group"], o["checker_found"])
        elif o["group"] == "risk_ruling":
            key = ("assert_risk", o["run_key"])
        else:
            key = ("assert", o["run_key"])
        strata[key].append(i)
    keep: dict[int, float] = {}
    for key, idx in strata.items():
        if key[0] == "quote" and key[3] == "risk_ruling":
            n = len(idx)                                   # census
        elif key[0] == "quote":
            n = min(len(idx), a.nf_cap if key[4] == 0 else a.found_n)
        elif key[0] == "assert_risk":
            n = len(idx)
        else:
            n = min(len(idx), a.assert_per_run)
        for i in rng.sample(idx, n):
            keep[i] = len(idx) / n

    # ── Dedupe identical texts within a deal → items ───────────────────────────
    items: dict[tuple, str] = {}
    key_rows = []
    for i, w in sorted(keep.items()):
        o = occ[i]
        ik = (o["deal"], o["kind"], _norm(o["text"]))
        if ik not in items:
            items[ik] = f"{'Q' if o['kind'] == 'quote' else 'S'}{len(items) + 1:05d}"
        key_rows.append({"item_id": items[ik], **{k: o[k] for k in (
            "deal", "model", "condition", "run", "kind", "agent", "phase", "group", "checker_found",
            "checker_method", "objection", "objection_model_status")}, "weight": round(w, 6)})
    # Population counts per run (for per-run totals, e.g. fabricated quotes per run)
    pop = defaultdict(int)
    for o in occ:
        pop[(o["deal"], o["model"], o["condition"], o["run"], o["kind"])] += 1

    first = {}
    for i in sorted(keep):
        o = occ[i]
        first.setdefault(items[(o["deal"], o["kind"], _norm(o["text"]))], o)
    sheet = [{"item_id": iid, "deal_id": o["deal"], "kind": o["kind"], "text": scrub(o["text"]),
              "cited_as": o["cited_as"], "label": "", "supporting_passage": "", "note": ""}
             for iid, o in first.items()]
    out_dir.mkdir(parents=True, exist_ok=True)
    fields = ["item_id", "deal_id", "kind", "text", "cited_as", "label", "supporting_passage", "note"]
    for n in range(1, a.labellers + 1):
        rows = sheet[:]
        random.Random(a.seed * 100 + n).shuffle(rows)
        rows.sort(key=lambda r: r["deal_id"])           # by deal (one set of documents open at a time)
        write_csv(out_dir / f"labels_{n}.csv", rows, fields)
    write_csv(out_dir / "key.csv", key_rows)
    write_csv(out_dir / "population.csv",
              [{"deal": k[0], "model": k[1], "condition": k[2], "run": k[3], "kind": k[4], "count": v}
               for k, v in sorted(pop.items())])
    n_q = sum(r["kind"] == "quote" for r in sheet)
    (out_dir / "export.json").write_text(json.dumps({
        "settings": vars(a), "occurrences": len(occ), "sampled_occurrences": len(keep),
        "items": len(sheet), "quote_items": n_q, "assertion_items": len(sheet) - n_q}, indent=2))
    print(f"{len(occ):,} occurrences → {len(keep):,} sampled → {len(sheet):,} unique items to label "
          f"({n_q:,} quotes, {len(sheet) - n_q:,} assertions), per labeller.")
    print(f"Sheets: {out_dir}/labels_*.csv   Key (keep closed while labelling): {out_dir}/key.csv")


if __name__ == "__main__":
    main()
