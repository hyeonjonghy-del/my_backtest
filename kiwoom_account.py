"""Shared read-only Kiwoom account access for Streamlit strategy pages."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from datetime import datetime, timezone

import streamlit as st


KIWOOM_DEFAULT_BASE_URL = "https://api.kiwoom.com"
KIWOOM_ACCOUNT_PATH = "/api/us/acnt"
KIWOOM_SOURCE = "Kiwoom account (read only)"
MANUAL_SOURCE = "Manual input"


def parse_kiwoom_number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _validated_config(section: object) -> dict[str, str] | None:
    if not isinstance(section, Mapping):
        return None
    app_key = str(section.get("app_key", "")).strip()
    app_secret = str(section.get("app_secret", "")).strip()
    base_url = str(section.get("base_url", KIWOOM_DEFAULT_BASE_URL)).strip().rstrip("/")
    if not app_key or not app_secret:
        return None
    if base_url != KIWOOM_DEFAULT_BASE_URL:
        raise RuntimeError("Real-account mode requires base_url=https://api.kiwoom.com")
    return {"app_key": app_key, "app_secret": app_secret, "base_url": base_url}


def read_kiwoom_profiles() -> dict[str, dict[str, str]]:
    """Read a legacy single account and optional [kiwoom.accounts.*] profiles."""
    try:
        root = st.secrets["kiwoom"]
    except (KeyError, TypeError, AttributeError):
        return {}

    profiles: dict[str, dict[str, str]] = {}
    legacy = _validated_config(root)
    if legacy is not None:
        legacy["label"] = str(root.get("label", "Default account"))
        profiles["default"] = legacy

    accounts = root.get("accounts", {}) if isinstance(root, Mapping) else {}
    if isinstance(accounts, Mapping):
        for profile_name, section in accounts.items():
            config = _validated_config(section)
            if config is None:
                continue
            config["label"] = str(section.get("label", profile_name))
            profiles[str(profile_name)] = config
    return profiles


def kiwoom_post(
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
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
            response_headers = {key.lower(): value for key, value in response.headers.items()}
    except urllib.error.HTTPError as exc:
        message = "HTTP request failed"
        try:
            error_payload = json.loads(exc.read().decode("utf-8"))
            message = str(
                error_payload.get("return_msg")
                or error_payload.get("message")
                or error_payload.get("error_description")
                or message
            )
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            pass
        raise RuntimeError(f"Kiwoom API error ({exc.code}): {message[:200]}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not connect to Kiwoom API: {exc.reason}") from None
    except (TimeoutError, json.JSONDecodeError):
        raise RuntimeError("Kiwoom API timed out or returned an invalid response.") from None

    return_code = payload.get("return_code")
    if return_code not in (None, 0, "0"):
        message = str(payload.get("return_msg") or "Request was rejected")
        raise RuntimeError(f"Kiwoom API rejected the request ({return_code}): {message[:200]}")
    return payload, response_headers


@st.cache_data(ttl=23 * 60 * 60, show_spinner=False)
def issue_kiwoom_token(app_key: str, app_secret: str, base_url: str) -> str:
    payload, _ = kiwoom_post(
        base_url,
        "/oauth2/token",
        {"grant_type": "client_credentials", "appkey": app_key, "secretkey": app_secret},
    )
    token = str(payload.get("token", "")).strip()
    if not token:
        raise RuntimeError("Kiwoom did not return an access token.")
    return token


def request_kiwoom_tr(
    base_url: str,
    token: str,
    api_id: str,
    body: dict[str, object] | None = None,
) -> dict[str, object]:
    merged: dict[str, object] = {}
    rows: list[object] = []
    continuation: tuple[str, str] | None = None
    for _ in range(20):
        payload, headers = kiwoom_post(
            base_url,
            KIWOOM_ACCOUNT_PATH,
            body or {},
            token=token,
            api_id=api_id,
            continuation=continuation,
        )
        merged.update({key: value for key, value in payload.items() if key != "result_list"})
        page_rows = payload.get("result_list")
        if isinstance(page_rows, list):
            rows.extend(page_rows)
        if headers.get("cont-yn", "").upper() != "Y" or not headers.get("next-key"):
            break
        continuation = ("Y", headers["next-key"])
    else:
        raise RuntimeError("Kiwoom continuation response exceeded the safety limit.")
    if rows:
        merged["result_list"] = rows
    return merged


def find_kiwoom_cash_details(payload: object) -> tuple[float | None, str | None]:
    candidate_fields = (
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
    dictionaries: list[dict[str, object]] = []

    def collect(value: object) -> None:
        if isinstance(value, dict):
            dictionaries.append(value)
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(payload)
    for row in dictionaries:
        lowered = {str(key).lower(): value for key, value in row.items()}
        currency = str(
            lowered.get("crnc_code")
            or lowered.get("crnc_cd")
            or lowered.get("currency")
            or ""
        ).strip().upper()
        usd_named_amount = parse_kiwoom_number(lowered.get("usd_ord_psbl_amt"))
        if usd_named_amount is not None:
            return max(usd_named_amount, 0.0), "usd_ord_psbl_amt"
        if currency != "USD":
            continue
        for field in candidate_fields:
            if field in lowered:
                amount = parse_kiwoom_number(lowered[field])
                if amount is not None:
                    return max(amount, 0.0), field
    return None, None


def load_kiwoom_account(
    config: dict[str, str],
    symbols: tuple[str, ...],
) -> dict[str, object]:
    token = issue_kiwoom_token(config["app_key"], config["app_secret"], config["base_url"])
    holdings_payload = request_kiwoom_tr(config["base_url"], token, "ust21070")
    rows = holdings_payload.get("result_list", [])
    if not isinstance(rows, list):
        rows = []

    normalized_symbols = tuple(symbol.upper() for symbol in symbols)
    shares = {symbol: 0.0 for symbol in normalized_symbols}
    prices: dict[str, float] = {}
    other_positions: list[str] = []
    non_usd_positions_skipped = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("stk_cd", "")).strip().upper()
        quantity = parse_kiwoom_number(row.get("poss_qty"))
        if quantity is None:
            quantity = parse_kiwoom_number(row.get("qty"))
        quantity = max(quantity or 0.0, 0.0)
        currency = str(
            row.get("crnc_code")
            or row.get("crnc_cd")
            or row.get("currency")
            or ""
        ).strip().upper()
        if currency != "USD":
            if quantity > 0:
                non_usd_positions_skipped += 1
            continue
        if symbol in shares:
            shares[symbol] += quantity
            price = parse_kiwoom_number(row.get("now_pric"))
            if price is not None:
                prices[symbol] = abs(price)
        elif quantity > 0:
            other_positions.append(symbol or str(row.get("frgn_stk_nm", "Unknown")))

    cash = None
    cash_field = None
    cash_warning = None
    try:
        cash_payload = request_kiwoom_tr(config["base_url"], token, "ust21110")
        cash, cash_field = find_kiwoom_cash_details(cash_payload)
        if cash is None:
            cash_warning = "The USD cash field could not be recognized; enter cash manually."
    except RuntimeError as exc:
        cash_warning = f"Cash lookup failed; enter cash manually. ({exc})"

    return {
        "fetched_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "shares": shares,
        "prices": prices,
        "cash": cash,
        "cash_field": cash_field,
        "cash_warning": cash_warning,
        "other_positions": sorted(set(other_positions)),
        "non_usd_positions_skipped": non_usd_positions_skipped,
    }


def cash_display_label(field: str) -> str:
    return {
        "fc_ord_alowa": "USD orderable cash",
        "fc_entra": "USD cash deposit",
        "usd_ord_psbl_amt": "USD orderable cash",
    }.get(field, "USD cash")


def render_account_controls(symbols: tuple[str, ...], widget_key: str) -> dict[str, object]:
    symbols = tuple(symbol.upper() for symbol in symbols)
    source = st.radio(
        "Account source",
        [KIWOOM_SOURCE, MANUAL_SOURCE],
        horizontal=False,
        key=f"{widget_key}_account_source",
    )
    snapshot = None
    profile_name = ""
    profile_label = ""
    shares = {symbol: 0.0 for symbol in symbols}
    current_cash = 0.0
    account_value = 0.0
    display_label = "USD cash"

    if source == KIWOOM_SOURCE:
        profiles = read_kiwoom_profiles()
        if profiles:
            profile_name = st.selectbox(
                "Kiwoom account",
                list(profiles),
                format_func=lambda name: profiles[name]["label"],
                key=f"{widget_key}_profile",
            )
            profile_label = profiles[profile_name]["label"]
        st.caption(
            f"Read-only: only USD cash and USD-denominated {', '.join(symbols)} are used; "
            "KRW assets are excluded and orders are never submitted."
        )
        snapshot_key = f"{widget_key}_snapshot_{profile_name or 'missing'}"
        snapshot = st.session_state.get(snapshot_key)
        if st.button("Load Kiwoom real account", use_container_width=True, key=f"{widget_key}_load"):
            try:
                if not profiles:
                    raise RuntimeError(
                        "Add [kiwoom] or [kiwoom.accounts.NAME] credentials to .streamlit/secrets.toml."
                    )
                with st.spinner("Loading Kiwoom holdings and cash..."):
                    snapshot = load_kiwoom_account(profiles[profile_name], symbols)
                st.session_state[snapshot_key] = snapshot
                st.success("Account snapshot loaded.")
            except RuntimeError as exc:
                st.error(str(exc))

        if snapshot:
            snapshot_shares = snapshot.get("shares", {})
            shares = {symbol: float(snapshot_shares.get(symbol, 0.0)) for symbol in symbols}
            snapshot_cash = snapshot.get("cash")
            cash_field = str(snapshot.get("cash_field") or "")
            display_label = cash_display_label(cash_field)
            if snapshot_cash is None:
                st.warning(str(snapshot.get("cash_warning") or "Enter USD cash manually."))
                current_cash = st.number_input(
                    "Current USD cash (fallback)",
                    min_value=0.0,
                    value=0.0,
                    step=1000.0,
                    key=f"{widget_key}_cash_fallback_{profile_name}",
                )
                display_label = "USD cash (manual)"
            else:
                current_cash = float(snapshot_cash)
            st.metric(display_label, f"${current_cash:,.2f}")
            if cash_field:
                st.caption(f"Kiwoom response field: {cash_field}")

            snapshot_prices = snapshot.get("prices", {})
            account_value = current_cash + sum(
                shares[symbol] * float(snapshot_prices.get(symbol, 0.0)) for symbol in symbols
            )
            st.caption(
                f"Loaded {snapshot.get('fetched_at', '')} | "
                + " | ".join(f"{symbol} {shares[symbol]:g}" for symbol in symbols)
            )
            other_positions = snapshot.get("other_positions", [])
            if other_positions:
                st.warning(
                    "Other USD holdings are excluded from this strategy value: "
                    + ", ".join(map(str, other_positions))
                )
            skipped_count = int(snapshot.get("non_usd_positions_skipped", 0))
            if skipped_count:
                st.warning(f"{skipped_count} non-USD holding row(s) were excluded.")
        else:
            st.info("Load the Kiwoom real account before running the backtest.")
    else:
        account_value = st.number_input(
            "Account value ($)",
            min_value=0.0,
            value=10000.0,
            step=1000.0,
            key=f"{widget_key}_manual_account_value",
        )
        shares = {
            symbol: st.number_input(
                f"Current {symbol} shares",
                min_value=0.0,
                value=0.0,
                step=1.0,
                key=f"{widget_key}_manual_{symbol.lower()}_shares",
            )
            for symbol in symbols
        }
        current_cash = st.number_input(
            "Current cash ($)",
            min_value=0.0,
            value=10000.0,
            step=1000.0,
            key=f"{widget_key}_manual_cash",
        )
        display_label = "Current cash (manual)"

    return {
        "source": source,
        "snapshot": snapshot,
        "profile_name": profile_name,
        "profile_label": profile_label,
        "shares": shares,
        "cash": float(current_cash),
        "account_value": float(account_value),
        "cash_label": display_label,
    }


def render_account_summary(account_state: dict[str, object], estimated_account_value: float) -> None:
    if account_state["source"] != KIWOOM_SOURCE or not account_state["snapshot"]:
        return
    shares = account_state["shares"]
    symbols = list(shares)
    st.subheader("Loaded Kiwoom Account")
    columns = st.columns(len(symbols) + 2)
    for column, symbol in zip(columns, symbols):
        column.metric(f"Current {symbol} shares", f"{float(shares[symbol]):,.0f}")
    columns[-2].metric(
        str(account_state["cash_label"]),
        f"${float(account_state['cash']):,.2f}",
    )
    columns[-1].metric("Estimated account value", f"${estimated_account_value:,.2f}")
    snapshot = account_state["snapshot"]
    cash_field = str(snapshot.get("cash_field") or "manual")
    profile_label = str(account_state.get("profile_label") or "Default account")
    st.caption(
        f"Account: {profile_label} | Loaded {snapshot.get('fetched_at', '')} | "
        f"Cash source: {cash_field} | KRW assets excluded"
    )
