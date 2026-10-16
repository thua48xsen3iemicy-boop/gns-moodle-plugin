"""Temporary loopback-only authentication; mTLS will be terminated by a gateway."""
import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False)


def require_token(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    secret = os.environ.get("SLOT_API_TOKEN", "")
    if not (secret and credentials and
            secrets.compare_digest(credentials.credentials, secret)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid API token",
                            headers={"WWW-Authenticate": "Bearer"})


def websocket_token_ok(protocol_header: str | None) -> bool:
    """Browser clients: new WebSocket(url, ['lab-slot', `bearer.${token}`])."""
    tokens = [x.strip() for x in (protocol_header or "").split(",")]
    expected = "bearer." + os.environ.get("SLOT_API_TOKEN", "")
    return bool(os.environ.get("SLOT_API_TOKEN") and "lab-slot" in tokens and
                any(secrets.compare_digest(t, expected) for t in tokens))
