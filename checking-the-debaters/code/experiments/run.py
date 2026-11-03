"""
experiments/run.py — Run the ablation grid (deals × models × conditions × repeats).

    python experiments/run.py models                         # model ids your key can use
    python experiments/run.py ping --models M1 M2            # one tiny call per model
    python experiments/run.py prepare --study S              # parse + freeze compression
    python experiments/run.py run --study S --models M1 M2   # the grid (resumable)
    python experiments/run.py status --study S

Each run is a separate `python app.py` process, so its flags (ABLATION, model)
are fixed for the whole run. Study-wide settings (document cap, output budget,
reasoning effort) are fixed at `prepare`; `--variant` adds arms that change one of
them (e.g. reasoning effort, compression) on top of a condition. A study refuses to
continue on changed pipeline code unless told to. Completed runs are skipped on re-run; a crashed run
resumes from its checkpoint. Run order is shuffled (fixed seed) so that time-of-day
effects on the API are spread across conditions rather than confounded with them.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    CONDITIONS, EXP, ROOT, Deal, base_condition, code_identity, load_deals, model_slug, parse_variant,
    run_dir, run_metrics, study_dir, write_csv,
)

# Approximate number of calls that carry the document block, per condition (counted on the
# scripted pipeline; real runs differ with lifeline use and retries). Used only for estimates.
_DOC_CALLS = {"A": 7, "B": 45, "C": 45, "D": 40, "E": 41}


def _key() -> tuple[str, str]:
    from config import CEREBRAS_API_KEY, CEREBRAS_BASE_URL
    if not CEREBRAS_API_KEY:
        raise SystemExit("CEREBRAS_API_KEY is not set (put it in .env).")
    return CEREBRAS_API_KEY, CEREBRAS_BASE_URL


def _http(method: str, url: str, key: str, body: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, method=method, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "User-Agent": "openai-python/1.0"},
        data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except json.JSONDecodeError:
            return e.code, {}


# ── models / ping ──────────────────────────────────────────────────────────────

def cmd_models(_a) -> None:
    key, base = _key()
    code, data = _http("GET", f"{base}/models", key)
    if code != 200:
        raise SystemExit(f"GET /models → HTTP {code}: {data}")
    for m in data.get("data", []):
        print(m.get("id"))


def cmd_ping(a) -> None:
    key, base = _key()
    bad = 0
    for m in a.models:
        t = time.time()
        body = {"model": m, "max_tokens": 400,
                "messages": [{"role": "user", "content": "Reply with the single word OK."}]}
        if a.reasoning_effort:
            body["reasoning_effort"] = a.reasoning_effort
        code, data = _http("POST", f"{base}/chat/completions", key, body)
        dt = time.time() - t
        if code != 200:
            bad += 1
            print(f"✗ {m}: HTTP {code} {json.dumps(data)[:300]}")
            continue
        msg = (data.get("choices") or [{}])[0].get("message", {})
        text = msg.get("content") or ""
        think = "<think>" in text or bool(msg.get("reasoning") or msg.get("reasoning_content"))
        print(f"✓ {m}: {dt:.1f}s reply={text.strip()[:60]!r} usage={data.get('usage')} "
              f"{'(emits reasoning — stripped before checking)' if think else ''}")
    if bad:
        raise SystemExit(1)


# ── prepare ────────────────────────────────────────────────────────────────────

def cmd_prepare(a) -> None:
    if a.tier:                          # tier decides config's default caps; set it before config loads
        os.environ["CEREBRAS_TIER"] = a.tier
    from ingestion.compress import TemplateIndex, compress_chunks
    from ingestion.deal_briefing import content_fingerprint, load_or_parse
    from ingestion.document_parser import collect_documents
    from config import CEREBRAS_TIER
    from config import MAX_DOC_CHARS as _cfg_cap
    from config import MAX_OUTPUT_TOKENS as _cfg_out
    MAX_DOC_CHARS = a.max_doc_chars or _cfg_cap
    a.max_output_tokens = a.max_output_tokens or _cfg_out

    study = study_dir(a.study)
    deals_file = Path(a.deals)
    all_deals = load_deals(deals_file)
    deals = [d for d in all_deals if a.include_tuned or not d.tuned]
    if not deals:
        raise SystemExit("no deals to prepare (all marked tuned=1? use --include-tuned)")
    if (study / "prepared.json").exists() and not a.force:
        raise SystemExit(f"{study} is already prepared. Changing its compression would invalidate "
                         "completed runs; use a new study name, or --force if no runs exist yet.")
    if a.force and any((study / "runs").glob("*/*/*/r*/result.json")):
        raise SystemExit("--force refused: this study already has completed runs.")
    study.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(deals_file, study / "deals.csv")

    index_path = study / "line_index.json"
    if index_path.exists():
        index_path.unlink()
    index = TemplateIndex(index_path)
    parsed: dict[str, tuple[Deal, list, list[Path]]] = {}
    for d in all_deals:     # every listed deal teaches templates; only study deals are run
        paths = collect_documents(d.docs_dir, recursive=True)
        if not paths:
            raise SystemExit(f"{d.deal_id}: no supported documents in {d.docs_dir}")
        print(f"parsing {d.deal_id} ({len(paths)} files)…", flush=True)
        chunks, _ = load_or_parse(d.company, paths, study / "cache")
        parsed[d.deal_id] = (d, chunks, paths)

    # Learn shared templates over all deals (two passes, so the first deal also benefits), then freeze.
    for _ in range(2):
        for d, chunks, _ in parsed.values():
            compress_chunks(chunks, d.company, index, learn=True)

    info = {}
    for d, chunks, paths in (parsed[x.deal_id] for x in deals):
        out, _log = compress_chunks(chunks, d.company, index, learn=False)
        raw_n, new_n = sum(len(c.text) for c in chunks), sum(len(c.text) for c in out)
        info[d.deal_id] = {"company": d.company, "files": len(paths),
                           "content_fingerprint": content_fingerprint(paths),
                           "raw_chars": raw_n, "compressed_chars": new_n,
                           "truncated_at": MAX_DOC_CHARS if new_n > MAX_DOC_CHARS else None,
                           "approx_doc_tokens": min(new_n, MAX_DOC_CHARS) // 4}
    settings = {"CEREBRAS_TIER": CEREBRAS_TIER,
                "MAX_DOC_CHARS": str(MAX_DOC_CHARS), "MAX_OUTPUT_TOKENS": str(a.max_output_tokens),
                "CEREBRAS_REASONING_EFFORT": a.reasoning_effort, "DOC_COMPRESSION": "1"}
    (study / "prepared.json").write_text(json.dumps(
        {"deals": info, "max_doc_chars": MAX_DOC_CHARS, "settings": settings,
         "prepared": time.strftime("%Y-%m-%dT%H:%M:%S")}, indent=2))

    print(f"\nPrepared {len(info)} deals → {study}  (CEREBRAS_TIER={CEREBRAS_TIER}; every run: "
          + ", ".join(f"{k}={v}" for k, v in settings.items()) + ")")
    print(f"{'deal':<16}{'files':>6}{'raw chars':>12}{'sent chars':>12}{'≈tokens/call':>14}")
    for k, v in info.items():
        cut = "  ← truncated to the cap in every condition" if v["truncated_at"] else ""
        print(f"{k:<16}{v['files']:>6}{v['raw_chars']:>12,}{v['compressed_chars']:>12,}{v['approx_doc_tokens']:>14,}{cut}")
    tot = sum(v["approx_doc_tokens"] for v in info.values())
    print("\nRough input tokens per repeat of every deal (documents only, before any prompt caching):")
    for c in CONDITIONS:
        print(f"  {c}: ≈{tot * _DOC_CALLS[c] / 1e6:.1f}M per model")
    if CEREBRAS_TIER == "free":
        # Free-tier limits per config.py (Cerebras docs, checked 2026-10-21): 1M tokens/day, 30k tokens/min.
        print("\nFree tier: about 1M tokens/day. Rough runs/day per deal (documents only, so optimistic):")
        for k, v in info.items():
            per = {c: v["approx_doc_tokens"] * _DOC_CALLS[c] for c in CONDITIONS}
            print(f"  {k:<12}" + "  ".join(f"{c}: {1e6 / max(per[c], 1):.1f}" for c in CONDITIONS))


# ── run ────────────────────────────────────────────────────────────────────────

def _plan(study: Path, a) -> list[dict]:
    deals = load_deals(study / "deals.csv")
    prepared = json.loads((study / "prepared.json").read_text())["deals"]
    if a.deals:
        unknown = set(a.deals) - {d.deal_id for d in deals}
        if unknown:
            raise SystemExit(f"unknown deal ids: {sorted(unknown)}")
        deals = [d for d in deals if d.deal_id in a.deals]
    deals = [d for d in deals if d.deal_id in prepared]
    arms = list(a.conditions) + [f"{c}@{name}" for name in a.variant_env for c in a.variant_conditions]
    cells = [{"deal": d.deal_id, "company": d.company, "docs_dir": str(d.docs_dir),
              "model": m, "condition": arm, "run": k}
             for d in deals for m in a.models for arm in arms for k in range(1, a.runs + 1)]
    random.Random(a.seed).shuffle(cells)
    return cells


def _model_backend(spec: str, default_backend: str = "cerebras") -> tuple[str, str]:
    """Parse 'anthropic:claude-sonnet-4-5' or 'gpt-oss-120b' → (backend, model)."""
    if ":" in spec:
        b, m = spec.split(":", 1)
        if b in ("cerebras", "anthropic", "gemini"):
            return b, m
    return default_backend, spec


def _execute(study: Path, cell: dict, a, stop: threading.Event | None = None) -> dict:
    if stop is not None and stop.is_set():
        return {**cell, "status": "pending", "seconds": 0, "skipped": True, "quota": False}
    out = run_dir(study, cell["deal"], cell["model"], cell["condition"], cell["run"])
    if (out / "result.json").exists():
        return {**cell, "status": "done", "seconds": 0, "skipped": True, "quota": False}
    out.mkdir(parents=True, exist_ok=True)
    arm = cell["condition"]
    variant = a.all_variants.get(arm.split("@", 1)[1], {}) if "@" in arm else {}
    settings = {**a.settings, **variant}
    backend, model_name = _model_backend(cell["model"], getattr(a, "backend", "cerebras"))
    model_env_key = {"cerebras": "CEREBRAS_MODEL", "anthropic": "ANTHROPIC_MODEL", "gemini": "GEMINI_MODEL"}[backend]
    env = {
        **os.environ,
        **settings,
        "LLM_BACKEND": backend,
        model_env_key: model_name,
        "ABLATION": base_condition(arm),
        "RUN_ID": f"{study.name}/{cell['deal']}/{model_slug(cell['model'])}/{cell['condition']}/r{cell['run']}",
        "TEMPLATE_LEARN": "0",
        "TEMPLATE_INDEX_PATH": str(study / "line_index.json"),
        "CACHE_DIR": str(study / "cache"),
        "CHECKPOINT_DIR": str(out / "ck"),
        "PARALLEL_WORKERS": str(a.workers),
        "PYTHONUNBUFFERED": "1",
        "COLUMNS": "120",
    }
    cmd = [sys.executable, str(ROOT / "app.py"), "--company", cell["company"], "--docs-dir", cell["docs_dir"],
           "--skip-search", "--output", str(out / "report")]
    t = time.time()
    status = "failed"
    quota = False
    attempts = 0
    log_path = out / "log.txt"
    for attempt in range(1 + a.retries):
        if stop is not None and stop.is_set():
            break
        attempts += 1
        pos = log_path.stat().st_size if log_path.exists() else 0
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(f"\n===== attempt {attempt + 1} {time.strftime('%Y-%m-%dT%H:%M:%S')} =====\n")
            log.flush()
            try:
                rc = subprocess.run(cmd, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                    timeout=a.timeout).returncode
            except subprocess.TimeoutExpired:
                log.write(f"\n[harness] timed out after {a.timeout}s\n")
                rc = -1
        reports = sorted((out / "report").glob("*.json"))
        if rc == 0 and reports:
            shutil.copyfile(reports[-1], out / "result.json")
            mds = sorted((out / "report").glob("*.md"))
            if mds:
                shutil.copyfile(mds[-1], out / "report.md")
            status = "done"
            break
        tail = log_path.read_text(encoding="utf-8", errors="replace")[pos:] if log_path.exists() else ""
        if "HTTP 402" in tail or "payment_required" in tail:
            quota = True
            if stop is not None:
                stop.set()
            break
    seconds = round(time.time() - t, 1)
    if quota and status != "done" and not any((out / "ck").glob("*.pkl")):
        shutil.rmtree(out, ignore_errors=True)
        return {**cell, "status": "pending", "seconds": round(seconds), "skipped": False, "quota": True}
    (out / "run_meta.json").write_text(json.dumps({
        **a.code, "settings": settings, "workers": a.workers, "attempts": attempts,
        "elapsed_s": seconds, "status": status, "finished": time.strftime("%Y-%m-%dT%H:%M:%S")}, indent=1))
    return {**cell, "status": status, "seconds": round(seconds), "skipped": False, "quota": quota}


def _manifest(study: Path) -> list[dict]:
    plan = json.loads((study / "plan.json").read_text())
    rows = []
    for c in plan:
        out = run_dir(study, c["deal"], c["model"], c["condition"], c["run"])
        row = {k: c[k] for k in ("deal", "model", "condition", "run")}
        res = out / "result.json"
        meta = json.loads((out / "run_meta.json").read_text()) if (out / "run_meta.json").exists() else {}
        if res.exists():
            rep = json.loads(res.read_text(encoding="utf-8"))
            u = rep.get("run", {}).get("usage", {})
            row.update(status="done", verdict=rep.get("verdict"),
                       risk_model_vote=rep.get("risk_gate", {}).get("model_vote"),
                       risk_final_vote=rep.get("risk_gate", {}).get("vote"),
                       calls=u.get("calls"), input_tokens=u.get("input_tokens"),
                       output_tokens=u.get("output_tokens"), cached_tokens=u.get("cached_tokens"),
                       elapsed_s=meta.get("elapsed_s"), code_hash=meta.get("code_hash"),
                       commit=meta.get("commit"), **run_metrics(rep),
                       path=str(res.relative_to(study)))
        else:
            row.update(status="failed" if (out / "log.txt").exists() else "pending")
        rows.append(row)
    fields = ["deal", "model", "condition", "run", "status", "verdict", "risk_model_vote", "risk_final_vote",
              "calls", "input_tokens", "output_tokens", "cached_tokens", "elapsed_s",
              *run_metrics({}).keys(), "code_hash", "commit", "path"]
    write_csv(study / "manifest.csv", rows, fields)
    return rows


def cmd_run(a) -> None:
    study = study_dir(a.study)
    if not (study / "prepared.json").exists():
        raise SystemExit(f"run `python experiments/run.py prepare --study {a.study}` first")
    a.conditions = [c.upper() for c in a.conditions]
    if bad := [c for c in a.conditions if c not in CONDITIONS]:
        raise SystemExit(f"unknown conditions {bad}")
    prepared = json.loads((study / "prepared.json").read_text())
    a.settings = prepared.get("settings") or {"MAX_DOC_CHARS": str(prepared["max_doc_chars"])}
    a.settings.setdefault("CEREBRAS_TIER", os.getenv("CEREBRAS_TIER", "free"))
    free = a.settings["CEREBRAS_TIER"] == "free"
    # Free tier (5 requests/min): one run at a time, one call at a time, unless told otherwise.
    a.jobs = a.jobs or (1 if free else 2)
    a.workers = a.workers or (1 if free else 3)

    # Variants: defined once per study; a name can't be redefined after it has runs.
    vfile = study / "variants.json"
    known = json.loads(vfile.read_text()) if vfile.exists() else {}
    a.variant_env = dict(parse_variant(v) for v in (a.variant or []))
    for name, env in a.variant_env.items():
        if name in known and known[name] != env:
            raise SystemExit(f"variant {name!r} already defined in this study as {known[name]}; use a new name.")
    a.all_variants = {**known, **a.variant_env}
    vfile.write_text(json.dumps(a.all_variants, indent=1))
    a.variant_conditions = [c.upper() for c in a.variant_conditions]
    if bad := [c for c in a.variant_conditions if c not in CONDITIONS]:
        raise SystemExit(f"unknown --variant-conditions {bad}")

    # Code guard: every run of a study uses the same pipeline code unless explicitly allowed.
    a.code = code_identity()
    cfile = study / "code.json"
    history = json.loads(cfile.read_text()) if cfile.exists() else []
    if history and history[-1]["code_hash"] != a.code["code_hash"]:
        if not a.allow_code_change:
            raise SystemExit(
                f"Pipeline code changed since this study's runs started (was {history[-1]['code_hash']} "
                f"@ {history[-1]['commit'] or '?'}, now {a.code['code_hash']} @ {a.code['commit'] or '?'}).\n"
                "Start a new study, or pass --allow-code-change (each run records its code hash; "
                "analyze.py will report the split).")
    if not history or history[-1]["code_hash"] != a.code["code_hash"]:
        history.append({**a.code, "first_used": time.strftime("%Y-%m-%dT%H:%M:%S")})
        cfile.write_text(json.dumps(history, indent=1))

    cells = _plan(study, a)
    # The plan accumulates: re-running with more models/runs adds cells, never drops finished ones.
    old = json.loads((study / "plan.json").read_text()) if (study / "plan.json").exists() else []
    key = lambda c: (c["deal"], c["model"], c["condition"], c["run"])  # noqa: E731
    merged = {key(c): c for c in old} | {key(c): c for c in cells}
    (study / "plan.json").write_text(json.dumps(list(merged.values()), indent=1))

    todo = [c for c in cells if not (run_dir(study, c["deal"], c["model"], c["condition"], c["run"])
                                     / "result.json").exists()]
    print(f"{len(cells)} runs planned, {len(cells) - len(todo)} already done, {len(todo)} to run "
          f"({a.jobs} at a time × {a.workers} parallel calls each).", flush=True)
    if a.dry_run:
        for c in todo[:20]:
            print("  ", c["deal"], c["model"], c["condition"], f"r{c['run']}")
        return
    done = failed = 0
    stop = threading.Event()
    quota_halted = False
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.jobs) as pool:
        futs = [pool.submit(_execute, study, c, a, stop) for c in todo]
        for f in as_completed(futs):
            r = f.result()
            if r.get("skipped") and r["status"] == "pending":
                continue
            done += r["status"] == "done"
            failed += r["status"] == "failed"
            el = time.time() - t0
            ran = max(done + failed, 1)
            eta = el / ran * (len(todo) - done - failed)
            print(f"[{done + failed}/{len(todo)}] {r['status']:<6} {r['deal']} {r['model']} {r['condition']} "
                  f"r{r['run']} ({r['seconds']}s) — {failed} failed, ETA {eta / 60:.0f} min", flush=True)
            if r.get("quota") and not quota_halted:
                quota_halted = True
                print("[quota] HTTP 402 payment_required — halting remaining runs (checkpoints preserved).",
                      flush=True)
    rows = _manifest(study)
    print(f"\nmanifest: {study / 'manifest.csv'} — "
          f"{sum(r['status'] == 'done' for r in rows)}/{len(rows)} done")
    if failed:
        print("Failed runs keep their log.txt; re-run the same command to retry (they resume).")


def cmd_status(a) -> None:
    study = study_dir(a.study)
    rows = _manifest(study)
    from collections import Counter
    by = Counter((r["model"], r["condition"], r["status"]) for r in rows)
    for (m, c, s), n in sorted(by.items()):
        print(f"{m:<28}{c}  {s:<8}{n}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("models")
    s = sub.add_parser("ping")
    s.add_argument("--models", nargs="+", required=True)
    s.add_argument("--reasoning-effort", help="also send reasoning_effort (e.g. low/medium/none) to test acceptance")
    s = sub.add_parser("prepare")
    s.add_argument("--study", required=True)
    s.add_argument("--deals", default=str(EXP / "deals.csv"))
    s.add_argument("--include-tuned", action="store_true",
                   help="include deals marked tuned=1 (the checker was developed on them)")
    s.add_argument("--force", action="store_true")
    s.add_argument("--max-doc-chars", type=int,
                   help="document cap for every run of the study (default: config's value for the tier)")
    s.add_argument("--max-output-tokens", type=int,
                   help="output budget per call incl. reasoning tokens (default: config's value for the tier; "
                        "the A/B suite used 24576 on paid)")
    s.add_argument("--tier", choices=["free", "paid"],
                   help="Cerebras tier for every run of the study (default: CEREBRAS_TIER from .env); "
                        "sets the default document cap, output budget and rate limits")
    s.add_argument("--reasoning-effort", default="medium", choices=["low", "medium", "high"])
    s = sub.add_parser("run")
    s.add_argument("--study", required=True)
    s.add_argument("--models", nargs="+", required=True,
                   help="model IDs; optionally prefixed with backend:MODEL (e.g. anthropic:claude-sonnet-4-5)")
    s.add_argument("--backend", default="cerebras", choices=["cerebras", "anthropic", "gemini"],
                   help="default LLM backend when not prefixed on --models (default: cerebras)")
    s.add_argument("--conditions", nargs="+", default=list(CONDITIONS))
    s.add_argument("--runs", type=int, default=3, help="repeats per deal × model × condition")
    s.add_argument("--deals", nargs="+", help="subset of deal ids (default: all prepared)")
    s.add_argument("--jobs", type=int, help="runs in parallel (default: 1 on free tier, 2 on paid)")
    s.add_argument("--workers", type=int, help="parallel LLM calls inside each run (default: 1 free, 3 paid)")
    s.add_argument("--timeout", type=int, default=5400, help="seconds per run attempt")
    s.add_argument("--retries", type=int, default=1, help="extra attempts per failed run (resumes)")
    s.add_argument("--seed", type=int, default=20261021)
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--variant", action="append", metavar="NAME:KEY=VAL[,KEY=VAL]",
                   help="extra arm overriding study settings, e.g. effort_low:CEREBRAS_REASONING_EFFORT=low "
                        "or nocompress:DOC_COMPRESSION=0 (repeatable)")
    s.add_argument("--variant-conditions", nargs="+", default=["E"],
                   help="conditions each variant is run under (default: E)")
    s.add_argument("--allow-code-change", action="store_true",
                   help="continue a study after the pipeline code changed (recorded per run)")
    s = sub.add_parser("status")
    s.add_argument("--study", required=True)
    a = p.parse_args()
    {"models": cmd_models, "ping": cmd_ping, "prepare": cmd_prepare, "run": cmd_run,
     "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    main()
