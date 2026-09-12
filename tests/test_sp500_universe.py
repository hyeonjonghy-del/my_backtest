import pandas as pd

from core.sp500_universe import (
    UniverseDataError,
    get_universe_at,
    parse_historical_changes,
    reconstruct_membership,
)


def test_reconstructs_exact_effective_date_membership() -> None:
    current = ["AAA", "CCC", "DDD"]
    changes = [
        (pd.Timestamp("2020-02-15"), ["CCC"], ["BBB"]),
        (pd.Timestamp("2020-04-10"), ["DDD"], []),
    ]
    timeline = reconstruct_membership(
        current, changes, pd.Timestamp("2020-01-01"), pd.Timestamp("2020-05-01")
    )

    assert get_universe_at(pd.Timestamp("2020-02-14"), timeline) == ["AAA", "BBB"]
    assert get_universe_at(pd.Timestamp("2020-02-15"), timeline) == ["AAA", "CCC"]
    assert get_universe_at(pd.Timestamp("2020-04-10"), timeline) == ["AAA", "CCC", "DDD"]


def test_change_parser_rejects_navigation_table() -> None:
    navigation = pd.DataFrame({"page": ["S&P 500"], "link": ["Companies"]})
    try:
        parse_historical_changes(navigation)
    except UniverseDataError:
        return
    raise AssertionError("navigation table must not be accepted as historical changes")
