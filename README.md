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
| Review | Samsung Electronics Trend / Vol v2 | `pages/4_Samsung_Electronics_Trend_Vol.py` | Trend-gated exposure with RV20 reduction and P&L audit |
| Keep | SPY / UPRO Bull/Bear v3 | `pages/4_us_bull_bear_app_v3.py` | Practical US bull/bear version |
| Keep | SOXX / SOXL Vol Target v5 | `pages/5_soxx_soxl_vol_target_app_v5.py` | Semiconductor aggressive satellite |
| Keep | QQQ / TQQQ Vol Target v2 | `pages/6_qqq_tqqq_vol_target_app_v2.py` | Nasdaq growth satellite |
| Keep | Dividend Screener | `pages/7_dividend_screener.py` | Supporting screener |
| Keep | Chart Doctor Bluechip | `pages/8_chartdoctor_bluechip.py` | Research strategy |
| Keep | QQQ / GLD / SGOV Momentum v2 | `pages/9_QQQ_Gold_Momentum_v2.py` | SGOV rank-based defensive allocation |

## Deleted From Active Workspace

- `pages/1_KOSPI_Momentum_v2.py`
- `pages/2_SP500_Momentum_v2.py`
- `pages/3_korea_bull_bear_app_v0.py`
- `pages/3_korea_bull_bear_app_v2.py`
- `pages/3_korea_bull_bear_app_v3.py`
- `pages/5_soxx_soxl_vol_target_app.py`
- `pages/5_soxx_soxl_vol_target_app_v2.py`
- `pages/5_soxx_soxl_vol_target_app_v3.py`
- `pages/5_soxx_soxl_vol_target_app_v4.py`
- `pages/5_soxx_soxl_vol_target_app_v6.py`
- `pages/6_qqq_tqqq_vol_target_app.py`
- `pages/4_kosdaq150_bull_bear_app_v2.py`
- `pages/4_kosdaq150_bull_bear_app_v3.py`
- `pages/4_kosdaq150_vol_harvest_app_v4.py`
- `pages/7_us_bull_bear_app_v2.py`
- `pages/8_larry_williams_breakout_app.py`
