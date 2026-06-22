"""QLD / TQQQ strategy with QQQ bear sleeve.

QLD is used as the base trend and volatility signal. Bull regimes allocate to
QLD and TQQQ, while bear regimes use QQQ as the defensive equity sleeve instead
of holding QLD. TQQQ is modeled as 1.5x QLD-equivalent risk because QLD is 2x
and TQQQ is 3x.
"""

from __future__ import annotations

from pathlib import Path
import re


SOURCE_PAGE = Path(__file__).with_name("5_soxx_soxl_vol_target_app_v5.py")


def replace_between(source: str, start: str, end: str, replacement: str) -> str:
    start_idx = source.index(start)
    end_idx = source.index(end, start_idx)
    return source[:start_idx] + replacement.rstrip() + "\n\n" + source[end_idx:]


code = SOURCE_PAGE.read_text(encoding="utf-8")

code = code.replace(
    '"""SOXX / SOXL trend and volatility-target backtest v5."""',
    '"""USD / SOXL trend with SOXX bear sleeve backtest v1."""',
)
code = code.replace(
    'SOXX = "SOXX"\nSOXL = "SOXL"',
    'SOXX = "SOXX"\nUSD = "USD"\nSOXL = "SOXL"\nSOXL_LEVERAGE = 1.5',
)
code = code.replace('"soxx": "#2563EB",', '"soxx": "#2563EB",\n    "usd": "#EA580C",')
code = code.replace('page_title="SOXX/SOXL Vol Target Backtest V5"', 'page_title="USD/SOXL Bear-SOXX Vol Target V1"')
code = code.replace('SOXX / SOXL Volatility Target Backtest V5', 'USD / SOXL Volatility Target with SOXX Bear Sleeve V1')
code = code.replace(
    'Default: Strong Bull uses SOXL tactically, Weak Bull shifts toward SOXX, '
    'and deep-drawdown turnarounds stay active until short-term momentum breaks',
    'Default: USD is the base signal, bull regimes use USD/SOXL, and bear regimes hold a SOXX defensive sleeve',
)
code = code.replace('"SOXL": "rgba(220, 38, 38, 0.72)",', '"USD": "rgba(234, 88, 12, 0.70)",\n        "SOXL": "rgba(220, 38, 38, 0.72)",')

code = replace_between(
    code,
    'def metric_row(',
    'def rebalance_weights(',
    '''def metric_row(
    name: str,
    daily_ret: pd.Series,
    soxx_w: pd.Series | None = None,
    usd_w: pd.Series | None = None,
    soxl_w: pd.Series | None = None,
) -> dict[str, object]:
    metrics = calc_metrics(daily_ret)
    return {
        "Strategy": name,
        "Total": metrics["total"],
        "CAGR": metrics["cagr"],
        "MDD": metrics["mdd"],
        "Sharpe": metrics["sharpe"],
        "Calmar": metrics["calmar"],
        "Monthly Win": metrics["win_m"],
        "Avg SOXX": np.nan if soxx_w is None else soxx_w.mean(),
        "Avg USD": np.nan if usd_w is None else usd_w.mean(),
        "Avg SOXL": np.nan if soxl_w is None else soxl_w.mean(),
        "Max SOXL": np.nan if soxl_w is None else soxl_w.max(),
    }''',
)
code = code.replace('current = pd.Series({"SOXX": 0.0, "SOXL": 0.0})', 'current = pd.Series({"SOXX": 0.0, "USD": 0.0, "SOXL": 0.0})')

