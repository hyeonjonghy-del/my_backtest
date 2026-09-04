"""Shared calculations and configuration helpers for read-only execution alerts."""

from __future__ import annotations

import json
import math
import os
import urllib.parse
import urllib.request
import urllib.error
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


TRADING_DAYS = 252
KIWOOM_BASE_URL = "https://api.kiwoom.com"
KIWOOM_US_ACCOUNT_PATH = "/api/us/acnt"
KIWOOM_DOMESTIC_ACCOUNT_PATH = "/api/dostk/acnt"


def load_secrets(repo_root: Path) -> dict[str, Any]:
    """Merge user and repository Streamlit secrets without logging values."""
    try:
        import tomllib
    except ImportError:  # pragma: no cover
        import tomli as tomllib

    merged: dict[str, Any] = {}
    for path in (Path.home() / ".streamlit" / "secrets.toml", repo_root / ".streamlit" / "secrets.toml"):
        if not path.exists():
            continue
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
        _deep_merge(merged, parsed)
    return merged


def _deep_merge(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value


def kiwoom_profile(secrets: dict[str, Any], profile: str) -> dict[str, str]:
    root = secrets.get("kiwoom", {})
    if not isinstance(root, dict):
        raise RuntimeError("secrets.toml에 [kiwoom] 설정이 없습니다.")
    section: Any
    if profile == "default":
        section = root
    else:
        accounts = root.get("accounts", {})
        section = accounts.get(profile, {}) if isinstance(accounts, dict) else {}
    if not isinstance(section, dict):
        section = {}
    app_key = str(section.get("app_key", "")).strip()
    app_secret = str(section.get("app_secret", "")).strip()
    if not app_key or not app_secret:
        raise RuntimeError(f"키움 계좌 프로필 [{profile}]의 App Key/Secret이 없습니다.")
    base_url = str(section.get("base_url", KIWOOM_BASE_URL)).strip().rstrip("/")
    if base_url != KIWOOM_BASE_URL:
        raise RuntimeError("실전계좌 알림은 https://api.kiwoom.com 만 사용합니다.")
    return {
        "app_key": app_key,
        "app_secret": app_secret,
        "base_url": base_url,
        "label": str(section.get("label", profile)),
    }


def telegram_config(secrets: dict[str, Any]) -> tuple[str, str]:
    section = secrets.get("telegram", {})
    if not isinstance(section, dict):
        section = {}
    token = (
        os.getenv("TELEGRAM_BOT_TOKEN")
        or os.getenv("TELEGRAM_TOKEN")
        or str(section.get("bot_token", "")).strip()
        or str(secrets.get("telegram_bot_token", "")).strip()
    )
    chat_id = (
        os.getenv("TELEGRAM_CHAT_ID")
        or str(section.get("chat_id", "")).strip()
        or str(secrets.get("telegram_chat_id", "")).strip()
    )
    if not token or not chat_id:
        raise RuntimeError("기존 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 설정을 찾지 못했습니다.")
    return token, chat_id


def send_telegram(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError("Telegram sendMessage가 거절되었습니다.")


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def _kiwoom_post(
    base_url: str,
    path: str,
    body: dict[str, object],
    *,
    token: str | None = None,
    api_id: str | None = None,
    continuation: tuple[str, str] | None = None,
) -> tuple[dict[str, object], dict[str, str]]:
    headers = {"Content-Type": "application/json;charset=UTF-8"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    if api_id:
        headers["api-id"] = api_id
    if continuation:
        headers["cont-yn"], headers["next-key"] = continuation
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
            response_headers = {key.lower(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as exc:
        try:
            error = json.loads(exc.read().decode("utf-8"))
            message = str(error.get("return_msg") or error.get("message") or "HTTP request failed")
        except Exception:
            message = "HTTP request failed"
        raise RuntimeError(f"키움 API 오류 ({exc.code}): {message[:200]}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"키움 API 연결 실패: {exc.reason}") from None
    code = payload.get("return_code")
    if code not in (None, 0, "0"):
        raise RuntimeError(f"키움 API 요청 거절 ({code}): {payload.get('return_msg', '')}")
    return payload, response_headers


def _kiwoom_token(config: dict[str, str]) -> str:
    payload, _ = _kiwoom_post(
        config["base_url"],
        "/oauth2/token",
        {
            "grant_type": "client_credentials",
            "appkey": config["app_key"],
            "secretkey": config["app_secret"],
        },
    )
    token = str(payload.get("token", "")).strip()
    if not token:
        raise RuntimeError("키움 API가 접근 토큰을 반환하지 않았습니다.")
    return token


def _kiwoom_tr(
    config: dict[str, str],
    token: str,
    path: str,
    api_id: str,
    body: dict[str, object],
    list_field: str | None,
) -> dict[str, object]:
    merged: dict[str, object] = {}
    rows: list[object] = []
    continuation: tuple[str, str] | None = None
    for _ in range(20):
        payload, headers = _kiwoom_post(
            config["base_url"],
            path,
            body,
            token=token,
            api_id=api_id,
            continuation=continuation,
        )
        for key, value in payload.items():
            if key != list_field:
                merged[key] = value
        if list_field and isinstance(payload.get(list_field), list):
            rows.extend(payload[list_field])
        if headers.get("cont-yn", "").upper() != "Y" or not headers.get("next-key"):
            break
        continuation = ("Y", headers["next-key"])
    else:
        raise RuntimeError("키움 계좌 조회 페이지 수가 안전 한도를 초과했습니다.")
    if list_field:
        merged[list_field] = rows
    return merged


def _find_usd_cash(payload: object) -> tuple[float | None, str | None]:
    rows: list[dict[str, object]] = []

    def collect(value: object) -> None:
        if isinstance(value, dict):
            rows.append(value)
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(payload)
    candidates = (
        "fc_ord_alowa",
        "fc_entra",
        "ord_psbl_amt",
        "frgn_ord_psbl_amt",
        "frgn_stk_ord_psbl_amt",
        "ovrs_ord_psbl_amt",
        "usd_ord_psbl_amt",
        "frgn_stk_dps",
        "frgn_stk_dps_amt",
        "dps",
        "deposit",
    )
    for row in rows:
        lowered = {str(key).lower(): value for key, value in row.items()}
        direct = _number(lowered.get("usd_ord_psbl_amt"))
        if direct is not None:
            return max(direct, 0.0), "usd_ord_psbl_amt"
        currency = str(
            lowered.get("crnc_code") or lowered.get("crnc_cd") or lowered.get("currency") or ""
        ).strip().upper()
        if currency != "USD":
            continue
        for field in candidates:
            amount = _number(lowered.get(field))
            if amount is not None:
                return max(amount, 0.0), field
    return None, None


def load_us_account(config: dict[str, str], symbols: tuple[str, ...]) -> dict[str, object]:
    token = _kiwoom_token(config)
    holdings = _kiwoom_tr(config, token, KIWOOM_US_ACCOUNT_PATH, "ust21070", {}, "result_list")
    wanted = tuple(symbol.upper() for symbol in symbols)
    shares = {symbol: 0.0 for symbol in wanted}
    for row in holdings.get("result_list", []):
        if not isinstance(row, Mapping):
            continue
        symbol = str(row.get("stk_cd", "")).strip().upper()
        currency = str(row.get("crnc_code") or row.get("crnc_cd") or row.get("currency") or "").strip().upper()
        if symbol not in shares or currency != "USD":
            continue
        quantity = _number(row.get("poss_qty"))
        if quantity is None:
            quantity = _number(row.get("qty"))
        shares[symbol] += max(quantity or 0.0, 0.0)
    cash_payload = _kiwoom_tr(config, token, KIWOOM_US_ACCOUNT_PATH, "ust21110", {}, None)
    cash, cash_field = _find_usd_cash(cash_payload)
    return {
        "shares": shares,
        "cash": cash,
        "cash_field": cash_field,
        "cash_warning": None if cash is not None else "USD 예수금 필드를 인식하지 못했습니다.",
    }


def _domestic_code(value: object) -> str:
    text = str(value or "").strip().upper()
    digits = "".join(character for character in text if character.isdigit())
    return digits[-6:] if len(digits) >= 6 else text


def load_domestic_account(config: dict[str, str], symbols: tuple[str, ...]) -> dict[str, object]:
    token = _kiwoom_token(config)
    holdings = _kiwoom_tr(
        config,
        token,
        KIWOOM_DOMESTIC_ACCOUNT_PATH,
        "kt00018",
        {"qry_tp": "1", "dmst_stex_tp": "KRX"},
        "acnt_evlt_remn_indv_tot",
    )
    wanted = tuple(_domestic_code(symbol) for symbol in symbols)
    shares = {symbol: 0.0 for symbol in wanted}
    for row in holdings.get("acnt_evlt_remn_indv_tot", []):
        if not isinstance(row, Mapping):
            continue
        code = _domestic_code(row.get("stk_cd"))
        if code not in shares:
            continue
        quantity = _number(row.get("rmnd_qty"))
        if quantity is None:
            quantity = _number(row.get("trde_able_qty"))
        shares[code] += max(quantity or 0.0, 0.0)
    cash_payload = _kiwoom_tr(
        config,
        token,
        KIWOOM_DOMESTIC_ACCOUNT_PATH,
        "kt00001",
        {"qry_tp": "2"},
        None,
    )
    cash = None
    cash_field = None
    for field in ("ord_alow_amt", "ord_alowa", "entr", "d2_entra"):
        amount = _number(cash_payload.get(field))
        if amount is not None:
            cash, cash_field = max(amount, 0.0), field
            break
    return {
        "shares": shares,
        "cash": cash,
        "cash_field": cash_field,
        "cash_warning": None if cash is not None else "원화 예수금 필드를 인식하지 못했습니다.",
    }


def normalize_index(frame: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
    result = frame.copy()
    result.index = pd.to_datetime(result.index).tz_localize(None).normalize()
    return result.sort_index()


def load_yahoo_chart(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    period1 = int(datetime.combine(start.date(), datetime.min.time(), tzinfo=timezone.utc).timestamp())
    period2 = int(
        datetime.combine((end + timedelta(days=1)).date(), datetime.min.time(), tzinfo=timezone.utc).timestamp()
    )
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?period1={period1}&period2={period2}&interval=1d&events=history&includeAdjustedClose=true"
    )
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=25) as response:
        payload = json.load(response)
    result = payload["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    adjusted = result["indicators"].get("adjclose", [{}])[0].get("adjclose", quote["close"])
    frame = pd.DataFrame(
        {
            "close": quote["close"],
            "adjclose": adjusted,
        },
        index=pd.to_datetime(result["timestamp"], unit="s").normalize(),
    )
    return normalize_index(frame).dropna(subset=["adjclose"])


def build_trend(price: pd.Series, fast: pd.Series, slow: pd.Series) -> pd.Series:
    return fast > slow


def build_regime(
    trend: pd.Series,
    fast: pd.Series,
    slow: pd.Series,
    volatility: pd.Series,
    strong_spread: float,
    weak_vol_cutoff: float,
) -> pd.Series:
    spread = (fast / slow - 1).replace([np.inf, -np.inf], np.nan)
    strong = trend.fillna(False) & (spread >= strong_spread) & (volatility <= weak_vol_cutoff)
    weak = trend.fillna(False) & ~strong
    result = pd.Series("Bear", index=trend.index, dtype="object")
    result.loc[weak] = "Weak Bull"
    result.loc[strong] = "Strong Bull"
    return result


def build_turnaround(
    price: pd.Series,
    fast: pd.Series,
    slow: pd.Series,
    drawdown_trigger: float,
    exit_fast_window: int,
    exit_slow_window: int,
    exit_confirm_days: int,
) -> pd.Series:
    drawdown = price / price.cummax() - 1
    golden_cross = (fast > slow) & (fast.shift(1) <= slow.shift(1))
    exit_signal = price.rolling(exit_fast_window).mean() < price.rolling(exit_slow_window).mean()
    active = pd.Series(False, index=price.index)
    armed = False
    in_turnaround = False
    exit_count = 0
    for date in price.index:
        if pd.notna(drawdown.loc[date]) and drawdown.loc[date] <= -drawdown_trigger:
            armed = True
        if bool(golden_cross.loc[date]) and armed:
            in_turnaround = True
            armed = False
            exit_count = 0
        if in_turnaround:
            active.loc[date] = True
            exit_count = exit_count + 1 if bool(exit_signal.loc[date]) else 0
            if exit_count >= exit_confirm_days:
                active.loc[date] = False
                in_turnaround = False
                exit_count = 0
    return active


US_STRATEGIES: tuple[dict[str, Any], ...] = (
    {
        "name": "SOXX / SOXL",
        "symbols": ("SOXX", "SOXL"),
        "profile": "soxx",
        "target_vol": 0.45,
        "leveraged_cap": 0.50,
        "max_risk": 1.50,
        "strong_base_share": 0.20,
        "strong_cash_sweep": 0.0,
        "weak_multiplier": 0.75,
        "weak_base_share": 0.80,
        "weak_leveraged_cap": 0.15,
        "weak_cash_sweep": 0.0,
        "turnaround_drawdown": 0.20,
        "turnaround_leveraged_weight": 0.50,
        "bear_base": 0.20,
    },
    {
        "name": "QQQ / TQQQ Holdings V2",
        "symbols": ("QQQ", "TQQQ"),
        "profile": "default",
        "target_vol": 0.35,
        "leveraged_cap": 0.45,
        "max_risk": 1.80,
        "strong_base_share": 0.60,
        "strong_cash_sweep": 0.50,
        "weak_multiplier": 0.75,
        "weak_base_share": 0.60,
        "weak_leveraged_cap": 0.15,
        "weak_cash_sweep": 0.20,
        "turnaround_drawdown": 0.10,
        "turnaround_leveraged_weight": 0.50,
        "bear_base": 0.30,
    },
)


def calculate_us_target(config: dict[str, Any], end: datetime) -> dict[str, Any]:
    base, leveraged = config["symbols"]
    start = datetime(2015, 1, 1)
    base_data = load_yahoo_chart(base, start, end)
    leveraged_data = load_yahoo_chart(leveraged, start, end)
    index = base_data.index.intersection(leveraged_data.index)
    if len(index) < 250:
        raise RuntimeError(f"{config['name']}: 가격 이력이 부족합니다.")
    base_data = base_data.reindex(index)
    leveraged_data = leveraged_data.reindex(index)
    price = base_data["adjclose"].ffill()
    returns = price.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0)
    fast = price.rolling(30).mean()
    slow = price.rolling(200).mean()
    volatility = returns.rolling(20).std() * math.sqrt(TRADING_DAYS)
    trend = build_trend(price, fast, slow)
    regime = build_regime(trend, fast, slow, volatility, 0.05, 0.55)
    turnaround = build_turnaround(
        price,
        fast,
        slow,
        config["turnaround_drawdown"],
        10,
        60,
        2,
    )
    current_regime = str(regime.ffill().iloc[-1])
    is_turnaround = bool(turnaround.fillna(False).iloc[-1])
    current_vol = float(volatility.ffill().iloc[-1])
    weights = target_weight(config, current_regime, is_turnaround, current_vol)
    previous_regime = str(regime.ffill().iloc[-2])
    was_turnaround = bool(turnaround.fillna(False).iloc[-2])
    previous_vol = float(volatility.ffill().iloc[-2])
    previous_weights = target_weight(
        config,
        previous_regime,
        was_turnaround,
        previous_vol,
    )
    prices = {
        base: float(base_data["adjclose"].ffill().iloc[-1]),
        leveraged: float(leveraged_data["adjclose"].ffill().iloc[-1]),
    }
    return {
        "signal_date": index[-1].date(),
        "regime": "Turnaround Bull" if is_turnaround else current_regime,
        "volatility": current_vol,
        "weights": weights,
        "previous_weights": previous_weights,
        "prices": prices,
    }


def target_weight(
    config: dict[str, Any],
    regime: str,
    is_turnaround: bool,
    current_vol: float,
) -> dict[str, float]:
    base, leveraged = config["symbols"]
    if is_turnaround:
        leveraged_w = config["turnaround_leveraged_weight"]
        return {base: 1 - leveraged_w, leveraged: leveraged_w}
    if regime == "Bear" or not math.isfinite(current_vol) or current_vol <= 0:
        return {base: config["bear_base"], leveraged: 0.0}
    desired = min(config["target_vol"] / current_vol, config["max_risk"])
    if regime == "Weak Bull":
        desired = min(desired * config["weak_multiplier"], config["max_risk"])
        base_share = config["weak_base_share"]
        leveraged_cap = config["weak_leveraged_cap"]
        cash_sweep = config["weak_cash_sweep"]
    else:
        base_share = config["strong_base_share"]
        leveraged_cap = config["leveraged_cap"]
        cash_sweep = config["strong_cash_sweep"]
    base_w = min(desired * base_share, 1.0)
    leveraged_w = min(max((desired - base_w) / 3, 0.0), leveraged_cap)
    used_risk = base_w + leveraged_w * 3
    base_w = min(base_w + max(desired - used_risk, 0.0), 1 - leveraged_w)
    base_w = min(base_w + max(1 - base_w - leveraged_w, 0.0) * cash_sweep, 1 - leveraged_w)
    total = base_w + leveraged_w
    if total > 1:
        base_w, leveraged_w = base_w / total, leveraged_w / total
    return {base: base_w, leveraged: leveraged_w}


def whole_share_plan(
    weights: dict[str, float],
    prices: dict[str, float],
    shares: dict[str, float],
    cash: float,
    previous_weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    account_value = float(cash) + sum(float(shares.get(s, 0)) * prices[s] for s in prices)
    target_changed = previous_weights is None or any(
        not math.isclose(
            float(weights.get(symbol, 0.0)),
            float(previous_weights.get(symbol, 0.0)),
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        for symbol in prices
    )
    orders: dict[str, dict[str, float | int | str]] = {}
    invested = 0.0
    for symbol, price in prices.items():
        current_shares = int(float(shares.get(symbol, 0)))
        target_shares = (
            math.floor(account_value * weights.get(symbol, 0.0) / price)
            if target_changed
            else current_shares
        )
        delta = target_shares - current_shares
        invested += target_shares * price
        orders[symbol] = {
            "current": current_shares,
            "target": target_shares,
            "order": delta,
            "action": "매수" if delta > 0 else "매도" if delta < 0 else "유지",
        }
    return {
        "account_value": account_value,
        "target_cash": max(account_value - invested, 0.0) if target_changed else float(cash),
        "target_changed": target_changed,
        "orders": orders,
    }

