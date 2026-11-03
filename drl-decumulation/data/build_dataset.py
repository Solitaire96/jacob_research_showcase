"""Build data/market_monthly.csv from the raw files in data/raw/.

Inputs (all monthly, downloaded 2026-11-02, see data/SOURCES.md):
  F-F_Research_Data_Factors.csv  Mkt-RF and RF, percent (Ken French data library, CRSP)
  GS10.csv      10-year Treasury constant-maturity yield, percent (FRED)
  TB3MS.csv     3-month T-bill secondary-market rate, percent (FRED)
  FEDFUNDS.csv  effective federal funds rate, percent (FRED)
  CPIAUCSL.csv  CPI-U, all items, seasonally adjusted (FRED)

Output columns (one row per month, nominal unless stated):
  eq_nom    US total-market total return (Mkt-RF + RF)
  bond_nom  10-year constant-maturity Treasury total return, par-bond approximation
  infl      CPI month-over-month inflation
  eq_real, bond_real   (1 + r_nom) / (1 + infl) - 1
  fedfunds, term_spread (GS10 - TB3MS), infl_12m (trailing 12-month CPI inflation)
           decimals, observed at the END of the month. The environment lags them
           one month before the agent sees them.

No network access; run from the repo root: python3 data/build_dataset.py
"""
from pathlib import Path

import numpy as np
import pandas as pd

RAW = Path(__file__).resolve().parent / "raw"
OUT = Path(__file__).resolve().parent / "market_monthly.csv"


def _fred(name: str) -> pd.Series:
    df = pd.read_csv(RAW / f"{name}.csv")
    df.columns = ["date", name]
    df["date"] = pd.to_datetime(df["date"]).dt.to_period("M")
    s = pd.to_numeric(df[name], errors="coerce")
    s.index = df["date"]
    return s.dropna()


def _french() -> pd.DataFrame:
    rows = []
    with open(RAW / "F-F_Research_Data_Factors.csv") as fh:
        for line in fh:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 5 and len(parts[0]) == 6 and parts[0].isdigit():
                rows.append(parts)
            elif rows and "Annual" in line:
                break  # monthly block ends where the annual block begins
    df = pd.DataFrame(rows, columns=["ym", "mkt_rf", "smb", "hml", "rf"])
    df.index = pd.PeriodIndex(pd.to_datetime(df["ym"], format="%Y%m"), freq="M")
    out = df[["mkt_rf", "rf"]].astype(float) / 100.0
    return out


def par_bond_return(y_prev: np.ndarray, y_now: np.ndarray, maturity: float = 10.0) -> np.ndarray:
    """One-month total return on a par bond bought at yield y_prev, repriced at y_now
    with maturity shortened by one month. Semi-annual coupons, yields in decimals."""
    c = y_prev                      # coupon equals the purchase yield (par bond)
    n = 2 * (maturity - 1.0 / 12.0)  # remaining semi-annual periods
    yh = y_now / 2.0
    with np.errstate(divide="ignore", invalid="ignore"):
        annuity = np.where(yh > 1e-9, (1 - (1 + yh) ** (-n)) / yh, n)
    price = (c / 2.0) * annuity + (1 + yh) ** (-n)
    return price - 1.0 + y_prev / 12.0


def build() -> pd.DataFrame:
    ff = _french()
    gs10 = _fred("GS10") / 100.0
    tb3 = _fred("TB3MS") / 100.0
    ff_rate = _fred("FEDFUNDS") / 100.0
    cpi = _fred("CPIAUCSL")

    df = pd.DataFrame({
        "eq_nom": ff["mkt_rf"] + ff["rf"],
        "gs10": gs10,
        "tb3": tb3,
        "fedfunds": ff_rate,
        "cpi": cpi,
    }).dropna()

    df["bond_nom"] = par_bond_return(df["gs10"].shift(1).values, df["gs10"].values)
    df["infl"] = df["cpi"].pct_change()
    df["infl_12m"] = df["cpi"].pct_change(12)
    df["term_spread"] = df["gs10"] - df["tb3"]
    df["eq_real"] = (1 + df["eq_nom"]) / (1 + df["infl"]) - 1
    df["bond_real"] = (1 + df["bond_nom"]) / (1 + df["infl"]) - 1
    df = df.dropna()

    cols = ["eq_nom", "bond_nom", "infl", "eq_real", "bond_real",
            "fedfunds", "term_spread", "infl_12m"]
    out = df[cols].copy()
    out.index = out.index.astype(str)
    out.index.name = "month"
    return out


if __name__ == "__main__":
    data = build()
    data.round(8).to_csv(OUT)
    ann = lambda s: (1 + s).prod() ** (12 / len(s)) - 1
    print(f"wrote {OUT.name}: {len(data)} months, {data.index[0]} to {data.index[-1]}")
    for c in ["eq_real", "bond_real"]:
        print(f"  {c}: {ann(data[c]):.2%}/yr geometric, {data[c].std() * np.sqrt(12):.2%} vol")