code = replace_between(
    code,
    'def build_strategy_weights(',
    'def calc_target_weight(',
    '''def build_strategy_weights(
    price: pd.Series,
    trend_signal: pd.Series,
    regime: pd.Series,
    turnaround_signal: pd.Series,
    vol: pd.Series,
    target_vol: float,
    soxl_cap: float,
    max_risk_exposure: float,
    strong_usd_risk_share: float,
    weak_risk_multiplier: float,
    weak_usd_risk_share: float,
    weak_soxl_cap: float,
    turnaround_soxl_weight: float,
    bear_soxx: float,
    rebalance: str,
) -> pd.DataFrame:
    vol_lag = vol.shift(1).replace(0, np.nan)
    desired_risk = (target_vol / vol_lag).clip(0, max_risk_exposure).fillna(0.0)

    weights = pd.DataFrame(0.0, index=price.index, columns=["SOXX", "USD", "SOXL"])

    strong_risk = desired_risk
    strong_usd = (strong_risk * strong_usd_risk_share).clip(0, 1)
    strong_soxl = ((strong_risk - strong_usd) / SOXL_LEVERAGE).clip(0, soxl_cap)
    strong_used = strong_usd + strong_soxl * SOXL_LEVERAGE
    strong_usd = (strong_usd + (strong_risk - strong_used).clip(lower=0)).clip(0, 1 - strong_soxl)

    weak_risk = (desired_risk * weak_risk_multiplier).clip(0, max_risk_exposure)
    weak_usd = (weak_risk * weak_usd_risk_share).clip(0, 1)
    weak_soxl = ((weak_risk - weak_usd) / SOXL_LEVERAGE).clip(0, weak_soxl_cap)
    weak_used = weak_usd + weak_soxl * SOXL_LEVERAGE
    weak_usd = (weak_usd + (weak_risk - weak_used).clip(lower=0)).clip(0, 1 - weak_soxl)

    weights["SOXL"] = np.select(
        [regime == "Strong Bull", regime == "Weak Bull"],
        [strong_soxl, weak_soxl],
        default=0.0,
    )
    weights["USD"] = np.select(
        [regime == "Strong Bull", regime == "Weak Bull"],
        [strong_usd, weak_usd],
        default=0.0,
    )
    weights["SOXX"] = np.where(regime == "Bear", bear_soxx, 0.0)
    weights.loc[turnaround_signal, "SOXX"] = 0.0
    weights.loc[turnaround_signal, "USD"] = 1 - turnaround_soxl_weight
    weights.loc[turnaround_signal, "SOXL"] = turnaround_soxl_weight
    total = weights.sum(axis=1)
    scale = pd.Series(np.where(total > 1, 1 / total, 1), index=weights.index)
    weights = weights.mul(scale, axis=0).clip(0, 1)
    return rebalance_weights(weights, rebalance)''',
)

code = replace_between(
    code,
    'def calc_target_weight(',
    'def backtest(',
    '''def calc_target_weight(
    regime: str,
    is_turnaround: bool,
    current_vol: float,
    target_vol: float,
    soxl_cap: float,
    max_risk_exposure: float,
    strong_usd_risk_share: float,
    weak_risk_multiplier: float,
    weak_usd_risk_share: float,
    weak_soxl_cap: float,
    turnaround_soxl_weight: float,
    bear_soxx: float,
) -> pd.Series:
    if is_turnaround:
        return pd.Series({"SOXX": 0.0, "USD": 1 - turnaround_soxl_weight, "SOXL": turnaround_soxl_weight})

    if regime == "Bear" or pd.isna(current_vol) or current_vol <= 0:
        return pd.Series({"SOXX": bear_soxx, "USD": 0.0, "SOXL": 0.0})

    desired_risk = min(target_vol / current_vol, max_risk_exposure)
    if regime == "Weak Bull":
        desired_risk = min(desired_risk * weak_risk_multiplier, max_risk_exposure)
        usd_risk_share = weak_usd_risk_share
        effective_soxl_cap = weak_soxl_cap
    else:
        usd_risk_share = strong_usd_risk_share
        effective_soxl_cap = soxl_cap

    usd_w = min(desired_risk * usd_risk_share, 1.0)
    soxl_w = min(max((desired_risk - usd_w) / SOXL_LEVERAGE, 0.0), effective_soxl_cap)
    risk_used = usd_w + soxl_w * SOXL_LEVERAGE
    usd_w = min(usd_w + max(desired_risk - risk_used, 0.0), 1 - soxl_w)

    target = pd.Series({"SOXX": 0.0, "USD": usd_w, "SOXL": soxl_w}).clip(0, 1)
    if target.sum() > 1:
        target = target / target.sum()
    return target''',
)

code = replace_between(
    code,
    'def backtest(',
    'def build_execution_plan(',
    '''def backtest(weights: pd.DataFrame, ret_soxx: pd.Series, ret_usd: pd.Series, ret_soxl: pd.Series, cost_rate: float) -> pd.Series:
    turnover = weights.diff().abs().sum(axis=1).fillna(weights.abs().sum(axis=1))
    daily_ret = weights["SOXX"] * ret_soxx + weights["USD"] * ret_usd + weights["SOXL"] * ret_soxl - turnover * cost_rate
    return daily_ret.replace([np.inf, -np.inf], np.nan).fillna(0.0)''',
)

