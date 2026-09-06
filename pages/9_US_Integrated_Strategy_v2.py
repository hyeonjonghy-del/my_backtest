"""Streamlit page for integrated US Strategy 9 v2."""

from datetime import date

import streamlit as st

from integrated_us_backtest import download, run


st.set_page_config(page_title="US Integrated Strategy 9 v2", page_icon="US", layout="wide")
st.title("US Integrated Strategy 9 v2")
st.caption("Choose a fixed or 12-month momentum growth mix, with GLD and SGOV momentum allocation.")

growth_mode_label = st.radio(
    "Growth sleeve allocation",
    ("Fixed 50:50", "12-month momentum 70:30"),
    horizontal=True,
    help=(
        "Momentum mode compares the trailing 12-month performance of the SOXX/SOXL and "
        "QQQ/TQQQ sleeves. The stronger sleeve receives 70%; ties and the warm-up period remain 50:50."
    ),
)
growth_allocation_mode = {
    "Fixed 50:50": "fixed_50_50",
    "12-month momentum 70:30": "momentum_70_30",
}[growth_mode_label]

with st.expander("Allocation rules", expanded=True):
    st.markdown(
        """
        - Growth sleeve: choose **fixed SOXX/SOXL 50% + QQQ/TQQQ 50%** or
          **12-month momentum winner 70% + runner-up 30%**
        - Momentum ties and the initial 12-month warm-up use **50% / 50%**
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
    with st.spinner("Downloading strategy data (BIL is used before SGOV history begins)..."):
        try:
            prices = download(str(start), str(end))
            nav, summary = run(prices, cost_bps=cost_bps,
                               sgov_rank2_weight=sgov_rank2,
                               sgov_rank1_weight=sgov_rank1,
                               growth_allocation_mode=growth_allocation_mode,
                               evaluation_start=str(start))
        except Exception as exc:
            st.error(f"Data download or backtest failed: {exc}")
            st.info("If the server cannot reach Yahoo Finance, retry from a network that allows the connection.")
            st.stop()

    st.success(
        f"Completed for {nav.index[0].date()} to {nav.index[-1].date()} "
        f"with {growth_mode_label}."
    )
    st.caption(
        "Earlier observations are used only to prepare moving averages and 12-month momentum. "
        "Before SGOV launched, BIL returns are used as the cash proxy."
    )
    st.subheader("Performance summary")
    percent_columns = {
        "CAGR", "MDD", "Volatility", "Average growth weight", "Average SOXL weight",
        "Average TQQQ weight", "Average SGOV weight", "Average SOXX-family share of growth",
    }
    formats = {column: "{:.2%}" for column in percent_columns}
    formats.update({"Sharpe": "{:.2f}", "Final NAV": "{:.3f}"})
    st.dataframe(summary.style.format(formats, na_rep="-"), use_container_width=True)
    st.subheader("NAV comparison")
    st.line_chart(nav)
    st.download_button("Download NAV CSV", nav.to_csv().encode("utf-8-sig"), "us_integrated_strategy_v2_nav.csv", "text/csv")
    st.download_button("Download summary CSV", summary.to_csv().encode("utf-8-sig"), "us_integrated_strategy_v2_summary.csv", "text/csv")
