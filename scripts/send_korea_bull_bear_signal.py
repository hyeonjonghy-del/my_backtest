"""Send the KODEX 200 / Leverage ON-OFF v1 close signal to Telegram.

The script is designed for Windows Task Scheduler. It reads Telegram secrets
from environment variables first, then from .streamlit/secrets.toml.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.execution_alerts import (  # noqa: E402
    kiwoom_profile,
    load_domestic_account,
    load_secrets,
    load_yahoo_chart,
)

YFINANCE_CACHE = ROOT / "data" / "yfinance-cache"
YFINANCE_CACHE.mkdir(parents=True, exist_ok=True)
LOG_DIR = ROOT / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

KODEX_200 = "069500"
KODEX_LEVERAGE = "122630"
TRADING_DAYS = 252
KST = ZoneInfo("Asia/Seoul")

DEFAULTS = {
    "start_date": datetime(2016, 5, 16).date(),
    "ma_window": 100,
    "vol_window": 20,
    "vol_threshold": 0.50,
    "vol_source": "KODEX 200",
    "use_high_vol_fallback": True,
    "high_vol_kodex_weight": 0.50,
    "leverage_weight": 1.00,
    "after_close_fill_rate": 0.70,
}


def normalize_index(df: pd.DataFrame | pd.Series) -> pd.DataFrame | pd.Series:
    out = df.copy()
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    return out.sort_index()


def finite_return(ret: pd.Series) -> pd.Series:
    return ret.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def load_krx_ohlcv(ticker: str, start_str: str, end_str: str) -> pd.DataFrame:
    try:
        import yfinance as yf

        yf.set_tz_cache_location(str(YFINANCE_CACHE))
    except Exception:
        pass

    from pykrx import stock

    raw = stock.get_market_ohlcv_by_date(start_str, end_str, ticker)
    if raw.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    df = pd.DataFrame(
        {
            "open": pd.to_numeric(raw["시가"], errors="coerce"),
            "high": pd.to_numeric(raw["고가"], errors="coerce"),
            "low": pd.to_numeric(raw["저가"], errors="coerce"),
            "close": pd.to_numeric(raw["종가"], errors="coerce"),
            "volume": pd.to_numeric(raw["거래량"], errors="coerce"),
        }
    )
    df = normalize_index(df).dropna(how="all")
    return df.where(df > 0)


def load_krx_ohlcv_with_retry(ticker: str, start_str: str, end_str: str, attempts: int = 5, delay_seconds: int = 15) -> pd.DataFrame:
    try:
        yahoo = load_yahoo_chart(
            f"{ticker}.KS",
            datetime.strptime(start_str, "%Y%m%d"),
            datetime.strptime(end_str, "%Y%m%d"),
        )
        if not yahoo.empty:
            close = pd.to_numeric(yahoo["close"], errors="coerce")
            return pd.DataFrame(
                {
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": np.nan,
                },
                index=yahoo.index,
            )
    except Exception as exc:
        write_log(f"Yahoo fallback for {ticker} failed: {exc!r}")

    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            data = load_krx_ohlcv(ticker, start_str, end_str)
            if not data.empty:
                if attempt > 1:
                    write_log(f"Loaded {ticker} on attempt {attempt}")
                return data
            write_log(f"{ticker} returned empty data on attempt {attempt}")
        except Exception as exc:
            last_error = exc
            write_log(f"{ticker} load failed on attempt {attempt}: {exc!r}")
        if attempt < attempts:
            time.sleep(delay_seconds)
    if last_error is not None:
        raise last_error
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


def build_signal(close: pd.Series, ma_window: int, vol_price: pd.Series, vol_window: int, vol_threshold: float):
    ma = close.rolling(ma_window).mean()
    realized_vol = finite_return(vol_price.pct_change()).rolling(vol_window).std() * np.sqrt(TRADING_DAYS)
    trend_signal = (close > ma).rename("Trend Signal")
    low_vol_signal = (realized_vol < vol_threshold).rename("Low Vol Signal")
    signal = (trend_signal & low_vol_signal).rename("Leverage Signal")
    return signal, trend_signal, ma, realized_vol


def build_target_weights(
    dates: pd.DatetimeIndex,
    leverage_signal: pd.Series,
    trend_signal: pd.Series,
    realized_vol: pd.Series,
    leverage_weight: float,
    use_high_vol_fallback: bool,
    high_vol_kodex_weight: float,
    vol_threshold: float,
) -> pd.DataFrame:
    leverage_signal = leverage_signal.reindex(dates).fillna(False)
    trend_signal = trend_signal.reindex(dates).fillna(False)
    realized_vol = realized_vol.reindex(dates)
    high_vol_bull = trend_signal & (~leverage_signal) & (realized_vol >= vol_threshold)

    lev_weight = leverage_signal.astype(float) * leverage_weight
    kodex_weight = pd.Series(0.0, index=dates)
    if use_high_vol_fallback:
        kodex_weight = high_vol_bull.astype(float) * high_vol_kodex_weight
    cash_weight = (1.0 - lev_weight - kodex_weight).clip(lower=0.0)
    return pd.DataFrame(
        {
            "KODEX Leverage": lev_weight.clip(0.0, 1.0),
            "KODEX 200": kodex_weight.clip(0.0, 1.0),
            "Cash": cash_weight.clip(0.0, 1.0),
        },
        index=dates,
    )


def fmt_pct(value: float) -> str:
    return f"{value:.0%}"


def fmt_allocation(weights: pd.Series) -> str:
    return (
        f"KODEX Leverage {fmt_pct(float(weights['KODEX Leverage']))}, "
        f"KODEX 200 {fmt_pct(float(weights['KODEX 200']))}, "
        f"Cash {fmt_pct(float(weights['Cash']))}"
    )


def load_streamlit_secrets() -> dict[str, object]:
    return load_secrets(ROOT)


def get_telegram_config() -> tuple[str, str]:
    secrets = load_streamlit_secrets()
    telegram = secrets.get("telegram", {}) if isinstance(secrets.get("telegram"), dict) else {}

    token = (
        os.getenv("TELEGRAM_BOT_TOKEN")
        or os.getenv("TELEGRAM_TOKEN")
        or str(telegram.get("bot_token", "")).strip()
        or str(secrets.get("telegram_bot_token", "")).strip()
    )
    chat_id = (
        os.getenv("TELEGRAM_CHAT_ID")
        or str(telegram.get("chat_id", "")).strip()
        or str(secrets.get("telegram_chat_id", "")).strip()
    )
    if not token or not chat_id:
        raise RuntimeError(
            "Telegram secrets are missing. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID, "
            "or add them to .streamlit/secrets.toml."
        )
    return token, chat_id


def write_log(text: str) -> None:
    now = datetime.now(KST)
    log_path = LOG_DIR / f"kodex_signal_{now:%Y%m%d}.log"
    try:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"[{now:%Y-%m-%d %H:%M:%S}] {text}\n")
    except OSError:
        pass


def send_telegram(text: str) -> None:
    token, chat_id = get_telegram_config()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram sendMessage failed: {payload}")


def send_telegram_with_retry(text: str, attempts: int = 3, delay_seconds: int = 5) -> None:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            send_telegram(text)
            if attempt > 1:
                write_log(f"Telegram message sent on attempt {attempt}")
            return
        except Exception as exc:
            last_error = exc
            write_log(f"Telegram send failed on attempt {attempt}: {exc!r}")
            if attempt < attempts:
                time.sleep(delay_seconds)
    if last_error is not None:
        raise last_error


def calculate_message(now: datetime) -> str:
    today = now.date()
    end_str = today.strftime("%Y%m%d")
    warmup_days = max(DEFAULTS["ma_window"], DEFAULTS["vol_window"], 120) * 3
    extended_start = DEFAULTS["start_date"] - timedelta(days=warmup_days)

    kodex_200 = load_krx_ohlcv_with_retry(KODEX_200, extended_start.strftime("%Y%m%d"), end_str)
    kodex_lev = load_krx_ohlcv_with_retry(KODEX_LEVERAGE, extended_start.strftime("%Y%m%d"), end_str)
    if kodex_200.empty or kodex_lev.empty:
        raise RuntimeError("KODEX ETF data could not be loaded.")

    common_idx = kodex_200.index.intersection(kodex_lev.index)
    common_idx = common_idx[(common_idx.date >= DEFAULTS["start_date"]) & (common_idx.date <= today)]
    if len(common_idx) < 60:
        raise RuntimeError("Not enough KODEX trading-day data.")

    latest_date = common_idx[-1].date()
    if latest_date != today:
        return (
            "[KODEX 200 / Leverage ON-OFF v1]\n"
            f"실행시각: {now:%Y-%m-%d %H:%M} KST\n"
            f"상태: 오늘({today}) KRX 데이터가 아직 없습니다.\n"
            f"마지막 데이터: {latest_date}\n"
            "조치: 매매 전 Streamlit 화면 또는 증권사 가격을 한 번 더 확인하세요."
        )

    full_idx = kodex_200.index.intersection(kodex_lev.index)
    full_idx = full_idx[full_idx <= common_idx[-1]]
    kodex_close = kodex_200["close"].reindex(full_idx).ffill()
    lev_close = kodex_lev["close"].reindex(full_idx).ffill()
    vol_price = kodex_close if DEFAULTS["vol_source"] == "KODEX 200" else lev_close

    signal, trend_signal, ma, realized_vol = build_signal(
        kodex_close,
        DEFAULTS["ma_window"],
        vol_price,
        DEFAULTS["vol_window"],
        DEFAULTS["vol_threshold"],
    )
    target_weights = build_target_weights(
        common_idx,
        signal,
        trend_signal,
        realized_vol,
        DEFAULTS["leverage_weight"],
        DEFAULTS["use_high_vol_fallback"],
        DEFAULTS["high_vol_kodex_weight"],
        DEFAULTS["vol_threshold"],
    )

    full_target = target_weights.iloc[-1]

    latest_signal = bool(signal.reindex(common_idx).iloc[-1])
    latest_trend = bool(trend_signal.reindex(common_idx).iloc[-1])
    latest_close = float(kodex_close.reindex(common_idx).iloc[-1])
    latest_lev_close = float(lev_close.reindex(common_idx).iloc[-1])
    latest_ma = float(ma.reindex(common_idx).iloc[-1])
    latest_vol = float(realized_vol.reindex(common_idx).iloc[-1])

    secrets = load_streamlit_secrets()
    profile = kiwoom_profile(secrets, "korea")
    snapshot = load_domestic_account(profile, (KODEX_LEVERAGE, KODEX_200))
    if snapshot.get("cash") is None:
        raise RuntimeError(
            "원화 주문가능 예수금을 읽지 못했습니다. "
            + str(snapshot.get("cash_warning") or "")
        )
    cash = float(snapshot["cash"])
    current = {
        KODEX_LEVERAGE: int(float(snapshot["shares"].get(KODEX_LEVERAGE, 0.0))),
        KODEX_200: int(float(snapshot["shares"].get(KODEX_200, 0.0))),
    }
    prices = {KODEX_LEVERAGE: latest_lev_close, KODEX_200: latest_close}
    account_value = cash + sum(current[code] * prices[code] for code in prices)
    target = {
        KODEX_LEVERAGE: math.floor(
            account_value * float(full_target["KODEX Leverage"]) / latest_lev_close
        ),
        KODEX_200: math.floor(
            account_value * float(full_target["KODEX 200"]) / latest_close
        ),
    }
    delta = {code: target[code] - current[code] for code in target}
    after_close = {
        code: math.trunc(delta[code] * DEFAULTS["after_close_fill_rate"])
        for code in delta
    }
    next_open = {code: delta[code] - after_close[code] for code in delta}
    target_invested = sum(target[code] * prices[code] for code in target)
    target_cash = max(account_value - target_invested, 0.0)
    action = "비중 변경 필요" if any(delta.values()) else "비중 변경 없음"

    def order_text(name: str, quantity: int) -> str:
        if quantity > 0:
            return f"- {name}: 매수 {quantity}주"
        if quantity < 0:
            return f"- {name}: 매도 {abs(quantity)}주"
        return f"- {name}: 유지 (주문 없음)"

    lines = [
        "[KODEX 200 / Leverage ON-OFF v1]",
        f"실행시각: {now:%Y-%m-%d %H:%M} KST",
        f"기준일: {latest_date}",
        f"상태: {action}",
        "",
        f"계좌: {profile['label']}",
        f"목표비중: {fmt_allocation(full_target)}",
        (
            f"현재: KODEX Leverage {current[KODEX_LEVERAGE]}주, "
            f"KODEX 200 {current[KODEX_200]}주, 예수금 {cash:,.0f}원"
        ),
        (
            f"목표: KODEX Leverage {target[KODEX_LEVERAGE]}주, "
            f"KODEX 200 {target[KODEX_200]}주, 목표현금 약 {target_cash:,.0f}원"
        ),
        "",
        "EXECUTION 1 - 오늘 시간외 종가 (70%):",
        order_text("KODEX Leverage", after_close[KODEX_LEVERAGE]),
        order_text("KODEX 200", after_close[KODEX_200]),
        "",
        "EXECUTION 2 - 다음 정규장 시가 (잔여 30%):",
        order_text("KODEX Leverage", next_open[KODEX_LEVERAGE]),
        order_text("KODEX 200", next_open[KODEX_200]),
        "",
        "신호:",
        f"Leverage Signal: {'Pass' if latest_signal else 'Wait'}",
        f"Trend: {'Pass' if latest_trend else 'Wait'}",
        f"KODEX 200: {latest_close:,.0f} / MA{DEFAULTS['ma_window']}: {latest_ma:,.0f}",
        f"{DEFAULTS['vol_source']} RV{DEFAULTS['vol_window']}: {latest_vol:.1%} / cap {DEFAULTS['vol_threshold']:.0%}",
        f"KODEX Leverage close: {latest_lev_close:,.0f}",
        "",
        "※ 읽기 전용입니다. 주문은 자동 제출되지 않습니다.",
    ]
    return "\n".join(lines)


def main() -> int:
    now = datetime.now(KST)
    try:
        write_log("Start KODEX notifier")
        message = calculate_message(now)
        send_telegram_with_retry(message)
        write_log("Telegram message sent successfully")
        print(message)
        return 0
    except Exception as exc:
        error_message = (
            "[KODEX 200 / Leverage ON-OFF v1]\n"
            f"실행시각: {now:%Y-%m-%d %H:%M} KST\n"
            f"상태: 자동 알림 실패\n"
            f"오류: {exc}"
        )
        try:
            send_telegram_with_retry(error_message)
            write_log(f"Failure message sent to Telegram: {exc}")
        except Exception:
            write_log(f"Failure message could not be sent to Telegram: {exc}")
        print(error_message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

