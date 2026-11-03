"""Retirement simulator: one step function, used by both the gym environment
(training, one path) and the vectorised evaluator (many paths, all policies on the
same draws).

Timing within month t, all amounts in real (CPI-deflated) dollars:
  1. the retiree observes wealth W_t, age, and macro state from month t-1;
  2. chooses equity weight e_t and a withdrawal d_t (0 <= d_t <= W_t);
  3. consumes c_t = guaranteed income + d_t;
  4. W_{t+1} = (W_t - d_t) * (1 + e_t * r_eq + (1 - e_t) * r_bond).
The objective is  E[ sum_t beta^t S(t) u(c_t) ],  with S(t) the Gompertz survival
probability. Weighting by S(t) is the exact expectation over the death date; it
replaces sampling a death coin every month, which only adds variance.
"""
from dataclasses import dataclass, asdict

import numpy as np

from . import data as D
from .mortality import monthly_survival_curve, sample_death_month
from .utility import crra, certainty_equivalent


@dataclass(frozen=True)
class Config:
    start_age: float = 65.0
    end_age: float = 105.0            # S(105 | 65) = 0.5%; horizon truncated there
    initial_wealth: float = 1_000_000.0
    guaranteed_income: float = 2_000.0  # real $/month, a Social Security-like floor
    gamma: float = 4.0                # CRRA relative risk aversion
    c_ref: float = 5_000.0            # utility normaliser, real $/month
    beta: float = 0.999               # monthly time preference (about 1.2%/yr)
    min_spend_rate: float = 0.01      # agent's withdrawal range, annualised share of wealth
    max_spend_rate: float = 0.60
    decision_months: int = 12         # agent re-decides once a year; returns, withdrawals
                                      # and rebalancing stay monthly inside the year
    block: int = 12                   # bootstrap block length, months

    @property
    def n_months(self) -> int:
        return int(round((self.end_age - self.start_age) * 12))

    def weights(self) -> np.ndarray:
        S = monthly_survival_curve(self.start_age, self.n_months)
        return (self.beta ** np.arange(self.n_months)) * S

    def as_dict(self):
        return asdict(self)


# ---------------------------------------------------------------- observations

class ObsBuilder:
    """Maps simulator state to the agent's observation vector.

    Default: [log wealth / initial, years elapsed / horizon]. With use_macro=True it also
    gets the path-lagged macro state (z-scored with TRAIN moments) and last month's
    portfolio return. Under block bootstrapping with annual decisions those extra
    features carry no information about the coming year (see data.block_bootstrap), so
    they are off by default and kept only for the ablation in RESULTS.md."""

    def __init__(self, cfg: Config, use_macro: bool = False):
        self.cfg = cfg
        self.use_macro = use_macro
        self.dim = 6 if use_macro else 2
        self.mu, self.sd = D.macro_moments(D.TRAIN)

    def __call__(self, wealth, t, macro, last_ret):
        wealth = np.atleast_1d(wealth)
        n = wealth.shape[0]
        out = np.empty((n, self.dim), dtype=np.float32)
        out[:, 0] = np.clip(np.log(np.maximum(wealth, 1.0) / self.cfg.initial_wealth), -8.0, 3.0) / 2.0
        out[:, 1] = (t / 12.0) / (self.cfg.end_age - self.cfg.start_age)
        if self.use_macro:
            out[:, 2:5] = (np.atleast_2d(macro) - self.mu) / self.sd
            out[:, 5] = np.atleast_1d(last_ret) * 10.0
        return out


def action_to_rule(action, cfg: Config):
    """Agent action in [-1, 1]^2 -> (equity weight, annualised spending rate).
    Spending is log-scaled over [min, max] so that typical rates (3-8%) sit mid-range
    rather than pinned against the edge of the actor's tanh."""
    a = np.clip(np.atleast_2d(action), -1.0, 1.0)
    equity = (a[:, 0] + 1.0) / 2.0
    lo, hi = cfg.min_spend_rate, cfg.max_spend_rate
    rate = lo * (hi / lo) ** ((a[:, 1] + 1.0) / 2.0)
    return equity, rate


