"""Explicit cash-flow confirmation: holdings alone cannot prove a deposit."""
import hashlib


def confirmation_key(account_state, widget_key):
    snapshot = account_state.get('snapshot') or {}
    identity = (
        account_state.get('source'), account_state.get('profile_name'),
        snapshot.get('fetched_at'), tuple(sorted(account_state['shares'].items())),
        float(account_state['cash']),
    )
    token = hashlib.sha256(repr(identity).encode('utf-8')).hexdigest()[:20]
    return f'{widget_key}_cash_flow_confirmed_{token}'


def render_cash_flow_confirmation(account_state, widget_key):
    import streamlit as st

    confirmed = st.checkbox(
        '추가 입금 반영 — 전체 계좌 재계산',
        value=False,
        key=confirmation_key(account_state, widget_key),
        help='입금 후 최신 계좌를 불러온 다음 선택하세요. 입금액만 따로 배분하지 않고, '
        '추가 입금을 포함한 전체 계좌를 현재 목표 비중(10일 옵션 ON이면 보정 비중)으로 계산합니다. '
        '매수와 매도가 함께 발생할 수 있습니다. 입금이 없으면 선택하지 마세요.',
    )
    if confirmed:
        st.warning(
            '추가 입금 확인: 전체 계좌를 현재 목표 비중으로 재계산합니다. '
            '계산 결과를 확인한 뒤 직접 주문하세요. 체결 후 계좌를 다시 조회하면 확인 선택은 해제됩니다.'
        )
    return confirmed
