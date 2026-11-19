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


def offered_protocols(protocol_header: str | None) -> list[str]:
    """The subprotocols a WebSocket client offers, as listed in its Sec-WebSocket-Protocol header."""
    return [x.strip() for x in (protocol_header or "").split(",")]


def websocket_token_ok(protocol_header: str | None) -> bool:
    """Clients: new WebSocket(url, ['lab-slot', `bearer.${token}`]); the gateway also offers 'lab-slot.2'.

    The console gateway uses SLOT_CONSOLE_TOKEN, which opens consoles only, never the REST API.
    """
    tokens = offered_protocols(protocol_header)
    secrets_ok = [s for s in (os.environ.get("SLOT_API_TOKEN"),
                              os.environ.get("SLOT_CONSOLE_TOKEN")) if s]
    return bool(os.environ.get("SLOT_API_TOKEN") and ("lab-slot" in tokens or "lab-slot.2" in tokens) and
                any(secrets.compare_digest(t, "bearer." + s) for t in tokens for s in secrets_ok))
