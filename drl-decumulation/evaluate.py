"""Evaluate every checkpoint against the benchmark rules; write results/ and figures.

    python3 evaluate.py            # all checkpoints in checkpoints/
    python3 evaluate.py --quick    # fewer paths, for a smoke test

Protocol (fixed seeds, common random numbers across policies):
  tune    3,000 bootstrap lifetimes from TRAIN (1955-07..1999-12): grid-search the rules
  in      5,000 fresh lifetimes from TRAIN: in-sample comparison
  test    5,000 lifetimes from TEST (2000-01..2026-09): held out from all training and tuning
Agent-vs-rule differences carry a 95% paired bootstrap interval over lifetimes.
"""
import argparse
import json
from pathlib import Path

import numpy as np

from decumulation import data as D, policies as Pl
from decumulation.sim import Config, ObsBuilder, simulate, metrics
from decumulation.utility import certainty_equivalent

ROOT = Path(__file__).resolve().parent
RES = ROOT / "results"

INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"


def paired_ci(run_a, run_b, cfg, n_boot=200, seed=7):
    """95% bootstrap CI for CEC(a) - CEC(b), resampling lifetimes jointly."""
    w = cfg.weights()
    rng = np.random.default_rng(seed)
    P = run_a["cons"].shape[0]
    diffs = []
    for _ in range(n_boot):
        i = rng.integers(0, P, P)
        diffs.append(certainty_equivalent(run_a["cons"][i], w, cfg.gamma, cfg.c_ref)
                     - certainty_equivalent(run_b["cons"][i], w, cfg.gamma, cfg.c_ref))
    return [float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))]


def profile(run, cfg):
    """Median and P10/P90 of equity weight and annualised spending rate, by year of age."""
    W = run["wealth"][:, :-1]
    rate = np.where(W > 1.0, run["withdrawal"] * 12.0 / np.maximum(W, 1.0), np.nan)
    ages = cfg.start_age + np.arange(0, cfg.n_months, 12) / 12.0
    out = {"age": ages.tolist()}
    for k, arr in [("equity", run["equity"]), ("spend_rate", rate)]:
        yearly = arr[:, ::12]
        for q in (10, 50, 90):
            out[f"{k}_p{q}"] = np.nanpercentile(yearly, q, axis=0).round(4).tolist()
    return out


