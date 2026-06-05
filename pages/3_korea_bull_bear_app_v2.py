"""KODEX 200 / KODEX Leverage tiered RV strategy v2.

This page is based on 3_korea_bull_bear_app_v1.py and applies the v2
allocation rules at runtime:
- Trend pass and RV < low tier: KODEX Leverage 100%.
- Trend pass and low tier <= RV < middle split: KODEX Leverage + KODEX 200.
- Trend pass and middle split <= RV < high tier: KODEX 200 + cash.
- Trend fail or RV >= high tier: cash 100%.

Default tiers are intentionally closer to the observed KODEX 200 RV20 range:
25%, 35%, and 45%.
"""

from __future__ import annotations

from pathlib import Path


def replace_once(source: str, old: str, new: str, label: str) -> str:
    if old not in source:
        raise RuntimeError(f"Could not apply v2 patch: {label}")
    return source.replace(old, new, 1)


def replace_block(source: str, start_marker: str, end_marker: str, replacement: str, label: str) -> str:
    start = source.find(start_marker)
    if start < 0:
        raise RuntimeError(f"Could not apply v2 patch: {label} start")
    end = source.find(end_marker, start)
    if end < 0:
        raise RuntimeError(f"Could not apply v2 patch: {label} end")
    return source[:start] + replacement + source[end:]


source_path = Path(__file__).with_name("3_korea_bull_bear_app_v1.py")
source = source_path.read_text(encoding="utf-8")

source = replace_once(
    source,
    '"""KODEX 200 / KODEX Leverage ON/OFF strategy v1.\n\nv1 keeps the v0 core idea and adds a high-volatility bull fallback:\n- Signal asset: KODEX 200.\n- Main trading asset: KODEX Leverage.\n- Hold KODEX Leverage when trend and volatility filters pass.\n- Optionally hold KODEX 200 + cash when trend passes but RV is above the cap.\n- Hold cash when the trend filter fails.\n"""',
    '"""KODEX 200 / KODEX Leverage tiered RV strategy v2.\n\nv2 keeps the v1 trend filter and replaces the single RV fallback with\nfour volatility tiers:\n- Signal asset: KODEX 200.\n- Hold all KODEX Leverage when trend passes and RV is low.\n- Blend KODEX Leverage + KODEX 200 in the lower middle RV tier.\n- Blend KODEX 200 + cash in the upper middle RV tier.\n- Hold cash when the trend filter fails.\n"""',
    "module docstring",
)

source = replace_once(
    source,
    'st.set_page_config(page_title="KODEX ON/OFF v1", page_icon="KR", layout="wide")\nst.title("KODEX 200 / Leverage ON-OFF Strategy v1")\nst.caption(\n    "v1 adds a high-volatility bull fallback: when trend passes but RV exceeds the cap, "\n    "the strategy can hold KODEX 200 plus cash instead of moving fully to cash."\n)',
    'st.set_page_config(page_title="KODEX ON/OFF v2", page_icon="KR", layout="wide")\nst.title("KODEX 200 / Leverage Tiered RV Strategy v2")\nst.caption(\n    "v2 uses KODEX 200 trend plus RV tiers: all leverage, leverage + KODEX 200, "\n    "KODEX 200 + cash, then all cash. Defaults: 25%, 35%, 45%."\n)',
    "page title",
)

source = replace_block(
    source,
    "def build_target_weights(",
    "\n\n\ndef backtest_next_open(",
    '''def build_target_weights(
    dates: pd.DatetimeIndex,
    leverage_signal: pd.Series,
    trend_signal: pd.Series,
    realized_vol: pd.Series,
    low_rv_threshold: float,
    high_rv_threshold: float,
    mid_lev_weight: float,
    mid_kodex_weight: float,
) -> pd.DataFrame:
    leverage_signal = leverage_signal.reindex(dates).fillna(False)
    trend_signal = trend_signal.reindex(dates).fillna(False)
    realized_vol = realized_vol.reindex(dates)
    mid_rv_threshold = (low_rv_threshold + high_rv_threshold) / 2

    all_leverage = trend_signal & leverage_signal
    lev_kodex_mix = trend_signal & (realized_vol >= low_rv_threshold) & (realized_vol < mid_rv_threshold)
    kodex_cash_mix = trend_signal & (realized_vol >= mid_rv_threshold) & (realized_vol < high_rv_threshold)

    lev_weight = all_leverage.astype(float)
    lev_weight += lev_kodex_mix.astype(float) * mid_lev_weight

    kodex_weight = lev_kodex_mix.astype(float) * (1.0 - mid_lev_weight)
    kodex_weight += kodex_cash_mix.astype(float) * mid_kodex_weight

    cash_weight = (1.0 - lev_weight - kodex_weight).clip(lower=0.0)
    return pd.DataFrame(
        {
            "KODEX Leverage": lev_weight.clip(0.0, 1.0),
            "KODEX 200": kodex_weight.clip(0.0, 1.0),
            "Cash": cash_weight.clip(0.0, 1.0),
        },
        index=dates,
    )''',
    "target weights",
)

