import io, json, zipfile
import pandas as pd
import pytest
from test_comp_strategy import dataset
from strategies.comp.strategy import CompConfig, backtest, load_bundle, score_snapshot

def scored_data():
    data=dataset()
    data.fscore_scores=pd.DataFrame(dict(date=data.features.date, ticker=data.features.ticker,
        fscore=4., fscore_raw=4., fscore_filed=pd.Timestamp('2025-03-01'), missing_reason=''))
    return data

def test_filter_keeps_comp_order_and_excludes_fractional_threshold():
    data=scored_data()
    ranked=score_snapshot(data.features,CompConfig())
    data.fscore_scores.loc[data.fscore_scores.ticker.eq(ranked.index[0]),'fscore']=3.
    data.fscore_scores.loc[data.fscore_scores.ticker.eq(ranked.index[1]),'fscore']=3.2
    result=backtest(data,CompConfig(fscore_mode='filter'))
    assert result['trades'].query("side == 'buy'").ticker.tolist()==ranked.index[1:11].tolist()
    assert result['latest'].loc[ranked.index[1],'fscore']==3.2

def test_unknown_higher_candidate_blocks_instead_of_silent_exclusion():
    data=scored_data()
    first=score_snapshot(data.features,CompConfig()).index[0]
    data.fscore_scores=data.fscore_scores[~data.fscore_scores.ticker.eq(first)]
    with pytest.raises(ValueError,match='미확인'):
        backtest(data,CompConfig(fscore_mode='filter'))

def test_known_missing_is_excluded_and_off_matches_legacy():
    data=scored_data()
    first=score_snapshot(data.features,CompConfig()).index[0]
    mask=data.fscore_scores.ticker.eq(first)
    data.fscore_scores.loc[mask,'fscore']=float('nan')
    data.fscore_scores.loc[mask,'missing_reason']='missing_gross'
    assert first not in backtest(data,CompConfig(fscore_mode='filter'))['trades'].ticker.tolist()
    pd.testing.assert_frame_equal(backtest(data,CompConfig())['equity'],backtest(dataset(),CompConfig())['equity'])

def test_disclosure_on_signal_and_duplicate_or_invalid_scores_block():
    for mutation in ['same_day','duplicate','infinity','no_reason']:
        data=scored_data()
        if mutation=='same_day': data.fscore_scores.loc[0,'fscore_filed']=data.fscore_scores.loc[0,'date']
        if mutation=='duplicate': data.fscore_scores=pd.concat([data.fscore_scores,data.fscore_scores.iloc[:1]])
        if mutation=='infinity': data.fscore_scores.loc[0,'fscore']=float('inf')
        if mutation=='no_reason': data.fscore_scores.loc[0,'fscore']=float('nan')
        with pytest.raises(ValueError): data.validate()

def test_optional_bundle_roundtrip_and_no_scores_refusal():
    data=scored_data(); buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as archive:
        archive.writestr('metadata.json',json.dumps(data.metadata))
        for name,frame in [('features.csv',data.features),('prices.csv',data.prices),('benchmarks.csv',data.benchmarks),('fscore.csv',data.fscore_scores)]:
            archive.writestr(name,frame.to_csv(index=False))
    loaded=load_bundle(buffer.getvalue())
    assert len(loaded.fscore_scores)==12
    assert backtest(loaded,CompConfig(fscore_mode='filter'))['metrics']['trades']==10
    with pytest.raises(ValueError,match='ZIP'):
        backtest(dataset(),CompConfig(fscore_mode='filter'))
