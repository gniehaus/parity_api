import os
from cryptography.fernet import Fernet


def get_fernet():
    key = os.getenv("ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("Missing ENCRYPTION_KEY environment variable")
    return Fernet(key.encode())


def encrypt_secret(secret: str) -> str:
    if not secret:
        return ""
    return get_fernet().encrypt(secret.encode()).decode()


def decrypt_secret(encrypted_secret: str) -> str:
    if not encrypted_secret:
        return ""
    return get_fernet().decrypt(encrypted_secret.encode()).decode()