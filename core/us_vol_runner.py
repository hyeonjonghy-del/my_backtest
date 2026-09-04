from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd


TRADING_DAYS = 252


@dataclass(frozen=True)
class USVolConfig:
    name: str
    base_symbol: str
    leveraged_symbol: str
    start_date: datetime
    end_date: datetime
    fast_window: int
    slow_window: int
    vol_window: int
    trend_rule: str
    target_vol: float
    leveraged_cap: float
    max_risk_exposure: float
    bear_base_weight: float
    rebalance: str
    cost_rate: float
    allocation_mode: str = "Risk-adjusted"


def normalize_index(df: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
    out = df.copy()
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    return out.sort_index()


def load_yahoo_chart(symbol: str, start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    period1 = int(datetime.combine(start_dt.date(), datetime.min.time(), tzinfo=timezone.utc).timestamp())
    period2 = int(datetime.combine((end_dt + timedelta(days=1)).date(), datetime.min.time(), tzinfo=timezone.utc).timestamp())
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?period1={period1}&period2={period2}&interval=1d&events=history&includeAdjustedClose=true"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as response:
        payload = json.load(response)

    result = payload["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    adjclose = result["indicators"].get("adjclose", [{}])[0].get("adjclose", quote["close"])
    index = pd.to_datetime(result["timestamp"], unit="s").normalize()
    df = pd.DataFrame(
        {
            "open": quote["open"],
            "high": quote["high"],
            "low": quote["low"],
            "close": quote["close"],
            "adjclose": adjclose,
            "volume": quote["volume"],
        },
        index=index,
    )
    return normalize_index(df).dropna(subset=["adjclose"])


def build_trend_signal(price: pd.Series, fast_ma: pd.Series, slow_ma: pd.Series, rule: str) -> pd.Series:
    if rule == "MA Fast > MA Slow":
        return fast_ma > slow_ma
    if rule == "Close > MA Slow":
        return price > slow_ma
    return (price > slow_ma) & (fast_ma > slow_ma)


def rebalance_weights(weights: pd.DataFrame, frequency: str, columns: list[str]) -> pd.DataFrame:
    if frequency == "Daily":
        return weights

    out = weights.copy() * 0.0
    current = pd.Series({column: 0.0 for column in columns})
    last_key = None
    for date, row in weights.iterrows():
        key = date.isocalendar()[:2] if frequency == "Weekly" else (date.year, date.month)
        if key != last_key:
            current = row
            last_key = key
        out.loc[date] = current
    return out


def build_strategy_weights(
    price: pd.Series,
    trend_signal: pd.Series,
    vol: pd.Series,
    config: USVolConfig,
) -> pd.DataFrame:
    base = config.base_symbol
    leveraged = config.leveraged_symbol
    signal = trend_signal.shift(1).fillna(False)
    vol_lag = vol.shift(1).replace(0, np.nan)
    desired_risk = (config.target_vol / vol_lag).clip(0, config.max_risk_exposure).fillna(0.0)

    weights = pd.DataFrame(0.0, index=price.index, columns=[base, leveraged])
    if config.allocation_mode == "Risk-adjusted":
        leveraged_w = (desired_risk / 3).clip(0, config.leveraged_cap)
        base_w = (desired_risk - leveraged_w * 3).clip(0, 1 - leveraged_w)
    elif config.allocation_mode == "Capital-first":
        capital = desired_risk.clip(0, 1)
        leveraged_w = capital.clip(0, config.leveraged_cap)
        base_w = (capital - leveraged_w).clip(0, 1 - leveraged_w)
    else:
        leveraged_w = pd.Series(config.leveraged_cap, index=price.index)
        base_w = pd.Series(1 - config.leveraged_cap, index=price.index)

    weights[leveraged] = np.where(signal, leveraged_w, 0.0)
    weights[base] = np.where(signal, base_w, config.bear_base_weight)
    total = weights.sum(axis=1)
    scale = pd.Series(np.where(total > 1, 1 / total, 1), index=weights.index)
    weights = weights.mul(scale, axis=0).clip(0, 1)
    return rebalance_weights(weights, config.rebalance, [base, leveraged])


def backtest(weights: pd.DataFrame, ret_base: pd.Series, ret_leveraged: pd.Series, config: USVolConfig) -> pd.Series:
    """Rebalance only after target weights change; otherwise let holdings drift."""
    columns = [config.base_symbol, config.leveraged_symbol]
    asset_values = pd.Series(0.0, index=columns)
    cash = 1.0
    previous_nav = 1.0
    previous_target: pd.Series | None = None
    daily_ret = pd.Series(0.0, index=weights.index)
    asset_returns = pd.DataFrame(
        {config.base_symbol: ret_base, config.leveraged_symbol: ret_leveraged}
    ).reindex(weights.index).fillna(0.0)

    for index_number, date in enumerate(weights.index):
        nav_before = float(cash + asset_values.sum())
        target = weights.loc[date, columns].clip(0, 1).fillna(0.0)
        target_changed = previous_target is None or not np.allclose(
            target.to_numpy(dtype=float),
            previous_target.to_numpy(dtype=float),
            rtol=0.0,
            atol=1e-12,
        )
        if target_changed and nav_before > 0:
            current_weights = asset_values / nav_before
            traded_fraction = float((target - current_weights).abs().sum())
            investable = max(nav_before * (1 - traded_fraction * config.cost_rate), 0.0)
            asset_values = investable * target
            cash = investable * max(0.0, 1 - float(target.sum()))
            previous_target = target.copy()

        asset_values = asset_values * (1 + asset_returns.loc[date])
        close_nav = float(cash + asset_values.sum())
        daily_ret.loc[date] = close_nav / previous_nav - 1 if index_number > 0 else close_nav - 1
        previous_nav = close_nav

    return daily_ret.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def calc_metrics(daily_ret: pd.Series) -> dict[str, object]:
    daily_ret = daily_ret.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    nav = (1 + daily_ret).cumprod()
    years = len(nav) / TRADING_DAYS
    total = nav.iloc[-1] - 1
    cagr = nav.iloc[-1] ** (1 / years) - 1 if years > 0 and nav.iloc[-1] > 0 else -1.0
    dd = nav / nav.cummax() - 1
    mdd = dd.min()
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(TRADING_DAYS) if daily_ret.std() > 0 else 0.0
    calmar = cagr / abs(mdd) if mdd < 0 else 0.0
    win_m = (nav.resample("ME").last().pct_change().dropna() > 0).mean()
    return {
        "nav": nav,
        "daily_returns": daily_ret,
        "drawdown": dd,
        "total": total,
        "cagr": cagr,
        "mdd": mdd,
        "sharpe": sharpe,
        "calmar": calmar,
        "monthly_win": win_m,
    }


def _adjusted_open_returns(df: pd.DataFrame) -> pd.Series:
    adj_factor = (df["adjclose"] / df["close"]).replace([np.inf, -np.inf], np.nan).ffill()
    adj_open = (df["open"] * adj_factor).replace([np.inf, -np.inf], np.nan).ffill()
    return (adj_open.shift(-1) / adj_open - 1).replace([np.inf, -np.inf], np.nan).fillna(0.0)


def run_us_vol_strategy(config: USVolConfig) -> dict[str, object]:
    warmup_start = config.start_date - timedelta(days=max(config.slow_window, config.vol_window) * 3)
    base_df = load_yahoo_chart(config.base_symbol, warmup_start, config.end_date)
    leveraged_df = load_yahoo_chart(config.leveraged_symbol, warmup_start, config.end_date)

    common_idx = base_df.index.intersection(leveraged_df.index)
    common_idx = common_idx[
        (common_idx.date >= config.start_date.date())
        & (common_idx.date <= config.end_date.date())
    ]
    if len(common_idx) < 200:
        raise ValueError(f"{config.name}: not enough data for the selected period.")

    full_idx = common_idx.union(base_df.index[base_df.index < common_idx[0]])
    base_df = base_df.reindex(full_idx).sort_index()
    leveraged_df = leveraged_df.reindex(full_idx).sort_index()

    price = base_df["adjclose"].ffill()
    close_ret_base_full = base_df["adjclose"].pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
    ret_base_full = _adjusted_open_returns(base_df)
    ret_leveraged_full = _adjusted_open_returns(leveraged_df)

    fast_ma = price.rolling(config.fast_window).mean()
    slow_ma = price.rolling(config.slow_window).mean()
    vol = close_ret_base_full.rolling(config.vol_window).std() * np.sqrt(TRADING_DAYS)
    trend_signal = build_trend_signal(price, fast_ma, slow_ma, config.trend_rule)
    weights_full = build_strategy_weights(price, trend_signal, vol, config)

    weights = weights_full.reindex(common_idx).fillna(0.0)
    ret_base = ret_base_full.reindex(common_idx).fillna(0.0)
    ret_leveraged = ret_leveraged_full.reindex(common_idx).fillna(0.0)
    daily_ret = backtest(weights, ret_base, ret_leveraged, config)
    metrics = calc_metrics(daily_ret)

    latest_trend = bool(trend_signal.reindex(weights.index).ffill().iloc[-1])
    latest_weight = weights.iloc[-1]
    cash_weight = max(0.0, 1 - float(latest_weight.sum()))
    current_position = (
        f"{config.base_symbol} {latest_weight[config.base_symbol]:.1%}, "
        f"{config.leveraged_symbol} {latest_weight[config.leveraged_symbol]:.1%}, "
        f"Cash {cash_weight:.1%}"
    )

    return {
        "name": config.name,
        "daily_returns": metrics["daily_returns"],
        "nav": metrics["nav"],
        "drawdown": metrics["drawdown"],
        "metrics": {
            "total": metrics["total"],
            "cagr": metrics["cagr"],
            "mdd": metrics["mdd"],
            "sharpe": metrics["sharpe"],
            "calmar": metrics["calmar"],
            "monthly_win": metrics["monthly_win"],
        },
        "current_position": current_position,
        "current_signal": "Bull" if latest_trend else "Bear",
        "weights": weights,
    }


def default_us_configs(start_date: datetime, end_date: datetime) -> list[USVolConfig]:
    return [
        USVolConfig(
            name="US Bull/Bear v3",
            base_symbol="SPY",
            leveraged_symbol="UPRO",
            start_date=start_date,
            end_date=end_date,
            fast_window=30,
            slow_window=200,
            vol_window=20,
            trend_rule="MA Fast > MA Slow",
            target_vol=0.35,
            leveraged_cap=0.50,
            max_risk_exposure=1.80,
            bear_base_weight=0.50,
            rebalance="Daily",
            cost_rate=0.0025,
        ),
        USVolConfig(
            name="SOXX/SOXL Vol Target",
            base_symbol="SOXX",
            leveraged_symbol="SOXL",
            start_date=start_date,
            end_date=end_date,
            fast_window=30,
            slow_window=200,
            vol_window=20,
            trend_rule="MA Fast > MA Slow",
            target_vol=0.45,
            leveraged_cap=0.50,
            max_risk_exposure=1.50,
            bear_base_weight=0.20,
            rebalance="Daily",
            cost_rate=0.0025,
        ),
        USVolConfig(
            name="QQQ/TQQQ Vol Target",
            base_symbol="QQQ",
            leveraged_symbol="TQQQ",
            start_date=start_date,
            end_date=end_date,
            fast_window=30,
            slow_window=200,
            vol_window=20,
            trend_rule="MA Fast > MA Slow",
            target_vol=0.35,
            leveraged_cap=0.45,
            max_risk_exposure=1.40,
            bear_base_weight=0.30,
            rebalance="Daily",
            cost_rate=0.0010,
        ),
    ]
