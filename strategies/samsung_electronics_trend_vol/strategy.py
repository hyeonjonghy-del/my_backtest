"""Samsung Electronics practical trend strategy with conditional leverage.

Signals observed at a close are executed at the following trading day's open.
Before the leveraged ETF's listing, its return is modeled as twice Samsung's
daily return less the configured annual expense. Actual ETF OHLC may be mixed
in after listing and is identified row by row in the result.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


TRADING_DAYS = 252


@dataclass(frozen=True)
class StrategyConfig:
    long_ma_window: int = 200
    fast_ma_window: int = 20
    fast_ma_slope_window: int = 5
    momentum_window: int = 60
    recent_range_window: int = 20
    strong_momentum_threshold: float = 0.05
    strong_volatility_cap: float = 0.65
    leverage_weight: float = 0.25
    early_reentry_weight: float = 0.65
    crash_drawdown_threshold: float = 0.15
    crash_volatility_threshold: float = 0.80
    leverage_expense_rate: float = 0.0029
    fee_rate: float = 0.0015

    def validate(self) -> None:
        for name in (
            "long_ma_window", "fast_ma_window", "fast_ma_slope_window",
            "momentum_window", "recent_range_window",
        ):
            if getattr(self, name) < 2:
                raise ValueError(f"{name} must be at least 2")
        if self.fast_ma_window >= self.long_ma_window:
            raise ValueError("fast_ma_window must be shorter than long_ma_window")
        if not 0 <= self.strong_momentum_threshold <= 1:
            raise ValueError("strong_momentum_threshold must be in [0, 1]")
        if not 0 < self.strong_volatility_cap <= self.crash_volatility_threshold <= 3:
            raise ValueError("volatility thresholds must satisfy 0 < strong <= crash <= 3")
        if not 0 <= self.leverage_weight <= 0.5:
            raise ValueError("leverage_weight must be in [0, 0.5]")
        if not 0 <= self.early_reentry_weight <= 1:
            raise ValueError("early_reentry_weight must be in [0, 1]")
        if not 0 < self.crash_drawdown_threshold < 1:
            raise ValueError("crash_drawdown_threshold must be in (0, 1)")
        if not 0 <= self.leverage_expense_rate < 0.2:
            raise ValueError("leverage_expense_rate must be in [0, 0.2)")
        if not 0 <= self.fee_rate < 0.1:
            raise ValueError("fee_rate must be in [0, 0.1)")


def _clean_prices(ohlcv: pd.DataFrame) -> pd.DataFrame:
    missing = {"open", "close"}.difference(ohlcv.columns)
    if missing:
        raise ValueError(f"missing required columns: {', '.join(sorted(missing))}")
    prices = ohlcv.loc[:, ["open", "close"]].copy()
    prices.index = pd.to_datetime(prices.index).tz_localize(None).normalize()
    prices = prices[~prices.index.duplicated(keep="last")].sort_index()
    prices = prices.apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
    return prices.where(prices > 0).dropna()


def build_signals(close: pd.Series, config: StrategyConfig) -> pd.DataFrame:
    """Build normal-bull, early-reentry, strong-bull and crash regimes."""

    config.validate()
    close = pd.to_numeric(close, errors="coerce").replace([np.inf, -np.inf], np.nan)
    daily_return = close.pct_change(fill_method=None)
    long_ma = close.rolling(config.long_ma_window, min_periods=config.long_ma_window).mean()
    fast_ma = close.rolling(config.fast_ma_window, min_periods=config.fast_ma_window).mean()
    momentum = close.pct_change(config.momentum_window, fill_method=None)
    realized_volatility = (
        daily_return.rolling(config.fast_ma_window, min_periods=config.fast_ma_window).std()
        * np.sqrt(TRADING_DAYS)
    )
    recent_low = close.rolling(config.recent_range_window, min_periods=config.recent_range_window).min()
    recent_high = close.rolling(config.recent_range_window, min_periods=config.recent_range_window).max()
    rebound = close / recent_low - 1.0
    pullback = close / recent_high - 1.0

    trend_ok = (close > long_ma).fillna(False)
    fast_ma_rising = (fast_ma > fast_ma.shift(config.fast_ma_slope_window)).fillna(False)
    crash = (
        (pullback <= -config.crash_drawdown_threshold)
        | (realized_volatility > config.crash_volatility_threshold)
    ).fillna(False)
    early_reentry = (
        ~trend_ok & fast_ma_rising & (close > fast_ma) & (rebound > 0.05) & ~crash
    ).fillna(False)
    strong_bull = (
        trend_ok
        & fast_ma_rising
        & (momentum > config.strong_momentum_threshold)
        & (realized_volatility <= config.strong_volatility_cap)
        & ~crash
    ).fillna(False)

    samsung_weight = pd.Series(0.0, index=close.index)
    leverage_weight = pd.Series(0.0, index=close.index)
    samsung_weight.loc[trend_ok] = 1.0
    samsung_weight.loc[early_reentry] = config.early_reentry_weight
    samsung_weight.loc[strong_bull] = 1.0 - config.leverage_weight
    leverage_weight.loc[strong_bull] = config.leverage_weight
    samsung_weight.loc[crash] = 0.0
    leverage_weight.loc[crash] = 0.0
    cash_weight = (1.0 - samsung_weight - leverage_weight).clip(0.0, 1.0)

    regime = pd.Series("Cash / risk off", index=close.index, dtype="object")
    regime.loc[trend_ok] = "Samsung / normal bull"
    regime.loc[early_reentry] = "Samsung / early reentry"
    regime.loc[strong_bull] = "Samsung + leverage / strong bull"
    regime.loc[crash] = "Cash / crash protection"

    return pd.DataFrame(
        {
            "close": close,
            "long_ma": long_ma,
            "fast_ma": fast_ma,
            "momentum": momentum,
            "realized_volatility": realized_volatility,
            "pullback": pullback,
            "rebound": rebound,
            "trend_ok": trend_ok,
            "fast_ma_rising": fast_ma_rising,
            "early_reentry": early_reentry,
            "strong_bull": strong_bull,
            "crash": crash,
            "target_samsung_weight": samsung_weight,
            "target_leverage_weight": leverage_weight,
            "target_cash_weight": cash_weight,
            "target_effective_exposure": samsung_weight + 2.0 * leverage_weight,
            "regime": regime,
        }
    )


def _leverage_returns(
    prices: pd.DataFrame,
    leverage_ohlcv: pd.DataFrame | None,
    config: StrategyConfig,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    samsung_overnight = (prices["open"] / prices["close"].shift(1) - 1.0).fillna(0.0)
    samsung_intraday = (prices["close"] / prices["open"] - 1.0).fillna(0.0)
    leverage_overnight = (2.0 * samsung_overnight).clip(lower=-0.99)
    samsung_daily = (1.0 + samsung_overnight) * (1.0 + samsung_intraday) - 1.0
    leverage_daily = (2.0 * samsung_daily - config.leverage_expense_rate / TRADING_DAYS).clip(lower=-0.99)
    leverage_intraday = ((1.0 + leverage_daily) / (1.0 + leverage_overnight) - 1.0).clip(lower=-0.99)
    source = pd.Series("Synthetic 2x", index=prices.index, dtype="object")

    if leverage_ohlcv is not None and not leverage_ohlcv.empty:
        actual = _clean_prices(leverage_ohlcv).reindex(prices.index)
        actual_overnight = actual["open"] / actual["close"].shift(1) - 1.0
        actual_intraday = actual["close"] / actual["open"] - 1.0
        valid = actual[["open", "close"]].notna().all(axis=1) & actual["close"].shift(1).notna()
        leverage_overnight.loc[valid] = actual_overnight.loc[valid]
        leverage_intraday.loc[valid] = actual_intraday.loc[valid]
        source.loc[valid] = "Actual ETF"
    return leverage_overnight.fillna(0.0), leverage_intraday.fillna(0.0), source


def run_backtest(
    ohlcv: pd.DataFrame,
    config: StrategyConfig,
    leverage_ohlcv: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Run a next-open multi-asset backtest with turnover costs."""

    prices = _clean_prices(ohlcv)
    signals = build_signals(prices["close"], config)
    target_columns = ["target_samsung_weight", "target_leverage_weight", "target_cash_weight"]
    executable = signals[target_columns].shift(1).fillna(0.0)

    samsung_overnight = (prices["open"] / prices["close"].shift(1) - 1.0).fillna(0.0)
    samsung_intraday = (prices["close"] / prices["open"] - 1.0).fillna(0.0)
    leverage_overnight, leverage_intraday, leverage_source = _leverage_returns(prices, leverage_ohlcv, config)

    nav = 1.0
    previous_samsung = 0.0
    previous_leverage = 0.0
    rows: list[dict[str, object]] = []
    for date in prices.index:
        starting_nav = nav
        overnight_contribution = (
            previous_samsung * float(samsung_overnight.loc[date])
            + previous_leverage * float(leverage_overnight.loc[date])
        )
        before_open = nav * (1.0 + overnight_contribution)
        new_samsung = float(executable.loc[date, "target_samsung_weight"])
        new_leverage = float(executable.loc[date, "target_leverage_weight"])
        turnover = abs(new_samsung - previous_samsung) + abs(new_leverage - previous_leverage)
        fee_cost = before_open * turnover * config.fee_rate
        after_fee = before_open - fee_cost
        intraday_contribution = (
            new_samsung * float(samsung_intraday.loc[date])
            + new_leverage * float(leverage_intraday.loc[date])
        )
        nav = after_fee * (1.0 + intraday_contribution)
        rows.append(
            {
                "strategy_nav": nav,
                "prior_samsung_weight": previous_samsung,
                "prior_leverage_weight": previous_leverage,
                "executed_samsung_weight": new_samsung,
                "executed_leverage_weight": new_leverage,
                "executed_cash_weight": 1.0 - new_samsung - new_leverage,
                "executed_effective_exposure": new_samsung + 2.0 * new_leverage,
                "turnover": turnover,
                "fee_cost": fee_cost,
                "overnight_contribution": before_open / starting_nav - 1.0 if starting_nav > 0 else 0.0,
                "fee_contribution": after_fee / before_open - 1.0 if before_open > 0 else 0.0,
                "intraday_contribution": nav / after_fee - 1.0 if after_fee > 0 else 0.0,
                "cash_all_day": previous_samsung == 0.0
                and previous_leverage == 0.0
                and new_samsung == 0.0
                and new_leverage == 0.0,
                "leverage_return_source": leverage_source.loc[date],
            }
        )
        previous_samsung = new_samsung
        previous_leverage = new_leverage

    result = signals.join(pd.DataFrame(rows, index=prices.index))
    result["buy_hold_nav"] = prices["close"] / prices["close"].iloc[0]
    result["strategy_return"] = result["strategy_nav"].pct_change(fill_method=None).fillna(0.0)
    result["buy_hold_return"] = result["buy_hold_nav"].pct_change(fill_method=None).fillna(0.0)
    return result


def performance_metrics(nav: pd.Series) -> dict[str, float]:
    clean = pd.to_numeric(nav, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if len(clean) < 2 or clean.iloc[0] <= 0:
        return {"total_return": 0.0, "cagr": 0.0, "mdd": 0.0, "sharpe": 0.0, "calmar": 0.0}
    daily = clean.pct_change(fill_method=None).dropna()
    elapsed_years = max((clean.index[-1] - clean.index[0]).days / 365.25, len(clean) / TRADING_DAYS)
    total_return = clean.iloc[-1] / clean.iloc[0] - 1.0
    cagr = (clean.iloc[-1] / clean.iloc[0]) ** (1.0 / elapsed_years) - 1.0
    drawdown = clean / clean.cummax() - 1.0
    mdd = float(drawdown.min())
    sharpe = float(daily.mean() / daily.std() * np.sqrt(TRADING_DAYS)) if daily.std() > 0 else 0.0
    calmar = float(cagr / abs(mdd)) if mdd < 0 else 0.0
    return {
        "total_return": float(total_return), "cagr": float(cagr), "mdd": mdd,
        "sharpe": sharpe, "calmar": calmar,
    }
