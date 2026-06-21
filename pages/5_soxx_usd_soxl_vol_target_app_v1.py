"""SOXX / USD / SOXL trend and volatility-target backtest page.

This page reuses the SOXX/SOXL v5 implementation and adapts it at runtime
so the base signal remains SOXX while the tactical leveraged sleeves can use
USD (2x semiconductor ETF) and SOXL (3x semiconductor ETF).
"""

from __future__ import annotations

from pathlib import Path


SOURCE_PAGE = Path(__file__).with_name("5_soxx_soxl_vol_target_app_v5.py")


def replace_between(source: str, start: str, end: str, replacement: str) -> str:
    start_idx = source.index(start)
    end_idx = source.index(end, start_idx)
    return source[:start_idx] + replacement.rstrip() + "\n\n" + source[end_idx:]


code = SOURCE_PAGE.read_text(encoding="utf-8")

code = code.replace(
    '"""SOXX / SOXL trend and volatility-target backtest v5."""',
    '"""SOXX / USD / SOXL trend and volatility-target backtest v1."""',
)
code = code.replace('SOXL = "SOXL"', 'USD = "USD"\nSOXL = "SOXL"\nUSD_LEVERAGE = 2.0\nSOXL_LEVERAGE = 3.0')
code = code.replace('"soxl": "#DC2626",', '"usd": "#EA580C",\n    "soxl": "#DC2626",')
code = code.replace(
    'page_title="SOXX/SOXL Vol Target Backtest V5"',
    'page_title="SOXX/USD/SOXL Vol Target Backtest V1"',
)
code = code.replace(
    "SOXX / SOXL Volatility Target Backtest V5",
    "SOXX / USD / SOXL Volatility Target Backtest V1",
)
code = code.replace(
    "Default: Strong Bull uses SOXL tactically, Weak Bull shifts toward SOXX, "
    "and deep-drawdown turnarounds stay active until short-term momentum breaks",
    "Default: Strong Bull can use SOXL aggressively, Weak Bull can use USD as a 2x middle gear, "
    "and SOXX remains the base signal and defensive sleeve",
)

