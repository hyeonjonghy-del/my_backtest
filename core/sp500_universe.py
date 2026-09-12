"""Point-in-time S&P 500 membership reconstruction.

The current constituents and the historical changes now live on separate
Wikipedia pages.  This module validates both schemas and reconstructs the
membership after every effective-date change.  It deliberately fails closed
instead of substituting today's constituents for missing history.
"""

from __future__ import annotations

from collections import defaultdict
import io
from typing import Iterable

import pandas as pd
import requests


CURRENT_COMPONENTS_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
HISTORICAL_CHANGES_URL = "https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500"


class UniverseDataError(ValueError):
    """Raised when point-in-time membership cannot be verified."""


def _flatten_column(column: object) -> str:
    if isinstance(column, tuple):
        parts = [str(part).strip() for part in column if str(part).lower() != "nan"]
        return " ".join(dict.fromkeys(parts))
    return str(column).strip()


def _ticker(value: object) -> str:
    ticker = str(value).split("|", 1)[0].strip().upper().replace(".", "-")
    return "" if ticker.lower() in {"", "nan", "none", "-"} else ticker


def parse_current_components(table: pd.DataFrame) -> tuple[list[str], dict[str, str]]:
    frame = table.copy()
    frame.columns = [_flatten_column(column) for column in frame.columns]
    symbol_column = next(
        (column for column in frame.columns if "symbol" in column.lower() or "ticker" in column.lower()),
        None,
    )
    if symbol_column is None:
        raise UniverseDataError("현재 S&P500 표에서 종목코드 열을 찾지 못했습니다.")
    sector_column = next(
        (column for column in frame.columns if "gics sector" in column.lower()),
        None,
    )
    tickers = [_ticker(value) for value in frame[symbol_column]]
    tickers = list(dict.fromkeys(ticker for ticker in tickers if ticker))
    if not 490 <= len(tickers) <= 510:
        raise UniverseDataError(f"현재 S&P500 구성종목 수가 비정상입니다: {len(tickers)}개")
    sector_map: dict[str, str] = {}
    if sector_column is not None:
        for raw_ticker, raw_sector in zip(frame[symbol_column], frame[sector_column]):
            ticker = _ticker(raw_ticker)
            if ticker:
                sector_map[ticker] = str(raw_sector).strip()
    return tickers, sector_map


def parse_historical_changes(
    table: pd.DataFrame,
) -> list[tuple[pd.Timestamp, list[str], list[str]]]:
    frame = table.copy()
    frame.columns = [_flatten_column(column) for column in frame.columns]
    date_column = next((column for column in frame.columns if "date" in column.lower()), None)
    added_column = next(
        (column for column in frame.columns if "added" in column.lower() and "ticker" in column.lower()),
        None,
    )
    removed_column = next(
        (column for column in frame.columns if "removed" in column.lower() and "ticker" in column.lower()),
        None,
    )
    if not all((date_column, added_column, removed_column)):
        raise UniverseDataError("S&P500 변경표의 날짜/편입/편출 열을 찾지 못했습니다.")

    records: list[tuple[pd.Timestamp, list[str], list[str]]] = []
    for _, row in frame.iterrows():
        date = pd.to_datetime(row[date_column], errors="coerce")
        if pd.isna(date):
            continue
        added = _ticker(row[added_column])
        removed = _ticker(row[removed_column])
        if added or removed:
            records.append((date.normalize(), [added] if added else [], [removed] if removed else []))
    records.sort(key=lambda record: record[0])
    if len(records) < 300:
        raise UniverseDataError(f"S&P500 과거 변경 건수가 비정상적으로 적습니다: {len(records)}건")
    return records


def reconstruct_membership(
    current_tickers: Iterable[str],
    changes: Iterable[tuple[pd.Timestamp, list[str], list[str]]],
    start_date: pd.Timestamp,
    as_of: pd.Timestamp | None = None,
) -> dict[pd.Timestamp, list[str]]:
    """Return membership snapshots effective on each exact change date.

    ``current_tickers`` must represent membership at ``as_of``. Reconstruction
    proceeds backwards so ticker reuse and renames do not require a lossy
    forward replay.
    """
    start_date = pd.Timestamp(start_date).normalize()
    as_of = pd.Timestamp.today().normalize() if as_of is None else pd.Timestamp(as_of).normalize()
    current = {_ticker(ticker) for ticker in current_tickers if _ticker(ticker)}
    grouped: dict[pd.Timestamp, dict[str, set[str]]] = defaultdict(
        lambda: {"added": set(), "removed": set()}
    )
    for raw_date, added, removed in changes:
        date = pd.Timestamp(raw_date).normalize()
        if start_date < date <= as_of:
            grouped[date]["added"].update(_ticker(ticker) for ticker in added if _ticker(ticker))
            grouped[date]["removed"].update(_ticker(ticker) for ticker in removed if _ticker(ticker))

    state = set(current)
    timeline: dict[pd.Timestamp, list[str]] = {}
    for date in sorted(grouped, reverse=True):
        # At the effective date, the additions and removals have taken effect.
        if len(current) >= 100 and not 480 <= len(state) <= 520:
            raise UniverseDataError(
                f"{date.date()} 복원 유니버스 종목 수가 비정상입니다: {len(state)}개"
            )
        timeline[date] = sorted(state)
        state.difference_update(grouped[date]["added"])
        state.update(grouped[date]["removed"])
    timeline[start_date] = sorted(state)
    return timeline


