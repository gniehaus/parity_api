import os
from cryptography.fernet import Fernet


def get_fernet() -> Fernet:
    key = os.getenv("APP_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("Missing APP_ENCRYPTION_KEY. Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"")
    return Fernet(key.encode())


def encrypt_secret(value: str) -> str:
    return get_fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    return get_fernet().decrypt(value.encode()).decode()
