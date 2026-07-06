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