code = replace_between(
    code,
    'def build_execution_plan(',
    'with st.sidebar:',
    '''def build_execution_plan(
    target_weights: pd.Series,
    prices: pd.Series,
    account_value: float,
    current_shares: pd.Series,
    current_cash: float,
) -> tuple[pd.DataFrame, float]:
    current_values = current_shares * prices
    effective_value = account_value if account_value > 0 else current_values.sum() + current_cash
    rows = []
    for symbol in ["SOXX", "USD", "SOXL"]:
        target_value = effective_value * target_weights[symbol]
        target_shares = np.floor(target_value / prices[symbol]) if prices[symbol] > 0 else 0
        order_shares = target_shares - current_shares[symbol]
        rows.append(
            {
                "Symbol": symbol,
                "Latest Price": prices[symbol],
                "Target Weight": target_weights[symbol],
                "Target Value": target_value,
                "Target Shares": target_shares,
                "Current Shares": current_shares[symbol],
                "Order": "Buy" if order_shares > 0 else "Sell" if order_shares < 0 else "Hold",
                "Order Shares": order_shares,
                "Estimated Order Value": abs(order_shares) * prices[symbol],
            }
        )
    target_cash = effective_value * max(0.0, 1 - target_weights.sum())
    return pd.DataFrame(rows), target_cash''',
)

code = code.replace('Strong Bull SOXX risk share (%)', 'Strong Bull USD risk share (%)')
code = code.replace('Weak Bull SOXX risk share (%)', 'Weak Bull USD risk share (%)')
code = code.replace('Bear-regime SOXX weight (%)', 'Bear-regime SOXX weight (%)')
code = code.replace('Current SOXX shares', 'Current SOXX shares')
code = code.replace('current_soxx_shares = st.number_input("Current SOXX shares", min_value=0.0, value=0.0, step=1.0)', 'current_soxx_shares = st.number_input("Current SOXX shares", min_value=0.0, value=0.0, step=1.0)\n    current_usd_shares = st.number_input("Current USD shares", min_value=0.0, value=0.0, step=1.0)')

code = code.replace('| Bull regime | SOXX MA{fast_window} > MA{slow_window} |', '| Bull regime | USD MA{fast_window} > MA{slow_window} |')
code = code.replace('| Strong Bull allocation | SOXX gets {strong_soxx_risk_share:.0%} of risk budget, SOXL gets the rest |', '| Strong Bull allocation | USD gets {strong_soxx_risk_share:.0%} of risk budget, SOXL gets the rest |')
code = code.replace('| Weak Bull allocation | {weak_risk_multiplier:.0%} risk budget, SOXX gets {weak_soxx_risk_share:.0%}, SOXL cap {weak_soxl_cap:.0%} |', '| Weak Bull allocation | {weak_risk_multiplier:.0%} risk budget, USD gets {weak_soxx_risk_share:.0%}, SOXL cap {weak_soxl_cap:.0%} |')
code = code.replace('| Turnaround allocation | SOXX {1 - turnaround_soxl_weight:.0%} + SOXL {turnaround_soxl_weight:.0%} |', '| Turnaround allocation | USD {1 - turnaround_soxl_weight:.0%} + SOXL {turnaround_soxl_weight:.0%} |')
code = code.replace('| Bear regime | Cash {1 - bear_soxx:.0%} + SOXX {bear_soxx:.0%} |', '| Bear regime | Cash {1 - bear_soxx:.0%} + SOXX {bear_soxx:.0%} |')