code = replace_between(
    code,
    "def metric_row(",
    "def rebalance_weights(",
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
        "Max USD": np.nan if usd_w is None else usd_w.max(),
        "Max SOXL": np.nan if soxl_w is None else soxl_w.max(),
    }''',
)

code = code.replace('current = pd.Series({"SOXX": 0.0, "SOXL": 0.0})', 'current = pd.Series({"SOXX": 0.0, "USD": 0.0, "SOXL": 0.0})')

code = replace_between(
    code,
    "def build_strategy_weights(",
    "def calc_target_weight(",
    '''def build_strategy_weights(
    price: pd.Series,
    trend_signal: pd.Series,
    regime: pd.Series,
    turnaround_signal: pd.Series,
    vol: pd.Series,
    target_vol: float,
    soxl_cap: float,
    usd_cap: float,
    max_risk_exposure: float,
    strong_soxx_risk_share: float,
    weak_risk_multiplier: float,
    weak_soxx_risk_share: float,
    weak_usd_cap: float,
    weak_soxl_cap: float,
    turnaround_usd_weight: float,
    turnaround_soxl_weight: float,
    bear_soxx: float,
    rebalance: str,
) -> pd.DataFrame:
    vol_lag = vol.shift(1).replace(0, np.nan)
    desired_risk = (target_vol / vol_lag).clip(0, max_risk_exposure).fillna(0.0)

    weights = pd.DataFrame(0.0, index=price.index, columns=["SOXX", "USD", "SOXL"])

    strong_risk = desired_risk
    strong_soxx = (strong_risk * strong_soxx_risk_share).clip(0, 1)
    strong_soxl = ((strong_risk - strong_soxx) / SOXL_LEVERAGE).clip(0, soxl_cap)
    strong_risk_left = (strong_risk - strong_soxx - strong_soxl * SOXL_LEVERAGE).clip(lower=0)
    strong_usd = (strong_risk_left / USD_LEVERAGE).clip(0, usd_cap)
    strong_risk_used = strong_soxx + strong_usd * USD_LEVERAGE + strong_soxl * SOXL_LEVERAGE
    strong_soxx = (strong_soxx + (strong_risk - strong_risk_used).clip(lower=0)).clip(0, 1 - strong_usd - strong_soxl)

    weak_risk = (desired_risk * weak_risk_multiplier).clip(0, max_risk_exposure)
    weak_soxx = (weak_risk * weak_soxx_risk_share).clip(0, 1)
    weak_usd = ((weak_risk - weak_soxx) / USD_LEVERAGE).clip(0, weak_usd_cap)
    weak_risk_left = (weak_risk - weak_soxx - weak_usd * USD_LEVERAGE).clip(lower=0)
    weak_soxl = (weak_risk_left / SOXL_LEVERAGE).clip(0, weak_soxl_cap)
    weak_risk_used = weak_soxx + weak_usd * USD_LEVERAGE + weak_soxl * SOXL_LEVERAGE
    weak_soxx = (weak_soxx + (weak_risk - weak_risk_used).clip(lower=0)).clip(0, 1 - weak_usd - weak_soxl)

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
    weights["SOXX"] = np.select(
        [regime == "Strong Bull", regime == "Weak Bull"],
        [strong_soxx, weak_soxx],
        default=bear_soxx,
    )
    weights.loc[turnaround_signal, "USD"] = turnaround_usd_weight
    weights.loc[turnaround_signal, "SOXL"] = turnaround_soxl_weight
    weights.loc[turnaround_signal, "SOXX"] = 1 - turnaround_usd_weight - turnaround_soxl_weight
    total = weights.sum(axis=1)
    scale = pd.Series(np.where(total > 1, 1 / total, 1), index=weights.index)
    weights = weights.mul(scale, axis=0).clip(0, 1)
    return rebalance_weights(weights, rebalance)''',
)

code = replace_between(
    code,
    "def calc_target_weight(",
    "def backtest(",
    '''def calc_target_weight(
    regime: str,
    is_turnaround: bool,
    current_vol: float,
    target_vol: float,
    soxl_cap: float,
    usd_cap: float,
    max_risk_exposure: float,
    strong_soxx_risk_share: float,
    weak_risk_multiplier: float,
    weak_soxx_risk_share: float,
    weak_usd_cap: float,
    weak_soxl_cap: float,
    turnaround_usd_weight: float,
    turnaround_soxl_weight: float,
    bear_soxx: float,
) -> pd.Series:
    if is_turnaround:
        target = pd.Series({"SOXX": 1 - turnaround_usd_weight - turnaround_soxl_weight, "USD": turnaround_usd_weight, "SOXL": turnaround_soxl_weight}).clip(0, 1)
        return target / target.sum() if target.sum() > 1 else target

    if regime == "Bear" or pd.isna(current_vol) or current_vol <= 0:
        return pd.Series({"SOXX": bear_soxx, "USD": 0.0, "SOXL": 0.0})

    desired_risk = min(target_vol / current_vol, max_risk_exposure)
    if regime == "Weak Bull":
        desired_risk = min(desired_risk * weak_risk_multiplier, max_risk_exposure)
        soxx_risk_share = weak_soxx_risk_share
        effective_usd_cap = weak_usd_cap
        effective_soxl_cap = weak_soxl_cap
        use_usd_first = True
    else:
        soxx_risk_share = strong_soxx_risk_share
        effective_usd_cap = usd_cap
        effective_soxl_cap = soxl_cap
        use_usd_first = False

    soxx_w = min(desired_risk * soxx_risk_share, 1.0)
    risk_left = max(desired_risk - soxx_w, 0.0)
    if use_usd_first:
        usd_w = min(risk_left / USD_LEVERAGE, effective_usd_cap)
        risk_left = max(risk_left - usd_w * USD_LEVERAGE, 0.0)
        soxl_w = min(risk_left / SOXL_LEVERAGE, effective_soxl_cap)
    else:
        soxl_w = min(risk_left / SOXL_LEVERAGE, effective_soxl_cap)
        risk_left = max(risk_left - soxl_w * SOXL_LEVERAGE, 0.0)
        usd_w = min(risk_left / USD_LEVERAGE, effective_usd_cap)

    risk_used = soxx_w + usd_w * USD_LEVERAGE + soxl_w * SOXL_LEVERAGE
    soxx_w = min(soxx_w + max(desired_risk - risk_used, 0.0), 1 - usd_w - soxl_w)

    target = pd.Series({"SOXX": soxx_w, "USD": usd_w, "SOXL": soxl_w}).clip(0, 1)
    if target.sum() > 1:
        target = target / target.sum()
    return target''',
)

code = replace_between(
    code,
    "def backtest(",
    "def build_execution_plan(",
    '''def backtest(weights: pd.DataFrame, ret_soxx: pd.Series, ret_usd: pd.Series, ret_soxl: pd.Series, cost_rate: float) -> pd.Series:
    turnover = weights.diff().abs().sum(axis=1).fillna(weights.abs().sum(axis=1))
    daily_ret = weights["SOXX"] * ret_soxx + weights["USD"] * ret_usd + weights["SOXL"] * ret_soxl - turnover * cost_rate
    return daily_ret.replace([np.inf, -np.inf], np.nan).fillna(0.0)''',
)

code = replace_between(
    code,
    "def build_execution_plan(",
    "with st.sidebar:",
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

code = code.replace('soxl_cap = st.slider("SOXL max weight (%)", 0, 80, 50, 5) / 100', 'soxl_cap = st.slider("Strong Bull SOXL max weight (%)", 0, 80, 45, 5) / 100\n    usd_cap = st.slider("Strong Bull USD max weight (%)", 0, 80, 35, 5) / 100')
code = code.replace('weak_soxl_cap = st.slider("Weak Bull SOXL max weight (%)", 0, 40, 15, 5) / 100', 'weak_usd_cap = st.slider("Weak Bull USD max weight (%)", 0, 80, 45, 5) / 100\n    weak_soxl_cap = st.slider("Weak Bull SOXL max weight (%)", 0, 40, 10, 5) / 100')
code = code.replace('turnaround_soxl_weight = st.slider("Turnaround SOXL weight (%)", 0, 80, 50, 5) / 100', 'turnaround_usd_weight = st.slider("Turnaround USD weight (%)", 0, 80, 25, 5) / 100\n    turnaround_soxl_weight = st.slider("Turnaround SOXL weight (%)", 0, 80, 25, 5) / 100')
code = code.replace('current_soxl_shares = st.number_input("Current SOXL shares", min_value=0.0, value=0.0, step=1.0)', 'current_usd_shares = st.number_input("Current USD shares", min_value=0.0, value=0.0, step=1.0)\n    current_soxl_shares = st.number_input("Current SOXL shares", min_value=0.0, value=0.0, step=1.0)')

code = code.replace('| SOXL cap | {soxl_cap:.0%} |', '| Strong Bull SOXL cap | {soxl_cap:.0%} |\n| Strong Bull USD cap | {usd_cap:.0%} |')
code = code.replace('| Strong Bull allocation | SOXX gets {strong_soxx_risk_share:.0%} of risk budget, SOXL gets the rest |', '| Strong Bull allocation | SOXX gets {strong_soxx_risk_share:.0%} of risk budget, then SOXL first, USD second |')
code = code.replace('| Weak Bull allocation | {weak_risk_multiplier:.0%} risk budget, SOXX gets {weak_soxx_risk_share:.0%}, SOXL cap {weak_soxl_cap:.0%} |', '| Weak Bull allocation | {weak_risk_multiplier:.0%} risk budget, SOXX gets {weak_soxx_risk_share:.0%}, USD cap {weak_usd_cap:.0%}, SOXL cap {weak_soxl_cap:.0%} |')
code = code.replace('| Turnaround allocation | SOXX {1 - turnaround_soxl_weight:.0%} + SOXL {turnaround_soxl_weight:.0%} |', '| Turnaround allocation | SOXX {1 - turnaround_usd_weight - turnaround_soxl_weight:.0%} + USD {turnaround_usd_weight:.0%} + SOXL {turnaround_soxl_weight:.0%} |')

code = code.replace('progress = st.progress(0, text="Loading SOXX/SOXL data...")', 'progress = st.progress(0, text="Loading SOXX/USD/SOXL data...")')
code = code.replace('    soxx = load_yahoo_chart(SOXX, warmup_start, end_dt)\n    soxl = load_yahoo_chart(SOXL, warmup_start, end_dt)', '    soxx = load_yahoo_chart(SOXX, warmup_start, end_dt)\n    usd = load_yahoo_chart(USD, warmup_start, end_dt)\n    soxl = load_yahoo_chart(SOXL, warmup_start, end_dt)')
code = code.replace('common_idx = soxx.index.intersection(soxl.index)', 'common_idx = soxx.index.intersection(usd.index).intersection(soxl.index)')
code = code.replace('soxx = soxx.reindex(full_idx).sort_index()\nsoxl = soxl.reindex(full_idx).sort_index()', 'soxx = soxx.reindex(full_idx).sort_index()\nusd = usd.reindex(full_idx).sort_index()\nsoxl = soxl.reindex(full_idx).sort_index()')
code = code.replace('soxx_adj_factor = (soxx["adjclose"] / soxx["close"]).replace([np.inf, -np.inf], np.nan).ffill()\nsoxl_adj_factor', 'soxx_adj_factor = (soxx["adjclose"] / soxx["close"]).replace([np.inf, -np.inf], np.nan).ffill()\nusd_adj_factor = (usd["adjclose"] / usd["close"]).replace([np.inf, -np.inf], np.nan).ffill()\nsoxl_adj_factor')
code = code.replace('soxx_adjopen = (soxx["open"] * soxx_adj_factor).replace([np.inf, -np.inf], np.nan).ffill()\nsoxl_adjopen', 'soxx_adjopen = (soxx["open"] * soxx_adj_factor).replace([np.inf, -np.inf], np.nan).ffill()\nusd_adjopen = (usd["open"] * usd_adj_factor).replace([np.inf, -np.inf], np.nan).ffill()\nsoxl_adjopen')
code = code.replace('ret_soxx_full = (soxx_adjopen.shift(-1) / soxx_adjopen - 1).replace([np.inf, -np.inf], np.nan).fillna(0.0)\nret_soxl_full', 'ret_soxx_full = (soxx_adjopen.shift(-1) / soxx_adjopen - 1).replace([np.inf, -np.inf], np.nan).fillna(0.0)\nret_usd_full = (usd_adjopen.shift(-1) / usd_adjopen - 1).replace([np.inf, -np.inf], np.nan).fillna(0.0)\nret_soxl_full')

code = code.replace('    target_vol,\n    soxl_cap,\n    max_risk_exposure,', '    target_vol,\n    soxl_cap,\n    usd_cap,\n    max_risk_exposure,')
code = code.replace('    weak_soxx_risk_share,\n    weak_soxl_cap,\n    turnaround_soxl_weight,', '    weak_soxx_risk_share,\n    weak_usd_cap,\n    weak_soxl_cap,\n    turnaround_usd_weight,\n    turnaround_soxl_weight,')

code = code.replace('ret_soxx = ret_soxx_full.reindex(common_idx).fillna(0.0)\nret_soxl = ret_soxl_full.reindex(common_idx).fillna(0.0)\nstrategy_ret = backtest(weights, ret_soxx, ret_soxl, cost_rate)', 'ret_soxx = ret_soxx_full.reindex(common_idx).fillna(0.0)\nret_usd = ret_usd_full.reindex(common_idx).fillna(0.0)\nret_soxl = ret_soxl_full.reindex(common_idx).fillna(0.0)\nstrategy_ret = backtest(weights, ret_soxx, ret_usd, ret_soxl, cost_rate)')
code = code.replace('bench_soxx = ret_soxx\nbench_soxl = ret_soxl\nfixed_20 = 0.8 * ret_soxx + 0.2 * ret_soxl\nfixed_30 = 0.7 * ret_soxx + 0.3 * ret_soxl', 'bench_soxx = ret_soxx\nbench_usd = ret_usd\nbench_soxl = ret_soxl\nfixed_20 = 0.7 * ret_soxx + 0.2 * ret_usd + 0.1 * ret_soxl\nfixed_30 = 0.5 * ret_soxx + 0.3 * ret_usd + 0.2 * ret_soxl')
code = code.replace('metric_row("Strategy", strategy_ret, weights["SOXX"], weights["SOXL"]),\n        metric_row("SOXX 100%", bench_soxx),\n        metric_row("SOXL 100%", bench_soxl),\n        metric_row("SOXX 80% + SOXL 20%", fixed_20),\n        metric_row("SOXX 70% + SOXL 30%", fixed_30),', 'metric_row("Strategy", strategy_ret, weights["SOXX"], weights["USD"], weights["SOXL"]),\n        metric_row("SOXX 100%", bench_soxx),\n        metric_row("USD 100%", bench_usd),\n        metric_row("SOXL 100%", bench_soxl),\n        metric_row("SOXX 70% + USD 20% + SOXL 10%", fixed_20),\n        metric_row("SOXX 50% + USD 30% + SOXL 20%", fixed_30),')

code = code.replace('f"SOXX {next_target[\'SOXX\']:.1%}, SOXL {next_target[\'SOXL\']:.1%}, Cash {1 - next_target.sum():.1%} | "', 'f"SOXX {next_target[\'SOXX\']:.1%}, USD {next_target[\'USD\']:.1%}, SOXL {next_target[\'SOXL\']:.1%}, Cash {1 - next_target.sum():.1%} | "')
code = code.replace('"SOXL": soxl["adjclose"].reindex(weights.index).ffill().iloc[-1],', '"USD": usd["adjclose"].reindex(weights.index).ffill().iloc[-1],\n        "SOXL": soxl["adjclose"].reindex(weights.index).ffill().iloc[-1],')
code = code.replace('current_shares = pd.Series({"SOXX": current_soxx_shares, "SOXL": current_soxl_shares})', 'current_shares = pd.Series({"SOXX": current_soxx_shares, "USD": current_usd_shares, "SOXL": current_soxl_shares})')
code = code.replace('"SOXL": calc_metrics(bench_soxl)["nav"],\n            "80/20": calc_metrics(fixed_20)["nav"],', '"USD": calc_metrics(bench_usd)["nav"],\n            "SOXL": calc_metrics(bench_soxl)["nav"],\n            "70/20/10": calc_metrics(fixed_20)["nav"],')
code = code.replace('"SOXL DD": calc_metrics(bench_soxl)["dd"],', '"USD DD": calc_metrics(bench_usd)["dd"],\n            "SOXL DD": calc_metrics(bench_soxl)["dd"],')
code = code.replace('"SOXL": calc_metrics(bench_soxl)["nav"],\n            },', '"USD": calc_metrics(bench_usd)["nav"],\n                "SOXL": calc_metrics(bench_soxl)["nav"],\n            },')
code = code.replace('"Applied SOXL": weights["SOXL"],\n            "Target SOXX": close_target_weights["SOXX"],\n            "Target SOXL": close_target_weights["SOXL"],', '"Applied USD": weights["USD"],\n            "Applied SOXL": weights["SOXL"],\n            "Target SOXX": close_target_weights["SOXX"],\n            "Target USD": close_target_weights["USD"],\n            "Target SOXL": close_target_weights["SOXL"],')
code = code.replace('for col in ["Total", "CAGR", "MDD", "Monthly Win", "Avg SOXX", "Avg SOXL", "Max SOXL"]:', 'for col in ["Total", "CAGR", "MDD", "Monthly Win", "Avg SOXX", "Avg USD", "Avg SOXL", "Max USD", "Max SOXL"]:')

exec(compile(code, str(SOURCE_PAGE), "exec"), {"__file__": str(SOURCE_PAGE), "__name__": "__main__"})
