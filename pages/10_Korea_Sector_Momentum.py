from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT / "strategies" / "korea_sector_momentum"
sys.path.insert(0, str(STRATEGY_DIR))

from run import load_monthly_prices  # noqa: E402
from strategy import backtest, metrics  # noqa: E402

st.set_page_config(page_title="Korea Sector Momentum", layout="wide")
st.title("Korea Sector Momentum")
st.caption("10개 섹터 중 연 1회 상위 5개를 선정하고, 월별 모멘텀과 하락 필터로 비중을 조절합니다.")

config = json.loads((STRATEGY_DIR / "config.json").read_text(encoding="utf-8"))

with st.sidebar:
    st.subheader("Backtest settings")
    with st.form("backtest_form"):
        start = st.date_input("Start", value=dt.date(2017, 1, 1))
        end = st.date_input("End", value=dt.date(2026, 8, 31))
        submitted = st.form_submit_button("Run KRX backtest", type="primary")

st.info("기본 룰: 연 1회 12개월 모멘텀으로 5개 섹터 선정, 월별 45/30/15/5/5 배분, 6개월 수익률이 음수인 섹터는 현금화.")

if submitted:
    if start >= end:
        st.error("Start 날짜는 End 날짜보다 빨라야 합니다.")
        st.stop()

    start_text = start.isoformat()
    end_text = end.isoformat()
    with st.spinner("KRX 데이터를 조회하고 백테스트 중입니다..."):
        prices = load_monthly_prices(config["sectors"], start_text, end_text)
        result = backtest(
            prices,
            config["sectors"],
            start_text,
            end_text,
            weights=config["weights"],
            selection_lookback=config["selection_lookback_months"],
            ranking_lookback=config["ranking_lookback_months"],
            downside_lookback=config["downside_lookback_months"],
            transaction_cost=config["transaction_cost"],
        )
    st.session_state["monthly"] = result.monthly
    st.session_state["metrics"] = metrics(result.monthly)

if "metrics" in st.session_state:
    values = st.session_state["metrics"]
    st.subheader("Backtest result")
    cols = st.columns(5)
    cols[0].metric("Cumulative return", f"{values['cumulative_return']:.1%}")
    cols[1].metric("CAGR", f"{values['cagr']:.1%}")
    cols[2].metric("Max drawdown", f"{values['max_drawdown']:.1%}")
    cols[3].metric("Annualized volatility", f"{values['annualized_volatility']:.1%}")
    cols[4].metric("Win rate", f"{values['win_rate']:.1%}")

    monthly = st.session_state["monthly"]
    st.subheader("Recent monthly results")
    st.dataframe(monthly.tail(24), use_container_width=True)
    st.download_button(
        "Download monthly results CSV",
        monthly.to_csv().encode("utf-8-sig"),
        file_name="korea_sector_momentum_monthly.csv",
        mime="text/csv",
    )
else:
    st.warning("왼쪽에서 기간을 확인한 뒤 Run KRX backtest를 눌러주세요.")

with st.expander("Universe and rules", expanded=False):
    st.json(config["sectors"])
    st.write({
        "weights": config["weights"],
        "selection_lookback_months": config["selection_lookback_months"],
        "ranking_lookback_months": config["ranking_lookback_months"],
        "downside_lookback_months": config["downside_lookback_months"],
        "transaction_cost": config["transaction_cost"],
    })
