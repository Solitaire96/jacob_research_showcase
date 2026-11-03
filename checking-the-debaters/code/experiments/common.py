"""
experiments/common.py — Shared paths and loaders for the ablation study.

Layout of one study (everything under experiments/studies/<name>/ is git-ignored):

    deals.csv               copy of the deal list used (frozen at prepare time)
    prepared.json           per-deal document fingerprints and compressed sizes
    line_index.json         template index learned over the study's deals, then frozen
    cache/                  parsed documents (shared by every run of a deal)
    runs/<deal>/<model>/<condition>/r<k>/
        result.json         the report JSON (present ⇔ run complete)
        report.md, log.txt, ck/ (checkpoints, for resuming a crashed run)
    manifest.csv            one row per planned run, with status
    labels/                 blinded label sheets + the separate key
    analysis/               summary.md and CSVs
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EXP = ROOT / "experiments"
CONDITIONS = "ABCDE"


def study_dir(name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise SystemExit(f"study name must be letters, digits, . _ - (got {name!r})")
    return Path(os.getenv("STUDIES_DIR") or EXP / "studies") / name


def model_slug(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", model)


@dataclass(frozen=True)
class Deal:
    deal_id: str
    company: str
    docs_dir: Path
    tuned: bool        # used while developing the checker → excluded from the study by default


def load_deals(path: Path) -> list[Deal]:
    deals = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if not row.get("deal_id") or row["deal_id"].startswith("#"):
                continue
            d = Path(row["docs_dir"])
            deals.append(Deal(row["deal_id"].strip(), row["company"].strip(),
                              d if d.is_absolute() else (ROOT / d), row.get("tuned", "0").strip() == "1"))
    ids = [d.deal_id for d in deals]
    if len(ids) != len(set(ids)):
        raise SystemExit(f"duplicate deal_id in {path}")
    for d in deals:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", d.deal_id):
            raise SystemExit(f"deal_id must be letters, digits, . _ - (got {d.deal_id!r})")
    return deals


def run_dir(study: Path, deal: str, model: str, cond: str, k: int) -> Path:
    return study / "runs" / deal / model_slug(model) / cond / f"r{k}"


def iter_results(study: Path):
    """Yield (deal, model_slug, condition, run_k, report_dict) for every completed run."""
    for p in sorted((study / "runs").glob("*/*/*/r*/result.json")):
        r_dir = p.parent
        cond_dir = r_dir.parent
        model_dir = cond_dir.parent
        deal_dir = model_dir.parent
        rep = json.loads(p.read_text(encoding="utf-8"))
        meta = r_dir / "run_meta.json"
        rep["_meta"] = json.loads(meta.read_text()) if meta.exists() else {}
        yield deal_dir.name, rep.get("run", {}).get("model") or model_dir.name, cond_dir.name, \
            int(r_dir.name[1:]), rep


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = fields or (list(rows[0].keys()) if rows else [])
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ── Arms: a condition letter, optionally with a named variant ("E@effort_low") ──

VARIANT_KEYS = ("CEREBRAS_REASONING_EFFORT", "MAX_OUTPUT_TOKENS", "MAX_DOC_CHARS", "DOC_COMPRESSION",
                "SOCRATIC_REFEREE")


def base_condition(arm: str) -> str:
    return arm.split("@", 1)[0]


def arm_order(arms) -> list[str]:
    """Base conditions A–E first, then variants grouped under their condition."""
    return sorted(set(arms), key=lambda a: (CONDITIONS.index(base_condition(a)), "@" in a, a))


def parse_variant(spec: str) -> tuple[str, dict[str, str]]:
    """'effort_low:CEREBRAS_REASONING_EFFORT=low,MAX_OUTPUT_TOKENS=32768' → (name, env)."""
    if ":" not in spec:
        raise SystemExit(f"--variant needs NAME:KEY=VALUE[,KEY=VALUE] (got {spec!r})")
    name, rest = spec.split(":", 1)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise SystemExit(f"variant name must be letters, digits, _ - (got {name!r})")
    env = {}
    for kv in rest.split(","):
        k, _, v = kv.partition("=")
        if k not in VARIANT_KEYS or not v:
            raise SystemExit(f"variant {name}: {kv!r} — allowed keys: {', '.join(VARIANT_KEYS)}")
        env[k] = v
    return name, env


# ── Code identity: a study must not silently span code changes ──────────────

CODE_GLOBS = ("app.py", "config.py", "llm.py", "agents/**/*.py", "ingestion/*.py", "orchestrator/*.py",
              "output/*.py")


def code_identity() -> dict:
    """Hash of the pipeline's source files (what decides model behaviour), plus git commit if available."""
    h = hashlib.sha256()
    for pat in CODE_GLOBS:
        for p in sorted(ROOT.glob(pat)):
            if "__pycache__" in p.parts:
                continue
            h.update(str(p.relative_to(ROOT)).encode())
            h.update(p.read_bytes())
    commit = ""
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                                text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return {"code_hash": h.hexdigest()[:16], "commit": commit}


# ── Per-run summary metrics (same fields the earlier A/B suite reported) ──────

def run_metrics(rep: dict) -> dict:
    cl = rep.get("citation_log", [])
    led = rep.get("fact_ledger") or {}
    aa = rep.get("argument_audit") or {}
    claims = aa.get("claims", [])
    attacks = aa.get("attacks", [])
    sc = rep.get("scorecards", {})
    return {
        "quotes_total": len(cl),
        "quotes_found": sum(bool(r.get("verified")) for r in cl),
        "ledger_facts": len(led.get("facts", [])),
        "ledger_rejected": len(led.get("rejected", [])),
        "ledger_conflicts": len(led.get("conflicts", [])),
        "claims_total": len(claims),
        "claims_grounded": sum(bool(c.get("grounded")) for c in claims),
        "claims_in": sum(c.get("status") == "IN" for c in claims),
        "claims_out": sum(c.get("status") == "OUT" for c in claims),
        "attacks": len(attacks),
        "defeats": sum(bool(a.get("defeat")) for a in attacks),
        "avg_panel_score": (round(sum(v["overall_score"] for v in sc.values()) / len(sc), 2) if sc else None),
        "score_revisions": len(rep.get("score_revisions", [])),
        "files_truncated": len(rep.get("documents_truncated") or []),
        "quant_calls": len(rep.get("quant_calls", [])),
    }
