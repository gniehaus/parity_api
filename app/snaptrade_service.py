import os
from snaptrade_client import SnapTrade

from .db import get_conn
from .security import encrypt_secret, decrypt_secret

snaptrade = SnapTrade(
    client_id=os.getenv("SNAPTRADE_CLIENT_ID"),
    consumer_key=os.getenv("SNAPTRADE_CONSUMER_KEY"),
)


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


import json
from decimal import Decimal
from .db import get_conn


def save_accounts_and_holdings(parity_user_id: str):
    accounts = list_accounts(parity_user_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            for account in accounts:
                account_id = account.get("id")
                total_value = (
                    account.get("balance", {})
                    .get("total", {})
                    .get("amount")
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
                        account.get("institution_name"),
                        account.get("name"),
                        account.get("number"),
                        Decimal(str(total_value or 0)),
                        json.dumps(account),
                    ),
                )

                cur.execute(
                    """
                    DELETE FROM holdings
                    WHERE parity_user_id = %s
                    AND account_id = %s
                    """,
                    (parity_user_id, account_id),
                )

                positions = get_account_positions(parity_user_id, account_id)

                for p in positions:
                    symbol_obj = p.get("symbol") or {}
                    symbol = (
                        symbol_obj.get("symbol")
                        or symbol_obj.get("ticker")
                        or symbol_obj.get("raw_symbol")
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
                            Decimal(str(p.get("units") or p.get("quantity") or 0)),
                            Decimal(str(p.get("price") or 0)),
                            Decimal(str(p.get("market_value") or p.get("value") or 0)),
                            symbol_obj.get("type", {}).get("code", "unknown"),
                            json.dumps(p),
                        ),
                    )

            conn.commit()

    return {
        "status": "synced",
        "accounts_synced": len(accounts),
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