"""Lab Slot REST client (slot/README.md). The manager is its only API client."""

import httpx
import ssl
from .ownership import command_headers, current
from .store import Fenced


class SlotError(RuntimeError):
    def __init__(self, status: int | None, code: str | None, message: str, detail=None):
        super().__init__(f"Slot {status or 'unreachable'} {code or ''}: {message}".strip())
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail if isinstance(detail, dict) else {}


class SlotUnavailable(SlotError):
    def __init__(self, message: str):
        super().__init__(None, "UNREACHABLE", message)


def _error(resp: httpx.Response) -> SlotError:
    try:
        body = resp.json()
    except ValueError:
        body = {}
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        return SlotError(resp.status_code, detail.get("code"), str(detail.get("message", ""))[:500], detail)
    if isinstance(detail, list):  # FastAPI validation error
        return SlotError(resp.status_code, "VALIDATION_ERROR", "Request rejected by slot validation")
    return SlotError(resp.status_code, None, str(detail or resp.reason_phrase)[:500])


class SlotClient:
    def __init__(self, address: str, port: int, api_token: str, *, timeout: float = 10.0,
                 scheme="https", ca_file=None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.address = address
        self._http = httpx.AsyncClient(
            base_url=f"{scheme}://{address}:{port}",
            verify=ssl.create_default_context(cafile=str(ca_file) if ca_file else None) if transport is None else True,
            headers={"Authorization": f"Bearer {api_token}"},
            timeout=httpx.Timeout(timeout),
            transport=transport,
            trust_env=False,
        )

    async def close(self):
        await self._http.aclose()

    async def _call(self, method, path, *, json=None, key=None, ok=(200, 202)):
        headers = {"Idempotency-Key": key} if key else {}
        if method in ("POST", "PUT", "DELETE", "PATCH"):
            headers.update(command_headers())
        try:
            resp = await self._http.request(method, path, json=json, headers=headers)
        except httpx.HTTPError as exc:
            raise SlotUnavailable(f"{method} {path}: {type(exc).__name__}") from exc
        if resp.status_code not in ok:
            error = _error(resp)
            if error.code == "STALE_OWNER":
                raise Fenced("Slot command owner superseded")
            raise error
        try:
            return resp.json()
        except ValueError as exc:
            raise SlotError(resp.status_code, "BAD_RESPONSE", "Malformed JSON") from exc

    async def healthz(self) -> bool:
        try:
            await self._call("GET", "/healthz")
            return True
        except SlotError:
            return False

    async def claim(self):
        command_headers()
        body = current.get().body()
        result = await self._call("POST", "/v1/fence", json=body)
        if any(result.get(k) != v for k, v in body.items()):
            raise SlotUnavailable("Slot did not confirm ownership protocol")
        return result

    async def readyz(self) -> tuple[bool, dict]:
        try:
            body = await self._call("GET", "/v1/readyz", ok=(200, 503))
        except SlotError as exc:
            return False, {"error": exc.code}
        return body.get("status") == "ready", body

    async def deploy(self, ticket: dict, key: str) -> dict:
        return await self._call("POST", "/v1/labs/deploy", json=ticket, key=key)

    async def resume(self, project_id: str, key: str) -> dict:
        return await self._call("POST", f"/v1/labs/{project_id}/resume", key=key)

    async def stop(self, project_id: str, key: str) -> dict:
        return await self._call("POST", f"/v1/labs/{project_id}/stop", key=key)

    async def operation(self, operation_id: str) -> dict:
        return await self._call("GET", f"/v1/operations/{operation_id}")

    async def lab_status(self, project_id: str) -> dict | None:
        try:
            return await self._call("GET", f"/v1/labs/{project_id}/status")
        except SlotError as exc:
            if exc.status == 404:
                return None
            raise

    async def topology(self, project_id: str) -> dict:
        return await self._call("GET", f"/v1/labs/{project_id}/topology")


class GatewayClient:
    """POST /internal/tickets of plugin/gateway/console_gateway.py."""

    def __init__(self, internal_url: str, internal_key: str, public_url: str, *, timeout: float = 5.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.public_url = public_url
        self._http = httpx.AsyncClient(
            base_url=internal_url,
            headers={"X-Internal-Key": internal_key},
            timeout=httpx.Timeout(timeout),
            transport=transport,
            trust_env=False,
        )

    async def close(self):
        await self._http.aclose()

    async def issue(self, *, slot: str, project_id: str, node_id: str, readonly: bool, who: str) -> dict:
        try:
            resp = await self._http.post(
                "/internal/tickets",
                json={"slot": slot, "project_id": project_id, "nodes": [node_id],
                      "readonly": readonly, "who": who},
            )
        except httpx.HTTPError as exc:
            raise SlotUnavailable(f"Gateway: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise SlotError(resp.status_code, "GATEWAY_ERROR", "Gateway refused the ticket")
        body = resp.json()
        return {"ticket": body["ticket"], "gateway_url": self.public_url,
                "expires_in": int(body.get("expires_in", 30))}
