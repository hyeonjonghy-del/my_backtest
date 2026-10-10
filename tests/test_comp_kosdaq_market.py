import pandas as pd
import pytest
from test_comp_market import market_data
from strategies.comp.strategy import CompConfig, backtest, kosdaq_exposure


def daily_data():
    data = market_data()
    days = pd.bdate_range('2025-04-09', '2025-04-15')
    data.prices = pd.concat([data.prices, pd.DataFrame([
        dict(date=d, ticker=t, open=100., close=100.) for d in days for t in data.features.ticker
    ])], ignore_index=True)
    data.benchmarks = pd.concat([data.benchmarks, pd.DataFrame(
        dict(date=days, KOSPI=100., KOSDAQ=200.))], ignore_index=True)
    data.benchmarks['KOSDAQ'] = 100.
    data.benchmarks.loc[data.benchmarks.date.between('2025-03-28', '2025-03-31'), 'KOSDAQ'] = 150.
    data.benchmarks.loc[data.benchmarks.date.between('2025-04-01', '2025-04-07'), 'KOSDAQ'] = 50.
    data.benchmarks.loc[data.benchmarks.date >= '2025-04-08', 'KOSDAQ'] = 200.
    data.metadata['end'] = '2025-04-15'
    data.metadata['signal_dates'].append('2025-04-11')
    return data


def test_daily_exit_and_reentry_next_open_with_last_scheduled_basket():
    data = daily_data()
    result = backtest(data, CompConfig(market_mode='kosdaq100'))
    sells = result['trades'].query("reason == 'market_cash'")
    assert len(sells) == 10 and sells.date.eq(pd.Timestamp('2025-04-02')).all()
    buys = result['trades'].query("side == 'buy'")
    assert len(buys) == 20
    assert buys.date.drop_duplicates().tolist() == [pd.Timestamp('2025-03-31'), pd.Timestamp('2025-04-09')]
    assert set(buys[buys.date == pd.Timestamp('2025-04-09')].ticker) == set(buys[buys.date == pd.Timestamp('2025-03-31')].ticker)
    assert result['equity'].loc['2025-04-02':'2025-04-08', 'cash_ratio'].eq(1).all()


def test_weak_start_stays_cash_and_missing_warmup_fails():
    data = daily_data()
    data.benchmarks.loc[data.benchmarks.date >= '2025-03-28', 'KOSDAQ'] = 50.
    result = backtest(data, CompConfig(market_mode='kosdaq100'))
    assert result['trades'].empty and result['metrics']['failed_buys'] == 0
    assert result['equity'].cash_ratio.eq(1).all()
    assert result['latest'].target_weight.sum() == 0
    assert result['market_signal_date'] == pd.Timestamp('2025-04-15')
    data.benchmarks = data.benchmarks.tail(30)
    with pytest.raises(ValueError, match='100'):
        backtest(data, CompConfig(market_mode='kosdaq100'))


def test_equal_to_mean_is_normal_and_future_index_cannot_change_past_signal():
    data = daily_data()
    days = pd.DatetimeIndex(data.benchmarks.query("date >= '2025-03-28'").date)
    old = kosdaq_exposure(data, days).loc[:'2025-04-07']
    data.benchmarks.loc[data.benchmarks.date > '2025-04-07', 'KOSDAQ'] = 10000.
    pd.testing.assert_frame_equal(kosdaq_exposure(data, days).loc[:'2025-04-07'], old)
    data.benchmarks['KOSDAQ'] = 100.
    assert kosdaq_exposure(data, days).exposure.eq(1).all()


def test_stock_stop_precedes_market_exit_and_missing_quote_delays_exit():
    data = daily_data()
    ticker = backtest(data, CompConfig())['trades'].query("side == 'buy'").ticker.iloc[0]
    data.prices.loc[(data.prices.date == pd.Timestamp('2025-04-01')) & (data.prices.ticker == ticker), 'close'] = 75.
    data.prices = data.prices[~((data.prices.date == pd.Timestamp('2025-04-02')) & (data.prices.ticker == ticker))]
    result = backtest(data, CompConfig(market_mode='kosdaq100'))
    tr = result['trades'].query("side == 'sell' and ticker == @ticker")
    assert tr.iloc[0].reason == 'close_stop_20pct'
    assert tr.iloc[0].date == pd.Timestamp('2025-04-03')
