"""Two-minute demo on the shipped checkpoints. No training, no network.

    python3 demo.py                    # held-out comparison + replay of a 2000 retiree
    python3 demo.py --cohort 2007-01   # replay someone who retired into the GFC

Prints a comparison table on held-out (2000-2026) bootstrap lifetimes and writes
results/demo_cohort_<start>.png: wealth and consumption along the real historical
sequence for the agent and three rules.
"""
import argparse
import json
from pathlib import Path

import numpy as np

from decumulation import data as D, policies as Pl
from decumulation.sim import Config, ObsBuilder, simulate, metrics

ROOT = Path(__file__).resolve().parent
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"


def default_agent():
    """The representative agent: median-seed DDPG on held-out CEC, per results.json."""
    res = ROOT / "results" / "results.json"
    if res.exists():
        reps = json.loads(res.read_text()).get("cohort_agents", {})
        return reps.get("ddpg") or next(iter(reps.values()), None)
    return None


def load_agent(name=None):
    from stable_baselines3 import DDPG, TD3
    ckpts = sorted(c for c in (ROOT / "checkpoints").glob("*.zip"))
    if not ckpts:
        raise SystemExit("no checkpoints/ found; run train.py first")
    name = name or default_agent()
    ck = next((c for c in ckpts if c.stem == name), None) or next(c for c in ckpts if "_macro" not in c.stem and "_leaky" not in c.stem)
    return ck.stem, {"ddpg": DDPG, "td3": TD3}[ck.stem.split("_")[0]].load(ck)


def tuned_rules(cfg):
    res = ROOT / "results" / "results.json"
    if res.exists():
        best = json.loads(res.read_text())["tuned_rules"]
    else:  # same defaults evaluate.py tunes to; recomputed if results.json is missing
        best = Pl.tune(cfg, D.block_bootstrap(D.load(D.TRAIN), 3000, cfg.n_months, seed=101), ObsBuilder(cfg))
    return {
        "4% rule, 60/40": Pl.four_percent(cfg),
        "Best annuitized rule": Pl.annuitized(cfg, **best["annuitized"]),
        "Best constant mix": Pl.constant_mix(cfg, **best["constant_mix"]),
    }


def main(n_paths, cohort, agent_name):
    cfg = Config()
    ob = ObsBuilder(cfg)
    name, model = load_agent(agent_name)
    agent_key = f"Agent ({name})"
    pols = {agent_key: Pl.agent_policy(model, cfg), **tuned_rules(cfg)}
    obs_for = {k: (ObsBuilder(cfg, use_macro="_macro" in name) if k == agent_key else ob) for k in pols}

    paths = D.block_bootstrap(D.load(D.TEST), n_paths, cfg.n_months, seed=202)
    print(f"{n_paths} held-out lifetimes (blocks drawn from 2000-01..2026-09), real $/month")
    print(f"{'policy':28s} {'CEC':>7s} {'1st-yr spend':>12s} {'spend@85 p10':>13s} {'P(deplete)':>10s} {'bequest p50':>12s}")
    for k, p in pols.items():
        m = metrics(simulate(p, paths, cfg, obs_for[k]), cfg)
        print(f"{k:28s} {m['cec']:7.0f} {m['first_year_cons_p50']:12.0f} {m['cons_age85_p10']:13.0f} "
              f"{m['p_depleted_before_death']:10.1%} {m['bequest_p50']:12,.0f}")

    hp = D.historical_path(cohort)
    runs = {k: simulate(p, hp, cfg, obs_for[k]) for k, p in pols.items()}
    months = D.load().index[hp.month_index[0]]
    print(f"\nReplay: retired at 65 in {cohort}, actual history to {months[-1]} "
          f"({len(months) / 12:.1f} years). Wealth at end, real $:")
    for k, r in runs.items():
        print(f"  {k:28s} {r['wealth'][0, -1]:12,.0f}   min monthly spend {r['cons'][0].min():7,.0f}")
    plot_cohort(runs, months, cohort)


def plot_cohort(runs, months, cohort):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 10, "axes.edgecolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED,
                         "axes.grid": True, "grid.color": GRID, "axes.spines.top": False,
                         "axes.spines.right": False, "lines.linewidth": 2})
    yrs = 65 + np.arange(len(months) + 1) / 12.0
    colors = [BLUE, MUTED, ORANGE, AQUA]
    styles = ["-", ":", "--", "-."]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    for (k, r), col, ls in zip(runs.items(), colors, styles):
        axes[0].plot(yrs, r["wealth"][0] / 1e6, color=col, linestyle=ls, label=k)
        cons = np.convolve(r["cons"][0], np.ones(12) / 12, mode="valid")  # trailing 12-month mean
        axes[1].plot(yrs[12:len(cons) + 12], cons, color=col, linestyle=ls)
    axes[0].set_ylabel("Portfolio, real $ millions"); axes[0].set_xlabel("Age")
    axes[1].set_ylabel("Consumption, real $/month (12-mo avg)"); axes[1].set_xlabel("Age")
    axes[0].legend(frameon=False, fontsize=8.5, loc="upper right")
    fig.suptitle(f"Retiring at 65 in {cohort}: the actual sequence of returns", x=0.01, ha="left", color=INK)
    fig.tight_layout()
    out = ROOT / "results" / f"demo_cohort_{cohort}.png"
    out.parent.mkdir(exist_ok=True)
    fig.savefig(out, dpi=150)
    print(f"\nwrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--paths", type=int, default=1000)
    ap.add_argument("--cohort", default="2000-01", help="YYYY-MM retirement month to replay")
    ap.add_argument("--agent", default=None, help="checkpoint stem, e.g. ddpg_g4_seed0")
    a = ap.parse_args()
    main(a.paths, a.cohort, a.agent)
