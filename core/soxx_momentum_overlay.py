"""Optional target-risk overlay; never a cap on actual mark-to-market weights."""
import numpy as np
import pandas as pd


def limit_target_risk(candidate, previous, bearish):
    """Keep SOXX + 3*SOXL target exposure from increasing while bearish."""
    result = candidate.copy()
    risk = float(candidate['SOXX'] + 3 * candidate['SOXL'])
    previous_risk = float(previous['SOXX'] + 3 * previous['SOXL'])
    if bearish and risk > previous_risk and risk > 0:
        result = result * (previous_risk / risk)
    return result


def apply_mom10_overlay(targets, price):
    """Today's open uses yesterday's 10-session return, including warm-up history."""
    bearish = price.pct_change(10, fill_method=None).lt(0).shift(1, fill_value=False)
    exposure = targets['SOXX'] + 3 * targets['SOXL']
    limited = []
    previous = 0.0
    for number, (date, risk) in enumerate(exposure.items()):
        selected = min(risk, previous) if number and bool(bearish.loc[date]) else risk
        limited.append(selected)
        previous = selected
    limit = pd.Series(limited, index=targets.index)
    factor = (limit / exposure.replace(0, np.nan)).clip(upper=1).fillna(1.0)
    return targets.mul(factor, axis=0)
