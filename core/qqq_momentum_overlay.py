"""QQQ/TQQQ adapter for the identical, validated SOXX/SOXL momentum rule."""
from core.soxx_momentum_overlay import apply_mom10_overlay as _apply
from core.soxx_momentum_overlay import limit_target_risk as _limit

_TO_MODEL = {'QQQ': 'SOXX', 'TQQQ': 'SOXL'}
_FROM_MODEL = {value: key for key, value in _TO_MODEL.items()}


def apply_mom10_overlay(targets, price):
    """Use QQQ prices; QQQ + 3*TQQQ is the target-risk measure."""
    return _apply(targets.rename(columns=_TO_MODEL), price).rename(columns=_FROM_MODEL)


def limit_target_risk(candidate, previous, bearish):
    return _limit(candidate.rename(index=_TO_MODEL), previous.rename(index=_TO_MODEL), bearish).rename(index=_FROM_MODEL)
