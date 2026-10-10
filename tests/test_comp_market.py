import pandas as pd
import pytest
from test_comp_strategy import dataset
from strategies.comp.strategy import CompConfig, backtest, market_exposure


def test_default_off_preserves_legacy_and_bad_mode_rejected():
    data = dataset()
    pd.testing.assert_frame_equal(backtest(data, CompConfig())['equity'],
                                  backtest(data, CompConfig(market_mode='off'))['equity'])
    with pytest.raises(ValueError): CompConfig(market_mode='unknown')
    with pytest.raises(ValueError, match='200'): backtest(data, CompConfig(market_mode='half'))


def market_data():
    data = dataset()
    days = pd.bdate_range(end='2025-04-08', periods=250)
    signal = pd.Timestamp('2025-04-04')
    prior = pd.Timestamp('2025-03-28')
    data.metadata['start'] = str(prior.date())
    data.metadata['signal_dates'] = [str(prior.date()), str(signal.date())]
    data.features.loc[:, 'date'] = prior
    data.prices = pd.DataFrame([dict(date=d, ticker=t, open=100., close=100.)
        for d in pd.bdate_range(prior, '2025-04-08') for t in data.features.ticker])
    close = [100. if d < prior else 50. for d in days]
    data.benchmarks = pd.DataFrame(dict(date=days, KOSPI=close, KOSDAQ=100.))
    return data


def test_two_week_confirmation_uses_original_calendar_and_past_only():
    data = market_data()
    signals = pd.DatetimeIndex(data.metadata['signal_dates'])
    result = market_exposure(data, signals)
    assert result.exposure.tolist() == [1., .5]
    # Starting at the second signal still retains its prior-week confirmation.
    assert market_exposure(data, signals[-1:]).exposure.iloc[0] == .5
    data.benchmarks.loc[data.benchmarks.date > signals[-1], 'KOSPI'] = 10000.
    pd.testing.assert_frame_equal(market_exposure(data, signals), result)


def test_half_sale_next_available_open_and_no_repeated_cut():
    data = market_data()
    cfg = CompConfig(market_mode='half')
    result = backtest(data, cfg)
    buys = result['trades'].query("side == 'buy'")
    sells = result['trades'].query("reason == 'market_exposure_half'")
    assert len(sells) == 10
    assert sells.date.eq(pd.Timestamp('2025-04-07')).all()
    assert sells.value.sum() == pytest.approx(buys.value.sum() * .5)
    assert result['equity'].iloc[-1].cash_ratio > .49
    ticker = sells.iloc[0].ticker
    data.prices = data.prices[~((data.prices.date == pd.Timestamp('2025-04-07')) & (data.prices.ticker == ticker))]
    delayed = backtest(data, cfg)['trades']
    assert delayed.query("reason == 'market_exposure_half' and ticker == @ticker").date.iloc[0] == pd.Timestamp('2025-04-08')


def test_start_in_risk_regime_invests_half_and_ranking_weights_match():
    data = market_data()
    extra = data.features.copy()
    extra['date'] = pd.Timestamp('2025-04-04')
    data.features = pd.concat([data.features, extra], ignore_index=True)
    result = backtest(data, CompConfig(market_mode='half', start_date='2025-04-04'))
    assert result['trades'].query("side == 'buy'").value.sum() == pytest.approx(50_000_000 / 1.001)
    assert result['latest'].target_weight.sum() == pytest.approx(.5)


@pytest.mark.parametrize('recover', [False, True])
def test_repeated_weak_week_or_recovery_does_not_trade_again_between_rebalances(recover):
    data = market_data()
    days = pd.bdate_range('2025-04-09', '2025-04-15')
    data.prices = pd.concat([data.prices, pd.DataFrame([dict(date=d,ticker=t,open=100.,close=100.)
        for d in days for t in data.features.ticker])], ignore_index=True)
    data.benchmarks = pd.concat([data.benchmarks,pd.DataFrame(dict(date=days,KOSPI=200. if recover else 50.,KOSDAQ=100.))], ignore_index=True)
    data.metadata['end']='2025-04-15'
    data.metadata['signal_dates'].append('2025-04-11')
    result = backtest(data, CompConfig(market_mode='half'))
    assert len(result['trades'].query("reason == 'market_exposure_half'")) == 10
    assert len(result['trades'].query("side == 'buy'")) == 10
