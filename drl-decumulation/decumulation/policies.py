"""Benchmark rules. Each returns a policy callable(obs, wealth, t) -> (equity, withdrawal).

The two parameterised families are tuned by grid search on TRAIN-window paths only,
then frozen; the agent has to beat the best member of each on held-out data.
"""
import itertools

import numpy as np

from . import data as D
from .mortality import monthly_survival_curve
from .sim import Config, ObsBuilder, simulate, objective


def four_percent(cfg: Config, equity=0.6, rate=0.04):
    """Bengen: fixed real withdrawal of 4% of initial wealth per year, 60/40, rebalanced
    monthly. The only benchmark that can run the portfolio to zero."""
    d = cfg.initial_wealth * rate / 12.0
    return lambda o, W, t: (equity, np.full_like(W, d))


def constant_mix(cfg: Config, equity, rate):
    """Fixed equity weight, withdraw a fixed fraction of current wealth."""
    return lambda o, W, t: (equity, W * rate / 12.0)


def annuity_factors(cfg: Config, real_rate):
    """a[t] = sum_{k>=0} S(t+k | alive at t) (1+r)^(-k/12), in months: the expected
    discounted number of remaining monthly payments. Withdrawing W/a[t] is the
    actuarially fair, mortality-aware spending rule (Merton/Yaari-style)."""
    T = cfg.n_months
    S = monthly_survival_curve(cfg.start_age, T)
    v = (1.0 + real_rate) ** (-np.arange(T) / 12.0)
    a = np.empty(T)
    for t in range(T):
        a[t] = (S[t:] / S[t] * v[: T - t]).sum()
    return a


def annuitized(cfg: Config, equity, real_rate):
    """Fixed equity weight, withdraw W / annuity_factor(age): spending rate rises with age."""
    a = annuity_factors(cfg, real_rate)
    return lambda o, W, t: (equity, W / a[t])


def age_in_bonds(cfg: Config):
    """Rule of thumb: equity = (110 - age)%, with annuitised spending at a 2% real rate."""
    a = annuity_factors(cfg, 0.02)
    return lambda o, W, t: (np.clip((110.0 - (cfg.start_age + t / 12.0)) / 100.0, 0, 1), W / a[t])


GRID_EQUITY = np.round(np.arange(0.0, 1.0001, 0.1), 2)
GRID_RATE = np.round(np.arange(0.02, 0.1001, 0.005), 3)
GRID_REAL_RATE = [-0.01, 0.0, 0.01, 0.02, 0.03, 0.04]


def tune(cfg: Config, paths: D.Paths, obs: ObsBuilder):
    """Grid-search both families on the given (training) paths. Returns best params."""
    best_cm = max(itertools.product(GRID_EQUITY, GRID_RATE),
                  key=lambda p: objective(simulate(constant_mix(cfg, *p), paths, cfg, obs), cfg))
    best_an = max(itertools.product(GRID_EQUITY, GRID_REAL_RATE),
                  key=lambda p: objective(simulate(annuitized(cfg, *p), paths, cfg, obs), cfg))
    return {"constant_mix": {"equity": float(best_cm[0]), "rate": float(best_cm[1])},
            "annuitized": {"equity": float(best_an[0]), "real_rate": float(best_an[1])}}


def held_rule_policy(decide, cfg: Config):
    """Policy that calls decide(obs) -> (equity, annual rate) at each decision month and
    holds that rule for the year: same equity weight (rebalanced monthly) and a monthly
    withdrawal of rate/12 of current wealth. Exactly what the training env does."""
    state = {}

    def pol(o, W, t):
        if t % cfg.decision_months == 0 or "e" not in state:
            state["e"], state["r"] = decide(o)
        return state["e"], W * state["r"] / 12.0
    return pol


def agent_policy(model, cfg: Config):
    """Wrap a trained SB3 model as a vectorised policy (deterministic actions)."""
    from .sim import action_to_rule
    return held_rule_policy(lambda o: action_to_rule(model.predict(o, deterministic=True)[0], cfg), cfg)
