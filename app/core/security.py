import secrets
from typing import Optional
from fastapi import Header, HTTPException, status
from app.core.config import settings


def verify_admin(x_admin_key: Optional[str] = Header(None)) -> bool:
    """
    Validates the X-Admin-Key header against ADMIN_API_KEY using constant-time comparison.
    If ADMIN_API_KEY is not configured or does not match, raises 401 Unauthorized.
    """
    if not settings.ADMIN_API_KEY or not x_admin_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Invalid Admin Key",
        )

    if not secrets.compare_digest(x_admin_key.strip(), settings.ADMIN_API_KEY.strip()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized: Invalid Admin Key",
        )

    return True
