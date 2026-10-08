"""Convert local research outputs to a PRIVATE portable input bundle."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import zipfile

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from strategies.comp.strategy import FACTORS


def export(source: Path, output: Path):
    summary = json.loads((source / 'summary.json').read_text(encoding='utf-8'))
    signal = pd.read_csv(source / 'signals_absolute_base.csv', dtype={'ticker': str})
    features = signal[signal.variant.eq('with_per')][['date', 'ticker', 'name', 'filed', *FACTORS]].copy()
    rows = []
    for path in sorted((source / 'kiwoom_histories' / 'price').glob('*.json')):
        value = json.loads(path.read_text(encoding='utf-8'))
        for row in value['rows']:
            if not row.get('dt'):
                continue
            date = pd.Timestamp(row['dt']).strftime('%Y-%m-%d')
            if summary['start'] <= date <= summary['end']:
                opening, closing = abs(float(row['open_pric'].replace(',', ''))), abs(float(row['cur_prc'].replace(',', '')))
                if opening > 0 and closing > 0:
                    rows.append({'date': date, 'ticker': path.stem, 'open': opening, 'close': closing})
    benchmark = {}
    cache = source / 'cache' if (source / 'cache/kospi.json').is_file() else source.parent / 'cache'
    for symbol in ('KOSPI', 'KOSDAQ'):
        raw = json.loads((cache / (symbol.lower() + '.json')).read_text(encoding='utf-8'))
        benchmark[symbol] = pd.Series({pd.Timestamp(row[0]): float(row[4]) for row in raw})
    indices = pd.DataFrame(benchmark).sort_index().loc[summary['start']:summary['end']]
    indices.index.name = 'date'
    indices = indices.reset_index()
    metadata = {'schema_version': 1, 'start': summary['start'], 'end': summary['end'],
                'signal_dates': sorted(features.date.unique().tolist()),
                'sector_history_available': False,
                'scope': 'Financial/price-complete research sample; not the original author strategy.',
                'limitations': summary['limitations']}
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.tmp')
    with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('metadata.json', json.dumps(metadata, ensure_ascii=False, indent=2))
        for name, frame in [('features.csv', features), ('prices.csv', pd.DataFrame(rows)), ('benchmarks.csv', indices)]:
            archive.writestr(name, frame.to_csv(index=False))
    temporary.replace(output)
    print(f'PRIVATE bundle created: {len(features)} feature rows; {len(rows)} price rows.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('data/comp/research.zip'))
    args = parser.parse_args()
    export(args.source, args.output)
