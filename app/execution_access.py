import os

from fastapi import HTTPException


def require_new_execution_enabled(
    parity_user_id: str,
) -> None:
    enabled = os.getenv(
        "EXECUTION_ENABLED",
        "false",
    ).lower() == "true"

    if not enabled:
        raise HTTPException(
            status_code=503,
            detail=(
                "New order execution is temporarily unavailable"
            ),
        )

    allowed_users = {
        user_id.strip()
        for user_id in os.getenv(
            "EXECUTION_ALLOWED_USERS",
            "",
        ).split(",")
        if user_id.strip()
    }

    if parity_user_id not in allowed_users:
        raise HTTPException(
            status_code=403,
            detail=(
                "New order execution is not enabled for this account"
            ),
        )