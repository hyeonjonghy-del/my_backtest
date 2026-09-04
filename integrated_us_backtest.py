"""Integrated US strategy backtest.

Outer allocator: strategy 9 (QQQ/GLD 12-month relative momentum).
Growth sleeve: 40% QQQ-only, 30% SOXX/SOXL, 30% QQQ/TQQQ.
The inner sleeves are monthly, lagged, volatility-targeted approximations of
pages 5 and 6.  The script deliberately keeps the outer 80%/20% allocation
and scales every growth sleeve proportionally when the outer risk weight is
20%.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

TICKERS = ["QQQ", "GLD", "SOXX", "SOXL", "TQQQ", "SGOV"]
GROWTH_SOXX_SOXL = 0.50
GROWTH_QQQ_TQQQ = 0.50


def download(start: str, end: str) -> pd.DataFrame:
    cache_dir = Path("outputs") / "yfinance_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        yf.set_tz_cache_location(str(cache_dir))
    except Exception:
        pass
    raw = yf.download(TICKERS, start=start, end=end, auto_adjust=True,
                      progress=False, group_by="column", threads=True)
    if isinstance(raw.columns, pd.MultiIndex):
        px = raw["Close"].copy()
    else:
        px = raw[["Close"]].rename(columns={"Close": TICKERS[0]})
    px = px.reindex(columns=TICKERS).dropna(how="all").ffill().dropna()
    if px.empty or len(px) < 300:
        raise RuntimeError("시장 데이터가 충분히 내려오지 않았습니다.")
    return px


def month_end(px: pd.DataFrame) -> pd.DataFrame:
    return px.resample("ME").last().dropna(how="all").ffill()


def pair_weights(underlying: pd.Series, levered: pd.Series, target_vol: float,
                 levered_cap: float, max_risk: float, weak_multiplier: float,
                 bear_underlying: float, strong_risk_share: float,
                 weak_risk_share: float) -> pd.DataFrame:
    """Monthly targets from lagged daily MA/vol signals, like pages 5 and 6."""
    d = pd.concat({"u": underlying, "l": levered}, axis=1).dropna()
    fast = d.u.rolling(30).mean()
    slow = d.u.rolling(200).mean()
    vol = d.u.pct_change().rolling(20).std() * np.sqrt(252)
    signal_fast, signal_slow, signal_vol = fast.shift(1), slow.shift(1), vol.shift(1)
    bull = signal_fast > signal_slow
    strong = bull & ((signal_fast / signal_slow - 1) >= 0.05) & (signal_vol <= 0.55)
    weak = bull & ~strong
    desired = (target_vol / signal_vol.replace(0, np.nan)).clip(0, max_risk).fillna(0)
    desired = desired.where(strong, desired * weak_multiplier).where(bull, 0)
    u_share = pd.Series(strong_risk_share, index=d.index).where(strong, weak_risk_share)
    # 3x leverage is treated as approximately 3x risk, matching page 5/6.
    u_w = (desired * u_share).clip(0, 1)
    l_w = ((desired - u_w) / 3).clip(0, levered_cap)
    used = u_w + 3 * l_w
    u_w = (u_w + (desired - used).clip(lower=0)).clip(0, 1 - l_w)
    u_w = u_w.where(bull, bear_underlying)
    l_w = l_w.where(bull, 0.0)
    return pd.DataFrame({underlying.name: u_w, levered.name: l_w}, index=d.index).resample("ME").last()


def metrics(nav: pd.Series) -> dict[str, float]:
    r = nav.pct_change().dropna()
    years = max((nav.index[-1] - nav.index[0]).days / 365.25, 1 / 365.25)
    dd = nav / nav.cummax() - 1
    vol = r.std(ddof=1) * np.sqrt(252)
    return {
        "CAGR": (nav.iloc[-1] / nav.iloc[0]) ** (1 / years) - 1,
        "MDD": dd.min(),
        "Volatility": vol,
        "Sharpe": r.mean() * 252 / vol if vol > 0 else np.nan,
        "Final NAV": nav.iloc[-1],
    }


def holdings_nav(
    prices: pd.DataFrame,
    target_weights: pd.DataFrame,
    cost_bps: float = 0.0,
) -> tuple[pd.Series, pd.Series]:
    """Trade only on target changes and let asset weights drift between trades."""
    prices = prices.ffill()
    returns = prices.pct_change().fillna(0.0)
    targets = target_weights.reindex(prices.index).fillna(0.0).reindex(columns=prices.columns, fill_value=0.0)
    asset_values = pd.Series(0.0, index=prices.columns)
    cash = 1.0
    previous_target: pd.Series | None = None
    nav = pd.Series(1.0, index=prices.index)
    turnover = pd.Series(0.0, index=prices.index)

    for index_number, date in enumerate(prices.index):
        nav_before = float(cash + asset_values.sum())
        desired = targets.loc[date].clip(0, 1)
        target_changed = previous_target is None or not np.allclose(
            desired.to_numpy(dtype=float),
            previous_target.to_numpy(dtype=float),
            rtol=0.0,
            atol=1e-12,
        )
        if target_changed and nav_before > 0:
            current_weights = asset_values / nav_before
            turnover.loc[date] = float((desired - current_weights).abs().sum())
            fee = nav_before * turnover.loc[date] * cost_bps / 10000
            investable = max(nav_before - fee, 0.0)
            asset_values = investable * desired
            cash = investable * max(0.0, 1 - float(desired.sum()))
            previous_target = desired.copy()

        asset_values = asset_values * (1 + returns.loc[date])
        nav.loc[date] = float(cash + asset_values.sum())

    return nav, turnover


def run(px: pd.DataFrame, cost_bps: float = 10.0,
        sgov_rank2_weight: float = 0.30,
        sgov_rank1_weight: float = 0.50) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not 0.0 <= sgov_rank2_weight <= 0.80 or not 0.0 <= sgov_rank1_weight <= 0.80:
        raise ValueError("SGOV weights must be between 0% and 80%.")
    if sgov_rank1_weight < sgov_rank2_weight:
        raise ValueError("SGOV rank-1 weight must be at least the rank-2 weight.")
    m = month_end(px)
    soxx_soxl = pair_weights(px.SOXX.rename("SOXX"), px.SOXL.rename("SOXL"),
                              target_vol=.45, levered_cap=.50, max_risk=1.5,
                              weak_multiplier=.75, bear_underlying=.20,
                              strong_risk_share=.20, weak_risk_share=.80)
    qqq_tqqq = pair_weights(px.QQQ.rename("QQQ"), px.TQQQ.rename("TQQQ"),
                            target_vol=.35, levered_cap=.45, max_risk=1.8,
                            weak_multiplier=.75, bear_underlying=.30,
                            strong_risk_share=.60, weak_risk_share=.60)
    # Strategy 9 v2 ranks the composite growth sleeve, GLD, and SGOV.
    inner_soxx = soxx_soxl.reindex(px.index, method="ffill").shift(1).fillna(0)
    inner_qqq = qqq_tqqq.reindex(px.index, method="ffill").shift(1).fillna(0)
    inner_targets = pd.DataFrame(0.0, index=px.index, columns=["SOXX", "SOXL", "QQQ", "TQQQ"])
    inner_targets[["SOXX", "SOXL"]] = inner_soxx * GROWTH_SOXX_SOXL
    inner_targets[["QQQ", "TQQQ"]] = inner_qqq * GROWTH_QQQ_TQQQ
    growth_nav, _ = holdings_nav(
        px[["SOXX", "SOXL", "QQQ", "TQQQ"]],
        inner_targets,
    )
    rank_prices = pd.concat({"Growth": growth_nav.resample("ME").last(), "GLD": m.GLD, "SGOV": m.SGOV}, axis=1)
    mom = rank_prices.pct_change(12).shift(1)
    growth_selected = (mom.Growth > mom.GLD) & (mom.Growth > 0)
    cash_rank = mom.rank(axis=1, ascending=False, method="min").SGOV
    targets = pd.DataFrame(0.0, index=m.index, columns=TICKERS + ["CASH"])
    targets["GLD"] = np.where(growth_selected, 0.20, 0.80)
    growth_weight = pd.Series(np.where(growth_selected, 0.80, 0.20), index=m.index)
    growth_weight.loc[growth_selected & (cash_rank == 2)] = 0.80 - sgov_rank2_weight
    growth_weight.loc[growth_selected & (cash_rank == 1)] = 0.80 - sgov_rank1_weight
    targets["SGOV"] = np.where(growth_selected & (cash_rank == 2), sgov_rank2_weight,
                                np.where(growth_selected & (cash_rank == 1), sgov_rank1_weight, 0.0))
    for col in soxx_soxl:
        targets[col] += growth_weight * GROWTH_SOXX_SOXL * soxx_soxl[col]
    for col in qqq_tqqq:
        targets[col] += growth_weight * GROWTH_QQQ_TQQQ * qqq_tqqq[col]
    targets["CASH"] = (1 - targets[TICKERS].sum(axis=1)).clip(lower=0)
    # Execute monthly target from the next trading day and mark daily NAV.
    daily_target = targets.reindex(px.index, method="ffill").fillna(0)
    daily_target = daily_target.shift(1).fillna(0)
    daily_ret = px.pct_change().fillna(0)
    nav, turnover = holdings_nav(px[TICKERS], daily_target[TICKERS], cost_bps)
    out = pd.DataFrame({"Integrated": nav})
    # Benchmarks use the same daily valuation convention.
    # Original strategy 9 benchmark NAV.
    bench_w = pd.DataFrame(0.0, index=px.index, columns=["QQQ", "GLD"])
    q = growth_selected.reindex(px.index, method="ffill").fillna(False)
    g = ~q
    bench_w.loc[q, "QQQ"], bench_w.loc[q, "GLD"] = .8, .2
    bench_w.loc[g, "QQQ"], bench_w.loc[g, "GLD"] = .2, .8
    br = (bench_w * daily_ret[["QQQ", "GLD"]]).sum(axis=1)
    out["Original_9_QQQ_GLD"] = (1 + br).cumprod()
    out["QQQ_buy_hold"] = (1 + daily_ret.QQQ).cumprod()
    out["GLD_buy_hold"] = (1 + daily_ret.GLD).cumprod()
    summary = pd.DataFrame({name: metrics(out[name]) for name in out.columns}).T
    summary["Average growth weight"] = growth_weight.reindex(px.index, method="ffill").mean()
    summary["Average SOXL weight"] = daily_target.SOXL.mean()
    summary["Average TQQQ weight"] = daily_target.TQQQ.mean()
    summary["Average SGOV weight"] = daily_target.SGOV.mean()
    summary.loc[summary.index != "Integrated", ["Average growth weight", "Average SOXL weight", "Average TQQQ weight", "Average SGOV weight"]] = np.nan
    return out, summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2010-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--out", default="outputs/integrated_us_backtest")
    args = ap.parse_args()
    end = args.end or pd.Timestamp.today().strftime("%Y-%m-%d")
    px = download(args.start, end)
    nav, summary = run(px)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    nav.to_csv(out / "nav.csv")
    summary.to_csv(out / "summary.csv")
    print(summary.round(4).to_string())
    print(f"\nSaved: {out / 'nav.csv'}")
    print(f"Saved: {out / 'summary.csv'}")


if __name__ == "__main__":
    main()