code = code.replace('progress = st.progress(0, text="Loading SOXX/SOXL data...")', 'progress = st.progress(0, text="Loading SOXX/USD/SOXL data...")')
code = code.replace('    soxx = load_yahoo_chart(SOXX, warmup_start, end_dt)\n    soxl = load_yahoo_chart(SOXL, warmup_start, end_dt)', '    soxx = load_yahoo_chart(SOXX, warmup_start, end_dt)\n    usd = load_yahoo_chart(USD, warmup_start, end_dt)\n    soxl = load_yahoo_chart(SOXL, warmup_start, end_dt)')
code = code.replace('common_idx = soxx.index.intersection(soxl.index)', 'common_idx = soxx.index.intersection(usd.index).intersection(soxl.index)')
code = code.replace('soxx = soxx.reindex(full_idx).sort_index()\nsoxl = soxl.reindex(full_idx).sort_index()', 'soxx = soxx.reindex(full_idx).sort_index()\nusd = usd.reindex(full_idx).sort_index()\nsoxl = soxl.reindex(full_idx).sort_index()')
code = code.replace('price = soxx["adjclose"].ffill()', 'price = usd["adjclose"].ffill()')
code = code.replace('soxx_adj_factor = (soxx["adjclose"] / soxx["close"]).replace([np.inf, -np.inf], np.nan).ffill()\nsoxl_adj_factor', 'soxx_adj_factor = (soxx["adjclose"] / soxx["close"]).replace([np.inf, -np.inf], np.nan).ffill()\nusd_adj_factor = (usd["adjclose"] / usd["close"]).replace([np.inf, -np.inf], np.nan).ffill()\nsoxl_adj_factor')
code = code.replace('soxx_adjopen = (soxx["open"] * soxx_adj_factor).replace([np.inf, -np.inf], np.nan).ffill()\nsoxl_adjopen', 'soxx_adjopen = (soxx["open"] * soxx_adj_factor).replace([np.inf, -np.inf], np.nan).ffill()\nusd_adjopen = (usd["open"] * usd_adj_factor).replace([np.inf, -np.inf], np.nan).ffill()\nsoxl_adjopen')
code = code.replace('close_ret_soxx_full = soxx["adjclose"].pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)', 'close_ret_usd_full = usd["adjclose"].pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)')
code = code.replace('ret_soxx_full = (soxx_adjopen.shift(-1) / soxx_adjopen - 1).replace([np.inf, -np.inf], np.nan).fillna(0.0)\nret_soxl_full', 'ret_soxx_full = (soxx_adjopen.shift(-1) / soxx_adjopen - 1).replace([np.inf, -np.inf], np.nan).fillna(0.0)\nret_usd_full = (usd_adjopen.shift(-1) / usd_adjopen - 1).replace([np.inf, -np.inf], np.nan).fillna(0.0)\nret_soxl_full')
code = code.replace('vol = close_ret_soxx_full.rolling(vol_window).std() * np.sqrt(TRADING_DAYS)', 'vol = close_ret_usd_full.rolling(vol_window).std() * np.sqrt(TRADING_DAYS)')

code = re.sub(
    r'weights_full = build_strategy_weights\([\s\S]*?\n\)\n\nweights =',
    '''weights_full = build_strategy_weights(
    price,
    trend_signal,
    regime_signal,
    turnaround_signal,
    vol,
    target_vol,
    soxl_cap,
    max_risk_exposure,
    strong_soxx_risk_share,
    weak_risk_multiplier,
    weak_soxx_risk_share,
    weak_soxl_cap,
    turnaround_soxl_weight,
    bear_soxx,
    rebalance,
)

weights =''',
    code,
    count=1,
)
code = re.sub(
    r'close_target_weights = pd\.DataFrame\([\s\S]*?\n\)\nret_soxx =',
    '''close_target_weights = pd.DataFrame(
    [
        calc_target_weight(
            str(close_regime_signal.ffill().loc[date]),
            bool(close_turnaround_signal.fillna(False).loc[date]),
            vol.ffill().loc[date],
            target_vol,
            soxl_cap,
            max_risk_exposure,
            strong_soxx_risk_share,
            weak_risk_multiplier,
            weak_soxx_risk_share,
            weak_soxl_cap,
            turnaround_soxl_weight,
            bear_soxx,
        )
        for date in common_idx
    ],
    index=common_idx,
)
ret_soxx =''',
    code,
    count=1,
)
code = code.replace('ret_soxx = ret_soxx_full.reindex(common_idx).fillna(0.0)\nret_soxl = ret_soxl_full.reindex(common_idx).fillna(0.0)\nstrategy_ret = backtest(weights, ret_soxx, ret_soxl, cost_rate)', 'ret_soxx = ret_soxx_full.reindex(common_idx).fillna(0.0)\nret_usd = ret_usd_full.reindex(common_idx).fillna(0.0)\nret_soxl = ret_soxl_full.reindex(common_idx).fillna(0.0)\nstrategy_ret = backtest(weights, ret_soxx, ret_usd, ret_soxl, cost_rate)')
code = code.replace('bench_soxx = ret_soxx\nbench_soxl = ret_soxl\nfixed_20 = 0.8 * ret_soxx + 0.2 * ret_soxl\nfixed_30 = 0.7 * ret_soxx + 0.3 * ret_soxl', 'bench_soxx = ret_soxx\nbench_usd = ret_usd\nbench_soxl = ret_soxl\nfixed_20 = 0.8 * ret_usd + 0.2 * ret_soxl\nfixed_30 = 0.7 * ret_usd + 0.3 * ret_soxl')
code = code.replace('metric_row("Strategy", strategy_ret, weights["SOXX"], weights["SOXL"]),\n        metric_row("SOXX 100%", bench_soxx),\n        metric_row("SOXL 100%", bench_soxl),\n        metric_row("SOXX 80% + SOXL 20%", fixed_20),\n        metric_row("SOXX 70% + SOXL 30%", fixed_30),', 'metric_row("Strategy", strategy_ret, weights["SOXX"], weights["USD"], weights["SOXL"]),\n        metric_row("SOXX 100%", bench_soxx),\n        metric_row("USD 100%", bench_usd),\n        metric_row("SOXL 100%", bench_soxl),\n        metric_row("USD 80% + SOXL 20%", fixed_20),\n        metric_row("USD 70% + SOXL 30%", fixed_30),')

