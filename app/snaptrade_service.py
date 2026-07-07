import json
import os
from decimal import Decimal
from snaptrade_client import SnapTrade

from .db import get_conn
from .security import encrypt_secret, decrypt_secret

snaptrade = SnapTrade(
    client_id=os.getenv("SNAPTRADE_CLIENT_ID"),
    consumer_key=os.getenv("SNAPTRADE_CONSUMER_KEY"),
)


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _num(value):
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _symbol_from_position(position):
    symbol = _string(_symbol_from_position(position))

    if isinstance(symbol, dict):
        return (
            symbol.get("symbol")
            or symbol.get("raw_symbol")
            or symbol.get("ticker")
            or symbol.get("description")
        )

    if symbol:
        return str(symbol)

    universal_symbol = _get(position, "universal_symbol")

    if isinstance(universal_symbol, dict):
        return (
            universal_symbol.get("symbol")
            or universal_symbol.get("ticker")
            or universal_symbol.get("raw_symbol")
        )

    return None


def _market_value(position):
    for key in ["market_value", "marketValue", "value"]:
        value = _get(position, key)
        if value is not None:
            return _num(value)

    quantity = _num(
        _get(position, "units")
        or _get(position, "quantity")
        or _get(position, "qty")
    )

    price = _num(
        _get(position, "price")
        or _get(position, "last_price")
        or _get(position, "lastPrice")
        or _get(position, "average_purchase_price")
    )

    if quantity is not None and price is not None:
        return quantity * price

    return None


