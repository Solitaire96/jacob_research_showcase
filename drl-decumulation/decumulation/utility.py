"""CRRA utility of real monthly consumption, and its certainty equivalent.

u(c) = ((c / c_ref)^(1-gamma) - 1) / (1 - gamma),   log(c / c_ref) when gamma = 1.

Normalising by c_ref keeps per-step rewards O(1) for the critic. The "- 1" makes
u(c_ref) = 0; over a fixed horizon with fixed weights it shifts every policy's
objective by the same constant, so it changes no ranking.
"""
import numpy as np


def crra(c, gamma, c_ref):
    x = np.maximum(np.asarray(c, dtype=float), 1e-9) / c_ref
    if abs(gamma - 1.0) < 1e-12:
        return np.log(x)
    return (x ** (1.0 - gamma) - 1.0) / (1.0 - gamma)


def crra_inverse(u, gamma, c_ref):
    if abs(gamma - 1.0) < 1e-12:
        return c_ref * np.exp(u)
    return c_ref * (1.0 + (1.0 - gamma) * np.asarray(u)) ** (1.0 / (1.0 - gamma))


def certainty_equivalent(cons, weights, gamma, c_ref):
    """Constant real monthly consumption with the same expected weighted utility.

    cons:    [P, T] consumption paths
    weights: [T]    beta^t * S(t), discount times survival
    """
    eu = (crra(cons, gamma, c_ref) * weights[None, :]).sum(axis=1).mean()
    return float(crra_inverse(eu / weights.sum(), gamma, c_ref))
