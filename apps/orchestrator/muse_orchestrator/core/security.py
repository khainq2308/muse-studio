from __future__ import annotations

import base64
import hashlib
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Header, HTTPException, status
from muse_orchestrator.core.config import settings


class SecretVault:
    def __init__(self, master_key: str) -> None:
        digest = hashlib.sha256(master_key.encode("utf-8")).digest()
        fernet_key = base64.urlsafe_b64encode(digest)
        self._fernet = Fernet(fernet_key)

    def encrypt(self, plaintext: str | None) -> str | None:
        if not plaintext:
            return None
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str | None) -> str | None:
        if not ciphertext:
            return None
        try:
            return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Không thể giải mã secret.") from exc

    @staticmethod
    def mask(secret: str | None) -> str:
        if not secret:
            return "[NOT_SET]"
        if len(secret) <= 8:
            return "********"
        return f"{secret[:3]}...{secret[-4:]}"


vault = SecretVault(settings.secret_encryption_key)


async def verify_single_admin(x_studio_admin_token: str | None = Header(default=None)) -> str:
    if settings.studio_admin_token and x_studio_admin_token != settings.studio_admin_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid X-Studio-Admin-Token.",
        )
    return "admin_operator"
