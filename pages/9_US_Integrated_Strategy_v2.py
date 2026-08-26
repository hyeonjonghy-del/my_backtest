"""Streamlit page for integrated US Strategy 9 v2."""

from datetime import date

import streamlit as st

from integrated_us_backtest import download, run


st.set_page_config(page_title="US Integrated Strategy 9 v2", page_icon="US", layout="wide")
st.title("US Integrated Strategy 9 v2")
st.caption("SOXX/SOXL 50% + QQQ/TQQQ 50%, with GLD and SGOV momentum allocation.")

with st.expander("Allocation rules", expanded=True):
    st.markdown(
        """
        - Growth sleeve: **SOXX/SOXL 50% + QQQ/TQQQ 50%**
        - Growth selected: Growth / GLD / SGOV = **80 / 20 / 0**
        - SGOV ranks 2nd: Growth / GLD / SGOV = **50 / 20 / 30**
        - SGOV ranks 1st: Growth / GLD / SGOV = **30 / 20 / 50**
        - GLD selected: Growth / GLD / SGOV = **20 / 80 / 0**
        """
    )

col1, col2, col3 = st.columns(3)
start = col1.date_input("Start date", value=date(2010, 1, 1))
end = col2.date_input("End date", value=date.today())
cost_bps = col3.number_input("One-way cost (bps)", min_value=0.0, value=10.0, step=1.0)

sgov_rank2 = st.slider("SGOV weight when ranked 2nd (%)", 0, 80, 30, 5) / 100
sgov_rank1 = st.slider("SGOV weight when ranked 1st (%)", 0, 80, 50, 5) / 100
if sgov_rank1 < sgov_rank2:
    st.error("SGOV rank-1 weight must be at least the rank-2 weight.")
    st.stop()
st.caption(f"Growth sleeve becomes {80 - sgov_rank2 * 100:.0f}% when SGOV is 2nd and {80 - sgov_rank1 * 100:.0f}% when SGOV is 1st.")

if start >= end:
    st.error("Start date must be before end date.")
    st.stop()

if st.button("Run integrated backtest", type="primary"):
    with st.spinner("Downloading QQQ, GLD, SOXX, SOXL, TQQQ, and SGOV data..."):
        try:
            prices = download(str(start), str(end))
            nav, summary = run(prices, cost_bps=cost_bps,
                               sgov_rank2_weight=sgov_rank2,
                               sgov_rank1_weight=sgov_rank1)
        except Exception as exc:
            st.error(f"Data download or backtest failed: {exc}")
            st.info("If the server cannot reach Yahoo Finance, retry from a network that allows the connection.")
            st.stop()

    st.success(f"Completed using {prices.index[0].date()} to {prices.index[-1].date()}.")
    st.subheader("Performance summary")
    st.dataframe(summary.style.format({"CAGR": "{:.2%}", "MDD": "{:.2%}", "Volatility": "{:.2%}", "Sharpe": "{:.2f}", "Final NAV": "{:.3f}"}), use_container_width=True)
    st.subheader("NAV comparison")
    st.line_chart(nav)
    st.download_button("Download NAV CSV", nav.to_csv().encode("utf-8-sig"), "us_integrated_strategy_v2_nav.csv", "text/csv")
    st.download_button("Download summary CSV", summary.to_csv().encode("utf-8-sig"), "us_integrated_strategy_v2_summary.csv", "text/csv")

