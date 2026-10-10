import json
from pathlib import Path
import zipfile

import pandas as pd
from streamlit.testing.v1 import AppTest
from test_comp_strategy import dataset
import strategies.comp as package
from strategies.comp import strategy as engine


def test_page_reloads_stale_engine_and_ignores_cached_package_exports(tmp_path, monkeypatch):
    data = dataset()
    days = pd.bdate_range(end='2025-04-08', periods=250)
    data.benchmarks = pd.DataFrame(dict(date=days, KOSPI=100., KOSDAQ=100.))
    bundle = tmp_path / 'research.zip'
    with zipfile.ZipFile(bundle, 'w') as archive:
        archive.writestr('metadata.json', json.dumps(data.metadata))
        for name in ('features', 'prices', 'benchmarks'):
            archive.writestr(name + '.csv', getattr(data, name).to_csv(index=False))
    monkeypatch.setenv('COMP_DATA_BUNDLE', str(bundle))
    old_calls = []
    def stale_config(**kwargs):
        old_calls.append(kwargs)
        raise TypeError("CompConfig.__init__() got an unexpected keyword argument 'market_mode'")
    monkeypatch.setattr(engine, 'CompConfig', stale_config)
    monkeypatch.setattr(package, 'CompConfig', stale_config)
    page = Path(__file__).resolve().parents[1] / 'pages' / '11_COMP_Strategy.py'
    app = AppTest.from_file(str(page)).run(timeout=30)
    assert not app.exception and not app.error
    assert not old_calls
    next(x for x in app.selectbox if x.label == '시장 비중 조절').set_value('half')
    next(x for x in app.button if x.label == '검증 실행').click()
    app.run(timeout=30)
    assert not app.exception and not app.error
    assert app.session_state['comp_result']['metrics']['config']['market_mode'] == 'half'
    # A pre-fix session must recalculate instead of showing its cached result.
    app.session_state['comp_result_version'] = 6
    app.session_state['comp_result'] = {'obsolete': True}
    app.run(timeout=30)
    assert not app.exception and not app.error
    assert app.session_state['comp_result_version'] == 7
    assert not old_calls
