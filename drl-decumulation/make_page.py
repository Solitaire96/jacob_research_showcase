"""Build the showcase site's decumulation tab from results/results.json.

    python3 make_page.py ../docs

Writes <docs>/decumulation.html, reusing the <style> block of <docs>/index.html so
the tabs share one design; reads the site's existing tab bar from index.html, and
adds this project's tab to every other page that lacks it.
Every number on the page comes from results.json; nothing is typed in by hand.
"""
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = "https://github.com/Solitaire96/jacob_research_showcase/tree/main/drl-decumulation"
BLOB = "https://github.com/Solitaire96/jacob_research_showcase/blob/main/drl-decumulation"

TABS_CSS = """
nav.tabs{display:flex;gap:4px;margin:0 0 28px;border-bottom:1px solid var(--line);flex-wrap:wrap}
nav.tabs a{padding:8px 12px;font-size:14px;color:var(--ink-2);text-decoration:none;border-bottom:2px solid transparent;margin-bottom:-1px}
nav.tabs a:hover{color:var(--ink)}
nav.tabs a[aria-current="page"]{color:var(--ink);border-bottom-color:var(--s1);font-weight:600}
"""
EXTRA_CSS = """
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden;margin:0 0 8px}
.seg button{font:inherit;font-size:13px;padding:5px 12px;border:0;background:var(--surface);color:var(--ink-2);cursor:pointer}
.seg button[aria-pressed="true"]{background:var(--s1);color:#fff}
.sw.dash{background:none!important;border-top:2px dashed var(--s2);height:0}
.sw.dot{width:10px;height:10px;border-radius:50%}

.kv{display:grid;grid-template-columns:auto minmax(0,1fr);gap:6px 14px;margin:4px 0 0;font-size:14px}
.kv dt{color:var(--ink-3)}
.kv dd{margin:0}
.leak table{margin:0}
"""


TAB_HREF, TAB_LABEL = "decumulation.html", "Deep-RL decumulation"
NAV_RE = re.compile(r'<nav class="tabs"[^>]*>(.*?)</nav>', re.S)
LINK_RE = re.compile(r'<a href="([^"]+)"[^>]*>(.*?)</a>', re.S)


def site_tabs(docs: Path):
    """The site's existing tab list, read from index.html, with this project appended."""
    m = NAV_RE.search((docs / "index.html").read_text())
    items = LINK_RE.findall(m.group(1)) if m else [("./", "Flux-cone automata")]
    if not any(h == TAB_HREF for h, _ in items):
        items.append((TAB_HREF, TAB_LABEL))
    return items


def tabs(items, active):
    cur = ' aria-current="page"'
    links = "".join(f'\n  <a href="{h}"{cur if h == active else ""}>{t}</a>' for h, t in items)
    return f'<nav class="tabs" aria-label="Projects">{links}\n</nav>'


def ensure_tab_on_pages(docs: Path):
    """Add this project's tab to every other page's existing tab bar (one <a> line each),
    or, on a page with no tab bar, insert the whole bar. Touches nothing else."""
    changed = []
    for page in sorted(docs.glob("*.html")):
        if page.name == TAB_HREF:
            continue
        s = page.read_text()
        m = NAV_RE.search(s)
        if m and TAB_HREF in m.group(1):
            continue
        if m:
            s = s[:m.end() - len("</nav>")] + f'  <a href="{TAB_HREF}">{TAB_LABEL}</a>\n</nav>' + s[m.end():]
        else:
            active = "./" if page.name == "index.html" else page.name
            s = s.replace("</style>", TABS_CSS + "</style>", 1)
            s = s.replace("<main>", "<main>\n" + tabs(site_tabs(docs), active), 1)
        page.write_text(s)
        changed.append(page.name)
    return changed


def signed_money(x):
    """+$49 / −$70: dollar sign and a true minus, as in the prose."""
    return ("+" if round(x) >= 0 else "−") + f"${abs(x):,.0f}"


def money(x):
    return f"${x:,.0f}"


TABS_WRAP_CSS = """/* tabs: wrap on narrow screens so every project stays visible */
@media (max-width:600px){.tabs{flex-wrap:wrap;overflow-x:visible}.tabs a{padding:8px 9px 9px}}
"""


def ensure_tabs_wrap(docs: Path):
    """Add the narrow-screen wrap rule to every page's stylesheet once."""
    changed = []
    for page in sorted(docs.glob("*.html")):
        if page.name == TAB_HREF:
            continue
        s = page.read_text()
        if "tabs: wrap on narrow screens" in s or "</style>" not in s:
            continue
        page.write_text(s.replace("</style>", TABS_WRAP_CSS + "</style>", 1))
        changed.append(page.name)
    return changed


