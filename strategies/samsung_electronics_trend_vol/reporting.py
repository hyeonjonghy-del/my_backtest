"""Calendar reports from the backtest's daily returns, including costs."""

import pandas as pd


def calendar_return_table(returns: pd.Series) -> pd.DataFrame:
    """Compound observed daily returns; leave unobserved months blank."""
    daily = returns.dropna().sort_index()
    columns = [f"{month}월" for month in range(1, 13)] + ["연간 수익률"]
    if daily.empty:
        return pd.DataFrame(columns=columns).rename_axis("연도")

    monthly = daily.add(1).groupby(daily.index.to_period("M")).prod().sub(1)
    frame = monthly.rename("수익률").to_frame()
    frame["연도"] = frame.index.year
    frame["월"] = frame.index.month
    table = frame.pivot(index="연도", columns="월", values="수익률").reindex(columns=range(1, 13))
    table.columns = columns[:-1]
    table["연간 수익률"] = daily.add(1).groupby(daily.index.year).prod().sub(1)
    return table.sort_index(ascending=False)
