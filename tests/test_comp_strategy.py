import io
import json
import unittest
import zipfile

import pandas as pd

from strategies.comp.strategy import (FACTORS, CompConfig, CompDataset, backtest,
                                      load_bundle, percent_rank, score_snapshot, sectors_asof, select_targets)


def features(date='2025-04-04'):
    rows = []
    for i in range(12):
        row = {'date': pd.Timestamp(date), 'ticker': f'{i+1:06d}', 'filed': pd.Timestamp('2025-03-01')}
        row.update({factor: float(i+1) for factor in FACTORS})
        row.update({'per': 20., 'op_previous': 10., 'op_latest': 10. + i, 'roe': .1 + i/100})
        rows.append(row)
    return pd.DataFrame(rows)


def dataset():
    days = pd.DatetimeIndex(['2025-04-04', '2025-04-07', '2025-04-08'])
    frame = features()
    price = pd.DataFrame([{'date': date, 'ticker': ticker, 'open': 100., 'close': 100.}
                          for date in days for ticker in frame.ticker])
    return CompDataset(frame, price, pd.DataFrame({'date': days, 'KOSPI': [100.] * 3, 'KOSDAQ': [100.] * 3}),
                       {'schema_version': 1, 'start': '2025-04-04', 'end': '2025-04-08', 'signal_dates': ['2025-04-04']})