def get_universe_at(date: pd.Timestamp, universe_dict: dict[pd.Timestamp, list[str]]) -> list[str]:
    decision_date = pd.Timestamp(date).normalize()
    valid_dates = [effective_date for effective_date in universe_dict if effective_date <= decision_date]
    return universe_dict[max(valid_dates)] if valid_dates else []


def load_point_in_time_universe(
    start_date: pd.Timestamp,
    as_of: pd.Timestamp | None = None,
    timeout: int = 20,
) -> tuple[dict[pd.Timestamp, list[str]], dict[str, str], dict[str, object]]:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; point-in-time-backtest/1.0)"}
    try:
        history_response = requests.get(HISTORICAL_CHANGES_URL, headers=headers, timeout=timeout)
        history_response.raise_for_status()
        history_tables = pd.read_html(io.StringIO(history_response.text))
    except Exception as exc:
        raise UniverseDataError(f"S&P500 구성 이력 다운로드 실패: {exc}") from exc
    if not history_tables:
        raise UniverseDataError("S&P500 구성 이력 표가 비어 있습니다.")

    changes = parse_historical_changes(history_tables[0])
    as_of_date = pd.Timestamp.today().normalize() if as_of is None else pd.Timestamp(as_of).normalize()
    applicable = [record for record in changes if record[0] <= as_of_date]
    if not applicable or applicable[-1][0] < as_of_date - pd.DateOffset(years=1):
        raise UniverseDataError("S&P500 변경 이력이 1년 이상 갱신되지 않아 안전하게 계산할 수 없습니다.")
    anchor_date = applicable[-1][0]

    # Wikipedia may update the live constituent table before an announced
    # change becomes effective. Anchor reconstruction to the last page revision
    # immediately after the latest *effective* change instead of today's page.
    revision_cutoff = (anchor_date + pd.Timedelta(days=2)).strftime("%Y-%m-%dT23:59:59Z")
    try:
        revision_response = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "prop": "revisions",
                "titles": "List of S&P 500 companies",
                "rvprop": "ids|timestamp",
                "rvlimit": 1,
                "rvstart": revision_cutoff,
                "rvdir": "older",
                "format": "json",
            },
            headers=headers,
            timeout=timeout,
        )
        revision_response.raise_for_status()
        page = next(iter(revision_response.json()["query"]["pages"].values()))
        revision = page["revisions"][0]
        revision_id = int(revision["revid"])
        anchor_response = requests.get(
            f"https://en.wikipedia.org/w/index.php?title=List_of_S%26P_500_companies&oldid={revision_id}",
            headers=headers,
            timeout=timeout,
        )
        anchor_response.raise_for_status()
        anchor_tables = pd.read_html(io.StringIO(anchor_response.text))
        current_response = requests.get(CURRENT_COMPONENTS_URL, headers=headers, timeout=timeout)
        current_response.raise_for_status()
        live_tables = pd.read_html(io.StringIO(current_response.text))
    except Exception as exc:
        raise UniverseDataError(f"기준일 구성종목 리비전 다운로드 실패: {exc}") from exc
    if not anchor_tables or not live_tables:
        raise UniverseDataError("기준일 또는 현재 구성종목 표가 비어 있습니다.")

    anchor_tickers, anchor_sector_map = parse_current_components(anchor_tables[0])
    _, live_sector_map = parse_current_components(live_tables[0])
    sector_map = {**anchor_sector_map, **live_sector_map}
    timeline = reconstruct_membership(anchor_tickers, applicable, start_date, anchor_date)
    all_tickers = {ticker for members in timeline.values() for ticker in members}
    audit = {
        "current_count": len(anchor_tickers),
        "change_count": len(applicable),
        "snapshot_count": len(timeline),
        "all_ticker_count": len(all_tickers),
        "latest_change_date": anchor_date,
        "anchor_revision_id": revision_id,
    }
    return timeline, sector_map, audit
