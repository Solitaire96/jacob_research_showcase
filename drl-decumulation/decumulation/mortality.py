"""Gompertz mortality.

Survival from age x to age x + t is  S = exp( e^{(x-m)/b} (1 - e^{t/b}) )
with modal age at death m and dispersion b. The defaults m = 88, b = 10 are the
unisex values Milevsky uses for retirement-income work; they give a median age at
death of about 85.7 for someone alive at 65. They are an illustrative law, not an
SSA or IRS table.
"""
import numpy as np

M_MODAL = 88.0
B_DISP = 10.0


def survival(age_from, t_years, m=M_MODAL, b=B_DISP):
    """P(alive at age_from + t | alive at age_from)."""
    return np.exp(np.exp((np.asarray(age_from) - m) / b) * (1.0 - np.exp(np.asarray(t_years) / b)))


def monthly_survival_curve(start_age, n_months, m=M_MODAL, b=B_DISP):
    """S[t] = P(alive at the start of month t | alive at start_age), t = 0..n_months-1."""
    t = np.arange(n_months) / 12.0
    return survival(start_age, t, m, b)


def median_age_at_death(start_age=65.0, m=M_MODAL, b=B_DISP):
    """Closed form: solve S = 1/2."""
    return start_age + b * np.log(1.0 - np.log(0.5) / np.exp((start_age - m) / b))


def sample_death_month(start_age, n_paths, n_months, rng, m=M_MODAL, b=B_DISP):
    """Month index in which each path dies (n_months if it survives the horizon).
    Inverse-CDF sampling from the same survival curve the reward uses."""
    S = monthly_survival_curve(start_age, n_months + 1, m, b)
    u = rng.random(n_paths)
    # first month t with S[t+1] < u: alive at start of t, dead by start of t+1
    return np.minimum(np.searchsorted(-S[1:], -u, side="right"), n_months)
