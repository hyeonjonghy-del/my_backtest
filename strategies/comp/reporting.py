"""Calendar-period performance derived from the selected equity curve."""
import numpy as np
import pandas as pd


def monthly_performance(curve: pd.DataFrame) -> pd.DataFrame:
    values = curve[['nav', 'KOSPI', 'KOSDAQ']].rename(columns={'nav': 'COMP'}).sort_index()
    if values.empty or not isinstance(values.index, pd.DatetimeIndex) or values.index.has_duplicates:
        raise ValueError('A nonempty, unique daily equity curve is required.')
    if not np.isfinite(values.to_numpy()).all() or values.le(0).any().any():
        raise ValueError('Equity values must be finite and positive.')
    endings = values.groupby(values.index.to_period('M')).last()
    previous = endings.shift(1)
    previous.iloc[0] = values.iloc[0]
    monthly = endings / previous - 1
    rows = []
    for year, group in monthly.groupby(monthly.index.year):
        for asset in values.columns:
            row = {'year': year, 'asset': asset, **{month: np.nan for month in range(1, 13)}}
            row.update({period.month: value for period, value in group[asset].items()})
            row['annual'] = float((1 + group[asset]).prod() - 1)
            rows.append(row)
    return pd.DataFrame(rows).set_index(['year', 'asset'])