source = replace_once(
    source,
    '''    vol_threshold_pct = st.slider("Realized volatility cap (%)", 10, 120, 50, 5)
    vol_source = st.selectbox("Volatility source", ["KODEX 200", "KODEX Leverage"], index=0)
    use_high_vol_fallback = st.checkbox("Use RV cap fallback", value=True)
    high_vol_kodex_weight_pct = st.slider("KODEX 200 weight when RV cap fails (%)", 0, 100, 50, 5)

    st.subheader("Position / Cost")
    leverage_weight_pct = st.slider("KODEX Leverage weight when signal passes (%)", 0, 100, 100, 5)''',
    '''    vol_source = st.selectbox("Volatility source", ["KODEX 200", "KODEX Leverage"], index=0)
    low_rv_threshold_pct = st.slider("All-leverage RV ceiling (%)", 10, 100, 25, 5)
    high_rv_threshold_pct = st.slider("All-cash RV floor (%)", 20, 120, 45, 5)
    mid_rv_threshold_pct = (low_rv_threshold_pct + high_rv_threshold_pct) / 2
    if low_rv_threshold_pct >= high_rv_threshold_pct:
        st.error("All-leverage RV ceiling must be lower than all-cash RV floor.")
    st.caption(f"Middle tiers split automatically at RV {mid_rv_threshold_pct:.1f}%.")

    st.subheader("Position / Cost")
    mid_lev_weight_pct = st.slider("Leverage weight in lower-middle RV tier (%)", 0, 100, 50, 5)
    mid_kodex_weight_pct = st.slider("KODEX 200 weight in upper-middle RV tier (%)", 0, 100, 50, 5)''',
    "sidebar RV controls",
)

source = replace_once(
    source,
    '''vol_threshold = vol_threshold_pct / 100
leverage_weight = leverage_weight_pct / 100
high_vol_kodex_weight = high_vol_kodex_weight_pct / 100''',
    '''low_rv_threshold = low_rv_threshold_pct / 100
high_rv_threshold = high_rv_threshold_pct / 100
mid_rv_threshold = (low_rv_threshold + high_rv_threshold) / 2
mid_lev_weight = mid_lev_weight_pct / 100
mid_kodex_weight = mid_kodex_weight_pct / 100''',
    "threshold variables",
)

source = replace_once(
    source,
    '''| Trading asset | KODEX Leverage |
| Entry / hold | KODEX 200 close > MA{ma_window} AND {vol_source} RV{vol_window} < {vol_threshold_pct}% |
| High-vol bull fallback | {'On' if use_high_vol_fallback else 'Off'}; if trend passes but RV cap fails, hold KODEX 200 {high_vol_kodex_weight_pct}%, cash {100 - high_vol_kodex_weight_pct}% |
| Exit | Trend filter fails |
| Execution | {execution_model} |
| After-close fill assumption | {after_close_fill_pct}% of required trade at same-day close; residual at next open |
| Main position | KODEX Leverage {leverage_weight_pct}%, cash {100 - leverage_weight_pct}% |''',
    '''| Trend filter | KODEX 200 close > MA{ma_window} |
| RV source | {vol_source} RV{vol_window} |
| Tier 1 | Trend pass and RV < {low_rv_threshold_pct}%: KODEX Leverage 100% |
| Tier 2 | Trend pass and {low_rv_threshold_pct}% <= RV < {mid_rv_threshold_pct:.1f}%: KODEX Leverage {mid_lev_weight_pct}%, KODEX 200 {100 - mid_lev_weight_pct}% |
| Tier 3 | Trend pass and {mid_rv_threshold_pct:.1f}% <= RV < {high_rv_threshold_pct}%: KODEX 200 {mid_kodex_weight_pct}%, cash {100 - mid_kodex_weight_pct}% |
| Tier 4 | Trend fail or RV >= {high_rv_threshold_pct}%: cash 100% |
| Execution | {execution_model} |
| After-close fill assumption | {after_close_fill_pct}% of required trade at same-day close; residual at next open |''',
    "rules table",
)

source = replace_once(source, 'st.info("Adjust the settings, then run the backtest. v1 adds a configurable RV cap fallback.")', 'st.info("Adjust the settings, then run the backtest. v2 uses four RV-based allocation tiers.")', "start info")
source = replace_once(source, '''if start_date >= end_date:
    st.error("Start date must be earlier than end date.")
    st.stop()''', '''if start_date >= end_date:
    st.error("Start date must be earlier than end date.")
    st.stop()

if low_rv_threshold >= high_rv_threshold:
    st.error("All-leverage RV ceiling must be lower than all-cash RV floor.")
    st.stop()''', "threshold validation")
