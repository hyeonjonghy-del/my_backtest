import unittest

import pandas as pd

from strategies.comp.reporting import monthly_performance, annual_performance, cagr


class ReportingTests(unittest.TestCase):
    def test_cagr_uses_elapsed_calendar_years_for_all_assets(self):
        curve = pd.DataFrame({'nav': [100., 121.], 'KOSPI': [100., 81.], 'KOSDAQ': [100., 100.]},
                             index=pd.to_datetime(['2024-01-01', '2026-01-01']))
        years = 731 / 365.25
        result = cagr(curve)
        self.assertAlmostEqual(result.COMP, 1.21 ** (1 / years) - 1)
        self.assertAlmostEqual(result.KOSPI, .81 ** (1 / years) - 1)
        self.assertEqual(result.KOSDAQ, 0.)
        self.assertTrue(cagr(curve.iloc[:1]).isna().all())

    def test_annual_comparison_includes_prior_year_end_and_compounds_to_total(self):
        curve = pd.DataFrame({'nav': [100., 80., 120., 132.], 'KOSPI': [100., 110., 99., 99.],
                              'KOSDAQ': [100., 100., 100., 110.]},
                             index=pd.to_datetime(['2024-08-01','2024-12-30','2025-12-30','2026-10-08']))
        table = annual_performance(curve)
        self.assertEqual(table.columns.tolist(), ['COMP','KOSPI','KOSDAQ'])
        self.assertAlmostEqual(table.loc[2024,'COMP'], -.2)
        self.assertAlmostEqual(table.loc[2025,'COMP'], .5)
        self.assertAlmostEqual(table.loc[2026,'COMP'], .1)
        for asset, column in [('COMP','nav'),('KOSPI','KOSPI'),('KOSDAQ','KOSDAQ')]:
            self.assertAlmostEqual((1 + table[asset]).prod(),curve[column].iloc[-1]/curve[column].iloc[0])
    def test_months_compound_across_year_boundary_with_partial_periods(self):
        curve = pd.DataFrame({'nav': [100., 110., 88., 132., 145.2],
                              'KOSPI': [100., 100., 110., 110., 99.],
                              'KOSDAQ': [100., 90., 90., 81., 81.]},
                             index=pd.to_datetime(['2025-11-14', '2025-11-28', '2025-12-30',
                                                   '2026-01-30', '2026-02-06']))
        table = monthly_performance(curve)
        self.assertEqual(table.columns.tolist(), [*range(1, 13), 'annual'])
        self.assertAlmostEqual(table.loc[(2025, 'COMP'), 11], .10)
        self.assertAlmostEqual(table.loc[(2025, 'COMP'), 12], -.20)
        self.assertAlmostEqual(table.loc[(2025, 'COMP'), 'annual'], -.12)
        self.assertAlmostEqual(table.loc[(2026, 'COMP'), 1], .50)
        self.assertAlmostEqual(table.loc[(2026, 'COMP'), 2], .10)
        self.assertAlmostEqual(table.loc[(2026, 'COMP'), 'annual'], .65)
        self.assertTrue(pd.isna(table.loc[(2025, 'COMP'), 1]))
        self.assertTrue(pd.isna(table.loc[(2026, 'COMP'), 12]))
        for asset, column in [('COMP', 'nav'), ('KOSPI', 'KOSPI'), ('KOSDAQ', 'KOSDAQ')]:
            annual = table.xs(asset, level='asset')['annual']
            self.assertAlmostEqual((1 + annual).prod() - 1, curve[column].iloc[-1] / curve[column].iloc[0] - 1)

    def test_single_day_has_zero_return_and_other_months_are_blank(self):
        curve = pd.DataFrame({'nav': [100.], 'KOSPI': [120.], 'KOSDAQ': [90.]},
                             index=pd.to_datetime(['2026-10-08']))
        table = monthly_performance(curve)
        self.assertTrue(table[10].eq(0).all())
        self.assertTrue(table.annual.eq(0).all())
        self.assertTrue(table[9].isna().all())

    def test_zero_nonfinite_and_duplicate_values_rejected(self):
        curve = pd.DataFrame({'nav': [100.], 'KOSPI': [120.], 'KOSDAQ': [90.]},
                             index=pd.to_datetime(['2026-10-08']))
        for value in [0., float('nan'), float('inf')]:
            broken = curve.copy()
            broken['nav'] = value
            with self.assertRaises(ValueError):
                monthly_performance(broken)
        with self.assertRaises(ValueError):
            monthly_performance(pd.concat([curve, curve]))


if __name__ == '__main__':
    unittest.main()
