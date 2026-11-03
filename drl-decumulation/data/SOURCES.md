# Data sources

All files in `raw/` were downloaded on 2026-11-02 and are frozen here; `build_dataset.py` turns them into `market_monthly.csv` without network access.

| File | Series | Source |
|---|---|---|
| `F-F_Research_Data_Factors.csv` | Mkt-RF and RF, monthly, percent (CRSP value-weighted US market; 1-month T-bill) | Kenneth R. French Data Library, built from the 202609 CRSP database |
| `GS10.csv` | 10-year Treasury constant-maturity yield, monthly average | FRED (Board of Governors) |
| `TB3MS.csv` | 3-month Treasury bill secondary-market rate, monthly average | FRED |
| `FEDFUNDS.csv` | Effective federal funds rate, monthly average | FRED |
| `CPIAUCSL.csv` | CPI-U, all items, seasonally adjusted | FRED (BLS) |

## Constructed series and their approximations

- **Equity** = Mkt-RF + RF: total return on the US total market, not an investable fund; no fees.
- **Bonds** = one-month return on a 10-year par Treasury bought at last month's yield and repriced at this month's yield (semi-annual coupons). Because GS10 is a monthly *average*, this smooths month-end moves: calendar 2022 comes out at −14.9% against roughly −17% for a month-end 10-year series. No fees, no credit.
- **Real returns** deflate by month-over-month CPI-U.
- **Macro state** (federal funds, term spread GS10 − TB3MS, trailing 12-month CPI inflation) is lagged one month in the environment, so the agent never sees an average that overlaps the month it is investing in.

## Why not SPY, AGG and LTIIT (the April prototype's inputs)

AGG starts in late 2003, so the prototype's "2000–2024" sample was really 2003-10 to 2023-12 (243 months) and contained neither the dot-com crash nor any inflationary decade. LTIIT (long-term TIPS yield) has the same problem. It also pulled from yfinance at import time, so results changed with every run. The series above go back to 1955, which allows a calendar split: train on 1955–1999, test on 2000–2026.
