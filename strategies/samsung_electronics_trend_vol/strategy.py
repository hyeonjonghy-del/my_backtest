"""Pure strategy logic for the Samsung Electronics trend/volatility model.

The model borrows the KODEX 200 bull/bear page's trend and volatility gates,
but removes leveraged ETFs and adds volatility-scaled exposure for the higher
idiosyncratic risk of a single stock.  Signals observed at a close are executed
at the following trading day's open.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


TRADING_DAYS = 252


@dataclass(frozen=True)
class StrategyConfig:
    long_ma_window: int = 120
    momentum_window: int = 60
    volatility_window: int = 20
    volatility_cap: float = 0.45
    target_volatility: float = 0.30
    min_invested_weight: float = 0.25
    max_invested_weight: float = 1.0
    fee_rate: float = 0.0015

    def validate(self) -> None:
        for name in ("long_ma_window", "momentum_window", "volatility_window"):
            if getattr(self, name) < 2:
                raise ValueError(f"{name} must be at least 2")
        if not 0 < self.volatility_cap <= 3:
            raise ValueError("volatility_cap must be in (0, 3]")
        if not 0 < self.target_volatility <= self.volatility_cap:
            raise ValueError("target_volatility must be positive and no greater than volatility_cap")
        if not 0 <= self.min_invested_weight <= self.max_invested_weight <= 1:
            raise ValueError("weights must satisfy 0 <= min <= max <= 1")
        if not 0 <= self.fee_rate < 0.1:
            raise ValueError("fee_rate must be in [0, 0.1)")


def _clean_prices(ohlcv: pd.DataFrame) -> pd.DataFrame:
    required = {"open", "close"}
    missing = required.difference(ohlcv.columns)
    if missing:
        raise ValueError(f"missing required columns: {', '.join(sorted(missing))}")

    prices = ohlcv.loc[:, ["open", "close"]].copy()
    prices.index = pd.to_datetime(prices.index).tz_localize(None).normalize()
    prices = prices[~prices.index.duplicated(keep="last")].sort_index()
    prices = prices.apply(pd.to_numeric, errors="coerce").replace([np.inf, -np.inf], np.nan)
    return prices.where(prices > 0).dropna()


def build_signals(close: pd.Series, config: StrategyConfig) -> pd.DataFrame:
    """Build close-based signals and the target Samsung weight."""

    config.validate()
    close = pd.to_numeric(close, errors="coerce").replace([np.inf, -np.inf], np.nan)
    long_ma = close.rolling(config.long_ma_window, min_periods=config.long_ma_window).mean()
    momentum = close.pct_change(config.momentum_window, fill_method=None)
    realized_volatility = (
        close.pct_change(fill_method=None)
        .rolling(config.volatility_window, min_periods=config.volatility_window)
        .std()
        * np.sqrt(TRADING_DAYS)
    )

    trend_ok = close > long_ma
    momentum_ok = momentum > 0
    volatility_ok = realized_volatility <= config.volatility_cap
    risk_on = (trend_ok & momentum_ok & volatility_ok).fillna(False)

    scaled_weight = (config.target_volatility / realized_volatility.replace(0, np.nan)).clip(
        lower=config.min_invested_weight,
        upper=config.max_invested_weight,
    )
    # A zero-volatility series is not a reason to exceed the configured maximum.
    scaled_weight = scaled_weight.where(realized_volatility > 0, config.max_invested_weight)
    target_weight = scaled_weight.where(risk_on, 0.0).fillna(0.0)

    regime = pd.Series("Cash / risk off", index=close.index, dtype="object")
    regime.loc[trend_ok & ~momentum_ok] = "Cash / momentum weak"
    regime.loc[trend_ok & momentum_ok & ~volatility_ok] = "Cash / volatility high"
    regime.loc[risk_on] = "Samsung / risk on"

    return pd.DataFrame(
        {
            "close": close,
            "long_ma": long_ma,
            "momentum": momentum,
            "realized_volatility": realized_volatility,
            "trend_ok": trend_ok.fillna(False),
            "momentum_ok": momentum_ok.fillna(False),
            "volatility_ok": volatility_ok.fillna(False),
            "risk_on": risk_on,
            "target_weight": target_weight,
            "target_cash_weight": 1.0 - target_weight,
            "regime": regime,
        }
    )


def run_backtest(ohlcv: pd.DataFrame, config: StrategyConfig) -> pd.DataFrame:
    """Run a next-open backtest, including turnover-based trading costs."""

    prices = _clean_prices(ohlcv)
    signals = build_signals(prices["close"], config)
    executable_weight = signals["target_weight"].shift(1).fillna(0.0)

    overnight_return = (prices["open"] / prices["close"].shift(1) - 1.0).fillna(0.0)
    intraday_return = (prices["close"] / prices["open"] - 1.0).fillna(0.0)

    nav = 1.0
    previous_weight = 0.0
    rows: list[dict[str, float]] = []
    for date in prices.index:
        before_open = nav * (1.0 + previous_weight * float(overnight_return.loc[date]))
        new_weight = float(executable_weight.loc[date])
        turnover = abs(new_weight - previous_weight)
        fee_cost = before_open * turnover * config.fee_rate
        after_fee = before_open - fee_cost
        nav = after_fee * (1.0 + new_weight * float(intraday_return.loc[date]))
        rows.append(
            {
                "strategy_nav": nav,
                "executed_weight": new_weight,
                "cash_weight": 1.0 - new_weight,
                "turnover": turnover,
                "fee_cost": fee_cost,
            }
        )
        previous_weight = new_weight

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
        "total_return": float(total_return),
        "cagr": float(cagr),
        "mdd": mdd,
        "sharpe": sharpe,
        "calmar": calmar,
    }
