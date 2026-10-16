"""Lab Slot: a single active lab runtime backed by GNS3 2.2.x."""
from contextlib import asynccontextmanager
import os

import httpx
from fastapi import Depends, FastAPI

from api.auth import require_token
from api.console import router as console_router
from api.health import router as health_router
from api.labs import router as labs_router
from api.nodes import router as nodes_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail closed: the Slot API has privileged access to its GNS3 runtime.
    if not os.environ.get("SLOT_API_TOKEN"):
        raise RuntimeError("SLOT_API_TOKEN is required (see slot.env.example)")
    base = os.getenv("GNS3_API_URL", "http://127.0.0.1:3080/v2").rstrip("/")
    if base != "http://127.0.0.1:3080/v2":
        raise RuntimeError("First prototype requires local GNS3 at 127.0.0.1:3080")
    async with httpx.AsyncClient(base_url=base + "/", timeout=30.0,
                                 trust_env=False) as client:
        app.state.gns3_http = client
        yield


app = FastAPI(title="Lab Slot API", version="0.1.0", lifespan=lifespan,
              description="Prototype. GNS3 project lifecycle and a controlled console/QGA gateway.")
app.include_router(health_router)
app.include_router(labs_router, dependencies=[Depends(require_token)])
app.include_router(nodes_router, dependencies=[Depends(require_token)])
app.include_router(console_router)  # WebSockets authenticate in their handler.
