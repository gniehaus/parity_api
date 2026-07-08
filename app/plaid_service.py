import os
import json
from decimal import Decimal

import plaid
from plaid.api import plaid_api
from plaid.model.country_code import CountryCode
from plaid.model.products import Products
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.accounts_balance_get_request import AccountsBalanceGetRequest


from plaid.model.investments_holdings_get_request import InvestmentsHoldingsGetRequest
from plaid.model.investments_transactions_get_request import InvestmentsTransactionsGetRequest


from .db import get_conn
from .security import encrypt_secret, decrypt_secret


def get_plaid_client():
    env = os.getenv("PLAID_ENV", "sandbox")

    if env == "production":
        host = plaid.Environment.Production
    elif env == "development":
        host = plaid.Environment.Development
    else:
        host = plaid.Environment.Sandbox

    configuration = plaid.Configuration(
        host=host,
        api_key={
            "clientId": os.getenv("PLAID_CLIENT_ID"),
            "secret": os.getenv("PLAID_SECRET"),
        },
    )

    api_client = plaid.ApiClient(configuration)
    return plaid_api.PlaidApi(api_client)


def create_link_token(parity_user_id: str):
    client = get_plaid_client()

    request = LinkTokenCreateRequest(
        products=[Products("auth"), Products("transactions"), Products("investments")],
        client_name="Parity",
        country_codes=[CountryCode("US")],
        language="en",
        user=LinkTokenCreateRequestUser(client_user_id=parity_user_id),
    )

    response = client.link_token_create(request)
    return response.to_dict()


def exchange_public_token(parity_user_id: str, public_token: str):
    client = get_plaid_client()

    request = ItemPublicTokenExchangeRequest(public_token=public_token)
    response = client.item_public_token_exchange(request).to_dict()

    access_token = response["access_token"]
    item_id = response["item_id"]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO plaid_items (
                    parity_user_id,
                    item_id,
                    encrypted_access_token,
                    created_at
                )
                VALUES (%s, %s, %s, NOW())
                """,
                (
                    parity_user_id,
                    item_id,
                    encrypt_secret(access_token),
                ),
            )
            conn.commit()

    return {
        "status": "connected",
        "item_id": item_id,
    }


def test_plaid_investments(parity_user_id: str):
    client = get_plaid_client()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT item_id, encrypted_access_token
                FROM plaid_items
                WHERE parity_user_id = %s
                ORDER BY created_at DESC
                """,
                (parity_user_id,),
            )
            items = cur.fetchall()

    results = []

    for item in items:
        access_token = decrypt_secret(item["encrypted_access_token"])

        request = InvestmentsHoldingsGetRequest(
            access_token=access_token,
        )

        response = client.investments_holdings_get(request).to_dict()

        accounts = response.get("accounts", [])
        holdings = response.get("holdings", [])
        securities = response.get("securities", [])

        security_by_id = {
            s.get("security_id"): s
            for s in securities
        }

        normalized_holdings = []

        for holding in holdings:
            security = security_by_id.get(holding.get("security_id"), {}) or {}

            quantity = holding.get("quantity") or 0
            price = holding.get("institution_price") or 0
            market_value = holding.get("institution_value")

            if market_value is None:
                market_value = float(quantity or 0) * float(price or 0)

            normalized_holdings.append({
                "account_id": holding.get("account_id"),
                "security_id": holding.get("security_id"),
                "symbol": security.get("ticker_symbol"),
                "name": security.get("name"),
                "security_type": security.get("type"),
                "quantity": float(quantity or 0),
                "price": float(price or 0),
                "market_value": float(market_value or 0),
                "iso_currency_code": holding.get("iso_currency_code"),
                "raw_holding": holding,
                "raw_security": security,
            })

        results.append({
            "item_id": item["item_id"],
            "accounts": accounts,
            "holdings": normalized_holdings,
            "securities": securities,
        })

    return {
        "status": "ok",
        "items_checked": len(items),
        "items": results,
    }
    

def sync_bank_accounts(parity_user_id: str):
    client = get_plaid_client()

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, item_id, encrypted_access_token
                FROM plaid_items
                WHERE parity_user_id = %s
                """,
                (parity_user_id,),
            )
            items = cur.fetchall()

            synced_accounts = 0

            for item in items:
                access_token = decrypt_secret(item["encrypted_access_token"])

                request = AccountsBalanceGetRequest(access_token=access_token)
                response = client.accounts_balance_get(request).to_dict()

                accounts = response.get("accounts", [])

                for account in accounts:
                    balances = account.get("balances", {}) or {}

                    cur.execute(
                        """
                        INSERT INTO bank_accounts (
                            id,
                            parity_user_id,
                            plaid_item_id,
                            name,
                            official_name,
                            subtype,
                            type,
                            mask,
                            current_balance,
                            available_balance,
                            iso_currency_code,
                            raw_json,
                            last_synced_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
                        ON CONFLICT (id)
                        DO UPDATE SET
                            name = EXCLUDED.name,
                            official_name = EXCLUDED.official_name,
                            subtype = EXCLUDED.subtype,
                            type = EXCLUDED.type,
                            mask = EXCLUDED.mask,
                            current_balance = EXCLUDED.current_balance,
                            available_balance = EXCLUDED.available_balance,
                            iso_currency_code = EXCLUDED.iso_currency_code,
                            raw_json = EXCLUDED.raw_json,
                            last_synced_at = NOW()
                        """,
                        (
                            account.get("account_id"),
                            parity_user_id,
                            item["item_id"],
                            account.get("name"),
                            account.get("official_name"),
                            str(account.get("subtype")),
                            str(account.get("type")),
                            account.get("mask"),
                            Decimal(str(balances.get("current") or 0)),
                            Decimal(str(balances.get("available") or 0)),
                            balances.get("iso_currency_code"),
                            json.dumps(account),
                        ),
                    )

                    synced_accounts += 1

                cur.execute(
                    """
                    UPDATE plaid_items
                    SET last_synced_at = NOW()
                    WHERE id = %s
                    """,
                    (item["id"],),
                )

            conn.commit()

    return {
        "status": "synced",
        "bank_accounts_synced": synced_accounts,
    }


def get_bank_accounts_from_db(parity_user_id: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT *
                FROM bank_accounts
                WHERE parity_user_id = %s
                ORDER BY current_balance DESC
                """,
                (parity_user_id,),
            )
            rows = cur.fetchall()

    return [
        {
            "account_id": row["id"],
            "name": row["name"],
            "official_name": row["official_name"],
            "subtype": row["subtype"],
            "type": row["type"],
            "mask": row["mask"],
            "balances": {
                "current": float(row["current_balance"] or 0),
                "available": float(row["available_balance"] or 0),
                "iso_currency_code": row["iso_currency_code"],
            },
            "last_synced_at": row["last_synced_at"],
        }
        for row in rows
    ]