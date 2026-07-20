from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .db import record_client_consent,get_advisory_status, create_advisory_client
from .auth import get_parity_user_id

router = APIRouter(
    prefix="/api/advisory",
    tags=["advisory"],
)

from .db import get_active_advisory_documents

class RecordConsentRequest(BaseModel):
    document_id: str

    consent_type: Literal[
        "accepted",
        "declined",
        "withdrawn",
    ] = "accepted"

    ip_address: str | None = None
    user_agent: str | None = None

    signature_method: Literal[
        "electronic",
        "wet_signature",
        "advisor_recorded",
    ] = "electronic"

    signature_reference: str | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)

class CreateAdvisoryClientRequest(BaseModel):
    email: str


@router.post("/client")
def create_client(
    payload: CreateAdvisoryClientRequest,
    request: Request,
):
    parity_user_id = get_parity_user_id(request)

    try:
        return create_advisory_client(
            parity_user_id=parity_user_id,
            email=payload.email,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc

import logging

from fastapi import HTTPException

logger = logging.getLogger(__name__)


@router.get("/documents")
def get_documents():
    try:
        documents = get_active_advisory_documents()

        return {
            "count": len(documents),
            "documents": documents,
        }

    except Exception as exc:
        logger.exception(
            "Failed to load advisory documents"
        )

        raise HTTPException(
            status_code=500,
            detail=f"Failed to load advisory documents: {exc}",
        ) from exc

@router.get("/status")
def advisory_status(request: Request):
    parity_user_id = get_parity_user_id(request)

    try:
        return get_advisory_status(parity_user_id)

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc


@router.post("/consents")
def create_consent(
    payload: RecordConsentRequest,
    request: Request,
):
    parity_user_id = get_parity_user_id(request)

    try:
        return record_client_consent(
            parity_user_id=parity_user_id,
            document_id=payload.document_id,
            consent_type=payload.consent_type,
            ip_address=payload.ip_address,
            user_agent=payload.user_agent,
            signature_method=payload.signature_method,
            signature_reference=payload.signature_reference,
            metadata=payload.metadata,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc