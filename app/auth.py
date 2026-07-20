from fastapi import FastAPI, HTTPException, Request

def get_parity_user_id(request: Request) -> str:
    user_id = request.headers.get("X-Parity-User-Id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Missing X-Parity-User-Id")
    return user_id
