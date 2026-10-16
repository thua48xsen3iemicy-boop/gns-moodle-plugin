from fastapi import APIRouter, Request

from api.client import call

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz():
    return {"status": "ok"}


@router.get("/v1/readyz")
async def readyz(request: Request):
    gns3 = await call(request, "GET", "version")
    return {"status": "ready", "engine": "gns3", "gns3": gns3}
