"""Optional private-data regression against a previously verified research summary."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from strategies.comp import CompConfig, backtest, load_bundle


def verify(bundle, reference):
    data = load_bundle(bundle.read_bytes())
    summary = json.loads(reference.read_text(encoding='utf-8'))
    for expected in summary['metrics']:
        config = CompConfig(rebalance_weeks=expected['rebalance_weeks'], top_n=expected['top_n'],
                            growth_policy=expected['growth_policy'],
                            per_mode='trailing' if expected['variant'].startswith('with_per') else 'none')
        actual = backtest(data, config)['metrics']
        for name in ('return', 'mdd', 'average_cash'):
            if abs(actual[name] - expected[name]) > 1e-9:
                raise AssertionError(f"{expected['variant']} {name}: {actual[name]} != {expected[name]}")
        if actual['trades'] != expected['trades'] or actual['stale_position_days'] != expected['stale_position_days']:
            raise AssertionError('Execution diagnostics mismatch.')
        print('PASS ' + expected['variant'], flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--reference', type=Path, required=True)
    args = parser.parse_args()
    verify(args.data, args.reference)