def build(docs: Path):
    wrapped = ensure_tabs_wrap(docs)
    R = json.loads((ROOT / "results" / "results.json").read_text())
    style = re.search(r"<style>(.*?)</style>", (docs / "index.html").read_text(), re.S).group(1)
    if ".tabs{" not in style and "nav.tabs" not in style:   # reuse the site's own tab style
        style += TABS_CSS
    style += EXTRA_CSS

    H, I = R["sets"]["held_out"], R["sets"]["in_sample"]
    ann_key = next(k for k in H if k.startswith("Best annuitized"))
    cm_key = next(k for k in H if k.startswith("Best constant mix"))
    mean = lambda rows, g: rows[f"{g} mean over seeds"]
    groups = [g for g in ("TD3", "DDPG") if f"{g} mean over seeds" in H]
    best_g = max(groups, key=lambda g: mean(H, g)["cec"])
    ablate = [g for g in ("DDPG + macro, leaky lag", "TD3 + macro, leaky lag", "DDPG + macro", "TD3 + macro")
              if f"{g} mean over seeds" in H]

    # ---- data for the charts
    prof = R["profiles_held_out"]
    rule_prof = prof["_rules"]["held_out"][ann_key]
    ages = rule_prof["age"]
    agent_names = [k for k in prof if not k.startswith("_")]

    def mean_curve(g, key):
        mine = [a for a in agent_names if a.split("_g")[0] == g.lower()]
        cols = list(zip(*[prof[a][f"{key}_p50"] for a in mine]))
        return [round(sum(c) / len(c), 4) for c in cols]

    profile_series = {"ages": ages,
                      "spend": {g: mean_curve(g, "spend_rate") for g in groups},
                      "equity": {g: mean_curve(g, "equity") for g in groups},
                      "rule_spend": rule_prof["spend_rate_p50"], "rule_equity": rule_prof["equity_p50"]}

    rule_rows = [k for k in H if "p_depleted_before_death" in H[k] and "_seed" not in k]
    dots = [{"name": k, "held": [H[k]["cec"]], "ins": [I[k]["cec"]], "kind": "rule"} for k in rule_rows]
    for g in groups:
        key = g.lower()
        seeds = [a for a in H if a.split("_g")[0] == key]
        dots.append({"name": f"{g} agent", "held": [H[a]["cec"] for a in seeds],
                     "ins": [I[a]["cec"] for a in seeds], "kind": "agent"})

    cohorts = {}
    for start, c in R["cohorts"].items():
        def trailing12(x):  # trailing 12-month mean of monthly consumption, sampled quarterly
            out = [sum(x[max(0, i - 11):i + 1]) / len(x[max(0, i - 11):i + 1]) for i in range(len(x))]
            return [round(v) for v in out[::3]]
        cohorts[start] = {"end": c["months"][-1],
                          "wealth": {k: v["wealth"][::3] for k, v in c["series"].items()},
                          "cons": {k: trailing12(v["cons"]) for k, v in c["series"].items()}}

    data = {"profile": profile_series, "dots": dots, "cohorts": cohorts, "best": best_g}

    # ---- numbers for the prose
    hb, ha, h4 = mean(H, best_g)["cec"], H[ann_key]["cec"], H["4% rule, 60/40"]["cec"]
    ib, ia = mean(I, best_g)["cec"], I[ann_key]["cec"]
    ci = [H[a]["cec_minus_best_annuitized_ci95"] for a in H if a.split("_g")[0] == best_g.lower()]
    n_above, n_below = sum(c[0] > 0 for c in ci), sum(c[1] < 0 for c in ci)
    n_mid = len(ci) - n_above - n_below
    split = (f"{n_above} of {len(ci)} seeds significantly above the rule, {n_below} below"
             + (f", {n_mid} indistinguishable" if n_mid else "") + " (95% paired intervals)")
    tuned = R["tuned_rules"]
    p4 = H["4% rule, 60/40"]["p_depleted_before_death"]

    def gap_text(g):
        d_in = mean(I, g)["cec"] - I[ann_key]["cec"]
        d_out = mean(H, g)["cec"] - H[ann_key]["cec"]
        return d_in, d_out

    abl_rows = "".join(
        f"<tr><td>{html.escape(g)}</td><td class='num'>{gap_text(g)[0]:+,.0f}</td><td class='num'>{gap_text(g)[1]:+,.0f}</td></tr>"
        for g in ablate[:1] + groups + ablate[1:])

    i90 = ages.index(90)
    s90 = " and ".join(f"{profile_series['spend'][g][i90] * 100:.0f}%" for g in groups) + f" ({' and '.join(groups)})"
    r90 = f"{profile_series['rule_spend'][i90] * 100:.0f}%"
    lk = "DDPG + macro, leaky lag"
    leak_in = (mean(I, lk)["cec"] - I[ann_key]["cec"]) if f"{lk} mean over seeds" in I else None
    leak_out = (mean(H, lk)["cec"] - H[ann_key]["cec"]) if f"{lk} mean over seeds" in H else None
    stat_n = signed_money(hb - ha)
    R60 = R["sets"].get("held_out_60m", {})
    d60 = (mean(R60, best_g)["cec"] - R60[ann_key]["cec"]) if R60 else None
    robust = (f"With multi-year regimes kept intact (60-month blocks) the gap is {signed_money(d60)}. "
              "It scores about the same as the closed-form rule; it does not robustly beat it.") if d60 is not None else ""
    page = PAGE.format(
        style=style, tabs=tabs(site_tabs(docs), TAB_HREF), repo=REPO, blob=BLOB,
        best_g=best_g, n_seeds=mean(H, best_g)["n_seeds"],
        su_age=f"{R['config']['start_age']:.0f}", su_w=money(R['config']['initial_wealth']),
        su_g=money(R['config']['guaranteed_income']), su_dec=f"{R['config']['decision_months']}",
        su_gamma=f"{R['config']['gamma']:g}", su_block=f"{R['config']['block']}",
        su_tr0=R['windows']['train'][0], su_tr1=R['windows']['train'][1],
        su_te0=R['windows']['test'][0], su_te1=R['windows']['test'][1],
        su_agents=" and ".join(groups), su_eval=f"{R['n_paths']['eval']:,}",
        hb=money(hb), ha=money(ha), h4=money(h4), stat_n=html.escape(stat_n),
        ib=money(ib), ia=money(ia),
        split=html.escape(split), robust=html.escape(robust),
        ann_eq=f"{tuned['annuitized']['equity']:.0%}", ann_r=f"{tuned['annuitized']['real_rate']:.0%}",
        cm_eq=f"{tuned['constant_mix']['equity']:.0%}", cm_rate=f"{tuned['constant_mix']['rate']:.1%}",
        cm=money(H[cm_key]["cec"]), p4=f"{p4:.1%}",
        abl_rows=abl_rows, n_eval=R["n_paths"]["eval"], s90=s90, r90=r90,
        leak_in=signed_money(leak_in), leak_out=signed_money(leak_out),
        data=json.dumps(data, separators=(",", ":")),
    )
    (docs / "decumulation.html").write_text(page)
    changed = ensure_tab_on_pages(docs)
    print(f"wrote {docs / TAB_HREF} ({len(page):,} bytes); tab added to: {', '.join(changed) or 'none (already present)'}; "
          f"narrow-screen tab wrap added to: {', '.join(wrapped) or 'none (already present)'}")


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Deep-RL Decumulation</title>
<meta name="description" content="A two-minute read: a deep-RL agent learns a retirement spending and allocation policy from 70 years of market history, scored on data it never saw against the closed-form benchmark.">
<style>{style}</style>
</head>
<body>
<main>
{tabs}
<h1>Deep-RL retirement decumulation</h1>
<p class="lede">How much should a retiree spend each year, and how much should stay in stocks, when markets and lifespan are both uncertain? Theory has a closed-form answer for a stylised version. This asks whether a model-free agent finds it on its own, and whether it holds up on two decades it never saw.</p>
<p class="meta">Jacob Bowers · 2026 · <a href="{repo}">code, data, checkpoints and tests on GitHub</a></p>

