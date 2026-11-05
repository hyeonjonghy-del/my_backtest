"""Repair a verified partial Yahoo split conversion, never infer from returns.

SOXX 3:1 effective 2026-11-05 (SEC supplement dated 2026-10-23).
On 2026-11-06 Yahoo served October in post-split units but Nov 2-4 OHLC
and adjusted close in pre-split units. These verified closes identify those
rows exactly. Already corrected rows are left unchanged.
"""
import math
import pandas as pd


def normalize_yahoo_split_data(frame, symbol):
    if symbol!='SOXX' or frame.empty:return frame
    anchors={'2026-11-02':453.65,'2026-11-03':465.0,'2026-11-04':450.41}
    event=pd.Timestamp('2026-11-05')
    # Require evidence that this response is on the post-split basis. This
    # leaves an original, uniformly pre-split historical response unchanged.
    event_present=(event in frame.index and 'split_ratio' in frame
                   and math.isclose(float(frame.at[event,'split_ratio']),3.0))
    marker=pd.Timestamp('2026-10-30')
    adjusted_marker=(marker in frame.index and pd.notna(frame.at[marker,'close'])
                     and math.isclose(float(frame.at[marker,'close']),157.17,abs_tol=.015,rel_tol=0))
    if not (event_present or adjusted_marker):return frame
    result=frame.copy()
    factor=1.0
    if adjusted_marker and 'adjclose' in frame and pd.notna(frame.at[marker,'adjclose']):
        factor=float(frame.at[marker,'adjclose'])/float(frame.at[marker,'close'])
    for day,raw_close in anchors.items():
        day=pd.Timestamp(day)
        if day not in result.index:continue
        close=result.at[day,'close']
        if pd.isna(close):continue
        old=math.isclose(float(close),raw_close,abs_tol=.015,rel_tol=0)
        corrected=math.isclose(float(close),raw_close/3,abs_tol=.015,rel_tol=0)
        if not (old or corrected):
            raise ValueError(f'SOXX {day.date()}: 분할 가격 기준 확인 필요')
        if old:
            for column in ('open','high','low','close','dividend'):
                if column in result:result.at[day,column]/=3
        if 'adjclose' in result and pd.notna(result.at[day,'adjclose']):
            adjusted=float(result.at[day,'adjclose'])
            if math.isclose(adjusted,raw_close*factor,abs_tol=.015,rel_tol=.003):result.at[day,'adjclose']/=3
            elif not math.isclose(adjusted,raw_close/3*factor,abs_tol=.015,rel_tol=.003):
                raise ValueError(f'SOXX {day.date()}: 수정주가 분할 기준 확인 필요')
    return result

