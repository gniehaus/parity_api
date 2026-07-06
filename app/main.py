import os
import uuid
from snaptrade_client import SnapTrade
from .db import get_conn
from .security import encrypt_secret, decrypt_secret


def client() -> SnapTrade:
    return SnapTrade(
        client_id=os.environ["SNAPTRADE_CLIENT_ID"],
        consumer_key=os.environ["SNAPTRADE_CONSUMER_KEY"],
    )


def _body(resp):
    return getattr(resp, "body", resp)


def get_or_create_snaptrade_user(app_user_id: str) -> tuple[str, str]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM snaptrade_users WHERE app_user_id = ?", (app_user_id,)).fetchone()
        if row:
            return row["snaptrade_user_id"], decrypt_secret(row["encrypted_user_secret"])

    snaptrade_user_id = f"parity-{app_user_id}-{uuid.uuid4().hex[:8]}"
    resp = client().authentication.register_snap_trade_user(user_id=snaptrade_user_id)
    body = _body(resp)
    user_secret = body["userSecret"]

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO snaptrade_users (app_user_id, snaptrade_user_id, encrypted_user_secret) VALUES (?, ?, ?)",
            (app_user_id, snaptrade_user_id, encrypt_secret(user_secret)),
        )
        conn.commit()

    return snaptrade_user_id, user_secret


def connection_url(app_user_id: str, custom_redirect: str | None = None) -> dict:
    user_id, user_secret = get_or_create_snaptrade_user(app_user_id)
    kwargs = {
        "user_id": user_id,
        "user_secret": user_secret,
        "connection_type": "read",
        "connection_portal_version": "v2",
    }
    if custom_redirect:
        kwargs["custom_redirect"] = custom_redirect
    resp = client().authentication.login_snap_trade_user(**kwargs)
    body = _body(resp)
    return {"redirect_url": body.get("redirectURI"), "snaptrade_user_id": user_id}


def accounts(app_user_id: str) -> list[dict]:
    user_id, user_secret = get_or_create_snaptrade_user(app_user_id)
    resp = client().account_information.list_user_accounts(user_id=user_id, user_secret=user_secret)
    return _body(resp)


def positions(app_user_id: str, account_id: str) -> list[dict]:
    user_id, user_secret = get_or_create_snaptrade_user(app_user_id)
    resp = client().account_information.get_user_account_positions(
        user_id=user_id,
        user_secret=user_secret,
        account_id=account_id,
    )
    return _body(resp)


def balances(app_user_id: str, account_id: str) -> dict:
    user_id, user_secret = get_or_create_snaptrade_user(app_user_id)
    # SDK versions vary; try common balance method names.
    ai = client().account_information
    if hasattr(ai, "get_user_account_balance"):
        return _body(ai.get_user_account_balance(user_id=user_id, user_secret=user_secret, account_id=account_id))
    if hasattr(ai, "get_user_account_balances"):
        return _body(ai.get_user_account_balances(user_id=user_id, user_secret=user_secret, account_id=account_id))
    return {}


def normalize_positions(raw_positions: list[dict]) -> list[dict]:
    normalized = []
    for p in raw_positions or []:
        symbol_obj = p.get("symbol") or {}
        symbol = symbol_obj.get("symbol") or symbol_obj.get("ticker") or p.get("symbol") or p.get("ticker")
        if isinstance(symbol, dict):
            symbol = symbol.get("symbol") or symbol.get("ticker")
        if not symbol:
            continue
        units = p.get("units", p.get("quantity", 0)) or 0
        price = p.get("price", p.get("last_price", 0)) or 0
        market_value = p.get("market_value", p.get("value", None))
        if market_value is None:
            market_value = float(units or 0) * float(price or 0)
        normalized.append({
            "symbol": str(symbol).upper(),
            "quantity": float(units or 0),
            "price": float(price or 0),
            "market_value": float(market_value or 0),
            "asset_type": ((symbol_obj.get("type") or {}).get("code") if isinstance(symbol_obj.get("type"), dict) else symbol_obj.get("type")) or "unknown",
            "name": symbol_obj.get("description") or symbol_obj.get("name") or str(symbol).upper(),
            "raw": p,
        })
    return normalized


def extract_cash(account: dict | None, balance_payload: dict | list | None) -> float:
    # Prefer account.balance.total.amount because your account list already has it.
    if account:
        try:
            return float(((account.get("balance") or {}).get("total") or {}).get("amount") or 0)
        except Exception:
            pass
    if isinstance(balance_payload, dict):
        for path in [("cash", "amount"), ("total", "amount"), ("amount",)]:
            cur = balance_payload
            try:
                for key in path:
                    cur = cur[key]
                return float(cur or 0)
            except Exception:
                continue
    return 0.0