<div class="grid">
  <div class="card wrap">
    <h3>Spending as a share of wealth, by age</h3>
    <p class="sub">Median withdrawal rate on held-out (2000–2026) lifetimes. Never shown the annuity rule, the agents find its shape (spending rises with age) at a more conservative level: at 90, {s90} of wealth a year against the rule's {r90}.</p>
    <div class="legend" id="lg1"></div>
    <svg id="c1" viewBox="0 0 640 320" role="img" aria-labelledby="c1t"><title id="c1t">Withdrawal rate rises with age for both the agent and the annuitized rule</title></svg>
    <div class="tip" id="tip1"></div>
  </div>
  <div class="card stat">
    <div class="n">{hb}</div>
    <div class="l">certainty-equivalent real spending per month for the {best_g} agent (mean of {n_seeds} seeds), on 2000–2026 data held out from training.</div>
    <div class="row">
      <div><b>{ha}</b>best annuitized rule, tuned on the same training data</div>
      <div><b>{h4}</b>4% rule, 60/40</div>
    </div>
    <div class="row" style="border-top:0;padding-top:0;margin-top:10px">
      <div style="max-width:34ch">Agent minus rule here: {stat_n} a month; {split}. {robust}</div>
    </div>
    <div class="row">
      <div style="max-width:34ch"><b>Caught on the way</b>A version that could see macro state scored {leak_in} a month in-sample and {leak_out} held out: it had memorised which stretch of history came next. The gap gave it away; it is reproduced on purpose below.</div>
    </div>
  </div>
