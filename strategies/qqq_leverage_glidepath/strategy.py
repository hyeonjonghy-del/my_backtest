"""Dynamic Nasdaq leverage glidepath with a Treasury-bill defensive sleeve.

Signals use completed QQQ closes. A changed target is first executable at the
next trading day's open; the backtest therefore keeps the old allocation over
the overnight gap and applies the new allocation only after that open.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd


RISK_ASSETS = ("QQQ", "QLD", "TQQQ")
CASH_ASSETS = ("BIL", "SGOV")
ASSETS = RISK_ASSETS + CASH_ASSETS
TARGET_COLUMNS = ("QQQ", "QLD", "TQQQ", "SGOV")

# SGOV denotes the defensive sleeve. The backtest executes that sleeve with
# BIL before SGOV history is available and SGOV thereafter.
REGIME_WEIGHTS: dict[str, pd.Series] = {
    "bear": pd.Series({"QQQ": 0.30, "QLD": 0.00, "TQQQ": 0.00, "SGOV": 0.70}),
    "caution": pd.Series({"QQQ": 0.35, "QLD": 0.15, "TQQQ": 0.00, "SGOV": 0.50}),
    "turn1": pd.Series({"QQQ": 0.35, "QLD": 0.15, "TQQQ": 0.00, "SGOV": 0.50}),
    "turn2": pd.Series({"QQQ": 0.35, "QLD": 0.25, "TQQQ": 0.10, "SGOV": 0.30}),
    "bull": pd.Series({"QQQ": 1 / 3, "QLD": 1 / 3, "TQQQ": 1 / 3, "SGOV": 0.00}),
}


@dataclass(frozen=True)
class GlidepathConfig:
    fast_days: int = 50
    slow_days: int = 150
    recovery_slope_days: int = 20
    peak_lookback_days: int = 63
    caution_drawdown: float = 0.075
    rebalance_monthly: bool = True
    cost_bps_per_traded_notional: float = 0.0

    def __post_init__(self) -> None:
        if self.fast_days < 2 or self.slow_days <= self.fast_days:
            raise ValueError("Require 2 <= fast_days < slow_days")
        if self.recovery_slope_days < 1 or self.peak_lookback_days < 2:
            raise ValueError("Slope and peak lookback windows must be positive")
        if not 0 < self.caution_drawdown < 1:
            raise ValueError("caution_drawdown must be between 0 and 1")
        if self.cost_bps_per_traded_notional < 0:
            raise ValueError("Trading cost cannot be negative")


def _normalize_index(frame: pd.DataFrame | pd.Series):
    result = frame.copy()
    result.index = pd.DatetimeIndex(result.index).tz_localize(None).normalize()
    return result.sort_index()


def build_signals(
    qqq_adjusted_close: pd.Series,
    config: GlidepathConfig | None = None,
) -> pd.DataFrame:
    """Build close-confirmed regimes without forward-looking values."""
    cfg = config or GlidepathConfig()
    qqq = _normalize_index(qqq_adjusted_close.astype(float)).dropna()
    fast = qqq.rolling(cfg.fast_days, min_periods=cfg.fast_days).mean()
    slow = qqq.rolling(cfg.slow_days, min_periods=cfg.slow_days).mean()
    peak = qqq.rolling(
        cfg.peak_lookback_days, min_periods=cfg.peak_lookback_days
    ).max()
    peak_drawdown = qqq / peak - 1.0
    fast_rising = fast > fast.shift(cfg.recovery_slope_days)

    regime = pd.Series(pd.NA, index=qqq.index, dtype="object")
    valid = slow.notna() & fast.notna() & peak.notna()
    regime.loc[valid] = "bear"
    below_slow_above_fast = valid & (qqq <= slow) & (qqq > fast)
    regime.loc[below_slow_above_fast] = "turn1"
    regime.loc[below_slow_above_fast & fast_rising] = "turn2"
    above_slow = valid & (qqq > slow)
    regime.loc[above_slow & (peak_drawdown <= -cfg.caution_drawdown)] = "caution"
    regime.loc[above_slow & (peak_drawdown > -cfg.caution_drawdown)] = "bull"

    return pd.DataFrame(
        {
            "QQQ": qqq,
            "MA_fast": fast,
            "MA_slow": slow,
            "Peak_drawdown": peak_drawdown,
            "MA_fast_rising": fast_rising,
            "Regime": regime,
        }
    )


def target_for_regime(regime: str) -> pd.Series:
    if regime not in REGIME_WEIGHTS:
        raise ValueError(f"Unknown regime: {regime}")
    return REGIME_WEIGHTS[regime].reindex(TARGET_COLUMNS).astype(float).copy()


def effective_nasdaq_exposure(target: pd.Series) -> float:
    return float(
        target.get("QQQ", 0.0)
        + 2.0 * target.get("QLD", 0.0)
        + 3.0 * target.get("TQQQ", 0.0)
    )


def latest_target(
    qqq_adjusted_close: pd.Series,
    config: GlidepathConfig | None = None,
) -> dict[str, object]:
    signals = build_signals(qqq_adjusted_close, config).dropna(subset=["Regime"])
    if signals.empty:
        raise ValueError("Not enough QQQ history to calculate a regime")
    date = signals.index[-1]
    regime = str(signals.loc[date, "Regime"])
    target = target_for_regime(regime)
    return {
        "signal_date": date,
        "regime": regime,
        "target": target,
        "effective_nasdaq_exposure": effective_nasdaq_exposure(target),
        "qqq": float(signals.loc[date, "QQQ"]),
        "ma_fast": float(signals.loc[date, "MA_fast"]),
        "ma_slow": float(signals.loc[date, "MA_slow"]),
        "peak_drawdown": float(signals.loc[date, "Peak_drawdown"]),
    }


def _prepare_frames(frames: Mapping[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    required_columns = {"open", "close", "adjclose"}
    missing_assets = [asset for asset in ASSETS if asset not in frames]
    if missing_assets:
        raise ValueError(f"Missing frames: {missing_assets}")
    prepared: dict[str, pd.DataFrame] = {}
    for asset in ASSETS:
        frame = _normalize_index(frames[asset])
        missing = required_columns.difference(frame.columns)
        if missing:
            raise ValueError(f"{asset} is missing columns: {sorted(missing)}")
        frame = frame.loc[:, ["open", "close", "adjclose"]].astype(float)
        frame["adjopen"] = frame["open"] * frame["adjclose"] / frame["close"]
        prepared[asset] = frame.replace([np.inf, -np.inf], np.nan)
    return prepared


def _actual_target(abstract_target: pd.Series, cash_asset: str) -> pd.Series:
    target = pd.Series(0.0, index=ASSETS)
    target.loc[list(RISK_ASSETS)] = abstract_target.loc[list(RISK_ASSETS)]
    target.loc[cash_asset] = float(abstract_target["SGOV"])
    return target


def _cash_asset_for_day(prepared: Mapping[str, pd.DataFrame], day: pd.Timestamp) -> str:
    sgov = prepared["SGOV"]
    if day in sgov.index and sgov.loc[day, ["adjopen", "adjclose"]].notna().all():
        return "SGOV"
    return "BIL"


def backtest_next_open(
    frames: Mapping[str, pd.DataFrame],
    start: str | pd.Timestamp,
    initial_capital: float = 100_000_000.0,
    config: GlidepathConfig | None = None,
) -> tuple[pd.DataFrame, dict[str, float | int | str]]:
    """Backtest fractional target weights with next-open signal execution.

    Costs are charged on gross traded notional: a complete A-to-B switch has
    turnover 2.0 because both the sale and purchase are counted.
    """
    cfg = config or GlidepathConfig()
    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive")
    prepared = _prepare_frames(frames)

    qqq = prepared["QQQ"]["adjclose"].dropna()
    signals = build_signals(qqq, cfg).dropna(subset=["Regime"])
    common_risk_dates = prepared["QQQ"].index
    for asset in RISK_ASSETS[1:]:
        common_risk_dates = common_risk_dates.intersection(prepared[asset].index)
    dates = common_risk_dates[common_risk_dates >= pd.Timestamp(start)]
    dates = dates.intersection(signals.index)
    dates = dates[dates.isin(qqq.index)]
    if len(dates) < 2:
        raise ValueError("Not enough common history after the requested start")

    # The first portfolio is established at an open using the prior close signal.
    first_i = qqq.index.get_indexer([dates[0]])[0]
    if first_i < 1:
        raise ValueError("A prior completed QQQ close is required")
    prior_signal_date = qqq.index[first_i - 1]
    while prior_signal_date not in signals.index and first_i > 1:
        first_i -= 1
        prior_signal_date = qqq.index[first_i - 1]
    if prior_signal_date not in signals.index:
        raise ValueError("No completed signal is available before the start")

    holdings = pd.Series(0.0, index=ASSETS)
    cash_asset = _cash_asset_for_day(prepared, dates[0])
    active_regime = str(signals.loc[prior_signal_date, "Regime"])
    initial_target = _actual_target(target_for_regime(active_regime), cash_asset)
    holdings = initial_capital * initial_target
    initial_intraday = pd.Series(1.0, index=ASSETS)
    for asset in ASSETS:
        if holdings[asset] and dates[0] in prepared[asset].index:
            row = prepared[asset].loc[dates[0]]
            initial_intraday[asset] = row["adjclose"] / row["adjopen"]
    holdings *= initial_intraday

    rows: list[dict[str, object]] = []
    regime_changes = 0
    gross_turnover_total = 0.0
    previous_month = dates[0].to_period("M")

    def append_row(day: pd.Timestamp, turnover: float, rebalanced: bool) -> None:
        total = float(holdings.sum())
        weights = holdings / total
        abstract = target_for_regime(active_regime)
        rows.append(
            {
                "Date": day,
                "Wealth": total / initial_capital,
                "Portfolio_value": total,
                "Regime": active_regime,
                "Effective_exposure": effective_nasdaq_exposure(abstract),
                "Gross_turnover": turnover,
                "Rebalanced": rebalanced,
                **{f"Weight_{asset}": float(weights[asset]) for asset in ASSETS},
                **{f"Target_{asset}": float(abstract[asset]) for asset in TARGET_COLUMNS},
            }
        )

    append_row(dates[0], 0.0, True)

    for position in range(1, len(dates)):
        day = dates[position]
        prior_day = dates[position - 1]

        # Old holdings bear the complete close-to-open gap.
        overnight = pd.Series(1.0, index=ASSETS)
        for asset in ASSETS:
            if not holdings[asset]:
                continue
            frame = prepared[asset]
            if day not in frame.index or prior_day not in frame.index:
                raise ValueError(f"Missing {asset} execution price on {day.date()}")
            overnight[asset] = frame.loc[day, "adjopen"] / frame.loc[prior_day, "adjclose"]
        holdings *= overnight
        total_open = float(holdings.sum())

        signal_regime = str(signals.loc[prior_day, "Regime"])
        cash_asset = _cash_asset_for_day(prepared, day)
        abstract_target = target_for_regime(signal_regime)
        actual_target = _actual_target(abstract_target, cash_asset)
        month = day.to_period("M")
        scheduled = cfg.rebalance_monthly and month != previous_month
        regime_changed = signal_regime != active_regime
        cash_changed = holdings.loc[list(CASH_ASSETS)].sum() > 0 and (
            holdings[cash_asset] == 0 and actual_target[cash_asset] > 0
        )
        rebalanced = regime_changed or scheduled or cash_changed
        turnover = 0.0
        if rebalanced:
            current_weights = holdings / total_open
            turnover = float((actual_target - current_weights).abs().sum())
            cost = total_open * turnover * cfg.cost_bps_per_traded_notional / 10_000.0
            total_open -= cost
            holdings = total_open * actual_target
            gross_turnover_total += turnover
            if regime_changed:
                regime_changes += 1
            active_regime = signal_regime

        intraday = pd.Series(1.0, index=ASSETS)
        for asset in ASSETS:
            if not holdings[asset]:
                continue
            row = prepared[asset].loc[day]
            intraday[asset] = row["adjclose"] / row["adjopen"]
        holdings *= intraday
        append_row(day, turnover, rebalanced)
        previous_month = month

    result = pd.DataFrame(rows).set_index("Date")
    returns = result["Wealth"].pct_change(fill_method=None).dropna()
    years = (result.index[-1] - result.index[0]).days / 365.2425
    drawdown = result["Wealth"] / result["Wealth"].cummax() - 1.0
    transition_dates = result.index[result["Regime"].ne(result["Regime"].shift())][1:]
    positions = result.index.get_indexer(transition_dates)
    gaps = pd.Series(positions).diff().dropna()
    volatility = float(returns.std(ddof=1) * np.sqrt(252))
    metrics: dict[str, float | int | str] = {
        "start": str(result.index[0].date()),
        "end": str(result.index[-1].date()),
        "multiple": float(result["Wealth"].iloc[-1]),
        "cagr": float(result["Wealth"].iloc[-1] ** (1 / years) - 1),
        "max_drawdown": float(drawdown.min()),
        "mdd_date": str(drawdown.idxmin().date()),
        "annual_volatility": volatility,
        "sharpe_rf0": float(returns.mean() / returns.std(ddof=1) * np.sqrt(252)),
        "regime_changes": int(regime_changes),
        "changes_within_5_days": int((gaps <= 5).sum()),
        "changes_within_10_days": int((gaps <= 10).sum()),
        "gross_turnover": float(gross_turnover_total),
        "average_effective_exposure": float(result["Effective_exposure"].mean()),
    }
    return result, metrics
