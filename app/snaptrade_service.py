import json
import os
import re
from decimal import Decimal
from snaptrade_client import SnapTrade

from .db import get_conn
from .security import encrypt_secret, decrypt_secret

snaptrade = SnapTrade(
    client_id=os.getenv("SNAPTRADE_CLIENT_ID"),
    consumer_key=os.getenv("SNAPTRADE_CONSUMER_KEY"),
)


def _to_plain(obj):
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool, Decimal)):
        return obj
    if isinstance(obj, list):
        return [_to_plain(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _to_plain(v) for k, v in obj.items()}
    if hasattr(obj, "to_dict"):
        try:
            return _to_plain(obj.to_dict())
        except Exception:
            pass
    if hasattr(obj, "__dict__"):
        try:
            return _to_plain(obj.__dict__)
        except Exception:
            pass
    return str(obj)


def _get(obj, key, default=None):
    obj = _to_plain(obj)
    if isinstance(obj, dict):
        return obj.get(key, default)
    return default


def _num(value):
    value = _to_plain(value)
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("amount") or value.get("value") or value.get("total") or value.get("price")
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _string(value, default=None):
    value = _to_plain(value)
    if value is None:
        return default
    if isinstance(value, dict):
        value = (
            value.get("symbol")
            or value.get("raw_symbol")
            or value.get("ticker")
            or value.get("code")
            or value.get("name")
            or value.get("description")
            or value.get("type")
        )
    if value is None:
        return default
    return str(value)


def _json(value):
    return json.dumps(_to_plain(value), default=str)


def _date(value):
    value = _string(value)
    if not value:
        return None
    return value[:10]


def _symbol_obj(position):
    return _get(position, "symbol") or _get(position, "universal_symbol") or _get(position, "security") or {}


def _symbol_from_position(position):
    symbol = _symbol_obj(position)
    if isinstance(symbol, dict):
        return symbol.get("symbol") or symbol.get("raw_symbol") or symbol.get("ticker")
    return _string(symbol)


def _market_value(position):
    for key in ["market_value", "marketValue", "value"]:
        parsed = _num(_get(position, key))
        if parsed is not None:
            return parsed

    quantity = _num(_get(position, "units") or _get(position, "quantity") or _get(position, "qty"))
    price = _num(_get(position, "price") or _get(position, "last_price") or _get(position, "lastPrice") or _get(position, "average_purchase_price"))

    if quantity is not None and price is not None:
        return quantity * price
    return None


def _account_total_value(account):
    balance = _get(account, "balance") or {}
    if isinstance(balance, dict):
        total = balance.get("total") or {}
        if isinstance(total, dict) and total.get("amount") is not None:
            return _num(total.get("amount"))
        if balance.get("amount") is not None:
            return _num(balance.get("amount"))

    return _num(_get(account, "total_value") or _get(account, "totalValue") or _get(account, "cash"))

    
def _extract_kind(position):
    position = _to_plain(position)
    symbol_obj = _symbol_obj(position)

    kind = (
        _get(position, "kind")
        or _get(symbol_obj, "kind")
        or _get(position, "instrument_type")
        or _get(position, "instrumentType")
    )

    return _string(kind, "").lower().replace("_", "").replace("-", "")

def _classify_position(position):
    position = _to_plain(position)

    kind = _extract_kind(position)
    symbol = _extract_symbol(position) or ""
    security_text = _extract_security_type(position)

    if kind == "stock":
        return "us_stock", "common_stock"

    if kind == "etf":
        if symbol in {"EFA", "VEA", "VXUS", "IEFA", "VWO", "IEMG", "EEM"}:
            return "international_equity", "etf"
        if symbol in {"SGOV", "BIL", "SHV", "TFLO", "USFR", "GOVT", "IEF", "TLT", "SHY"}:
            return "treasury", "etf"
        return "us_etf", "etf"

    if kind == "mutualfund":
        return "mutual_fund", "fund"

    if kind == "crypto":
        return "crypto", "crypto"

    if kind == "option":
        return "option", "option"

    if kind == "future":
        return "future", "future"

    if kind in {"cash", "currency"}:
        return "cash", "cash"

    # fallback logic continues here...
    return "unknown", "unknown"


def normalize_position(parity_user_id: str, account_id: str, position: dict):
    position = _to_plain(position)
    symbol_obj = _symbol_obj(position)

    symbol = _string(_symbol_from_position(position))
    raw_symbol = _string(_get(symbol_obj, "raw_symbol") or symbol)
    description = _string(_get(symbol_obj, "description") or _get(position, "description"))
    display_name = description or symbol

    quantity = _num(_get(position, "units") or _get(position, "quantity") or _get(position, "qty"))
    price = _num(_get(position, "price") or _get(position, "last_price") or _get(position, "lastPrice") or _get(position, "average_purchase_price"))
    market_value = _market_value(position)

    asset_class, security_type = _classify_position(position)

    position_direction = "short" if quantity is not None and quantity < 0 else "long"
    exposure_value = abs(market_value) if market_value is not None else None

    currency_obj = _get(symbol_obj, "currency") or _get(position, "currency") or {}
    currency = _string(_get(currency_obj, "code") or currency_obj, "USD")

    option_type = _string(_get(position, "option_type") or _get(position, "optionType"))
    if option_type:
        option_type = option_type.lower()

    return {
        "parity_user_id": parity_user_id,
        "account_id": account_id,
        "symbol": symbol,
        "raw_symbol": raw_symbol,
        "display_name": display_name,
        "description": description,
        "cusip": _string(_get(position, "cusip") or _get(symbol_obj, "cusip")),
        "isin": _string(_get(position, "isin") or _get(symbol_obj, "isin")),
        "figi": _string(_get(position, "figi_code") or _get(symbol_obj, "figi_code")),
        "asset_class": asset_class,
        "security_type": security_type,
        "asset_subtype": _string(_get(position, "asset_type") or _get(position, "type")),
        "currency": currency,
        "quantity": quantity,
        "price": price,
        "market_value": market_value,
        "cost_basis": _num(_get(position, "cost_basis") or _get(position, "costBasis")),
        "unrealized_gain_loss": _num(_get(position, "unrealized_gain_loss") or _get(position, "unrealizedGainLoss")),
        "unrealized_gain_loss_pct": _num(_get(position, "unrealized_gain_loss_pct") or _get(position, "unrealizedGainLossPercent")),
        "position_direction": position_direction,
        "exposure_value": exposure_value,
        "is_cash": asset_class == "cash",
        "is_margin": False,
        "is_short": position_direction == "short",
        "is_option": asset_class == "option",
        "underlying_symbol": _string(_get(position, "underlying_symbol") or _get(position, "underlyingSymbol")),
        "option_type": option_type,
        "expiration_date": _date(_get(position, "expiration_date") or _get(position, "expirationDate")),
        "strike_price": _num(_get(position, "strike_price") or _get(position, "strikePrice")),
        "multiplier": _num(_get(position, "multiplier")) or (Decimal("100") if asset_class == "option" else None),
        "contract_count": quantity if asset_class == "option" else None,
        "maturity_date": _date(_get(position, "maturity_date") or _get(position, "maturityDate")),
        "coupon_rate": _num(_get(position, "coupon_rate") or _get(position, "couponRate")),
        "face_value": _num(_get(position, "face_value") or _get(position, "faceValue")),
        "yield_rate": _num(_get(position, "yield_rate") or _get(position, "yieldRate")),
        "expense_ratio": _num(_get(position, "expense_ratio") or _get(position, "expenseRatio")),
        "fund_family": _string(_get(position, "fund_family") or _get(position, "fundFamily")),
        "raw_json": _json(position),
    }


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

            body = _to_plain(response.body)
            user_secret = body["userSecret"]

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

    body = _to_plain(response.body)

    return {
        "snaptrade_user_id": user["snaptrade_user_id"],
        "redirect_url": body["redirectURI"],
    }


def list_accounts(parity_user_id: str):
    user = get_or_create_snaptrade_user(parity_user_id)

    response = snaptrade.account_information.list_user_accounts(
        user_id=user["snaptrade_user_id"],
        user_secret=user["user_secret"],
    )

    return _to_plain(response.body) or []


def get_account_positions(parity_user_id: str, account_id: str):
    user = get_or_create_snaptrade_user(parity_user_id)

    response = snaptrade.account_information.get_user_account_positions(
        user_id=user["snaptrade_user_id"],
        user_secret=user["user_secret"],
        account_id=account_id,
    )

    return _to_plain(response.body) or []


def sync_brokerage_accounts_and_holdings(parity_user_id: str):
    accounts = list_accounts(parity_user_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            for account in accounts:
                account = _to_plain(account)

                account_id = _string(_get(account, "id"))
                if not account_id:
                    continue

                brokerage = _get(account, "brokerage")
                institution_name = _string(
                    _get(account, "institution_name")
                    or _get(account, "institution")
                    or _get(brokerage, "name")
                    or _get(_get(account, "meta"), "institution_name")
                )

                account_name = _string(
                    _get(account, "name")
                    or _get(account, "account_name")
                    or _get(account, "number")
                    or "Brokerage Account"
                )

                account_number_mask = _string(
                    _get(account, "number")
                    or _get(account, "account_number")
                    or _get(account, "account_number_mask")
                )

                total_value = _account_total_value(account)

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
                        total_value,
                        _json(account),
                    ),
                )

                try:
                    positions = get_account_positions(parity_user_id, account_id)
                except Exception as e:
                    print(f"Failed to fetch positions for account {account_id}: {e}")
                    positions = []

                cur.execute("DELETE FROM holdings WHERE parity_user_id = %s AND account_id = %s", (parity_user_id, account_id))
                cur.execute("DELETE FROM normalized_holdings WHERE parity_user_id = %s AND account_id = %s", (parity_user_id, account_id))

                for position in positions:
                    position = _to_plain(position)
                    normalized = normalize_position(parity_user_id, account_id, position)

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
                            normalized["symbol"],
                            normalized["quantity"],
                            normalized["price"],
                            normalized["market_value"],
                            normalized["asset_class"],
                            normalized["raw_json"],
                        ),
                    )

                    cur.execute(
                        """
                        INSERT INTO normalized_holdings (
                            parity_user_id, account_id,
                            symbol, raw_symbol, display_name, description, cusip, isin, figi,
                            asset_class, security_type, asset_subtype, currency,
                            quantity, price, market_value, cost_basis,
                            unrealized_gain_loss, unrealized_gain_loss_pct,
                            position_direction, exposure_value, is_cash, is_margin, is_short,
                            is_option, underlying_symbol, option_type, expiration_date,
                            strike_price, multiplier, contract_count,
                            maturity_date, coupon_rate, face_value, yield_rate,
                            expense_ratio, fund_family,
                            source, raw_json, synced_at
                        )
                        VALUES (
                            %s, %s,
                            %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s,
                            %s, %s, %s, %s,
                            %s, %s,
                            %s, %s, %s, %s, %s,
                            %s, %s, %s, %s,
                            %s, %s, %s,
                            %s, %s, %s, %s,
                            %s, %s,
                            'snaptrade', %s::jsonb, NOW()
                        )
                        """,
                        (
                            normalized["parity_user_id"],
                            normalized["account_id"],
                            normalized["symbol"],
                            normalized["raw_symbol"],
                            normalized["display_name"],
                            normalized["description"],
                            normalized["cusip"],
                            normalized["isin"],
                            normalized["figi"],
                            normalized["asset_class"],
                            normalized["security_type"],
                            normalized["asset_subtype"],
                            normalized["currency"],
                            normalized["quantity"],
                            normalized["price"],
                            normalized["market_value"],
                            normalized["cost_basis"],
                            normalized["unrealized_gain_loss"],
                            normalized["unrealized_gain_loss_pct"],
                            normalized["position_direction"],
                            normalized["exposure_value"],
                            normalized["is_cash"],
                            normalized["is_margin"],
                            normalized["is_short"],
                            normalized["is_option"],
                            normalized["underlying_symbol"],
                            normalized["option_type"],
                            normalized["expiration_date"],
                            normalized["strike_price"],
                            normalized["multiplier"],
                            normalized["contract_count"],
                            normalized["maturity_date"],
                            normalized["coupon_rate"],
                            normalized["face_value"],
                            normalized["yield_rate"],
                            normalized["expense_ratio"],
                            normalized["fund_family"],
                            normalized["raw_json"],
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
                    MAX(display_name) AS display_name,
                    MAX(asset_class) AS asset_class,
                    MAX(security_type) AS security_type,
                    SUM(quantity) AS quantity,
                    AVG(price) AS price,
                    SUM(market_value) AS market_value,
                    SUM(exposure_value) AS exposure_value,
                    BOOL_OR(is_option) AS is_option,
                    BOOL_OR(is_cash) AS is_cash
                FROM normalized_holdings
                WHERE parity_user_id = %s
                GROUP BY symbol
                ORDER BY SUM(market_value) DESC
                """,
                (parity_user_id,),
            )
            holdings = cur.fetchall()

            cur.execute(
                """
                SELECT
                    asset_class,
                    COALESCE(SUM(market_value), 0) AS market_value
                FROM normalized_holdings
                WHERE parity_user_id = %s
                GROUP BY asset_class
                ORDER BY COALESCE(SUM(market_value), 0) DESC
                """,
                (parity_user_id,),
            )
            allocation = cur.fetchall()

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
        "asset_allocation": [
            {
                "asset_class": a["asset_class"],
                "market_value": float(a["market_value"] or 0),
                "weight": float(a["market_value"] or 0) / total_assets if total_assets else 0,
            }
            for a in allocation
        ],
        "holdings": [
            {
                "symbol": h["symbol"],
                "display_name": h["display_name"],
                "asset_class": h["asset_class"],
                "security_type": h["security_type"],
                "quantity": float(h["quantity"] or 0),
                "price": float(h["price"] or 0),
                "market_value": float(h["market_value"] or 0),
                "exposure_value": float(h["exposure_value"] or 0),
                "is_option": h["is_option"],
                "is_cash": h["is_cash"],
                "weight": float(h["market_value"] or 0) / total_assets if total_assets else 0,
            }
            for h in holdings
        ],
    }