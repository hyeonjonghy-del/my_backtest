# Korea Sector Momentum V2 — ETF Transition

V2 retains the final momentum, cash, and allocation rules while constructing each
of the 10 sector return series as a **representative-stock-to-ETF transition**.

## Rules

1. From the backtest start through **2022-12**, every sector uses the equal-weight
   monthly return of its two representative stocks. This makes the historical
   stock-proxy period directly comparable with V1.
2. From **2023-01**, each sector changes to its ETF only when that ETF has also
   completed its first full calendar month of trading; otherwise it remains on
   the representative-stock proxy.
3. Returns—not raw price levels—are linked at the transition, preventing a false
   jump caused by different ETF and stock price units.
4. Each January, select the five sectors with the best trailing 12-month momentum.
5. Each month, rank those five sectors plus cash (fixed 0% momentum).
6. Allocate rank slots at **45% / 30% / 15% / 5% / 5%**. Cash receives its
   rank's allocation whenever it ranks in the top five.
7. Apply 0.10% times total absolute weight change as transaction cost.

## ETF transition universe

The actual transition month is the later of the ETF's first return month below
and 2023-01.

| Sector | ETF | First ETF-return month |
| --- | --- | --- |
| 반도체 | TIGER Fn반도체TOP10 (396500) | 2021-09 |
| 전력인프라 | KODEX AI전력핵심설비 (487240) | 2024-08 |
| 방산 | PLUS K방산 (449450) | 2023-02 |
| 조선 | SOL 조선TOP3플러스 (466920) | 2023-11 |
| 바이오 | KODEX 바이오 (244580) | 2016-06 |
| 자동차 | KODEX 자동차 (091180) | 2006-07 |
| 이차전지 | KODEX 2차전지산업 (305720) | 2018-10 |
| 인터넷플랫폼 | TIGER 인터넷TOP10 (365000) | 2020-11 |
| 금융 | KODEX 은행 (091170) | 2006-07 |
| K뷰티 ODM | SOL 화장품TOP3플러스 (0008T0) | 2025-02 |

## Important limitations

- The ETF leg uses closing-price returns. ETF distributions, tax, bid-ask spreads,
  and tracking difference are not fully reflected.
- The proxy leg remains subject to survivorship and representative-stock bias.
- The default start is **2017-01-01**; the loader fetches earlier data only to
  form the required 12-month momentum signal.

## Run

```bash
python run.py --start 2017-01-01 --end 2026-08-31
```

This repository is for research and education, not investment advice.
