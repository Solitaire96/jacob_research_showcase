"""
experiments/analyze.py — Results of an ablation study.

    python experiments/analyze.py --study S [--outcomes outcomes.csv] [--price MODEL=IN,OUT ...]

Writes experiments/studies/S/analysis/{summary.md, runs.csv, cells.csv, contrasts.csv}.

Works before labelling (automatic metrics only) and after (label-based metrics added).

Primary measure: fabrication rate = weighted share of labelled items whose label is
UNSUPPORTED or CONTRADICTED, by model × condition, for quotes and for unquoted
assertions separately. Intervals: percentile bootstrap, resampling deals (clusters), then
runs within each deal × model × condition cell. Contrasts between conditions reuse the
same resampled deals.

Labels: labels/labels_*.csv. With two labellers, items where they agree are used,
disagreements go to labels/disagreements.csv; put resolved labels in
labels/adjudicated.csv (item_id,label) to include them.
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import arm_order, base_condition, iter_results, read_csv, run_metrics, study_dir, write_csv  # noqa: E402
from export_labels import objection_statuses  # noqa: E402

QUOTE_LABELS = {"VERBATIM", "PARAPHRASE", "UNSUPPORTED", "CONTRADICTED"}
ASSERT_LABELS = {"SUPPORTED", "UNSUPPORTED", "CONTRADICTED", "OPINION"}
FABRICATED = {"UNSUPPORTED", "CONTRADICTED"}
EXCLUDED = {"OPINION"}


# ── statistics helpers ─────────────────────────────────────────────────────────

def fleiss_kappa(table: list[list[int]]) -> float | None:
    """table[i][j] = raters putting subject i in category j (same rater count per subject)."""
    if len(table) < 2:
        return None
    n = sum(table[0])
    if n < 2 or any(sum(r) != n for r in table):
        return None
    N, k = len(table), len(table[0])
    p = [sum(r[j] for r in table) / (N * n) for j in range(k)]
    P = [(sum(x * x for x in r) - n) / (n * (n - 1)) for r in table]
    Pbar, Pe = sum(P) / N, sum(x * x for x in p)
    return None if Pe == 1 else (Pbar - Pe) / (1 - Pe)


def cohen_kappa(a: list[str], b: list[str]) -> float | None:
    if not a:
        return None
    cats = set(a) | set(b)
    po = sum(x == y for x, y in zip(a, b)) / len(a)
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[c] * cb[c] for c in cats) / len(a) ** 2
    return None if pe == 1 else (po - pe) / (1 - pe)


def pct(x: float | None, d: int = 1) -> str:
    return "—" if x is None else f"{100 * x:.{d}f}%"


def ci(vals: list[float]) -> tuple[float, float] | None:
    vals = sorted(v for v in vals if v is not None)
    if len(vals) < 20:
        return None
    return vals[int(0.025 * len(vals))], vals[min(len(vals) - 1, int(0.975 * len(vals)))]


def roc_auc(scores: list[float], labels: list[bool]) -> float | None:
    """Rank-based Mann-Whitney U / ROC AUC (handles ties with 0.5)."""
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return None
    wins = sum((1.0 if p > n else 0.5 if p == n else 0.0) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


# ── loading ────────────────────────────────────────────────────────────────────

def load_labels(lab_dir: Path) -> tuple[dict[str, str], dict]:
    sheets = sorted(lab_dir.glob("labels_*.csv"))
    info: dict = {"labellers": 0}
    per: list[dict[str, str]] = []
    kinds: dict[str, str] = {}
    for s in sheets:
        d = {}
        for r in read_csv(s):
            kinds[r["item_id"]] = r["kind"]
            lab = (r.get("label") or "").strip().upper()
            if not lab:
                continue
            allowed = QUOTE_LABELS if r["kind"] == "quote" else ASSERT_LABELS
            if lab not in allowed:
                raise SystemExit(f"{s.name}: item {r['item_id']} has label {lab!r}; allowed: {sorted(allowed)}")
            d[r["item_id"]] = lab
        if d:
            per.append(d)
    info["labellers"] = len(per)
    if not per:
        return {}, info
    if len(per) == 1:
        labels = per[0]
    else:
        a, b = per[0], per[1]
        both = sorted(set(a) & set(b))
        info["double_labelled"] = len(both)
        info["kappa_full"] = cohen_kappa([a[i] for i in both], [b[i] for i in both])
        fab = lambda x: "FAB" if x in FABRICATED else "OK"  # noqa: E731
        info["kappa_fabricated"] = cohen_kappa([fab(a[i]) for i in both], [fab(b[i]) for i in both])
        labels = {i: a[i] for i in both if a[i] == b[i]}
        dis = [{"item_id": i, "kind": kinds.get(i, ""), "labeller_1": a[i], "labeller_2": b[i]}
               for i in both if a[i] != b[i]]
        info["disagreements"] = len(dis)
        write_csv(lab_dir / "disagreements.csv", dis, ["item_id", "kind", "labeller_1", "labeller_2"])
        for i in set(a) ^ set(b):            # labelled by only one person
            labels.setdefault(i, (a.get(i) or b.get(i)))
        info["single_labelled"] = len(set(a) ^ set(b))
    adj = lab_dir / "adjudicated.csv"
    if adj.exists():
        n = 0
        for r in read_csv(adj):
            if r.get("label", "").strip():
                labels[r["item_id"]] = r["label"].strip().upper()
                n += 1
        info["adjudicated"] = n
    return labels, info


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--study", required=True)
    ap.add_argument("--outcomes", help="CSV: deal_id,fund_decision(INVEST|PASS),outcome(positive|negative|unknown)")
    ap.add_argument("--price", nargs="*", default=[], help="MODEL=IN,OUT in USD per 1M tokens")
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--deals", nargs="+", help="only these deal ids (e.g. the held-out ones)")
    a = ap.parse_args()
    study = study_dir(a.study)
    out = study / ("analysis" if not a.deals else "analysis_" + "-".join(sorted(a.deals)))
    out.mkdir(parents=True, exist_ok=True)
    prices = {}
    for p in a.price:
        m, v = p.split("=")
        i, o = v.split(",")
        prices[m] = (float(i), float(o))

    # ── per-run records ─────────────────────────────────────────────────────
    runs: dict[tuple, dict] = {}
    for deal, model, cond, k, rep in iter_results(study):
        if a.deals and deal not in a.deals:
            continue
        rg = rep.get("risk_gate", {})
        obs = objection_statuses(rep)
        hv = rg.get("has_verified_quote") or {}
        evidenced = [o for o in obs if o["model_status"] in ("RESOLVED", "PARTIAL")]
        lifted = (rg.get("model_vote") or rg.get("vote")) == "INVEST"
        cl = rep.get("citation_log", [])
        u = rep.get("run", {}).get("usage", {})
        runs[(deal, model, cond, k)] = {
            "deal": deal, "model": model, "condition": cond, "run": k,
            "verdict": rep.get("verdict"), "risk_model_vote": rg.get("model_vote"), "risk_vote": rg.get("vote"),
            "lifted": int(lifted),
            "lift_unquoted_auto": int(lifted and any(not hv.get(str(o["idx"]), False) for o in evidenced)),
            "evidenced_objections": [o["idx"] for o in evidenced],
            "quotes": len(cl), "quotes_not_found": sum(not r["verified"] for r in cl),
            "input_tokens": u.get("input_tokens", 0), "output_tokens": u.get("output_tokens", 0),
            "cached_tokens": u.get("cached_tokens", 0), "calls": u.get("calls", 0),
            "elapsed_s": rep["_meta"].get("elapsed_s"), "code_hash": rep["_meta"].get("code_hash", ""),
            **run_metrics(rep),
        }
    if not runs:
        raise SystemExit("no completed runs")
    models = sorted({r["model"] for r in runs.values()})
    conds = arm_order(r["condition"] for r in runs.values())
    code_hashes = Counter(r["code_hash"] for r in runs.values())
    deals = sorted({r["deal"] for r in runs.values()})

    # ── labels → per-run weighted sums ─────────────────────────────────────
    lab_dir = study / "labels"
    labels, linfo = load_labels(lab_dir) if lab_dir.exists() else ({}, {"labellers": 0})
    key = read_csv(lab_dir / "key.csv") if (lab_dir / "key.csv").exists() else []
    acc = defaultdict(lambda: {"q_num": 0.0, "q_den": 0.0, "s_num": 0.0, "s_den": 0.0})
    conf = defaultdict(float)           # (model, cond, checker_found, label) → weight
    risk_quotes = defaultdict(list)     # (run, objection) → labels of its quotes (None = unlabelled)
    for r in key:
        rk = (r["deal"], r["model"], r["condition"], int(r["run"]))
        lab = labels.get(r["item_id"])
        if r["kind"] == "quote" and r["group"] == "risk_ruling" and r["objection"]:
            risk_quotes[(rk, int(r["objection"]))].append(lab)
        if lab is None or lab in EXCLUDED or rk not in runs:
            continue
        w = float(r["weight"])
        f = 1.0 if lab in FABRICATED else 0.0
        if r["kind"] == "quote":
            acc[rk]["q_num"] += w * f
            acc[rk]["q_den"] += w
            conf[(r["model"], r["condition"], int(r["checker_found"]), lab)] += w
        else:
            acc[rk]["s_num"] += w * f
            acc[rk]["s_den"] += w
    for rk, rr in runs.items():
        rr.update({k: round(v, 4) for k, v in acc[rk].items()} if rk in acc else
                  {"q_num": 0.0, "q_den": 0.0, "s_num": 0.0, "s_den": 0.0})
        # Label-based unsupported lift: an evidenced objection none of whose quotes is real.
        if rr["lifted"] and key:
            states = []
            for idx in rr["evidenced_objections"]:
                labs = risk_quotes.get((rk, idx), [])
                if any(x is None for x in labs):
                    states.append(None)
                else:
                    states.append(all(x in FABRICATED for x in labs))   # no quotes → True
            rr["lift_fabricated_label"] = ("" if any(s is None for s in states) else int(any(states)))
        else:
            rr["lift_fabricated_label"] = 0 if key else ""

    # ── cell metrics + cluster bootstrap ───────────────────────────────────
    cell_runs: dict[tuple, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for rr in runs.values():
        cell_runs[(rr["model"], rr["condition"])][rr["deal"]].append(rr)

    def ratio(rows, num, den):
        d = sum(r[den] for r in rows)
        return None if d == 0 else sum(r[num] for r in rows) / d

    def mean(rows, f):
        return None if not rows else sum(r[f] for r in rows) / len(rows)

    METRICS = {
        "quote_fab_rate": lambda rows: ratio(rows, "q_num", "q_den"),
        "assert_fab_rate": lambda rows: ratio(rows, "s_num", "s_den"),
        "fab_quotes_per_run": lambda rows: mean(rows, "q_num") if any(r["q_den"] for r in rows) else None,
        "not_found_share": lambda rows: ratio(rows, "quotes_not_found", "quotes"),
        "invest_rate": lambda rows: None if not rows else sum(r["verdict"] == "INVEST" for r in rows) / len(rows),
        "lift_rate": lambda rows: mean(rows, "lifted"),
        "lift_unquoted_auto": lambda rows: mean(rows, "lift_unquoted_auto"),
    }
    CONTRAST_METRICS = ("quote_fab_rate", "assert_fab_rate", "fab_quotes_per_run", "lift_unquoted_auto",
                        "invest_rate", "not_found_share")
    rng = random.Random(a.seed)
    boots: dict[tuple, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for _ in range(a.boot):
        ds = [rng.choice(deals) for _ in deals]
        for (m, c), by_deal in cell_runs.items():
            rows = []
            for d in ds:
                rs = by_deal.get(d, [])
                rows += [rng.choice(rs) for _ in rs]
            for name, fn in METRICS.items():
                boots[(m, c)][name].append(fn(rows))

    cells = []
    for m in models:
        for c in conds:
            rows = [r for rs in cell_runs[(m, c)].values() for r in rs]
            if not rows:
                continue
            row = {"model": m, "condition": c, "runs": len(rows), "deals": len(cell_runs[(m, c)])}
            for name, fn in METRICS.items():
                row[name] = fn(rows)
                iv = ci(boots[(m, c)][name])
                row[name + "_lo"], row[name + "_hi"] = iv if iv else (None, None)
            lab_runs = [r for r in rows if r["lift_fabricated_label"] != ""]
            row["lift_fabricated_label"] = mean(lab_runs, "lift_fabricated_label") if lab_runs else None
            row["verdicts"] = dict(Counter(r["verdict"] for r in rows))
            row["input_tokens"] = sum(r["input_tokens"] for r in rows)
            row["output_tokens"] = sum(r["output_tokens"] for r in rows)
            row["cached_tokens"] = sum(r["cached_tokens"] for r in rows)
            if m in prices:
                pi, po = prices[m]
                row["cost_usd"] = round((row["input_tokens"] * pi + row["output_tokens"] * po) / 1e6, 2)
            # Verdict stability across repeats: Fleiss κ over deals (subjects) × runs (raters)
            n = min(len(rs) for rs in cell_runs[(m, c)].values())
            cats = ["INVEST", "PASS", "VETOED"]
            table = [[sum(r["verdict"] == cat for r in rs[:n]) for cat in cats]
                     for rs in cell_runs[(m, c)].values()]
            row["fleiss_kappa"] = fleiss_kappa(table) if n >= 2 else None
            row["modal_agreement"] = (sum(max(t) for t in table) / (n * len(table))) if n else None
            cells.append(row)

    # Contrasts: same model, deals present in BOTH arms only, paired on the same resampled deals.
    # (Comparing cells over different deal sets would mix the arm effect with the deal effect.)
    contrasts = []
    crng = random.Random(a.seed + 1)
    for m in models:
        present = [c for c in conds if cell_runs.get((m, c))]
        base = [c for c in present if "@" not in c]
        pairs = [(x, y) for x, y in zip(base, base[1:])] + ([(base[0], base[-1])] if len(base) > 2 else [])
        pairs += [(base_condition(v), v) for v in present if "@" in v and base_condition(v) in base]
        for x, y in pairs:
            common = sorted(set(cell_runs[(m, x)]) & set(cell_runs[(m, y)]))
            if not common:
                continue
            rx = [r for d in common for r in cell_runs[(m, x)][d]]
            ry = [r for d in common for r in cell_runs[(m, y)][d]]
            diffs: dict[str, list] = defaultdict(list)
            for _ in range(a.boot):
                ds = [crng.choice(common) for _ in common]
                bx, by = [], []
                for d in ds:
                    bx += [crng.choice(cell_runs[(m, x)][d]) for _ in cell_runs[(m, x)][d]]
                    by += [crng.choice(cell_runs[(m, y)][d]) for _ in cell_runs[(m, y)][d]]
                for name in CONTRAST_METRICS:
                    vx, vy = METRICS[name](bx), METRICS[name](by)
                    if vx is not None and vy is not None:
                        diffs[name].append(vy - vx)
            for name in CONTRAST_METRICS:
                px, py = METRICS[name](rx), METRICS[name](ry)
                if px is None or py is None:
                    continue
                iv = ci(diffs[name]) if len(common) >= 2 else None
                contrasts.append({"model": m, "from": x, "to": y, "metric": name, "deals": len(common),
                                  "from_value": px, "to_value": py, "diff": py - px,
                                  "lo": iv[0] if iv else None, "hi": iv[1] if iv else None})

    # Checker vs human labels (quotes), weighted
    checker = []
    for m in models:
        for c in conds:
            cc = {k[2:]: v for k, v in conf.items() if k[0] == m and k[1] == c}
            if not cc:
                continue
            nf_fab = sum(v for (f, lab), v in cc.items() if f == 0 and lab in FABRICATED)
            nf_all = sum(v for (f, lab), v in cc.items() if f == 0)
            fab_all = sum(v for (f, lab), v in cc.items() if lab in FABRICATED)
            found_fab = sum(v for (f, lab), v in cc.items() if f == 1 and lab in FABRICATED)
            nf_para = sum(v for (f, lab), v in cc.items() if f == 0 and lab == "PARAPHRASE")
            checker.append({"model": m, "condition": c,
                            "precision_notfound": nf_fab / nf_all if nf_all else None,
                            "recall_fabricated": nf_fab / fab_all if fab_all else None,
                            "notfound_but_paraphrase": nf_para / nf_all if nf_all else None,
                            "found_but_fabricated": found_fab})

    # Outcomes
    outcome_rows = []
    if a.outcomes:
        oc = {r["deal_id"]: r for r in read_csv(Path(a.outcomes))}
        for m in models:
            for c in conds:
                for d, rs in cell_runs[(m, c)].items():
                    if d not in oc:
                        continue
                    inv = sum(r["verdict"] == "INVEST" for r in rs)
                    sc_vals = [r["avg_panel_score"] for r in rs if r.get("avg_panel_score") is not None]
                    avg_sc = round(sum(sc_vals) / len(sc_vals), 3) if sc_vals else None
                    outcome_rows.append({"model": m, "condition": c, "deal": d, "runs": len(rs),
                                         "invest_share": inv / len(rs),
                                         "avg_panel_score": avg_sc,
                                         "system_modal": "INVEST" if inv * 2 > len(rs) else "NOT_INVEST",
                                         "fund_decision": oc[d].get("fund_decision", "").upper(),
                                         "outcome": oc[d].get("outcome", "").lower()})
        write_csv(out / "outcomes_by_deal.csv", outcome_rows)

    # ── write ──────────────────────────────────────────────────────────────
    run_fields = ["deal", "model", "condition", "run", "code_hash", "elapsed_s", "verdict", "risk_model_vote",
                  "risk_vote", "lifted",
                  "lift_unquoted_auto", "lift_fabricated_label", "quotes", "quotes_not_found", "q_num", "q_den",
                  "s_num", "s_den", "calls", "input_tokens", "output_tokens", "cached_tokens",
                  *run_metrics({}).keys()]
    write_csv(out / "runs.csv", sorted(runs.values(), key=lambda r: (r["model"], r["condition"], r["deal"], r["run"])),
              run_fields)
    write_csv(out / "cells.csv", cells)
    write_csv(out / "contrasts.csv", contrasts)
    write_csv(out / "checker_vs_labels.csv", checker)

    L = [f"# Ablation results — study `{study.name}`", ""]
    L.append(f"{len(runs)} completed runs · {len(deals)} deals · models: {', '.join(models)} · "
             f"arms: {', '.join(conds)}")
    if len(code_hashes) > 1:
        L.append("\n> **Runs span " + str(len(code_hashes)) + " versions of the pipeline code** ("
                 + ", ".join(f"{h or 'unrecorded'}: {n} runs" for h, n in code_hashes.most_common())
                 + "). Differences between arms may be code differences; see runs.csv `code_hash`.")
    if len(deals) < 5:
        L.append(f"\n> **{len(deals)} deals.** Intervals resample deals; with fewer than ~5 they are not "
                 "meaningful. Treat every number below as descriptive.")
    lab_n = len(labels)
    L.append(f"\nLabels: {lab_n} items labelled, {linfo.get('labellers', 0)} labeller sheet(s) with labels."
             + (f" Cohen κ (all categories) {linfo['kappa_full']:.2f}, κ (fabricated vs not) "
                f"{linfo['kappa_fabricated']:.2f} on {linfo['double_labelled']} double-labelled items; "
                f"{linfo['disagreements']} disagreements (see labels/disagreements.csv)."
                if linfo.get("kappa_full") is not None else ""))
    L += ["", "## Fabrication (human labels)", "",
          "| model | cond | runs | quote fab. rate [95% CI] | fabricated quotes/run | assertion fab. rate [95% CI] |",
          "|---|---|---|---|---|---|"]
    for r in cells:
        def f(name, d=1):
            v = r[name]
            if v is None:
                return "—"
            s = pct(v, d) if "rate" in name else f"{v:.2f}"
            if r.get(name + "_lo") is not None:
                lo, hi = r[name + "_lo"], r[name + "_hi"]
                s += f" [{pct(lo, d)}, {pct(hi, d)}]" if "rate" in name else f" [{lo:.2f}, {hi:.2f}]"
            return s
        L.append(f"| {r['model']} | {r['condition']} | {r['runs']} | {f('quote_fab_rate')} | "
                 f"{f('fab_quotes_per_run')} | {f('assert_fab_rate')} |")
    L += ["", "## Veto and verdicts", "",
          "| model | cond | Risk lifted veto | lift w/o found quote (auto) | lift on fabricated evidence (labels) "
          "| INVEST rate | verdicts | κ across repeats | modal agreement |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in cells:
        k = "—" if r["fleiss_kappa"] is None else f"{r['fleiss_kappa']:.2f}"
        L.append(f"| {r['model']} | {r['condition']} | {pct(r['lift_rate'], 0)} | {pct(r['lift_unquoted_auto'], 0)} | "
                 f"{pct(r['lift_fabricated_label'], 0)} | {pct(r['invest_rate'], 0)} | "
                 f"{', '.join(f'{k2} {v}' for k2, v in sorted(r['verdicts'].items()))} | {k} | "
                 f"{pct(r['modal_agreement'], 0)} |")
    L += ["", "## Contrasts (later condition minus earlier, same model)", "",
          "Only deals present in both arms are compared; no interval with fewer than 2 such deals.", "",
          "| model | from→to | deals | metric | from | to | diff [95% CI] |", "|---|---|---|---|---|---|---|"]
    for c in contrasts:
        is_pct = any(k in c["metric"] for k in ("rate", "lift", "share"))
        fmt = (lambda v: pct(v)) if is_pct else (lambda v: f"{v:.2f}")
        iv = f" [{fmt(c['lo'])}, {fmt(c['hi'])}]" if c["lo"] is not None else ""
        L.append(f"| {c['model']} | {c['from']}→{c['to']} | {c['deals']} | {c['metric']} | {fmt(c['from_value'])} | "
                 f"{fmt(c['to_value'])} | {fmt(c['diff'])}{iv} |")
    L += ["", "## Automatic checker (no labels needed)", "",
          "| model | cond | NOT FOUND share of quotes | quotes/run | checker precision (NF→fabricated) "
          "| checker recall | NF that were paraphrases |", "|---|---|---|---|---|---|---|"]
    chk = {(c["model"], c["condition"]): c for c in checker}
    for r in cells:
        rows = [x for rs in cell_runs[(r["model"], r["condition"])].values() for x in rs]
        q = sum(x["quotes"] for x in rows) / len(rows)
        c = chk.get((r["model"], r["condition"]), {})
        L.append(f"| {r['model']} | {r['condition']} | {pct(r['not_found_share'])} | {q:.1f} | "
                 f"{pct(c.get('precision_notfound'))} | {pct(c.get('recall_fabricated'))} | "
                 f"{pct(c.get('notfound_but_paraphrase'))} |")
    L += ["", "## Debate structure (automatic; per-run means)", "",
          "| model | cond | quotes found | claims grounded | claims IN | ledger facts / rejected / conflicts "
          "| avg panel score | files truncated | elapsed s |", "|---|---|---|---|---|---|---|---|---|"]
    for r in cells:
        rows = [x for rs in cell_runs[(r["model"], r["condition"])].values() for x in rs]

        def avg(f, rows=rows):
            v = [x[f] for x in rows if x.get(f) is not None]
            return sum(v) / len(v) if v else None

        def share(num, den, rows=rows):
            d = sum(x[den] for x in rows)
            return None if not d else sum(x[num] for x in rows) / d
        el, ps = avg("elapsed_s"), avg("avg_panel_score")
        L.append(f"| {r['model']} | {r['condition']} | {pct(share('quotes_found', 'quotes_total'))} | "
                 f"{pct(share('claims_grounded', 'claims_total'))} | {pct(share('claims_in', 'claims_total'))} | "
                 f"{avg('ledger_facts'):.1f} / {avg('ledger_rejected'):.1f} / {avg('ledger_conflicts'):.1f} | "
                 f"{'—' if ps is None else f'{ps:.2f}'} | {avg('files_truncated'):.1f} | "
                 f"{'—' if el is None else f'{el:.0f}'} |")
    L.append("\nElapsed time depends on `--workers`, `--jobs` and API load; compare it only within one invocation.")
    L += ["", "## Tokens and cost", "", "| model | cond | runs | input tok | output tok | cached tok | cost |",
          "|---|---|---|---|---|---|---|"]
    for r in cells:
        L.append(f"| {r['model']} | {r['condition']} | {r['runs']} | {r['input_tokens']:,} | {r['output_tokens']:,} | "
                 f"{r['cached_tokens']:,} | {('$' + format(r['cost_usd'], ',.2f')) if 'cost_usd' in r else '—'} |")
    if outcome_rows:
        L += ["", "## Against fund decisions and outcomes (descriptive; deal count is small)", "",
              "| model | cond | deals | agrees with fund | score AUC vs fund | INVEST on positive | INVEST on negative | score AUC vs outcome |",
              "|---|---|---|---|---|---|---|---|"]
        for m in models:
            for c in conds:
                rs = [x for x in outcome_rows if x["model"] == m and x["condition"] == c]
                if not rs:
                    continue
                agree = sum((x["system_modal"] == "INVEST") == (x["fund_decision"] == "INVEST") for x in rs
                            if x["fund_decision"] in ("INVEST", "PASS"))
                nf = sum(x["fund_decision"] in ("INVEST", "PASS") for x in rs)
                f_sc = [x for x in rs if x["fund_decision"] in ("INVEST", "PASS") and x.get("avg_panel_score") is not None]
                auc_f = roc_auc([x["avg_panel_score"] for x in f_sc], [x["fund_decision"] == "INVEST" for x in f_sc])
                pos = [x for x in rs if x["outcome"] == "positive"]
                neg = [x for x in rs if x["outcome"] == "negative"]
                o_sc = [x for x in rs if x["outcome"] in ("positive", "negative") and x.get("avg_panel_score") is not None]
                auc_o = roc_auc([x["avg_panel_score"] for x in o_sc], [x["outcome"] == "positive" for x in o_sc])
                L.append(f"| {m} | {c} | {len(rs)} | {agree}/{nf} | "
                         f"{'—' if auc_f is None else f'{auc_f:.2f}'} | "
                         f"{sum(x['system_modal'] == 'INVEST' for x in pos)}/{len(pos)} | "
                         f"{sum(x['system_modal'] == 'INVEST' for x in neg)}/{len(neg)} | "
                         f"{'—' if auc_o is None else f'{auc_o:.2f}'} |")
    L += ["", "Definitions: fabricated = label UNSUPPORTED or CONTRADICTED. Rates are weighted to undo the "
          "stratified sampling in export_labels.py. 'Lift w/o found quote' = the Risk Agent voted INVEST while "
          "marking an objection RESOLVED/PARTIAL with no quote the checker found (before any gate). "
          "κ across repeats: Fleiss κ with deals as subjects and repeats as raters."]
    (out / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n→ {out}")


if __name__ == "__main__":
    main()
