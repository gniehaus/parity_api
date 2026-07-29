import os
from functools import lru_cache

import jwt
from fastapi import HTTPException, Request


CLERK_JWT_ISSUER = os.getenv(
    "CLERK_JWT_ISSUER",
    "https://clerk.parityoutcomes.com",
)


@lru_cache
def _clerk_jwks_client() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(
        f"{CLERK_JWT_ISSUER}/.well-known/jwks.json"
    )


def _legacy_header_is_allowed() -> bool:
    return os.getenv(
        "ALLOW_LEGACY_USER_HEADER",
        "false",
    ).lower() == "true"


def get_parity_user_id(request: Request) -> str:
    authorization = request.headers.get("Authorization", "")

    if authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()

        if not token:
            raise HTTPException(
                status_code=401,
                detail="Missing bearer token",
            )

        try:
            signing_key = (
                _clerk_jwks_client()
                .get_signing_key_from_jwt(token)
            )

            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=CLERK_JWT_ISSUER,
                options={"verify_aud": False},
            )
        except jwt.PyJWTError as exc:
            raise HTTPException(
                status_code=401,
                detail="Invalid or expired authentication token",
            ) from exc

        user_id = claims.get("sub")

        if not user_id:
            raise HTTPException(
                status_code=401,
                detail="Authentication token is missing its subject",
            )

        return str(user_id)

    if _legacy_header_is_allowed():
        user_id = request.headers.get("X-Parity-User-Id")

        if user_id:
            return user_id

    raise HTTPException(
        status_code=401,
        detail="Bearer authentication is required",
    )