def get_or_create_snaptrade_user(parity_user_id: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT snaptrade_user_id, encrypted_user_secret
                FROM snaptrade_users
                WHERE parity_user_id = %s
                """,
                (parity_user_id,),
            )
            row = cur.fetchone()

            if row:
                return {
                    "snaptrade_user_id": row["snaptrade_user_id"],
                    "user_secret": decrypt_secret(row["encrypted_user_secret"]),
                }

            snaptrade_user_id = f"parity-{parity_user_id}"

            response = snaptrade.authentication.register_snap_trade_user(
                user_id=snaptrade_user_id
            )

            user_secret = response.body["userSecret"]

            cur.execute(
                """
                INSERT INTO snaptrade_users (
                    parity_user_id,
                    snaptrade_user_id,
                    encrypted_user_secret
                )
                VALUES (%s, %s, %s)
                """,
                (
                    parity_user_id,
                    snaptrade_user_id,
                    encrypt_secret(user_secret),
                ),
            )
            conn.commit()

            return {
                "snaptrade_user_id": snaptrade_user_id,
                "user_secret": user_secret,
            }


def create_connection_url(parity_user_id: str):
    user = get_or_create_snaptrade_user(parity_user_id)

    response = snaptrade.authentication.login_snap_trade_user(
        user_id=user["snaptrade_user_id"],
        user_secret=user["user_secret"],
    )

    return {
        "snaptrade_user_id": user["snaptrade_user_id"],
        "redirect_url": response.body["redirectURI"],
    }

    
def _string(value, default=None):
    if value is None:
        return default
    if isinstance(value, dict):
        return (
            value.get("code")
            or value.get("name")
            or value.get("type")
            or value.get("description")
            or json.dumps(value, default=str)
        )
    return str(value)

def list_accounts(parity_user_id: str):
    user = get_or_create_snaptrade_user(parity_user_id)

    response = snaptrade.account_information.list_user_accounts(
        user_id=user["snaptrade_user_id"],
        user_secret=user["user_secret"],
    )

    return response.body


def get_account_positions(parity_user_id: str, account_id: str):
    user = get_or_create_snaptrade_user(parity_user_id)

    response = snaptrade.account_information.get_user_account_positions(
        user_id=user["snaptrade_user_id"],
        user_secret=user["user_secret"],
        account_id=account_id,
    )

    return response.body


def sync_brokerage_accounts_and_holdings(parity_user_id: str):
    accounts = list_accounts(parity_user_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            for account in accounts:
                account_id = str(_get(account, "id"))

                brokerage = _get(account, "brokerage")
                institution_name = (
                    _get(account, "institution_name")
                    or _get(account, "institution")
                    or (_get(brokerage, "name") if brokerage else None)
                )

                account_name = (
                    _get(account, "name")
                    or _get(account, "account_name")
                    or _get(account, "number")
                    or "Brokerage Account"
                )

                account_number_mask = (
                    _get(account, "number")
                    or _get(account, "account_number")
                    or _get(account, "account_number_mask")
                )

                balance = _get(account, "balance") or {}
                total = balance.get("total", {}) if isinstance(balance, dict) else {}
                total_value = total.get("amount")
                
                if total_value is None:
                    total_value = (
                        _get(account, "total_value")
                        or _get(account, "totalValue")
                        or _get(account, "cash")
                    )

                cur.execute(
                    """
                    INSERT INTO brokerage_accounts (
                        id,
                        parity_user_id,
                        institution_name,
                        account_name,
                        account_number_mask,
                        total_value,
                        raw_json,
                        last_synced_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
                    ON CONFLICT (id)
                    DO UPDATE SET
                        parity_user_id = EXCLUDED.parity_user_id,
                        institution_name = EXCLUDED.institution_name,
                        account_name = EXCLUDED.account_name,
                        account_number_mask = EXCLUDED.account_number_mask,
                        total_value = EXCLUDED.total_value,
                        raw_json = EXCLUDED.raw_json,
                        last_synced_at = NOW()
                    """,
                    (
                        account_id,
                        parity_user_id,
                        institution_name,
                        account_name,
                        account_number_mask,
                        _num(total_value),
                        json.dumps(account, default=str),
                    ),
                )

                positions = get_account_positions(parity_user_id, account_id)

                cur.execute(
                    """
                    DELETE FROM holdings
                    WHERE parity_user_id = %s
                    AND account_id = %s
                    """,
                    (parity_user_id, account_id),
                )

                for position in positions:
                    symbol = _string(_symbol_from_position(position))
                    
                    quantity = _num(
                        _get(position, "units")
                        or _get(position, "quantity")
                        or _get(position, "qty")
                    )
                    
                    price = _num(
                        _get(position, "price")
                        or _get(position, "last_price")
                        or _get(position, "lastPrice")
                        or _get(position, "average_purchase_price")
                    )
                    
                    market_value = _market_value(position)
                    
                    asset_type = _string(
                        _get(position, "asset_type")
                        or _get(position, "type")
                        or _get(position, "security_type"),
                        "equity",
                    )
                    
                    cur.execute(
                        """
                        INSERT INTO holdings (
                            parity_user_id,
                            account_id,
                            symbol,
                            quantity,
                            price,
                            market_value,
                            asset_type,
                            raw_json,
                            synced_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
                        """,
                        (
                            parity_user_id,
                            account_id,
                            symbol,
                            quantity,
                            price,
                            market_value,
                            asset_type,
                            json.dumps(position, default=str),
                        ),
                    )

            conn.commit()

    portfolio = get_portfolio_summary(parity_user_id)

    return {
        "status": "synced",
        "accounts_count": len(accounts),
        "holdings_count": len(portfolio.get("holdings", [])),
        "portfolio": portfolio,
    }


def get_portfolio_summary(parity_user_id: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(SUM(total_value), 0) AS total_assets
                FROM brokerage_accounts
                WHERE parity_user_id = %s
                """,
                (parity_user_id,),
            )
            account_totals = cur.fetchone()

            cur.execute(
                """
                SELECT
                    symbol,
                    SUM(quantity) AS quantity,
                    AVG(price) AS price,
                    SUM(market_value) AS market_value,
                    MAX(asset_type) AS asset_type
                FROM holdings
                WHERE parity_user_id = %s
                GROUP BY symbol
                ORDER BY SUM(market_value) DESC
                """,
                (parity_user_id,),
            )
            holdings = cur.fetchall()

    invested_value = sum(float(h["market_value"] or 0) for h in holdings)
    total_assets = float(account_totals["total_assets"] or 0)

    if total_assets <= 0:
        total_assets = invested_value

    cash = max(total_assets - invested_value, 0)

    return {
        "total_assets": total_assets,
        "cash": cash,
        "invested_value": invested_value,
        "cash_percentage": cash / total_assets if total_assets else 0,
        "holdings": [
            {
                "symbol": h["symbol"],
                "quantity": float(h["quantity"] or 0),
                "price": float(h["price"] or 0),
                "market_value": float(h["market_value"] or 0),
                "asset_type": h["asset_type"],
                "weight": (
                    float(h["market_value"] or 0) / total_assets
                    if total_assets
                    else 0
                ),
            }
            for h in holdings
        ],
    }