def rule_to_action(equity, rate, cfg: Config):
    """Inverse of action_to_rule (used in tests and for warm-start diagnostics)."""
    lo, hi = cfg.min_spend_rate, cfg.max_spend_rate
    return np.stack([2 * np.asarray(equity) - 1,
                     2 * np.log(np.asarray(rate) / lo) / np.log(hi / lo) - 1], axis=-1)


def step(wealth, equity, withdrawal, r_eq, r_bond, cfg: Config):
    """Vectorised single month. Returns (consumption, next wealth, portfolio return)."""
    equity = np.clip(equity, 0.0, 1.0)
    withdrawal = np.clip(withdrawal, 0.0, wealth)
    cons = cfg.guaranteed_income + withdrawal
    port = equity * r_eq + (1.0 - equity) * r_bond
    nxt = np.maximum((wealth - withdrawal) * (1.0 + port), 0.0)
    return cons, nxt, port


# ---------------------------------------------------------------- evaluation

def simulate(policy, paths: D.Paths, cfg: Config, obs: ObsBuilder):
    """Run `policy(obs_batch, wealth, t) -> (equity, withdrawal)` on every path.
    Returns dict of [P, T] arrays (wealth has T+1 columns)."""
    P, T = paths.eq.shape
    W = np.full(P, cfg.initial_wealth)
    last = np.zeros(P)
    wealth = np.empty((P, T + 1)); wealth[:, 0] = W
    cons = np.empty((P, T)); eq_w = np.empty((P, T)); wd = np.empty((P, T))
    for t in range(T):
        o = obs(W, t, paths.macro[:, t], last)
        e, d = policy(o, W, t)
        c, W, last = step(W, np.broadcast_to(e, (P,)), np.broadcast_to(d, (P,)),
                          paths.eq[:, t], paths.bond[:, t], cfg)
        cons[:, t] = c; eq_w[:, t] = e; wd[:, t] = np.minimum(d, wealth[:, t]); wealth[:, t + 1] = W
    return {"wealth": wealth, "cons": cons, "equity": eq_w, "withdrawal": wd}


def metrics(run, cfg: Config, death_seed: int = 12345):
    """Headline numbers. CEC uses the exact survival weighting; depletion and
    bequest use one sampled death date per path (common across policies)."""
    w = cfg.weights()
    P, T = run["cons"].shape
    rng = np.random.default_rng(death_seed)
    death = sample_death_month(cfg.start_age, P, T, rng)
    alive = np.arange(T)[None, :] < death[:, None]
    depleted_alive = ((run["wealth"][:, 1:] < 1.0) & alive).any(axis=1)
    bequest = run["wealth"][np.arange(P), np.minimum(death + 1, T)]
    m85 = int((85 - cfg.start_age) * 12)
    c85 = run["cons"][:, m85]
    floor_breach = (run["cons"] < 0.6 * cfg.c_ref) & alive   # below $3,000/mo while alive
    return {
        "cec": certainty_equivalent(run["cons"], w, cfg.gamma, cfg.c_ref),
        "p_depleted_before_death": float(depleted_alive.mean()),
        "p_any_month_below_3000": float(floor_breach.any(axis=1).mean()),
        "cons_age85_p10": float(np.percentile(c85, 10)),
        "cons_age85_p50": float(np.percentile(c85, 50)),
        "bequest_p50": float(np.median(bequest)),
        "first_year_cons_p50": float(np.median(run["cons"][:, :12].mean(axis=1))),
    }


def objective(run, cfg: Config) -> float:
    """Mean weighted utility (what the agent maximises); CEC is its monotone transform."""
    w = cfg.weights()
    return float((crra(run["cons"], cfg.gamma, cfg.c_ref) * w[None, :]).sum(axis=1).mean())