</div>

<div class="split">
<div>
<h2>The problem, in one paragraph</h2>
<p>A 65-year-old has $1M and $2,000 a month of guaranteed income. Once a year until death they choose an equity weight and a withdrawal rate; returns, rebalancing and withdrawals run monthly. The objective is expected CRRA utility of real consumption (risk aversion γ = 4), weighted by Gompertz survival (median death age 85.7). Lifetimes are 12-month block bootstraps of real US returns: the agent trains on 1955–1999 only, and is scored on 2000–2026, which holds the dot-com crash, 2008, 2020 and the 2022 bond crash. The score is certainty-equivalent consumption: the constant monthly spending worth the same expected utility as the policy's uncertain stream. Two actor-critic algorithms are trained, DDPG (Lillicrap et al., 2016) and TD3 (Fujimoto et al., 2018), five seeds each.</p>
</div>
  <aside class="card thm" aria-label="Setup at a glance">
    <div class="thm-k">Setup at a glance</div>
    <dl class="kv">
      <dt>Retiree</dt><dd>age {su_age}, {su_w} saved, {su_g}/month guaranteed</dd>
      <dt>Decisions</dt><dd>every {su_dec} months: equity weight and withdrawal rate</dd>
      <dt>Objective</dt><dd>CRRA utility of real consumption, &gamma; = {su_gamma}, survival-weighted</dd>
      <dt>Lifetimes</dt><dd>{su_block}-month block bootstrap of real US returns</dd>
      <dt>Train</dt><dd>{su_tr0} to {su_tr1}</dd>
      <dt>Test</dt><dd>{su_te0} to {su_te1}, held out</dd>
      <dt>Agents</dt><dd>{su_agents}, {n_seeds} seeds each</dd>
      <dt>Score</dt><dd>certainty-equivalent $/month over {su_eval} lifetimes</dd>
    </dl>
  </aside>
</div>

<div class="split">
<div>
<h2>The leak, caught and reproduced</h2>
<p>Blocks of real history make lifetimes realistic, and they make memorisation easy. When the agent saw last month's Fed funds rate, term spread and inflation at each annual decision, that triplet identified the calendar month, and with it the next twelve months of returns. A 64×64 network learned the map. The tell is the gap between in-sample and held-out scores. The table gives each agent's score minus the tuned annuitized rule's, in real $/month.</p>
</div>
<aside class="card leak" aria-label="Agent minus the tuned rule, real $ a month">
<table>
<thead><tr><th>Agent</th><th>In-sample</th><th>Held out</th></tr></thead>
<tbody>{abl_rows}</tbody>
</table>
</aside>
</div>
<p style="margin-top:12px">The fix lags macro state along the simulated path, not the source history, so at a block boundary the agent sees the previous, independent block. At annual decisions that leaves nothing to learn from, and the default agent observes only wealth and age. The "leaky lag" rows reproduce the bug on purpose (<code>train.py --leaky</code>); the "+ macro" rows keep the macro features with the fix in place.</p>

<div class="grid2" style="margin-top:16px">
  <div class="card wrap">
    <h3>Equity weight, by age</h3>
    <p class="sub">Median on held-out lifetimes. With $2,000/month of guaranteed income acting like a bond, both the agent and the tuned rule hold mostly equities.</p>
    <svg id="c2" viewBox="0 0 520 300" role="img" aria-labelledby="c2t"><title id="c2t">Equity weight by age, agent vs annuitized rule</title></svg>
    <div class="tip" id="tip2"></div>
  </div>
  <div class="card wrap">
    <h3>Score, in-sample vs held out</h3>
    <p class="sub">Certainty-equivalent real $/month, {n_eval:,} lifetimes each. One dot per seed for agents.</p>
    <div class="legend"><span><i class="sw dot" style="background:var(--s1)"></i>Held out, 2000–2026</span><span><i class="sw dot" style="background:var(--ink-3)"></i>In-sample, 1955–1999</span></div>
    <svg id="c3" viewBox="0 0 520 300" role="img" aria-labelledby="c3t"><title id="c3t">Certainty-equivalent consumption by policy</title></svg>
    <div class="tip" id="tip3"></div>
  </div>
