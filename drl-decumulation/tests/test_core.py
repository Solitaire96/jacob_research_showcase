import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "data"))

from decumulation import data as D, policies as Pl
from decumulation.env import DecumulationEnv
from decumulation.mortality import median_age_at_death, monthly_survival_curve, sample_death_month
from decumulation.sim import Config, ObsBuilder, simulate, step, objective, action_to_rule, rule_to_action
from decumulation.utility import crra, crra_inverse, certainty_equivalent
from build_dataset import par_bond_return

CFG = Config()


def test_macro_is_lagged_along_the_path_not_the_source():
    df = D.load(D.TRAIN)
    p = D.block_bootstrap(df, 50, 120, seed=3)
    src = df[D.MACRO_COLS].to_numpy()
    assert np.array_equal(p.macro[:, 1:], src[p.month_index[:, :-1]])   # previous simulated month
    assert np.array_equal(p.eq, df["eq_real"].to_numpy()[p.month_index])
    # at every block start, the observed macro is NOT the source month preceding the block
    starts = p.month_index[:, 12::12]
    assert (p.macro[:, 12::12] != src[starts - 1]).any(axis=2).mean() > 0.95


def test_default_observation_is_wealth_and_age_only():
    ob = ObsBuilder(CFG)
    o = ob(np.array([5e5, 2e6]), 24, np.ones((2, 3)), np.array([0.5, -0.5]))
    assert o.shape == (2, 2)
    assert np.allclose(o[:, 1], 2 / 40)


def test_blocks_are_contiguous_and_never_wrap():
    df = D.load(D.TEST)
    p = D.block_bootstrap(df, 200, 480, block=12, seed=4)
    assert p.month_index.min() >= 1 and p.month_index.max() < len(df)
    within = np.diff(p.month_index.reshape(200, 40, 12), axis=2)
    assert (within == 1).all()


def test_par_bond_unchanged_yield_earns_coupon():
    y = np.array([0.03, 0.06, 0.10])
    assert np.allclose(par_bond_return(y, y), y / 12.0, atol=1e-12)
    assert par_bond_return(np.array([0.04]), np.array([0.05]))[0] < 0  # yields up, price down


def test_mortality_median_and_sampler_agree():
    S = monthly_survival_curve(65, 480)
    assert S[0] == 1.0 and np.all(np.diff(S) < 0)
    assert abs(median_age_at_death() - 85.69) < 0.01
    d = sample_death_month(65, 200_000, 480, np.random.default_rng(0))
    assert abs(65 + np.median(d) / 12 - median_age_at_death()) < 0.1


@pytest.mark.parametrize("g", [1.0, 2.0, 4.0, 8.0])
def test_utility_inverse_and_cec_of_constant(g):
    c = np.array([1500.0, 5000.0, 12000.0])
    assert np.allclose(crra_inverse(crra(c, g, 5000), g, 5000), c)
    cons = np.full((10, CFG.n_months), 4321.0)
    assert abs(certainty_equivalent(cons, CFG.weights(), g, 5000) - 4321.0) < 1e-6


def test_step_identity_and_bounds():
    W = np.array([100.0, 1e6]); r_eq = np.array([0.1, -0.2]); r_b = np.array([0.0, 0.01])
    c, nxt, port = step(W, np.array([0.5, 1.5]), np.array([500.0, 1e4]), r_eq, r_b, CFG)
    assert c[0] == CFG.guaranteed_income + 100.0          # cannot withdraw more than wealth
    assert nxt[0] == 0.0
    assert np.isclose(nxt[1], (1e6 - 1e4) * (1 - 0.2))     # equity weight clipped to 1


def test_four_percent_rule_can_deplete_fraction_rules_cannot():
    T = CFG.n_months
    crash = D.Paths(eq=np.full((1, T), -0.01), bond=np.full((1, T), -0.005),
                    macro=np.zeros((1, T, 3)), month_index=np.ones((1, T), dtype=int))
    ob = ObsBuilder(CFG)
    assert simulate(Pl.four_percent(CFG), crash, CFG, ob)["wealth"][0, -1] == 0.0
    assert simulate(Pl.constant_mix(CFG, 0.6, 0.05), crash, CFG, ob)["wealth"][0, -1] > 0.0


def test_env_reproducible_under_seed():
    def roll(seed):
        env = DecumulationEnv(CFG); env.reset(seed=seed)
        return [env.step(np.array([0.2, -0.3]))[1] for _ in range(20)]
    assert roll(5) == roll(5)
    assert roll(5) != roll(6)


def test_env_and_evaluator_agree():
    """Training reward stream and evaluator objective are the same function."""
    env = DecumulationEnv(CFG); env.reset(seed=11)
    a = np.array([0.0, -0.3])
    total, k, done = 0.0, 0, False
    while not done:
        _, r, done, _, _ = env.step(a)
        total += env.learner_gamma ** k * r; k += 1
    assert k == CFG.n_months // CFG.decision_months
    pol = Pl.held_rule_policy(lambda o: action_to_rule(np.repeat(a[None], len(o), 0), CFG), CFG)
    run = simulate(pol, env.paths, CFG, ObsBuilder(CFG))
    assert np.isclose(total / env.reward_scale, objective(run, CFG), rtol=1e-9)


def test_action_mapping_round_trip_and_range():
    e, r = action_to_rule(np.array([[-1, -1], [1, 1], [0.2, 0.0]]), CFG)
    assert np.allclose(e[:2], [0, 1]) and np.allclose(r[:2], [CFG.min_spend_rate, CFG.max_spend_rate])
    assert np.allclose(rule_to_action(e, r, CFG), [[-1, -1], [1, 1], [0.2, 0.0]])


def test_observation_has_no_test_window_scaling():
    mu_train, sd_train = D.macro_moments(D.TRAIN)
    ob = ObsBuilder(CFG, use_macro=True)
    assert np.array_equal(ob.mu, mu_train) and np.array_equal(ob.sd, sd_train)
