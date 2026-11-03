"""Frozen monthly market data and block-bootstrap path generation.

Nothing here touches the network: the CSV is built once by data/build_dataset.py.
"""
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

DATA_CSV = Path(__file__).resolve().parent.parent / "data" / "market_monthly.csv"

# Training and held-out windows. The split is by calendar time, not by bootstrap draw,
# so the test window contains regimes (2000-02, 2008, 2020, the 2022 bond crash)
# the agent never sampled during training.
TRAIN = ("1955-07", "1999-12")
TEST = ("2000-01", "2026-09")

MACRO_COLS = ["fedfunds", "term_spread", "infl_12m"]


def load(window=None) -> pd.DataFrame:
    df = pd.read_csv(DATA_CSV, index_col="month")
    if window is not None:
        df = df.loc[window[0]:window[1]]
    return df


@dataclass
class Paths:
    """P simulated retirements of T months each, drawn from one data window.

    eq[p, t], bond[p, t]   real returns realised during month t
    macro[p, t, :]         macro state of the PATH's previous month t-1 (for t = 0, an
                           independently drawn month). Lagging along the simulated path, not
                           along the source history, matters: at a block boundary the
                           source month just before the new block would identify which
                           block of history comes next, and a network can memorise it.
    """
    eq: np.ndarray
    bond: np.ndarray
    macro: np.ndarray
    month_index: np.ndarray  # row of the source window used for month t (for audit)


def block_bootstrap(df: pd.DataFrame, n_paths: int, n_months: int, block: int = 12,
                    seed: int = 0, macro_lag: str = "path") -> Paths:
    """Stationary-in-block bootstrap: each path is a concatenation of contiguous
    `block`-month runs of history with uniformly random start months. Blocks never wrap
    past the end of the window, and start at row >= 1 so the lagged macro row exists."""  # (row >= 1 kept for historical_path parity)
    rng = np.random.default_rng(seed)
    n = len(df)
    if n < block + 1:
        raise ValueError("window shorter than one block")
    n_blocks = -(-n_months // block)
    starts = rng.integers(1, n - block + 1, size=(n_paths, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_paths, -1)[:, :n_months]
    eq = df["eq_real"].to_numpy()[idx]
    bond = df["bond_real"].to_numpy()[idx]
    src = df[MACRO_COLS].to_numpy()
    prev = np.empty_like(idx)
    prev[:, 1:] = idx[:, :-1]
    prev[:, 0] = rng.integers(0, n, size=n_paths)   # independent of the first block
    if macro_lag == "source":
        # THE LEAK, kept only to reproduce it: the source month before each block
        # identifies which block of history follows. Never the default.
        prev = idx - 1
    elif macro_lag != "path":
        raise ValueError(macro_lag)
    return Paths(eq=eq, bond=bond, macro=src[prev], month_index=idx)


def historical_path(start: str, end: str = None) -> Paths:
    """The actual monthly sequence from `start` (no resampling): one path for replaying
    a specific retirement cohort, e.g. retiring into January 2000."""
    df = load()
    pos = df.index.get_loc(start)
    stop = len(df) if end is None else df.index.get_loc(end) + 1
    idx = np.arange(pos, stop)[None, :]
    return Paths(eq=df["eq_real"].to_numpy()[idx], bond=df["bond_real"].to_numpy()[idx],
                 macro=df[MACRO_COLS].to_numpy()[idx - 1], month_index=idx)


def macro_moments(window=TRAIN):
    """Mean and std of the macro columns over the training window, for normalisation.
    Always computed on TRAIN so the test window cannot leak into the scaling."""
    df = load(window)
    return df[MACRO_COLS].mean().to_numpy(), df[MACRO_COLS].std().to_numpy()