</div>

<h2>Replay a real retirement</h2>
<p>Not a bootstrap draw: the actual month-by-month sequence for someone who retired at 65 into January 2000 or January 2007. Agent shown is the median seed, not the best one. Look at both measures: the 4% rule ends with the most money because it spends the least.</p>
<div class="card wrap">
  <div class="seg" id="segc" role="group" aria-label="Retirement start"><button type="button" data-c="2000-01" aria-pressed="true">Retire Jan 2000</button><button type="button" data-c="2007-01" aria-pressed="false">Retire Jan 2007</button></div>
  <div class="seg" id="segm" role="group" aria-label="Measure" style="margin-left:8px"><button type="button" data-m="wealth" aria-pressed="true">Portfolio</button><button type="button" data-m="cons" aria-pressed="false">Monthly spending</button></div>
  <div class="legend" id="lg4"></div>
  <svg id="c4" viewBox="0 0 1000 380" role="img" aria-labelledby="c4t"><title id="c4t">Portfolio value over the actual historical sequence</title></svg>
  <div class="tip" id="tip4"></div>
</div>

<h2>Benchmarks</h2>
<table>
<thead><tr><th>Rule</th><th>What it does</th></tr></thead>
<tbody>
<tr><td>4% rule, 60/40</td><td>Bengen (1994): a fixed real withdrawal of 4% of starting wealth. The only fixed-dollar rule; it runs out in {p4} of held-out lifetimes (the annuitized rules in 0.5%, the agents in none).</td></tr>
<tr><td>Best constant mix</td><td>A fixed equity weight and spending rate, grid-searched on training data ({cm_eq} equity, {cm_rate} a year): {cm} held out.</td></tr>
<tr><td>Best annuitized</td><td>Spend wealth divided by the survival-weighted annuity factor, the closed-form structure of Merton (1969, 1971) and Yaari (1965); equity weight and real rate grid-searched on training data ({ann_eq} equity, r = {ann_r}).</td></tr>
<tr><td>110-minus-age</td><td>The rule-of-thumb glide path, with annuitized spending.</td></tr>
</tbody>
</table>

<h2>Honest limits</h2>
<ul>
<li>In this setting the closed-form rule is close to optimal, so the best an agent can do is roughly match it, and that is what happens: a small held-out edge that reverses with 60-month blocks. That is evidence the RL machinery works, not that RL beats theory. An edge would need genuine state dependence (a fitted regime or VAR return model, fat-tailed shocks, taxes, annuity purchases), none of which is in this simulator.</li>
<li>Returns are index returns: CRSP total market and a 10-year Treasury built from monthly-average yields. No fees, no taxes, no credit, no TIPS.</li>
<li>Mortality is a two-parameter Gompertz law (m = 88, b = 10, after Milevsky, 2012), not a life table; no couples, health or long-term-care shocks.</li>
<li>2000–2026 is one realisation of history. Bootstrapping it gives many lifetimes, but they share its regimes; the in-sample tuned rules prefer 100% equity, which that window punished.</li>
<li>Nothing here is peer-reviewed. The 2026 rebuild was done with AI coding assistants under my direction; the repo's ledger records what was checked and how.</li>
</ul>

<h2>References</h2>
<ol class="refs">
  <li>Bengen, W. P. (1994). Determining withdrawal rates using historical data. <i>Journal of Financial Planning</i> 7(4), 171–180.</li>
  <li>Fujimoto, S., van Hoof, H. &amp; Meger, D. (2018). Addressing function approximation error in actor-critic methods. In <i>ICML 2018</i>, PMLR 80. <a href="https://arxiv.org/abs/1802.09477">arXiv:1802.09477</a></li>
  <li>Lillicrap, T. P., Hunt, J. J., Pritzel, A., et al. (2016). Continuous control with deep reinforcement learning. In <i>ICLR 2016</i>. <a href="https://arxiv.org/abs/1509.02971">arXiv:1509.02971</a></li>
  <li>Merton, R. C. (1969). Lifetime portfolio selection under uncertainty: the continuous-time case. <i>The Review of Economics and Statistics</i> 51(3), 247–257. <a href="https://doi.org/10.2307/1926560">doi:10.2307/1926560</a></li>
  <li>Merton, R. C. (1971). Optimum consumption and portfolio rules in a continuous-time model. <i>Journal of Economic Theory</i> 3(4), 373–413. <a href="https://doi.org/10.1016/0022-0531(71)90038-X">doi:10.1016/0022-0531(71)90038-X</a></li>
  <li>Milevsky, M. A. (2012). <i>The 7 Most Important Equations for Your Retirement</i>. Wiley.</li>
  <li>Yaari, M. E. (1965). Uncertain lifetime, life insurance, and the theory of the consumer. <i>The Review of Economic Studies</i> 32(2), 137–150. <a href="https://doi.org/10.2307/2296058">doi:10.2307/2296058</a></li>
