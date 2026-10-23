import numpy as np
import pandas as pd

from strategies.samsung_electronics_trend_vol.reporting import calendar_return_table


def test_compounds_first_day_costs_and_partial_years():
    daily = pd.Series(
        [-0.01, 0.1, -0.1, 0.2, 0.0],
        index=pd.to_datetime(["2024-11-15", "2024-11-29", "2024-12-31", "2025-01-02", "2025-03-03"]),
    )
    table = calendar_return_table(daily)
    assert list(table.index) == [2025, 2024]
    assert np.isclose(table.loc[2024, "11월"], 0.99 * 1.1 - 1)
    assert np.isclose(table.loc[2024, "연간 수익률"], 0.99 * 1.1 * 0.9 - 1)
    assert np.isclose(table.loc[2025, "연간 수익률"], 0.2)
    assert pd.isna(table.loc[2025, "2월"])
    assert table.loc[2025, "3월"] == 0.0
    assert list(table.columns) == [f"{month}월" for month in range(1, 13)] + ["연간 수익률"]
    for year, row in table.iterrows():
        assert np.isclose(row.iloc[:12].dropna().add(1).prod() - 1, row["연간 수익률"])


def test_empty_report_has_all_columns():
    table = calendar_return_table(pd.Series(dtype=float, index=pd.DatetimeIndex([])))
    assert table.empty
    assert table.index.name == "연도"
    assert len(table.columns) == 13
