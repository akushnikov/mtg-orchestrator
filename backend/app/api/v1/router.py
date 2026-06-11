from fastapi import APIRouter

from app.api.v1 import instances


api_router = APIRouter()
api_router.include_router(instances.router, prefix="/instances", tags=["instances"])