code = re.sub(
    r'next_target = calc_target_weight\([\s\S]*?\n\)\nlatest_prices =',
    '''next_target = calc_target_weight(
    str(close_regime_signal.reindex(weights.index).ffill().iloc[-1]),
    latest_turnaround,
    latest_vol,
    target_vol,
    soxl_cap,
    max_risk_exposure,
    strong_soxx_risk_share,
    weak_risk_multiplier,
    weak_soxx_risk_share,
    weak_soxl_cap,
    turnaround_soxl_weight,
    bear_soxx,
)
latest_prices =''',
    code,
    count=1,
)
code = code.replace('f"SOXX {next_target[\'SOXX\']:.1%}, SOXL {next_target[\'SOXL\']:.1%}, Cash {1 - next_target.sum():.1%} | "', 'f"SOXX {next_target[\'SOXX\']:.1%}, USD {next_target[\'USD\']:.1%}, SOXL {next_target[\'SOXL\']:.1%}, Cash {1 - next_target.sum():.1%} | "')
code = code.replace('f"SOXX {vol_window}D volatility {latest_vol:.1%}"', 'f"USD {vol_window}D volatility {latest_vol:.1%}"')
code = code.replace('"SOXL": soxl["adjclose"].reindex(weights.index).ffill().iloc[-1],', '"USD": usd["adjclose"].reindex(weights.index).ffill().iloc[-1],\n        "SOXL": soxl["adjclose"].reindex(weights.index).ffill().iloc[-1],')
code = code.replace('current_shares = pd.Series({"SOXX": current_soxx_shares, "SOXL": current_soxl_shares})', 'current_shares = pd.Series({"SOXX": current_soxx_shares, "USD": current_usd_shares, "SOXL": current_soxl_shares})')
code = code.replace('"SOXL": calc_metrics(bench_soxl)["nav"],\n            "80/20": calc_metrics(fixed_20)["nav"],', '"USD": calc_metrics(bench_usd)["nav"],\n            "SOXL": calc_metrics(bench_soxl)["nav"],\n            "80/20": calc_metrics(fixed_20)["nav"],')
code = code.replace('"SOXL DD": calc_metrics(bench_soxl)["dd"],', '"USD DD": calc_metrics(bench_usd)["dd"],\n            "SOXL DD": calc_metrics(bench_soxl)["dd"],')
code = code.replace('"SOXL": calc_metrics(bench_soxl)["nav"],\n            },', '"USD": calc_metrics(bench_usd)["nav"],\n                "SOXL": calc_metrics(bench_soxl)["nav"],\n            },')
code = code.replace('"SOXX": price.reindex(common_idx),', '"USD": price.reindex(common_idx),')
code = code.replace('static_line_chart(signal_df, "SOXX Trend", yaxis_title="Price", height=320)', 'static_line_chart(signal_df, "USD Trend", yaxis_title="Price", height=320)')
code = code.replace('"Applied SOXL": weights["SOXL"],\n            "Target SOXX": close_target_weights["SOXX"],\n            "Target SOXL": close_target_weights["SOXL"],', '"Applied USD": weights["USD"],\n            "Applied SOXL": weights["SOXL"],\n            "Target SOXX": close_target_weights["SOXX"],\n            "Target USD": close_target_weights["USD"],\n            "Target SOXL": close_target_weights["SOXL"],')
code = code.replace('for col in ["Total", "CAGR", "MDD", "Monthly Win", "Avg SOXX", "Avg SOXL", "Max SOXL"]:', 'for col in ["Total", "CAGR", "MDD", "Monthly Win", "Avg SOXX", "Avg USD", "Avg SOXL", "Max SOXL"]:')

code = code.replace("SOXX", "QQQ")
code = code.replace("USD", "QLD")
code = code.replace("SOXL", "TQQQ")
code = code.replace("soxx", "qqq")
code = code.replace("usd", "qld")
code = code.replace("soxl", "tqqq")

exec(compile(code, str(SOURCE_PAGE), "exec"), {"__file__": str(SOURCE_PAGE), "__name__": "__main__"})
