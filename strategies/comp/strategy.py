"""Offline COMP research engine. No credentials, account access, or order API."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import io
import json
import math
import zipfile

import numpy as np
import pandas as pd


FACTORS = ['r5', 'r20', 'r21', 'r60', 'r63', 'r125', 'r252', 'near_high',
           'institution', 'foreign', 'foreign_ratio', 'roe', 'per', 'op_previous', 'op_latest']


@dataclass(frozen=True)
class CompConfig:
    rebalance_weeks: int = 6
    top_n: int = 10
    per_mode: str = 'trailing'
    growth_policy: str = 'positive_base'
    sector_mode: str = 'all'
    allowed_sectors: tuple[str, ...] = ()
    max_per_sector: int | None = None
    stop_loss: float = .20
    cost_bps: float = 10.
    initial_capital: float = 100_000_000.
    start_date: str | None = None
    end_date: str | None = None
    fscore_mode: str = 'off'
    fscore_threshold: float = 3.
    market_mode: str = 'off'

    def __post_init__(self):
        if self.market_mode not in ('off', 'half'):
            raise ValueError('Invalid market exposure mode.')
        if self.fscore_mode not in ('off', 'complete_case', 'filter'):
            raise ValueError('Invalid F-SCORE mode.')
        if not math.isfinite(self.fscore_threshold) or not 0 <= self.fscore_threshold < 4:
            raise ValueError('F-SCORE threshold must be in [0, 4).')
        if self.rebalance_weeks not in (4, 6):
            raise ValueError('Use 4/6 weeks.')
        if isinstance(self.top_n, bool) or not isinstance(self.top_n, int) or self.top_n < 1:
            raise ValueError('Holding count must be a positive integer.')
        if self.per_mode not in ('trailing', 'none'):
            raise ValueError('Historical forward PER is not available in this model.')
        if self.growth_policy not in ('positive_base', 'absolute_base'):
            raise ValueError('Invalid growth policy.')
        if self.sector_mode not in ('all', 'selected'):
            raise ValueError('Invalid sector mode.')
        if self.sector_mode == 'selected' and not self.allowed_sectors:
            raise ValueError('Select at least one sector.')
        if self.max_per_sector is not None and not 1 <= self.max_per_sector <= self.top_n:
            raise ValueError('Invalid sector holding limit.')
        if not 0 < self.stop_loss < 1 or not math.isfinite(self.cost_bps) or not 0 <= self.cost_bps < 10000:
            raise ValueError('Invalid stop or trading cost.')
        if not math.isfinite(self.initial_capital) or self.initial_capital <= 0:
            raise ValueError('Initial capital must be positive.')
        for value in (self.start_date, self.end_date):
            if value is not None:
                parsed = pd.Timestamp(value)
                if pd.isna(parsed) or parsed.tzinfo is not None or parsed != parsed.normalize():
                    raise ValueError('Use daily dates without a timezone.')
        if self.start_date and self.end_date and pd.Timestamp(self.start_date) > pd.Timestamp(self.end_date):
            raise ValueError('Start date must not exceed end date.')

    @property
    def needs_sectors(self):
        return self.sector_mode != 'all' or self.max_per_sector is not None


@dataclass
class CompDataset:
    features: pd.DataFrame
    prices: pd.DataFrame
    benchmarks: pd.DataFrame
    metadata: dict
    sectors: pd.DataFrame | None = None
    fscore_scores: pd.DataFrame | None = None

    def validate(self):
        required = {'date', 'ticker', 'filed', *FACTORS}
        if not required.issubset(self.features.columns):
            raise ValueError('Incomplete feature schema.')
        if not {'date', 'ticker', 'open', 'close'}.issubset(self.prices.columns):
            raise ValueError('Incomplete price schema.')
        if not {'date', 'KOSPI', 'KOSDAQ'}.issubset(self.benchmarks.columns):
            raise ValueError('KOSPI/KOSDAQ trading calendar is required.')
        for frame in (self.features, self.prices, self.benchmarks):
            frame['date'] = pd.to_datetime(frame.date, errors='raise')
            if frame.date.isna().any() or not frame.date.eq(frame.date.dt.normalize()).all():
                raise ValueError('Dates must be nonempty daily timestamps.')
        self.features['filed'] = pd.to_datetime(self.features.filed, errors='raise')
        if not (self.features.filed < self.features.date).all():
            raise ValueError('Financial information must precede the signal day.')
        for frame in (self.features, self.prices):
            frame['ticker'] = frame.ticker.astype(str)
            if not frame.ticker.str.fullmatch(r'\d{6}').all() or frame.duplicated(['date', 'ticker']).any():
                raise ValueError('Invalid or duplicate stock/date records.')
        if self.benchmarks.date.duplicated().any():
            raise ValueError('Duplicate benchmark dates.')
        for column in FACTORS:
            self.features[column] = pd.to_numeric(self.features[column], errors='raise')
        for frame, columns in [(self.prices, ['open', 'close']), (self.benchmarks, ['KOSPI', 'KOSDAQ'])]:
            for column in columns:
                frame[column] = pd.to_numeric(frame[column], errors='raise')
                if not np.isfinite(frame[column]).all() or (frame[column] <= 0).any():
                    raise ValueError('Prices must be finite and positive.')
        start, end = pd.Timestamp(self.metadata['start']), pd.Timestamp(self.metadata['end'])
        if start > end or not self.features.date.between(start, end).all():
            raise ValueError('Invalid declared data window.')
        calendar = self.benchmarks.loc[self.benchmarks.date.between(start, end), 'date']
        if calendar.empty or calendar.min() != start or calendar.max() != end:
            raise ValueError('Benchmark calendar must cover the declared data window.')
        signals = pd.DatetimeIndex(self.metadata['signal_dates'])
        if signals.empty or signals.has_duplicates or not signals.is_monotonic_increasing or not signals.isin(calendar).all():
            raise ValueError('Invalid completed-week signal calendar.')
        if not self.features.date.isin(signals).all():
            raise ValueError('Features contain undeclared signal dates.')
        periods = signals.to_period('W-FRI')
        trading = pd.DatetimeIndex(calendar)
        last_sessions = pd.Series(trading, index=trading).groupby(trading.to_period('W-FRI')).max()
        if periods.has_duplicates or any(date != last_sessions.loc[period] for date, period in zip(signals, periods)):
            raise ValueError('Signals must be one completed market-week close each.')
        if self.sectors is not None:
            required = {'ticker', 'sector', 'effective_date', 'known_date'}
            if not required.issubset(self.sectors.columns):
                raise ValueError('Point-in-time sector dates are required.')
            self.sectors['ticker'] = self.sectors.ticker.astype(str)
            if not self.sectors.ticker.str.fullmatch(r'\d{6}').all():
                raise ValueError('Invalid sector stock codes.')
            for column in ('effective_date', 'known_date'):
                self.sectors[column] = pd.to_datetime(self.sectors[column], errors='raise')
                if self.sectors[column].isna().any():
                    raise ValueError('Missing sector dates.')
            if self.sectors.sector.isna().any() or self.sectors.sector.astype(str).str.strip().eq('').any():
                raise ValueError('Missing sector names.')
            if self.sectors.duplicated(['ticker', 'effective_date', 'known_date']).any():
                raise ValueError('Duplicate sector classification records.')
        if self.fscore_scores is not None:
            scores = self.fscore_scores
            if not {'date', 'ticker', 'fscore', 'fscore_filed', 'missing_reason'}.issubset(scores.columns):
                raise ValueError('Incomplete F-SCORE schema.')
            scores['date'] = pd.to_datetime(scores.date, errors='raise')
            scores['fscore_filed'] = pd.to_datetime(scores.fscore_filed, errors='raise')
            if scores.date.dt.tz is not None or scores.fscore_filed.dt.tz is not None:
                raise ValueError('F-SCORE dates must not contain a timezone.')
            scores['ticker'] = scores.ticker.astype(str)
            scores['fscore'] = pd.to_numeric(scores.fscore, errors='raise')
            if scores.duplicated(['date', 'ticker']).any() or not scores.ticker.str.fullmatch(r'\d{6}').all():
                raise ValueError('Invalid or duplicate F-SCORE stock/date records.')
            if not scores.date.isin(signals).all():
                raise ValueError('F-SCORE dates must be declared weekly signals.')
            valid = scores.fscore.notna()
            if not scores.loc[valid, 'fscore'].between(0, 4).all():
                raise ValueError('F-SCORE must be finite and in [0, 4].')
            if not (scores.loc[valid, 'fscore_filed'] < scores.loc[valid, 'date']).all():
                raise ValueError('F-SCORE disclosure must precede the signal day.')
            if scores.loc[~valid, 'missing_reason'].fillna('').eq('').any():
                raise ValueError('Unavailable F-SCORE requires an explicit missing reason.')
        return self


def load_bundle(content: bytes) -> CompDataset:
    if len(content) > 100 * 1024**2:
        raise ValueError('Bundle exceeds 100 MB.')
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as error:
        raise ValueError('Invalid ZIP bundle.') from error
    with archive:
        names = archive.namelist()
        allowed = {'metadata.json', 'features.csv', 'prices.csv', 'benchmarks.csv', 'sectors.csv', 'fscore.csv'}
        if len(names) != len(set(names)) or not set(names).issubset(allowed):
            raise ValueError('Unexpected or duplicate bundle entries.')
        if not (allowed - {'sectors.csv', 'fscore.csv'}).issubset(names):
            raise ValueError('Missing bundle entries.')
        if sum(item.file_size for item in archive.infolist()) > 500 * 1024**2:
            raise ValueError('Expanded bundle exceeds 500 MB.')
        if any(item.flag_bits & 1 for item in archive.infolist()):
            raise ValueError('Encrypted ZIP entries are not supported.')
        metadata = json.loads(archive.read('metadata.json'))
        if not isinstance(metadata, dict) or metadata.get('schema_version') != 1:
            raise ValueError('Unsupported COMP bundle schema.')
        if not {'start', 'end', 'signal_dates'}.issubset(metadata) or not isinstance(metadata['signal_dates'], list):
            raise ValueError('Incomplete bundle metadata.')
        def csv(name):
            return pd.read_csv(io.BytesIO(archive.read(name)), dtype={'ticker': str})
        return CompDataset(csv('features.csv'), csv('prices.csv'), csv('benchmarks.csv'), metadata,
                           csv('sectors.csv') if 'sectors.csv' in names else None,
                           csv('fscore.csv') if 'fscore.csv' in names else None).validate()


def percent_rank(values: pd.Series, lower_is_better=False):
    return 100 * (len(values) - values.rank(ascending=lower_is_better, method='average')) / len(values)


def sectors_asof(history: pd.DataFrame, date: pd.Timestamp):
    visible = history[(history.effective_date <= date) & (history.known_date < date)]
    return visible.sort_values(['effective_date', 'known_date']).drop_duplicates('ticker', keep='last').set_index('ticker').sector


def score_snapshot(features: pd.DataFrame, config: CompConfig, sectors: pd.Series | None = None):
    sample = features.copy().set_index('ticker').sort_index()
    sample = sample.replace([np.inf, -np.inf], np.nan).dropna(subset=FACTORS)
    sample = sample[sample.per.gt(0) & sample.foreign_ratio.between(0, 100)]
    denominator = sample.op_previous if config.growth_policy == 'positive_base' else sample.op_previous.abs()
    sample['op_growth'] = (sample.op_latest - sample.op_previous) / denominator.where(denominator > 0)
    sample = sample.dropna(subset=['op_growth'])
    if config.needs_sectors:
        if sectors is None:
            raise ValueError('Historical sector classifications are required.')
        sample['sector'] = sectors.reindex(sample.index)
        if sample.sector.isna().any():
            raise ValueError('Sector coverage is incomplete for the signal universe.')
        if config.sector_mode == 'selected':
            sample = sample[sample.sector.isin(config.allowed_sectors)]
    else:
        sample['sector'] = 'NOT_APPLIED'
    if sample.empty:
        sample['comp'] = pd.Series(dtype=float)
        return sample
    sample['rank_momentum'] = sum(percent_rank(sample[f'r{n}']) for n in (20, 60, 125, 252)) / 4
    sample['smart_money'] = sum(percent_rank(sample[col]) for col in ('institution', 'foreign', 'foreign_ratio')) / 3
    sample['price_momentum'] = sum(percent_rank(sample[col]) for col in ('r5', 'r21', 'r63', 'near_high')) / 4
    valuation = ['roe', 'op_growth'] + (['per'] if config.per_mode == 'trailing' else [])
    sample['valuation'] = sum(percent_rank(sample[col], col == 'per') for col in valuation) / len(valuation)
    sample['comp'] = .4 * sample.rank_momentum + .3 * sample.smart_money + .2 * sample.price_momentum + .1 * sample.valuation
    return sample.sort_values('comp', ascending=False)


def select_targets(ranked: pd.DataFrame, config: CompConfig):
    target, counts = [], {}
    for ticker, row in ranked.iterrows():
        if config.max_per_sector is not None and counts.get(row.sector, 0) >= config.max_per_sector:
            continue
        target.append(ticker)
        counts[row.sector] = counts.get(row.sector, 0) + 1
        if len(target) == config.top_n:
            break
    return target


def apply_fscore(ranked, scores, config):
    """Filter after COMP scoring; unknown candidates cannot be silently excluded."""
    from .fscore import filter_by_fscore, rank_with_fscore
    scores = scores.drop(columns='date', errors='ignore').set_index('ticker')
    filtered = (filter_by_fscore(ranked, scores, config.fscore_threshold)
                if config.fscore_mode == 'filter' else rank_with_fscore(ranked, scores, reorder=False))
    selected = select_targets(filtered, config)
    required = ranked.index
    if len(selected) >= config.top_n:
        required = ranked.index[:ranked.index.get_loc(selected[-1])+1]
    if not set(required).issubset(scores.index):
        raise ValueError('현재 설정의 상위 후보 F-SCORE가 미확인입니다. 누락 후보를 제외해 계산하지 않습니다. 기간·보유 수·순위 설정을 확인하세요.')
    return filtered


def market_exposure(dataset, signal_dates):
    """Past-only 200-session KOSPI mean; confirm on two original weekly closes."""
    index = dataset.benchmarks.sort_values('date').set_index('date').KOSPI
    average = index.rolling(200, min_periods=200).mean()
    original = pd.DatetimeIndex(dataset.metadata['signal_dates'])
    weekly = pd.DataFrame({'KOSPI': index.reindex(original), 'ma200': average.reindex(original)})
    below = weekly.KOSPI.lt(weekly.ma200)
    weekly['exposure'] = np.where(below & below.shift(1, fill_value=False), .5, 1.)
    selected = weekly.reindex(signal_dates)
    if selected.ma200.isna().any():
        raise ValueError('시장 비중 조절에는 시작일 이전 200거래일 지수가 필요합니다. 시장 비중 조절용 ZIP을 업로드하세요.')
    return selected


def backtest(dataset: CompDataset, config: CompConfig):
    dataset.validate()
    if config.needs_sectors and dataset.sectors is None:
        raise ValueError('Sector options require historical sector classifications.')
    if config.fscore_mode != 'off' and dataset.fscore_scores is None:
        raise ValueError('F-SCORE가 포함된 데이터 ZIP을 업로드하세요.')
    available_start, available_end = pd.Timestamp(dataset.metadata['start']), pd.Timestamp(dataset.metadata['end'])
    start = pd.Timestamp(config.start_date) if config.start_date else available_start
    requested_end = pd.Timestamp(config.end_date) if config.end_date else available_end
    if start < available_start:
        raise ValueError('Requested start precedes available data.')
    end = min(requested_end, available_end)
    calendar = pd.DatetimeIndex(dataset.benchmarks.sort_values('date').date)
    calendar = calendar[(calendar >= start) & (calendar <= end)]
    if calendar.empty:
        raise ValueError('No available trading days in the selected period.')
    signal_dates = pd.DatetimeIndex(dataset.metadata['signal_dates'])
    signal_dates = signal_dates[(signal_dates >= calendar[0]) & (signal_dates <= calendar[-1])]
    if signal_dates.empty:
        raise ValueError('No completed weekly signals in the selected period.')
    scheduled = set(signal_dates[::config.rebalance_weeks])
    market = market_exposure(dataset, signal_dates) if config.market_mode == 'half' else None
    exposure = 1.
    scores, targets = {}, {}
    groups = {date: group for date, group in dataset.features.groupby('date', sort=False)}
    fscore_groups = ({date: group for date, group in dataset.fscore_scores.groupby('date', sort=False)}
                     if dataset.fscore_scores is not None else {})
    for date in signal_dates:
        frame = groups.get(date, dataset.features.iloc[:0])
        sectors = sectors_asof(dataset.sectors, date) if config.needs_sectors else None
        scores[date] = score_snapshot(frame, config, sectors)
        if config.fscore_mode != 'off' and date in scheduled:
            if date not in fscore_groups:
                raise ValueError(f'{date.date()}의 F-SCORE 자료가 없습니다. 현재 데이터에서 검증 가능한 교체 시작일을 선택하세요.')
            scores[date] = apply_fscore(scores[date], fscore_groups[date], config)
        targets[date] = select_targets(scores[date], config)
    # Only scheduled targets can ever be held. Keep their entire in-window
    # history, including stopped/missing-quote days and delayed exits.
    traded_tickers = {ticker for date in scheduled for ticker in targets[date]}
    trade_prices = dataset.prices.loc[dataset.prices.ticker.isin(traded_tickers) &
                                     dataset.prices.date.between(calendar[0], calendar[-1]),
                                     ['date', 'ticker', 'open', 'close']]
    quotes = {(row.date, row.ticker): (row.open, row.close) for row in trade_prices.itertuples()}
    cash, holdings, pending_sell, pending_buy = config.initial_capital, {}, {}, []
    fee = config.cost_bps / 10000
    equity, trades = [], []
    stale_days, failed_buys = 0, 0
    for date in calendar:
        for ticker in list(pending_sell):
            quote = quotes.get((date, ticker))
            if quote is None:
                continue
            fraction, reason = pending_sell.pop(ticker)
            holding = holdings[ticker]
            sold = holding['shares'] * fraction
            gross = sold * quote[0]
            cash += gross * (1 - fee)
            holding['shares'] -= sold
            if fraction == 1:
                del holdings[ticker]
            trades.append({'date': date, 'ticker': ticker, 'side': 'sell', 'value': gross, 'reason': reason})
        budget = cash / len(pending_buy) if pending_buy else 0.
        for ticker in pending_buy:
            quote = quotes.get((date, ticker))
            if quote is None or ticker in holdings or len(holdings) >= config.top_n:
                failed_buys += 1
                continue
            spend = min(cash, budget * exposure)
            if spend <= 0:
                continue
            shares = spend / (quote[0] * (1 + fee))
            cash -= spend
            holdings[ticker] = {'shares': shares, 'entry': quote[0], 'last': quote[0], 'market_exposure': exposure}
            trades.append({'date': date, 'ticker': ticker, 'side': 'buy', 'value': shares * quote[0], 'reason': 'rebalance'})
        pending_buy = []
        for ticker, holding in holdings.items():
            quote = quotes.get((date, ticker))
            if quote is None:
                stale_days += 1
                continue
            holding['last'] = quote[1]
            if quote[1] / holding['entry'] - 1 <= -config.stop_loss:
                pending_sell[ticker] = (1., 'close_stop_20pct' if config.stop_loss == .20 else 'close_stop')
        nav = cash + sum(h['shares'] * h['last'] for h in holdings.values())
        equity.append({'date': date, 'nav': nav, 'cash_ratio': cash / nav, 'positions': len(holdings)})
        if market is not None and date in signal_dates:
            exposure = float(market.loc[date, 'exposure'])
            for ticker, holding in holdings.items():
                if exposure < holding['market_exposure']:
                    if ticker not in pending_sell:
                        pending_sell[ticker] = (1 - exposure / holding['market_exposure'], 'market_exposure_half')
                    holding['market_exposure'] = exposure
        if date in scheduled:
            for ticker in holdings:
                if config.market_mode == 'off' or ticker not in pending_sell or pending_sell[ticker][0] < 1:
                    pending_sell[ticker] = (1., 'scheduled_rebalance')
            pending_buy = targets[date]
    curve = pd.DataFrame(equity).set_index('date')
    benchmark = dataset.benchmarks.set_index('date').reindex(calendar)
    for symbol in ('KOSPI', 'KOSDAQ'):
        curve[symbol] = config.initial_capital * benchmark[symbol] / benchmark[symbol].iloc[0]
    values = curve[['nav', 'KOSPI', 'KOSDAQ']].rename(columns={'nav': 'COMP'})
    drawdown = values / values.cummax() - 1
    daily = curve.nav.pct_change().fillna(0)
    from .reporting import cagr
    annualized = cagr(curve)
    metrics = {'return': float(curve.nav.iloc[-1] / curve.nav.iloc[0] - 1),
               'start': str(calendar[0].date()), 'end': str(calendar[-1].date()),
               'requested_start': str(start.date()), 'requested_end': str(requested_end.date()),
               'available_end': str(available_end.date()), 'end_limited_by_data': requested_end > available_end,
               'mdd': float(drawdown.COMP.min()),
               'cagr': float(annualized.COMP),
               'average_cash': float(curve.cash_ratio.mean()), 'trades': len(trades),
               'stale_position_days': stale_days, 'failed_buys': failed_buys,
               'annualized_volatility': float(daily.std() * np.sqrt(252)), 'config': asdict(config)}
    for symbol in ('KOSPI', 'KOSDAQ'):
        metrics[symbol.lower() + '_return'] = float(curve[symbol].iloc[-1] / curve[symbol].iloc[0] - 1)
        metrics[symbol.lower() + '_mdd'] = float(drawdown[symbol].min())
        metrics[symbol.lower() + '_cagr'] = float(annualized[symbol])
    latest_date = signal_dates[-1] if config.fscore_mode == 'off' else max(scheduled)
    latest = scores[latest_date].copy()
    latest['selected'] = latest.index.isin(targets[latest_date])
    latest['target_weight'] = [1 / len(targets[latest_date]) if ticker in targets[latest_date] else 0 for ticker in latest.index]
    if market is not None:
        latest['target_weight'] *= float(market.loc[latest_date, 'exposure'])
    return {'equity': curve, 'drawdown': drawdown,
            'trades': pd.DataFrame(trades, columns=['date', 'ticker', 'side', 'value', 'reason']),
            'metrics': metrics, 'latest': latest, 'signal_date': latest_date,
            'latest_scheduled': latest_date in scheduled, 'scores': scores, 'market': market}
