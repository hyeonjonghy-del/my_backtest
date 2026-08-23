from __future__ import annotations

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
    start = st.date_input("Start", value=None)
    end = st.date_input("End", value=None)
    run = st.button("Run KRX backtest", type="primary")

st.info("기본 룰: 연 1회 12개월 모멘텀으로 5개 섹터 선정, 월별 45/30/15/5/5 배분, 6개월 수익률이 음수인 섹터는 현금화.")

st.subheader("Universe")
st.json(config["sectors"])

if run:
    start_text = start.isoformat() if start else "2017-01-01"
    end_text = end.isoformat() if end else "2026-08-31"
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
    st.subheader("Metrics")
    st.json(metrics(result.monthly))
    st.subheader("Monthly results")
    st.dataframe(result.monthly, use_container_width=True)
