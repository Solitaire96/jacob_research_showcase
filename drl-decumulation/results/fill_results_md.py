"""Fill the placeholders in RESULTS.md and README.md from results/results.json.

    python3 results/fill_results_md.py

Run after evaluate.py. Every number these placeholders take is computed here from
results.json, so the documents cannot drift from the evaluation.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
R = json.loads((ROOT / "results" / "results.json").read_text())
S = R["sets"]
ANN = next(k for k in S["held_out"] if k.startswith("Best annuitized"))
CM = next(k for k in S["held_out"] if k.startswith("Best constant mix"))
FOUR = "4% rule, 60/40"
usd = lambda x: f"${x:,.0f}"
sgn = lambda x: f"{x:+,.0f}"


def seeds(set_name, group):
    return [k for k in S[set_name] if k.split("_g")[0] == group and "_seed" in k]


def main_table():
    H = S["held_out"]
    rows = ["| Policy | CEC $/mo | vs best annuitized | P(run out before death) | Spend at 85, P10 | Median bequest |",
            "|---|---:|---:|---:|---:|---:|"]
    for k in (FOUR, CM, "110-minus-age, annuitized", ANN):
        m = H[k]
        d = "" if k == ANN else sgn(m["cec"] - H[ANN]["cec"])
        rows.append(f"| {k} | {m['cec']:,.0f} | {d} | {m['p_depleted_before_death']:.1%} | "
                    f"{m['cons_age85_p10']:,.0f} | {m['bequest_p50']:,.0f} |")
    for g, label in (("ddpg", "DDPG agent"), ("td3", "TD3 agent")):
        ks = seeds("held_out", g)
        cec = [H[k]["cec"] for k in ks]
        above = sum(H[k]["cec_minus_best_annuitized_ci95"][0] > 0 for k in ks)
        below = sum(H[k]["cec_minus_best_annuitized_ci95"][1] < 0 for k in ks)
        p = lambda f: sum(H[k][f] for k in ks) / len(ks)
        rows.append(f"| **{label}**, mean of {len(ks)} seeds (range {min(cec):,.0f}–{max(cec):,.0f}) | "
                    f"**{sum(cec) / len(cec):,.0f}** | {sgn(sum(cec) / len(cec) - H[ANN]['cec'])} "
                    f"({above} of {len(ks)} seeds significantly above, {below} below; 95% paired CIs) | "
                    f"{p('p_depleted_before_death'):.1%} | {p('cons_age85_p10'):,.0f} | {p('bequest_p50'):,.0f} |")
    return "\n".join(rows)


def gap_table(groups, sets=("in_sample", "held_out")):
    head = "| Agent | " + " | ".join({"in_sample": "In-sample (1955–99)", "held_out": "Held out (2000–26)",
                                       "held_out_60m": "Held out, 60-month blocks"}[s] for s in sets) + " |"
    rows = [head, "|---|" + "---:|" * len(sets)]
    for g, label in groups:
        cells = []
        for s in sets:
            ks = seeds(s, g)
            if not ks:
                cells.append("—"); continue
            diffs = [S[s][k]["cec"] - S[s][ANN]["cec"] for k in ks]
            cells.append(f"{sgn(sum(diffs) / len(diffs))} ({', '.join(sgn(x) for x in diffs)})")
        rows.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(rows)


def policy_table():
    pr = R["profiles_held_out"]
    rule = pr["_rules"]["held_out"][ANN]
    ages = rule["age"]
    pick = [i for i, a in enumerate(ages) if a in (65, 70, 75, 80, 85, 90, 95)]
    mean = lambda g, key: [sum(pr[k][key][i] for k in pr if k.split("_g")[0] == g) /
                           sum(1 for k in pr if k.split("_g")[0] == g) for i in range(len(ages))]
    cols = {"DDPG equity": mean("ddpg", "equity_p50"), "TD3 equity": mean("td3", "equity_p50"),
            "Rule equity": rule["equity_p50"],
            "DDPG spend": mean("ddpg", "spend_rate_p50"), "TD3 spend": mean("td3", "spend_rate_p50"),
            "Rule spend": rule["spend_rate_p50"]}
    rows = ["| Age | " + " | ".join(cols) + " |", "|---:|" + "---:|" * len(cols)]
    for i in pick:
        rows.append(f"| {ages[i]:.0f} | " + " | ".join(f"{v[i]:.0%}" if "equity" in c else f"{v[i]:.1%}"
                                                       for c, v in cols.items()) + " |")
    rows.append("\nEquity is the weight on stocks; spend is the withdrawal rate, % of wealth per year.")
    return "\n".join(rows)


def cohort_table():
    rows = ["| Retired at 65 in | Policy | Wealth at end | Lowest monthly spend | Mean monthly spend |",
            "|---|---|---:|---:|---:|"]
    for start, c in R["cohorts"].items():
        for k, v in c["series"].items():
            rows.append(f"| {start} (to {c['months'][-1]}) | {k} | {usd(v['wealth'][-1])} | "
                        f"{usd(min(v['cons']))} | {usd(sum(v['cons']) / len(v['cons']))} |")
    return "\n".join(rows)


def ledger_beats_simple_rules():
    out = []
    for rule in (FOUR, CM):
        diffs = [S[s][k]["cec"] - S[s][rule]["cec"] for s in S for k in S[s]
                 if "_seed" in k and "_macro" not in k and "_leaky" not in k]
        out.append((rule, min(diffs), max(diffs)))
    return out


def fill():
    H = S["held_out"]
    dd = seeds("held_out", "ddpg")
    rep = {
        "MAIN_DDPG": usd(sum(H[k]["cec"] for k in dd) / len(dd)),
        "MAIN_4PCT": usd(H[FOUR]["cec"]),
        "MAIN_CM": usd(H[CM]["cec"]),
        "MAIN_TABLE": main_table(),
        "LEAK_TABLE": gap_table([("ddpg_leaky", "DDPG, macro lagged along source history (the leak)"),
                                 ("ddpg", "DDPG, wealth and age only (shipped)"),
                                 ("ddpg_macro", "DDPG + macro, lagged along the path (fixed)"),
                                 ("td3_macro", "TD3 + macro, lagged along the path (fixed)")]) +
                      "\n\nEach cell: mean of seeds (each seed), CEC minus the tuned annuitized rule, real $/month.",
        "ROBUST_TABLE": gap_table([("ddpg", "DDPG"), ("td3", "TD3")], sets=("held_out", "held_out_60m")) +
                        "\n\nEach cell: mean of seeds (each seed), CEC minus the tuned annuitized rule on the same lifetimes.",
        "POLICY_TABLE": policy_table(),
        "COHORT_TABLE": cohort_table(),
    }
    (four, f_lo, f_hi), (cm, c_lo, c_hi) = ledger_beats_simple_rules()
    beats_line_old = ("| Agents beat the 4% rule and constant-mix rules | **Robust** | "
                      "Holds in every set, every seed, by $280–1,100/month |")
    cm_status = "**Robust**" if c_lo > 0 else "**Not in every set**"
    beats_line_new = (f"| Agents beat the 4% rule | **Robust** | Every set, every seed: by {usd(f_lo)} to {usd(f_hi)}/month |\n"
                      f"| Agents beat the best constant-mix rule | {cm_status} | Margin per seed and set ranges "
                      f"{sgn(c_lo)} to {sgn(c_hi)}/month |")
    p = ROOT / "RESULTS.md"
    s = p.read_text()
    assert beats_line_old in s
    s = s.replace(beats_line_old, beats_line_new)
    for k, v in rep.items():
        assert k in s, k
        s = s.replace(k, v)
    p.write_text(s)
    print("RESULTS.md filled;", "4%:", usd(f_lo), usd(f_hi), "| constant mix:", sgn(c_lo), sgn(c_hi))

    gap = lambda s_, g: sum(S[s_][k]["cec"] - S[s_][ANN]["cec"] for k in seeds(s_, g)) / len(seeds(s_, g))
    leak_in, leak_out = gap("in_sample", "ddpg_leaky"), gap("held_out", "ddpg_leaky")
    headline = (
        f"Never shown the theory, the agent finds the closed-form policy's shape at a more conservative level "
        f"(see RESULTS.md, §4). On 2000–2026 data held out from training it scores "
        f"**{rep['MAIN_DDPG']}/month** certainty-equivalent (DDPG, five seeds) against **{usd(H[ANN]['cec'])}** for the "
        f"tuned annuitized rule, **{rep['MAIN_CM']}** for the best constant-percentage rule and **{rep['MAIN_4PCT']}** "
        f"for the 4% rule. Its score is about the same as the closed form's and does not robustly beat it: the {sgn(gap('held_out', 'ddpg'))} "
        f"held-out edge becomes {sgn(gap('held_out_60m', 'ddpg'))} when multi-year regimes are kept intact. "
        f"The most useful result is a failure caught on the way: an earlier version that could see macro state "
        f"memorised which block of history came next, scoring {sgn(leak_in)}/month in-sample and {sgn(leak_out)} "
        f"held out (`RESULTS.md`, §2).")
    p = ROOT / "README.md"
    s = p.read_text()
    assert "**Headline result.** HEADLINE" in s
    p.write_text(s.replace("**Headline result.** HEADLINE", "**Headline result.** " + headline))
    print("README headline filled")


if __name__ == "__main__":
    fill()