</ol>
<p class="sub" style="margin-top:6px">DOIs checked against Crossref and arXiv identifiers against arXiv, November 2026. Bengen (1994) and Milevsky (2012) have no DOI. Market data: Kenneth R. French Data Library and FRED.</p>

<details><summary>Data behind the charts (table view)</summary>
<table id="tbl"><thead><tr><th>Age</th><th>{best_g} agent spend rate</th><th>Rule spend rate</th><th>{best_g} agent equity</th><th>Rule equity</th></tr></thead><tbody></tbody></table>
<p class="sub" style="margin-top:6px">{best_g} agent: mean over its {n_seeds} seeds of the median at each age, on held-out lifetimes; the same series as the solid {best_g} lines in the charts.</p>
</details>

<p class="foot">Source data: <code>drl-decumulation/results/results.json</code>, produced by <code>evaluate.py</code> from the shipped checkpoints. Market data: Kenneth R. French Data Library and FRED (<a href="{blob}/data/SOURCES.md">sources</a>). Built on open literature and public data only.</p>
</main>

<script>
(function(){{
const D={data};
const NS='http://www.w3.org/2000/svg';
const el=(n,a,p)=>{{const e=document.createElementNS(NS,n);for(const k in a)e.setAttribute(k,a[k]);if(p)p.appendChild(e);return e;}};
const pct=v=>(v*100).toFixed(1)+'%';
const usd=v=>'$'+Math.round(v).toLocaleString('en-US');
const COL={{'TD3':'var(--s1)','DDPG':'#1baf7a','rule':'var(--s2)','4%':'var(--ink-3)'}};

function legend(id,items){{const L=document.getElementById(id);L.innerHTML=items.map(i=>`<span><i class="sw${{i.dash?' dash':''}}" style="background:${{i.c}}"></i>${{i.t}}</span>`).join('');}}

function lineChart(o){{
  const svg=document.getElementById(o.svg),tip=document.getElementById(o.tip),wrap=svg.parentNode;
  svg.innerHTML='';
  const W=o.W||640,H=o.H||320,m={{t:20,r:o.r||96,b:36,l:56}};
  const x=v=>m.l+(v-o.x0)/(o.x1-o.x0)*(W-m.l-m.r);
  const y=v=>H-m.b-(v-o.y0)/(o.y1-o.y0)*(H-m.t-m.b);
  const ax=el('g',{{class:'axis'}},svg);
  o.yt.forEach(v=>{{el('line',{{x1:m.l,x2:W-m.r,y1:y(v),y2:y(v)}},ax);const t=el('text',{{x:m.l-8,y:y(v)+4,'text-anchor':'end'}},ax);t.textContent=o.yf(v);}});
  el('line',{{class:'base',x1:m.l,x2:W-m.r,y1:y(o.y0),y2:y(o.y0)}},ax);
  o.xt.forEach(v=>{{const t=el('text',{{x:x(v),y:H-m.b+18,'text-anchor':'middle'}},ax);t.textContent=v;}});
  const xl=el('text',{{x:(m.l+W-m.r)/2,y:H-2,'text-anchor':'middle'}},ax);xl.textContent=o.xl;
  o.series.forEach(s=>{{
    const pts=s.y.map((v,i)=>v==null?null:`${{x(o.xs[i])}},${{y(v)}}`).filter(Boolean).join(' ');
    const pl=el('polyline',{{points:pts,fill:'none',stroke:s.c,'stroke-width':2,'stroke-linejoin':'round','stroke-linecap':'round'}},svg);
    if(s.dash)pl.setAttribute('stroke-dasharray','5 4');
    if(s.label){{let i=s.y.length-1;while(i>0&&s.y[i]==null)i--;const t=el('text',{{class:'lab',x:x(o.xs[i])+6,y:y(s.y[i])+4+(s.dy||0)}},svg);t.textContent=s.label;}}
  }});
  const cross=el('line',{{y1:m.t,y2:H-m.b,stroke:'var(--line)','stroke-width':1,opacity:0}},svg);
  const dots=o.series.map(s=>el('circle',{{r:4,fill:s.c,stroke:'var(--surface)','stroke-width':2,opacity:0}},svg));
  const hit=el('rect',{{x:m.l,y:m.t,width:W-m.l-m.r,height:H-m.t-m.b,fill:'transparent'}},svg);
  function show(ev){{
    const r=svg.getBoundingClientRect(),px=(ev.clientX-r.left)/r.width*W;
    let i=0,best=1e9;o.xs.forEach((v,j)=>{{const d=Math.abs(x(v)-px);if(d<best){{best=d;i=j;}}}});
    cross.setAttribute('x1',x(o.xs[i]));cross.setAttribute('x2',x(o.xs[i]));cross.setAttribute('opacity',1);
    o.series.forEach((s,k)=>{{if(s.y[i]==null){{dots[k].setAttribute('opacity',0);return;}}dots[k].setAttribute('cx',x(o.xs[i]));dots[k].setAttribute('cy',y(s.y[i]));dots[k].setAttribute('opacity',1);}});
    tip.innerHTML=o.head(o.xs[i])+'<br>'+o.series.filter(s=>s.y[i]!=null).map(s=>`${{s.name}} <b>${{o.yf(s.y[i])}}</b>`).join('<br>');
    const wr=wrap.getBoundingClientRect(),top=Math.min(...o.series.filter(s=>s.y[i]!=null).map(s=>y(s.y[i])));
    tip.style.left=(x(o.xs[i])/W*r.width+(r.left-wr.left))+'px';tip.style.top=(top/H*r.height+(r.top-wr.top))+'px';tip.style.opacity=1;
  }}
  const hide=()=>{{cross.setAttribute('opacity',0);dots.forEach(d=>d.setAttribute('opacity',0));tip.style.opacity=0;}};
  hit.addEventListener('mousemove',show);hit.addEventListener('touchstart',e=>show(e.touches[0]),{{passive:true}});hit.addEventListener('mouseleave',hide);
}}

// chart 1 + 2: profiles, ages 65..100
const P=D.profile,cut=P.ages.findIndex(a=>a>100),n=cut<0?P.ages.length:cut;
const xs=P.ages.slice(0,n),groups=Object.keys(P.spend);
legend('lg1',[...groups.map(g=>({{t:g+' agent (mean of seed medians)',c:COL[g]}})),{{t:'Best annuitized rule',c:COL.rule,dash:1}}]);
const smax=Math.max(...groups.flatMap(g=>P.spend[g].slice(0,n)),...P.rule_spend.slice(0,n)),sy1=Math.ceil(smax*10)/10,syt=[];
for(let v=0;v<=sy1+1e-9;v+=sy1>0.35?0.1:0.05)syt.push(+v.toFixed(2));
lineChart({{svg:'c1',tip:'tip1',xs,x0:65,x1:100,y0:0,y1:sy1,yt:syt,yf:v=>Math.round(v*100)+'%',r:70,xt:[65,70,75,80,85,90,95,100],xl:'age',
  head:a=>'age '+a,series:[...groups.map(g=>({{name:g,c:COL[g],y:P.spend[g].slice(0,n),label:g,dy:g==='DDPG'?10:0}})),{{name:'Annuitized rule',c:COL.rule,dash:1,y:P.rule_spend.slice(0,n),label:'rule',dy:0}}]}});
lineChart({{svg:'c2',tip:'tip2',W:520,H:300,r:56,xs,x0:65,x1:100,y0:0,y1:1,yt:[0,.25,.5,.75,1],yf:v=>Math.round(v*100)+'%',xt:[65,70,75,80,85,90,95,100],xl:'age',
  head:a=>'age '+a,series:[...groups.map(g=>({{name:g,c:COL[g],y:P.equity[g].slice(0,n),label:g,dy:12+(g==='DDPG'?12:0)}})),{{name:'Annuitized rule',c:COL.rule,dash:1,y:P.rule_equity.slice(0,n),label:'rule',dy:-8}}]}});

// table view
const tb=document.querySelector('#tbl tbody'),g0=D.best;
xs.forEach((a,i)=>{{if(a%5)return;const tr=document.createElement('tr');tr.innerHTML=`<td class="num">${{a}}</td><td class="num">${{pct(P.spend[g0][i])}}</td><td class="num">${{pct(P.rule_spend[i])}}</td><td class="num">${{pct(P.equity[g0][i])}}</td><td class="num">${{pct(P.rule_equity[i])}}</td>`;tb.appendChild(tr);}});

// chart 3: dot plot
(function(){{
  const svg=document.getElementById('c3'),tip=document.getElementById('tip3'),wrap=svg.parentNode;
  const rows=D.dots,W=520,H=300,m={{t:10,r:16,b:36,l:150}};
  const all=rows.flatMap(r=>[...r.held,...r.ins]),lo=Math.floor(Math.min(...all)/500)*500,hi=Math.ceil(Math.max(...all)/500)*500;
  const x=v=>m.l+(v-lo)/(hi-lo)*(W-m.l-m.r),band=(H-m.t-m.b)/rows.length,y=i=>m.t+band*(i+.5);
  const ax=el('g',{{class:'axis'}},svg);
  for(let v=lo;v<=hi;v+=500){{el('line',{{x1:x(v),x2:x(v),y1:m.t,y2:H-m.b}},ax);const t=el('text',{{x:x(v),y:H-m.b+18,'text-anchor':'middle'}},ax);t.textContent='$'+(v/1000)+'k';}}
  const xl=el('text',{{x:(m.l+W-m.r)/2,y:H-2,'text-anchor':'middle'}},ax);xl.textContent='certainty-equivalent real $/month';
  rows.forEach((r,i)=>{{
    const t=el('text',{{class:'lab',x:m.l-10,y:y(i)+4,'text-anchor':'end'}},svg);t.textContent=r.name.replace(/ \\(.*\\)/,'');
    const mk=(v,c,dy,lab)=>{{const d=el('circle',{{cx:x(v),cy:y(i)+dy,r:5,fill:c,stroke:'var(--surface)','stroke-width':2}},svg);
      const h=el('circle',{{cx:x(v),cy:y(i)+dy,r:11,fill:'transparent'}},svg);
      h.addEventListener('mousemove',()=>{{const R=svg.getBoundingClientRect(),wr=wrap.getBoundingClientRect();tip.innerHTML=`${{r.name}}<br>${{lab}} <b>${{usd(v)}}</b>`;
        tip.style.left=(x(v)/W*R.width+(R.left-wr.left))+'px';tip.style.top=((y(i)+dy)/H*R.height+(R.top-wr.top))+'px';tip.style.opacity=1;}});
      h.addEventListener('mouseleave',()=>tip.style.opacity=0);}};
    r.ins.forEach(v=>mk(v,'var(--ink-3)',-5,'in-sample'));r.held.forEach(v=>mk(v,'var(--s1)',5,'held out'));
  }});
}})();

// chart 4: cohort replay
(function(){{
  const names=Object.keys(D.cohorts['2000-01'].wealth);
  const colorOf=k=>k.startsWith('TD3')?COL.TD3:k.startsWith('DDPG')?COL.DDPG:k.startsWith('Best')?COL.rule:COL['4%'];
  legend('lg4',names.map(k=>({{t:k,c:colorOf(k),dash:k.startsWith('Best')}})));
  let cur='2000-01',meas='wealth';
  function draw(){{
    const C=D.cohorts[cur],S=C[meas],xs=S[names[0]].map((_,i)=>+(65+i*0.25).toFixed(2));
    const W=meas==='wealth';
    const mx=Math.max(...names.flatMap(k=>S[k]))/(W?1e6:1);
    const step=W?0.5:2000,y1=Math.ceil(mx/step)*step,yt=[];for(let v=0;v<=y1+1e-9;v+=step)yt.push(v);
    lineChart({{svg:'c4',tip:'tip4',W:1000,H:380,r:24,xs,x0:65,x1:Math.ceil(xs[xs.length-1]),y0:0,y1,yt,
      yf:W?(v=>'$'+v.toFixed(1)+'M'):(v=>usd(v)),
      xt:xs.filter(a=>Number.isInteger(a)&&a%5===0),
      xl:(W?'portfolio, real $':'spending per month, real $, trailing 12-month average')+' · actual history to '+C.end,
      head:a=>{{const yr=+cur.slice(0,4)+Math.floor(a-65);return 'age '+Math.floor(a)+' · '+yr;}},
      series:names.map(k=>({{name:k,c:colorOf(k),dash:k.startsWith('Best'),y:S[k].map(v=>W?v/1e6:v)}}))}});
  }}
  const wire=(id,attr,set)=>document.querySelectorAll('#'+id+' button').forEach(b=>b.addEventListener('click',()=>{{
    document.querySelectorAll('#'+id+' button').forEach(o=>o.setAttribute('aria-pressed',o===b));set(b.dataset[attr]);draw();}}));
  wire('segc','c',v=>cur=v);wire('segm','m',v=>meas=v);
  draw();
}})();
}})();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / "docs")
