# QQQ Leverage Glidepath

This strategy treats QQQ, QLD, and TQQQ as a single Nasdaq-100 exposure ladder,
not as three diversified assets. A Treasury-bill sleeve reduces exposure during
corrections and downtrends. BIL is used before SGOV's listing history; SGOV is
used when available.

## Default allocation

| Regime | QQQ | QLD | TQQQ | SGOV sleeve | Effective exposure |
| --- | ---: | ---: | ---: | ---: | ---: |
| Bear | 30% | 0% | 0% | 70% | 0.30x |
| Caution | 35% | 15% | 0% | 50% | 0.65x |
| Turn 1 | 35% | 15% | 0% | 50% | 0.65x |
| Turn 2 | 35% | 25% | 10% | 30% | 1.15x |
| Bull | 33.3% | 33.3% | 33.3% | 0% | 2.00x |

## Close-confirmed regimes

- **Bear:** QQQ is below both its 50-day and 150-day averages.
- **Turn 1:** QQQ is above its 50-day average but no higher than its 150-day
  average, and the 50-day average has not risen over the last 20 sessions.
- **Turn 2:** QQQ is above its 50-day average but no higher than its 150-day
  average, and the 50-day average is higher than it was 20 sessions earlier.
- **Caution:** QQQ is above its 150-day average but is at least 7.5% below its
  rolling 63-session high.
- **Bull:** QQQ is above its 150-day average and is less than 7.5% below its
  rolling 63-session high.

The order above is implemented explicitly; every valid trading day belongs to
exactly one regime.

## Execution convention

1. Calculate the regime only after the regular-session QQQ close is complete.
2. Keep the old allocation through the next overnight gap.
3. Execute a changed target at the next trading day's open.
4. Rebalance unchanged targets at the first trading-day open of each month.
5. Charge configurable costs on gross traded notional. A full A-to-B switch has
   gross turnover of 2.0 because both the sale and purchase are counted.

This convention prevents a close-signal/look-ahead error. The Streamlit page
also reports transitions occurring within five and ten trading days so that
whipsaw remains visible.

## Run

```powershell
pip install -r requirements.txt
streamlit run main.py
```

Open `pages/10_QQQ_Leverage_Glidepath.py` from the Streamlit Pages menu.

## API

```python
from strategies.qqq_leverage_glidepath import (
    GlidepathConfig,
    backtest_next_open,
    latest_target,
)
```

`backtest_next_open` accepts a mapping of QQQ, QLD, TQQQ, BIL, and SGOV OHLC
frames. Each frame must contain `open`, `close`, and `adjclose` columns.

## Important interpretation

The defensive sleeve protects only after a signal can be executed. A gap before
the next open is borne by the old allocation. QLD and TQQQ also target daily,
not long-horizon, multiples of the Nasdaq-100.
