"""Send exact SOXX/SOXL and QQQ/TQQQ execution quantities to Telegram."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.execution_alerts import (  # noqa: E402
    US_STRATEGIES,
    calculate_us_target,
    kiwoom_profile,
    load_us_account,
    load_secrets,
    send_telegram,
    telegram_config,
    whole_share_plan,
)


KST = ZoneInfo("Asia/Seoul")
NEW_YORK = ZoneInfo("America/New_York")
LOG_DIR = ROOT / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def write_log(message: str) -> None:
    now = datetime.now(KST)
    try:
        with (LOG_DIR / f"us_execution_{now:%Y%m%d}.log").open("a", encoding="utf-8") as log:
            log.write(f"[{now:%Y-%m-%d %H:%M:%S}] {message}\n")
    except OSError:
        pass


def build_messages(now: datetime) -> list[str]:
    secrets = load_secrets(ROOT)
    messages: list[str] = []

    for strategy in US_STRATEGIES:
        result = calculate_us_target(strategy, datetime.now(NEW_YORK))
        symbols = tuple(strategy["symbols"])
        profile = kiwoom_profile(secrets, str(strategy["profile"]))
        snapshot = load_us_account(profile, symbols)
        if snapshot.get("cash") is None:
            raise RuntimeError(
                f"{strategy['name']}: USD 주문가능 예수금을 읽지 못했습니다 "
                f"({snapshot.get('cash_warning', '원인 미상')})"
            )
        cash = float(snapshot["cash"])
        shares = {symbol: float(snapshot["shares"].get(symbol, 0.0)) for symbol in symbols}
        suppress_price_drift_rebalancing = strategy["name"] in {
            "SOXX / SOXL",
            "QQQ / TQQQ Holdings V2",
        }
        plan = whole_share_plan(
            result["weights"],
            result["prices"],
            shares,
            cash,
            previous_weights=(
                result["previous_weights"]
                if suppress_price_drift_rebalancing
                else None
            ),
        )
        base, leveraged = symbols

        sections = [
                f"[{strategy['name']} EXECUTION]",
                f"조회시각: {now:%Y-%m-%d %H:%M} KST",
                f"계좌: {profile['label']}",
                f"종가 신호: {result['signal_date']} / {result['regime']}",
                (
                    f"목표비중: {base} {result['weights'][base]:.1%}, "
                    f"{leveraged} {result['weights'][leveraged]:.1%}, "
                    f"현금 {1 - sum(result['weights'].values()):.1%}"
                ),
                *([
                    "전략비중 변경: 있음"
                    if plan["target_changed"]
                    else "전략비중 변경: 없음 (가격변동 리밸런싱 안 함)"
                ] if suppress_price_drift_rebalancing else []),
                (
                    f"현재: {base} {plan['orders'][base]['current']}주, "
                    f"{leveraged} {plan['orders'][leveraged]['current']}주, "
                    f"USD 예수금 ${cash:,.2f}"
                ),
                (
                    f"목표: {base} {plan['orders'][base]['target']}주, "
                    f"{leveraged} {plan['orders'][leveraged]['target']}주, "
                    f"목표현금 약 ${plan['target_cash']:,.2f}"
                ),
                "EXECUTION:",
                _order_line(base, plan["orders"][base]),
                _order_line(leveraged, plan["orders"][leveraged]),
                "",
                "※ 다음 미국 정규장 시가 지침이며 주문은 자동 제출되지 않습니다.",
            ]
        messages.append("\n".join(sections))

    return messages


def _order_line(symbol: str, order: dict[str, object]) -> str:
    quantity = abs(int(order["order"]))
    return f"- {symbol}: {order['action']} {quantity}주" if quantity else f"- {symbol}: 유지 (주문 없음)"


def send_with_retry(text: str, attempts: int = 3) -> None:
    token, chat_id = telegram_config(load_secrets(ROOT))
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            send_telegram(token, chat_id, text)
            return
        except Exception as exc:
            last_error = exc
            write_log(f"Telegram attempt {attempt} failed: {exc!r}")
            if attempt < attempts:
                time.sleep(5)
    if last_error:
        raise last_error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="계좌까지 조회하되 Telegram은 보내지 않음")
    args = parser.parse_args()
    now = datetime.now(KST)
    try:
        messages = build_messages(now)
        for message in messages:
            if not args.dry_run:
                send_with_retry(message)
            print(message)
            print()
        if not args.dry_run:
            write_log("Execution alerts sent")
        return 0
    except Exception as exc:
        write_log(f"Execution alert failed: {exc!r}")
        print(f"[미국 전략 EXECUTION] 실패: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