def main(quick=False):
    from stable_baselines3 import DDPG, TD3
    cfg = Config()
    ob = ObsBuilder(cfg)
    n_tune, n_eval = (500, 800) if quick else (3000, 5000)
    tune_p = D.block_bootstrap(D.load(D.TRAIN), n_tune, cfg.n_months, seed=101)
    sets = {"in_sample": D.block_bootstrap(D.load(D.TRAIN), n_eval, cfg.n_months, seed=303),
            "held_out": D.block_bootstrap(D.load(D.TEST), n_eval, cfg.n_months, seed=202),
            # robustness: 60-month blocks keep multi-year regimes intact (agents trained on 12)
            "held_out_60m": D.block_bootstrap(D.load(D.TEST), n_eval, cfg.n_months, block=60, seed=404)}

    best = Pl.tune(cfg, tune_p, ob)
    rules = {
        "4% rule, 60/40": Pl.four_percent(cfg),
        f"Best constant mix ({best['constant_mix']['equity']:.0%} eq, {best['constant_mix']['rate']:.1%}/yr)":
            Pl.constant_mix(cfg, **best["constant_mix"]),
        f"Best annuitized ({best['annuitized']['equity']:.0%} eq, r={best['annuitized']['real_rate']:.0%})":
            Pl.annuitized(cfg, **best["annuitized"]),
        "110-minus-age, annuitized": Pl.age_in_bonds(cfg),
    }
    ann_key = [k for k in rules if k.startswith("Best annuitized")][0]

    ckpts = sorted((ROOT / "checkpoints").glob("*.zip"))
    loaders = {"ddpg": DDPG, "td3": TD3}
    agents = {c.stem: loaders[c.stem.split("_")[0]].load(c) for c in ckpts}
    agent_ob = {a: ObsBuilder(cfg, use_macro=("_macro" in a or "_leaky" in a)) for a in agents}
    group = lambda a: a.split("_g")[0]                  # ddpg, td3, ddpg_macro, td3_macro, ddpg_leaky
    main_agents = [a for a in agents if "_macro" not in a and "_leaky" not in a]
    leaky_sets = {"in_sample": D.block_bootstrap(D.load(D.TRAIN), n_eval, cfg.n_months, seed=303, macro_lag="source"),
                  "held_out": D.block_bootstrap(D.load(D.TEST), n_eval, cfg.n_months, seed=202, macro_lag="source"),
                  "held_out_60m": D.block_bootstrap(D.load(D.TEST), n_eval, cfg.n_months, block=60, seed=404,
                                                    macro_lag="source")}

    out = {"config": cfg.as_dict(), "windows": {"train": D.TRAIN, "test": D.TEST},
           "n_paths": {"tune": n_tune, "eval": n_eval}, "tuned_rules": best, "sets": {}}
    profiles = {}
    for sname, paths in sets.items():
        rows, runs = {}, {}
        for name, pol in rules.items():
            runs[name] = simulate(pol, paths, cfg, ob)
            rows[name] = metrics(runs[name], cfg)
        for name, model in agents.items():
            # 60-month blocks put annual decisions mid-block, where macro state fingerprints
            # the month and the rest of the block is fixed history: agents that observe
            # macro would be scored on leaked information there, so they are left out.
            if sname == "held_out_60m" and name not in main_agents:
                continue
            # same lifetimes (same seeds, same returns); a leaky agent is shown the macro
            # state it was trained on, lagged along source history
            pp = leaky_sets[sname] if "_leaky" in name else paths
            r = simulate(Pl.agent_policy(model, cfg), pp, cfg, agent_ob[name])
            m = metrics(r, cfg)
            m["cec_minus_best_annuitized_ci95"] = paired_ci(r, runs[ann_key], cfg)
            rows[name] = m
            if sname == "held_out" and name in main_agents:
                profiles[name] = profile(r, cfg)
        for g in sorted({group(a) for a in agents}):
            cecs = [rows[a]["cec"] for a in agents if group(a) == g and a in rows]
            if not cecs:
                continue
            rows[f"{g.upper().replace('_MACRO', ' + macro').replace('_LEAKY', ' + macro, leaky lag')} mean over seeds"] = {
                "cec": float(np.mean(cecs)), "cec_min": float(np.min(cecs)),
                "cec_max": float(np.max(cecs)), "n_seeds": len(cecs),
                "minus_best_annuitized": float(np.mean(cecs) - rows[ann_key]["cec"])}
        profiles.setdefault("_rules", {})[sname] = {k: profile(runs[k], cfg) for k in (ann_key,)}
        out["sets"][sname] = rows
    out["profiles_held_out"] = profiles

    # Historical cohort replays: the actual monthly sequence, no resampling. Each
    # algorithm is represented by its median seed on held-out CEC (not its best).
    reps = {}
    for algo in sorted({group(a) for a in main_agents}):
        mine = sorted((a for a in main_agents if group(a) == algo), key=lambda a: out["sets"]["held_out"][a]["cec"])
        reps[algo] = mine[len(mine) // 2]
    out["cohort_agents"] = reps
    cohort_pols = {"4% rule, 60/40": (rules["4% rule, 60/40"], ob), "Best annuitized": (rules[ann_key], ob),
                   **{f"{al.upper()} ({name.split('_')[-1]})": (Pl.agent_policy(agents[name], cfg), agent_ob[name])
                      for al, name in reps.items()}}
    out["cohorts"] = {}
    for start in ("2000-01", "2007-01"):
        hp = D.historical_path(start)
        months = D.load().index[hp.month_index[0]].tolist()
        series = {}
        for k, (p, o) in cohort_pols.items():
            r = simulate(p, hp, cfg, o)
            series[k] = {"wealth": r["wealth"][0].round(0).tolist(), "cons": r["cons"][0].round(1).tolist(),
                         "equity": r["equity"][0].round(3).tolist()}
        out["cohorts"][start] = {"months": months, "series": series}
    RES.mkdir(exist_ok=True)
    (RES / "results.json").write_text(json.dumps(out, indent=2))
    (RES / "results_tables.md").write_text(markdown_tables(out))
    print_table(out)
    if main_agents:
        figures(out, cfg, ann_key)


def markdown_tables(out):
    """The same tables as print_table, as markdown, for RESULTS.md to quote."""
    lines = ["<!-- generated by evaluate.py from results.json; do not edit -->", ""]
    for sname, rows in out["sets"].items():
        title = {"in_sample": "In-sample (fresh 1955-1999 bootstrap lifetimes)",
                 "held_out": "Held out (2000-2026 bootstrap lifetimes)",
                 "held_out_60m": "Held out, 60-month blocks (robustness: multi-year regimes kept intact)"}[sname]
        lines += [f"### {title}", "",
                  "| Policy | CEC $/mo | P(run out before death) | Spend at 85, P10 | Median bequest | CEC minus best annuitized, 95% CI |",
                  "|---|---:|---:|---:|---:|---|"]
        for k, m in rows.items():
            if "p_depleted_before_death" not in m:
                lines.append(f"| **{k}** | **{m['cec']:,.0f}** | | | | mean {m['minus_best_annuitized']:+,.0f}; "
                             f"seeds {m['cec_min']:,.0f} to {m['cec_max']:,.0f} |")
                continue
            ci = m.get("cec_minus_best_annuitized_ci95")
            ci_s = f"[{ci[0]:+,.0f}, {ci[1]:+,.0f}]" if ci else ""
            lines.append(f"| {k} | {m['cec']:,.0f} | {m['p_depleted_before_death']:.1%} | "
                         f"{m['cons_age85_p10']:,.0f} | {m['bequest_p50']:,.0f} | {ci_s} |")
        lines.append("")
    return "\n".join(lines)


def print_table(out):
    for sname, rows in out["sets"].items():
        print(f"\n== {sname} ==  CEC = certainty-equivalent real consumption, $/month")
        print(f"{'policy':58s} {'CEC':>7s} {'P(deplete)':>10s} {'c@85 p10':>9s} {'bequest p50':>12s}")
        for k, m in rows.items():
            if "p_depleted_before_death" not in m:
                print(f"{k:58s} {m['cec']:7.0f}   (range {m['cec_min']:.0f}..{m['cec_max']:.0f}, {m['n_seeds']} seeds)")
                continue
            ci = m.get("cec_minus_best_annuitized_ci95")
            extra = f"  vs annuitized [{ci[0]:+.0f}, {ci[1]:+.0f}]" if ci else ""
            print(f"{k:58s} {m['cec']:7.0f} {m['p_depleted_before_death']:10.1%} "
                  f"{m['cons_age85_p10']:9.0f} {m['bequest_p50']:12,.0f}{extra}")


def figures(out, cfg, ann_key):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 10, "axes.edgecolor": MUTED, "axes.labelcolor": INK,
                         "xtick.color": MUTED, "ytick.color": MUTED, "axes.grid": True,
                         "grid.color": GRID, "grid.linewidth": 0.8, "axes.spines.top": False,
                         "axes.spines.right": False, "lines.linewidth": 2})

    # --- glide path and spending profile (held-out data)
    prof = out["profiles_held_out"]
    agents = [k for k in prof if not k.startswith("_")]
    algos = sorted({a.split("_")[0] for a in agents})
    rule = prof["_rules"]["held_out"][ann_key]
    ages = np.array(rule["age"])
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    algo_color = {"ddpg": BLUE, "td3": AQUA}
    for ax, key, ylab in [(axes[0], "equity", "Equity weight, %"), (axes[1], "spend_rate", "Withdrawal, % of wealth per year")]:
        for algo in algos:
            mine = [a for a in agents if a.startswith(algo)]
            med = np.array([prof[a][f"{key}_p50"] for a in mine])
            lo = np.array([prof[a][f"{key}_p10"] for a in mine]).mean(axis=0)
            hi = np.array([prof[a][f"{key}_p90"] for a in mine]).mean(axis=0)
            col = algo_color[algo]
            ax.fill_between(ages, lo * 100, hi * 100, color=col, alpha=0.12, linewidth=0)
            ax.plot(ages, med.mean(axis=0) * 100, color=col, linewidth=2.2, label=f"{algo.upper()} (mean of {len(mine)} seed medians; band P10–P90)")
        ax.plot(ages, np.array(rule[f"{key}_p50"]) * 100, color=ORANGE, linewidth=2, linestyle="--", label="Best annuitized rule")
        ax.set_xlabel("Age"); ax.set_ylabel(ylab); ax.set_xlim(65, 100)
    axes[0].set_ylim(0, 105)
    axes[1].set_ylim(0, 45)
    axes[1].legend(frameon=False, fontsize=8, loc="upper left")
    fig.suptitle("What the agent learned, on held-out 2000–2026 paths", x=0.01, ha="left", color=INK)
    fig.tight_layout()
    fig.savefig(RES / "fig_policy.png", dpi=150)
    plt.close(fig)

    # --- CEC comparison, held-out vs in-sample (dot plot)
    fig, ax = plt.subplots(figsize=(9, 4.2))
    rows_h, rows_i = out["sets"]["held_out"], out["sets"]["in_sample"]
    names = [k for k in rows_h if "p_depleted_before_death" in rows_h[k] and "_seed" not in k]
    names += [f"{al.upper()} agent, per seed" for al in algos]
    y = np.arange(len(names))[::-1]
    for yi, n in zip(y, names):
        if n.endswith("per seed"):
            al = n.split()[0].lower()
            hv = [rows_h[a]["cec"] for a in agents if a.split("_g")[0] == al]
            iv = [rows_i[a]["cec"] for a in agents if a.split("_g")[0] == al]
        else:
            hv, iv = [rows_h[n]["cec"]], [rows_i[n]["cec"]]
        ax.scatter(iv, [yi + 0.12] * len(iv), s=46, color=AQUA, edgecolor="white", linewidth=1.5, zorder=3)
        ax.scatter(hv, [yi - 0.12] * len(hv), s=46, color=BLUE, edgecolor="white", linewidth=1.5, zorder=3)
    ax.set_yticks(y); ax.set_yticklabels(names, color=INK)
    ax.set_xlabel("Certainty-equivalent consumption, real $ per month (higher is better)")
    ax.grid(axis="y", visible=False)
    ax.scatter([], [], s=46, color=AQUA, label="In-sample (1955–1999 paths)")
    ax.scatter([], [], s=46, color=BLUE, label="Held out (2000–2026 paths)")
    ax.legend(loc="lower right", frameon=False, fontsize=9)
    fig.tight_layout()
    fig.savefig(RES / "fig_cec.png", dpi=150)
    plt.close(fig)
    print(f"\nwrote {RES / 'results.json'}, fig_policy.png, fig_cec.png")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    main(ap.parse_args().quick)