source = replace_once(source, 'signal, trend_signal, ma, realized_vol = build_signal(kodex_close, ma_window, vol_price, vol_window, vol_threshold)', 'signal, trend_signal, ma, realized_vol = build_signal(kodex_close, ma_window, vol_price, vol_window, low_rv_threshold)', "main signal call")
source = replace_once(source, '''    leverage_weight,
    use_high_vol_fallback,
    high_vol_kodex_weight,
    vol_threshold,''', '''    low_rv_threshold,
    high_rv_threshold,
    mid_lev_weight,
    mid_kodex_weight,''', "main target args")
source = replace_once(source, '''    f"Latest leverage signal: {'Pass' if latest_signal else 'Wait'} | "
    f"Trend: {'Pass' if latest_trend_signal else 'Wait'} | "
    f"KODEX 200 {latest_close:,.0f} / MA{ma_window} {latest_ma:,.0f} / "
    f"{vol_source} RV{vol_window} {latest_vol:.1%} / cap {vol_threshold:.0%}"''', '''    f"Latest all-leverage tier: {'Pass' if latest_signal else 'Wait'} | "
    f"Trend: {'Pass' if latest_trend_signal else 'Wait'} | "
    f"KODEX 200 {latest_close:,.0f} / MA{ma_window} {latest_ma:,.0f} / "
    f"{vol_source} RV{vol_window} {latest_vol:.1%} / tiers {low_rv_threshold:.0%}, {mid_rv_threshold:.0%}, {high_rv_threshold:.0%}"''', "status caption")

source = replace_block(
    source,
    '        threshold_values = sorted(set([45, 50, 55, vol_threshold_pct]))',
    '        sensitivity = pd.DataFrame(records).sort_values(["Calmar", "CAGR"], ascending=False)',
    '''        low_threshold_values = sorted(set([20, 25, 30, low_rv_threshold_pct]))
        high_threshold_values = sorted(set([40, 45, 50, high_rv_threshold_pct]))
        records = []
        for ma_w in ma_values:
            for vol_w in vol_values:
                for low_threshold_pct in low_threshold_values:
                    for high_threshold_pct in high_threshold_values:
                        if low_threshold_pct >= high_threshold_pct:
                            continue
                        sig, trend_sig, _, test_rv = build_signal(kodex_close, ma_w, vol_price, vol_w, low_threshold_pct / 100)
                        test_targets = build_target_weights(
                            common_idx,
                            sig,
                            trend_sig,
                            test_rv,
                            low_threshold_pct / 100,
                            high_threshold_pct / 100,
                            mid_lev_weight,
                            mid_kodex_weight,
                        )
                        if execution_model == "Ideal same-close":
                            test_nav, test_weight, test_trades = backtest_portfolio_same_close(common_idx, test_targets, ret_cc, fee)
                        elif execution_model == "After-close fill + next-open residual":
                            test_nav, test_weight, test_trades = backtest_portfolio_after_close_fill(common_idx, test_targets, ret_co, ret_oc, fee, after_close_fill_pct / 100)
                        else:
                            test_nav, test_weight, test_trades = backtest_portfolio_next_open(common_idx, test_targets, ret_co, ret_oc, fee)
                        m = calc_metrics(test_nav)
                        records.append(
                            {
                                "MA": ma_w,
                                "RV Window": vol_w,
                                "Low RV": low_threshold_pct / 100,
                                "Mid RV": ((low_threshold_pct + high_threshold_pct) / 2) / 100,
                                "High RV": high_threshold_pct / 100,
                                "Execution": execution_model,
                                "CAGR": m["cagr"],
                                "MDD": m["mdd"],
                                "Calmar": m["calmar"],
                                "Sharpe": m["sharpe"],
                                "Total": m["total"],
                                "Exposure": (test_weight.drop(columns=["Cash"], errors="ignore").sum(axis=1) > 0).mean(),
                                "Trades": len(test_trades),
                            }
                        )
''',
    "sensitivity block",
)
source = source.replace('["RV Cap", "CAGR", "MDD", "Total", "Exposure"]', '["Low RV", "Mid RV", "High RV", "CAGR", "MDD", "Total", "Exposure"]')
source = replace_once(source, '''            f"{vol_source} RV{vol_window}": realized_vol.reindex(common_idx) * 100,
            "Vol Cap": pd.Series(vol_threshold_pct, index=common_idx),''', '''            f"{vol_source} RV{vol_window}": realized_vol.reindex(common_idx) * 100,
            "Low RV Tier": pd.Series(low_rv_threshold_pct, index=common_idx),
            "Mid RV Split": pd.Series(mid_rv_threshold_pct, index=common_idx),
            "High RV Tier": pd.Series(high_rv_threshold_pct, index=common_idx),''', "vol chart")
source = source.replace('"kodex_onoff_v1_trades.csv"', '"kodex_onoff_v2_trades.csv"')
source = source.replace('"kodex_onoff_v1_monthly.csv"', '"kodex_onoff_v2_monthly.csv"')

exec_globals = {"__name__": "__main__", "__file__": str(source_path)}
exec(compile(source, str(source_path) + "::v2", "exec"), exec_globals)