class CompTests(unittest.TestCase):
    def test_configuration_rejects_unsupported_options(self):
        for change in [{'rebalance_weeks': 5}, {'top_n': 20}, {'per_mode': 'forward'},
                       {'sector_mode': 'selected'}, {'max_per_sector': 0}, {'cost_bps': float('nan')}]:
            with self.assertRaises(ValueError):
                CompConfig(**change)

    def test_book_rank_direction_ties_and_bounds(self):
        values = pd.Series([1., 2., 2., 4.])
        self.assertEqual(percent_rank(values).tolist(), [0., 37.5, 37.5, 75.])
        self.assertEqual(percent_rank(values, True).iloc[0], 75.)

    def test_growth_policy_includes_turnarounds_only_in_alternative(self):
        frame = features()
        frame.loc[0, 'op_previous'] = -10.
        self.assertNotIn('000001', score_snapshot(frame, CompConfig()).index)
        self.assertIn('000001', score_snapshot(frame, CompConfig(growth_policy='absolute_base')).index)

    def test_sector_caps_are_independent_of_sector_filter(self):
        frame = features()
        mapping = pd.Series({f'{i+1:06d}': 'A' if i < 6 else 'B' for i in range(12)})
        config = CompConfig(max_per_sector=3)
        ranked = score_snapshot(frame, config, mapping)
        selected = select_targets(ranked, config)
        self.assertEqual(len(selected), 6)
        self.assertEqual(ranked.loc[selected].sector.value_counts().max(), 3)
        config = CompConfig(sector_mode='selected', allowed_sectors=('B',))
        self.assertEqual(set(score_snapshot(frame, config, mapping).sector), {'B'})

    def test_sector_settings_fail_closed_without_complete_history(self):
        with self.assertRaises(ValueError):
            backtest(dataset(), CompConfig(max_per_sector=3))
        with self.assertRaises(ValueError):
            score_snapshot(features(), CompConfig(max_per_sector=3), pd.Series({'000001': 'A'}))

    def test_no_future_sector_information(self):
        history = pd.DataFrame({'ticker': ['000001'] * 3, 'sector': ['old', 'future', 'same_day'],
                                'effective_date': pd.to_datetime(['2024-01-01', '2025-01-01', '2025-01-01']),
                                'known_date': pd.to_datetime(['2024-01-01', '2025-05-01', '2025-04-04'])})
        self.assertEqual(sectors_asof(history, pd.Timestamp('2025-04-04')).iloc[0], 'old')

    def test_next_open_and_equal_weight_allocation(self):
        data = dataset()
        data.prices.loc[data.prices.date.eq(pd.Timestamp('2025-04-07')), 'open'] = 150.
        result = backtest(data, CompConfig())
        buys = result['trades'].query("side == 'buy'")
        self.assertTrue(buys.date.eq(pd.Timestamp('2025-04-07')).all())
        self.assertEqual(len(buys), 10)
        self.assertLess(buys.value.max() - buys.value.min(), .01)
        self.assertEqual(result['equity'].positions.iloc[0], 0)

    def test_four_and_six_week_execution_calendars_differ(self):
        days = pd.bdate_range('2025-04-04', periods=42)
        signals = days[days.weekday == 4]
        frame = pd.concat([features(str(date.date())) for date in signals], ignore_index=True)
        prices = pd.DataFrame([{'date': date, 'ticker': ticker, 'open': 100., 'close': 100.}
                              for date in days for ticker in features().ticker])
        data = CompDataset(frame, prices, pd.DataFrame({'date': days, 'KOSPI': 100., 'KOSDAQ': 100.}),
                           {'start': str(days[0].date()), 'end': str(days[-1].date()),
                            'signal_dates': [str(date.date()) for date in signals]})
        for interval in (4, 6):
            result = backtest(data, CompConfig(rebalance_weeks=interval))
            buys = result['trades'].query("side == 'buy'").date.unique()
            expected = [days[days.get_loc(date) + 1] for date in signals[::interval] if days.get_loc(date) + 1 < len(days)]
            self.assertEqual(list(buys), expected)

    def test_stop_waits_for_next_open_and_holds_cash(self):
        data = dataset()
        extra = pd.DataFrame([{'date': pd.Timestamp('2025-04-09'), 'ticker': ticker, 'open': 70., 'close': 70.}
                              for ticker in data.features.ticker])
        data.prices = pd.concat([data.prices, extra], ignore_index=True)
        data.prices.loc[data.prices.date.eq(pd.Timestamp('2025-04-08')), 'close'] = 79.
        data.benchmarks.loc[len(data.benchmarks)] = [pd.Timestamp('2025-04-09'), 100., 100.]
        data.metadata['end'] = '2025-04-09'
        result = backtest(data, CompConfig())
        sells = result['trades'].query("side == 'sell'")
        self.assertEqual(len(sells), 10)
        self.assertTrue(sells.date.eq(pd.Timestamp('2025-04-09')).all())
        self.assertEqual(result['equity'].cash_ratio.iloc[-1], 1.)

    def test_missing_prices_are_reported_not_fabricated(self):
        data = dataset()
        data.prices = data.prices[~data.prices.date.eq(pd.Timestamp('2025-04-08'))]
        result = backtest(data, CompConfig())
        self.assertEqual(result['metrics']['stale_position_days'], 10)

    def test_drawdowns_use_each_series_own_running_peak(self):
        data = dataset()
        data.benchmarks['KOSPI'] = [100., 120., 90.]
        data.benchmarks['KOSDAQ'] = [100., 80., 100.]
        result = backtest(data, CompConfig())
        drawdown = result['drawdown']
        self.assertEqual(list(drawdown.columns), ['COMP', 'KOSPI', 'KOSDAQ'])
        self.assertTrue(drawdown.index.equals(result['equity'].index))
        self.assertEqual(drawdown.KOSPI.tolist(), [0., 0., -.25])
        self.assertAlmostEqual(drawdown.KOSDAQ.iloc[1], -.20)
        self.assertEqual(drawdown.KOSDAQ.iloc[-1], 0.)
        self.assertEqual(result['metrics']['kospi_mdd'], -.25)
        self.assertAlmostEqual(result['metrics']['kosdaq_mdd'], -.20)
        pd.testing.assert_series_equal(drawdown.COMP,
                                      (result['equity'].nav / result['equity'].nav.cummax() - 1).rename('COMP'))
        clipped = backtest(data, CompConfig(end_date='2025-04-07'))
        self.assertEqual(clipped['metrics']['kospi_mdd'], 0.)
        self.assertAlmostEqual(clipped['metrics']['kosdaq_mdd'], -.20)

    def test_same_day_financials_rejected(self):
        data = dataset()
        data.features['filed'] = data.features.date
        with self.assertRaises(ValueError):
            backtest(data, CompConfig())

    def test_duplicate_prices_rejected(self):
        data = dataset()
        data.prices = pd.concat([data.prices, data.prices.iloc[:1]])
        with self.assertRaises(ValueError):
            data.validate()

    def test_end_date_reports_actual_coverage_without_extending_nav(self):
        result = backtest(dataset(), CompConfig(end_date='2025-04-30'))
        self.assertTrue(result['metrics']['end_limited_by_data'])
        self.assertEqual(result['metrics']['requested_end'], '2025-04-30')
        self.assertEqual(result['metrics']['end'], '2025-04-08')
        self.assertEqual(result['equity'].index[-1], pd.Timestamp('2025-04-08'))

    def test_selected_period_excludes_later_prices_and_signals(self):
        data = dataset()
        result = backtest(data, CompConfig(end_date='2025-04-07'))
        self.assertFalse(result['metrics']['end_limited_by_data'])
        self.assertEqual(len(result['equity']), 2)
        self.assertEqual(result['signal_date'], pd.Timestamp('2025-04-04'))
        with self.assertRaises(ValueError):
            backtest(data, CompConfig(start_date='2025-04-08'))
        with self.assertRaises(ValueError):
            backtest(data, CompConfig(start_date='2025-03-01'))
        with self.assertRaises(ValueError):
            CompConfig(start_date='2025-04-10', end_date='2025-04-01')

    def test_bundle_schema_and_path_safety(self):
        data = dataset()
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as archive:
            archive.writestr('metadata.json', json.dumps(data.metadata))
            for name, frame in [('features.csv', data.features), ('prices.csv', data.prices), ('benchmarks.csv', data.benchmarks)]:
                archive.writestr(name, frame.to_csv(index=False))
        loaded = load_bundle(out.getvalue())
        self.assertEqual(loaded.features.ticker.iloc[0], '000001')
        bad = io.BytesIO()
        with zipfile.ZipFile(bad, 'w') as archive:
            archive.writestr('../credentials.json', '{}')
        with self.assertRaises(ValueError):
            load_bundle(bad.getvalue())
        with self.assertRaises(ValueError):
            load_bundle(b'not a zip')


if __name__ == '__main__':
    unittest.main()
