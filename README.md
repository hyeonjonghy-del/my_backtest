# my_backtest

Streamlit backtest workspace for a simplified strategy set.

## Run

```powershell
pip install -r requirements.txt
streamlit run main.py
```

## Current Decisions

| Decision | Strategy | File | Note |
| --- | --- | --- | --- |
| Keep | KOSPI 200 Momentum v3 | `pages/1_KOSPI_Momentum_v3.py` | Practical KOSPI momentum version |
| Keep | S&P 500 Momentum v3 | `pages/2_SP500_Momentum_v3.py` | Practical S&P 500 momentum version |
| Keep | KODEX 200 Bull/Bear v5 | `pages/3_korea_bull_bear_app_v5.py` | Practical Korea bull/bear version |
| Keep | SPY / UPRO Bull/Bear v3 | `pages/7_us_bull_bear_app_v3.py` | Practical US bull/bear version |
| Keep | QQQ / TQQQ Vol Target | `pages/10_qqq_tqqq_vol_target_app.py` | Nasdaq growth satellite |
| Keep | SOXX / SOXL Vol Target | `pages/9_soxx_soxl_vol_target_app.py` | Semiconductor aggressive satellite |
| Keep | Dividend Screener | `pages/5_dividend_screener.py` | Supporting screener |
| Keep | Chart Doctor Bluechip | `pages/6_chartdoctor_bluechip.py` | Research strategy |

## Deleted From Active Workspace

- `pages/1_KOSPI_Momentum_v2.py`
- `pages/2_SP500_Momentum_v2.py`
- `pages/3_korea_bull_bear_app_v2.py`
- `pages/3_korea_bull_bear_app_v3.py`
- `pages/3_korea_bull_bear_app_v4.py`
- `pages/4_kosdaq150_bull_bear_app_v2.py`
- `pages/4_kosdaq150_bull_bear_app_v3.py`
- `pages/4_kosdaq150_vol_harvest_app_v4.py`
- `pages/7_us_bull_bear_app_v2.py`
- `pages/8_larry_williams_breakout_app.py`
