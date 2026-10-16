"""Small, explicit GNS3 Controller API adapter (no arbitrary URL proxy)."""
import json

import httpx
from fastapi import HTTPException, Request


async def call(request: Request, method: str, path: str, payload: dict | None = None):
    """A fixed route path supplied by Slot code, never a user-controlled URL."""
    client: httpx.AsyncClient = request.app.state.gns3_http
    try:
        resp = await client.request(method, path.lstrip("/"), json=payload)
    except httpx.HTTPError as exc:
        raise HTTPException(502, detail=f"GNS3 connection failed: {type(exc).__name__}") from exc
    if resp.status_code >= 400:
        try:
            description = resp.json().get("message", "GNS3 request failed")
        except (json.JSONDecodeError, ValueError, AttributeError):
            description = "GNS3 request failed"
        code = resp.status_code if resp.status_code in (400, 403, 404, 409, 422) else 502
        raise HTTPException(code, detail=str(description))
    if not resp.content:
        return {}
    try:
        return resp.json()
    except ValueError:
        return {}
