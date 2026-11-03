"""Interactive demo on the shipped checkpoints:  streamlit run app.py

Pick an agent, a data window and a retirement month to replay. Everything runs on
the frozen CSV and saved weights; nothing trains and nothing is downloaded.
"""
import numpy as np
import pandas as pd
import streamlit as st

from decumulation import data as D, policies as Pl
from decumulation.sim import Config, ObsBuilder, simulate, metrics
from demo import ROOT, BLUE, ORANGE, AQUA, MUTED, tuned_rules

st.set_page_config(page_title="DRL decumulation", layout="wide")
cfg = Config()
ob = ObsBuilder(cfg)


@st.cache_resource
def agents():
    from stable_baselines3 import DDPG, TD3
    return {c.stem: {"ddpg": DDPG, "td3": TD3}[c.stem.split("_")[0]].load(c)
            for c in sorted((ROOT / "checkpoints").glob("*.zip")) if "_macro" not in c.stem and "_leaky" not in c.stem}


def agent_ob(name):
    return ObsBuilder(cfg, use_macro="_macro" in name)


@st.cache_data
def run_all(agent_name, window_name, n_paths, seed):
    window = D.TEST if window_name.startswith("Held") else D.TRAIN
    paths = D.block_bootstrap(D.load(window), n_paths, cfg.n_months, seed=seed)
    pols = {"Agent": Pl.agent_policy(agents()[agent_name], cfg), **tuned_rules(cfg)}
    rows, eq = {}, None
    for k, p in pols.items():
        r = simulate(p, paths, cfg, agent_ob(agent_name) if k == "Agent" else ob)
        rows[k] = metrics(r, cfg)
        if k == "Agent":
            eq = r["equity"][:, ::12]
    return pd.DataFrame(rows).T, eq


st.title("Retirement decumulation with deep RL")
st.caption("A DDPG agent chooses equity weight and withdrawal each month for a 65-year-old with "
           "$1M and $2,000/month of guaranteed income, maximising survival-weighted CRRA utility "
           "(γ = 4). All amounts are real (CPI-deflated) dollars.")

c1, c2, c3, c4 = st.columns(4)
names = list(agents())
if not names:
    st.error("No checkpoints found. Run train.py first."); st.stop()
from demo import default_agent
_d = default_agent()
agent_name = c1.selectbox("Agent checkpoint", names, index=names.index(_d) if _d in names else 0)
window_name = c2.selectbox("Bootstrap window", ["Held out: 2000–2026", "In-sample: 1955–1999"])
n_paths = c3.select_slider("Lifetimes", [200, 500, 1000, 2000], value=500)
seed = c4.number_input("Seed", 0, 10_000, 202)

table, eq = run_all(agent_name, window_name, n_paths, int(seed))
show = table[["cec", "first_year_cons_p50", "cons_age85_p10", "p_depleted_before_death", "bequest_p50"]].copy()
show.columns = ["Certainty-equiv. $/mo", "1st-year spend $/mo", "Spend at 85, P10", "P(run out)", "Median bequest"]
st.dataframe(show.style.format({"P(run out)": "{:.1%}", **{c: "{:,.0f}" for c in show.columns if c != "P(run out)"}}),
             use_container_width=True)
st.caption("Certainty-equivalent consumption is the constant monthly spending a retiree with this risk "
           "aversion would accept in place of the policy's uncertain stream. Higher is better.")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

left, right = st.columns(2)
with left:
    st.subheader("Learned glide path")
    ages = cfg.start_age + np.arange(eq.shape[1])
    fig, ax = plt.subplots(figsize=(6, 3.4))
    ax.fill_between(ages, np.percentile(eq, 10, 0) * 100, np.percentile(eq, 90, 0) * 100, color=BLUE, alpha=0.15, lw=0)
    ax.plot(ages, np.median(eq, 0) * 100, color=BLUE, lw=2)
    ax.set_ylim(0, 105); ax.set_xlim(65, 100); ax.set_xlabel("Age"); ax.set_ylabel("Equity weight, %")
    ax.spines[["top", "right"]].set_visible(False); ax.grid(color="#e4e3df")
    st.pyplot(fig)
    st.caption("Median and P10–P90 across lifetimes. The spread is the agent reacting to wealth and macro state.")

with right:
    st.subheader("Replay a real retirement")
    months = D.load().index
    choices = [m for m in months[1:-120] if m.endswith("-01")]
    cohort = st.selectbox("Retire at 65 in", choices, index=choices.index("2000-01"))
    hp = D.historical_path(cohort)
    pols = {"Agent": Pl.agent_policy(agents()[agent_name], cfg), **tuned_rules(cfg)}
    fig, ax = plt.subplots(figsize=(6, 3.4))
    for (k, p), col, ls in zip(pols.items(), [BLUE, MUTED, ORANGE, AQUA], ["-", ":", "--", "-."]):
        r = simulate(p, hp, cfg, agent_ob(agent_name) if k == "Agent" else ob)
        ax.plot(65 + np.arange(r["wealth"].shape[1]) / 12, r["wealth"][0] / 1e6, color=col, ls=ls, lw=2, label=k)
    ax.set_xlabel("Age"); ax.set_ylabel("Portfolio, real $M"); ax.legend(frameon=False, fontsize=8)
    ax.spines[["top", "right"]].set_visible(False); ax.grid(color="#e4e3df")
    st.pyplot(fig)
    st.caption("The actual month-by-month history from the chosen start, not a bootstrap draw. "
               "Cohorts starting before 2000 replay data the agent trained on.")
