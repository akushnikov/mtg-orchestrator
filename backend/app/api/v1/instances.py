from collections.abc import AsyncIterator
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.auth import OwnerUserID
from app.config import settings
from app.db import crud
from app.db.engine import AsyncSessionLocal
from app.db.models import ProxyInstance
from app.services import proxy_service


router = APIRouter()


class CreateInstanceRequest(BaseModel):
    domain: str


class ProxyInstanceResponse(BaseModel):
    id: int
    domain: str
    slug: str
    port: int
    status: str
    tg_url: str = ""
    created_at: str


class DefaultProxyResponse(BaseModel):
    id: int
    domain: str
    tg_url: str
    read_only: bool


class CreateStreamRequest(BaseModel):
    domain: str


class ProgressEvent(BaseModel):
    stage: str
    status: str
    detail: str = ""
    tg_url: str = ""


async def async_db_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session


def _to_response(row: ProxyInstance, tg_url: str = "") -> ProxyInstanceResponse:
    created_at = row.created_at
    if isinstance(created_at, datetime):
        created_at_str = created_at.isoformat()
    else:
        created_at_str = str(created_at)
    return ProxyInstanceResponse(
        id=row.id,
        domain=row.domain,
        slug=row.slug,
        port=row.port,
        status=row.status.value if hasattr(row.status, "value") else str(row.status),
        tg_url=tg_url,
        created_at=created_at_str,
    )


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=ProxyInstanceResponse,
)
async def create_instance(
    _user_id: OwnerUserID,
    body: CreateInstanceRequest,
    session: AsyncSession = Depends(async_db_session),
) -> ProxyInstanceResponse:
    try:
        row, tg_url = await proxy_service.create_instance(session, body.domain)
    except proxy_service.DuplicateDomainError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A proxy instance for this domain already exists",
        ) from exc
    except proxy_service.InvalidDomainError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Domain validation failed",
        ) from exc

    return _to_response(row, tg_url=tg_url)


@router.get("/", response_model=list[ProxyInstanceResponse])
async def list_instances(
    _user_id: OwnerUserID,
    session: AsyncSession = Depends(async_db_session),
) -> list[ProxyInstanceResponse]:
    rows = await crud.list_instances(session)
    return [_to_response(row) for row in rows]


@router.delete("/{instance_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_instance(
    _user_id: OwnerUserID,
    instance_id: int,
    session: AsyncSession = Depends(async_db_session),
) -> Response:
    try:
        await proxy_service.delete_instance(session, instance_id)
    except proxy_service.InstanceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Instance not found",
        ) from exc
    except proxy_service.LifecycleOperationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Instance lifecycle operation failed",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{instance_id}/stop", response_model=ProxyInstanceResponse)
async def stop_instance(
    _user_id: OwnerUserID,
    instance_id: int,
    session: AsyncSession = Depends(async_db_session),
) -> ProxyInstanceResponse:
    try:
        row = await proxy_service.stop_instance(session, instance_id)
    except proxy_service.InstanceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Instance not found",
        ) from exc
    except proxy_service.InstanceStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Instance is already stopped",
        ) from exc
    return _to_response(row)


@router.get("/default", response_model=DefaultProxyResponse)
async def get_default_proxy(_user_id: OwnerUserID) -> DefaultProxyResponse:
    tg_url = ""
    if settings.mtg_default_secret and settings.mtg_default_secret.startswith("ee"):
        tg_url = proxy_service.build_tg_proxy_url(
            settings.moscow_ip,
            settings.mtg_default_secret,
        )
    return DefaultProxyResponse(
        id=-1,
        domain=settings.mtg_default_domain,
        tg_url=tg_url,
        read_only=True,
    )


@router.post("/create/stream", response_class=EventSourceResponse)
async def create_instance_stream(
    body: CreateStreamRequest,
    _user_id: OwnerUserID,
    session: AsyncSession = Depends(async_db_session),
) -> AsyncIterator[ServerSentEvent]:
    yield ServerSentEvent(
        data=ProgressEvent(stage="validating", status="in_progress").model_dump(),
        event="progress",
    )
    try:
        _row, tg_url = await proxy_service.create_instance(session, body.domain)
    except proxy_service.InvalidDomainError as exc:
        yield ServerSentEvent(
            data=ProgressEvent(
                stage="validating",
                status="error",
                detail=str(exc),
            ).model_dump(),
            event="error",
            retry=0,
        )
        return
    except proxy_service.DuplicateDomainError:
        yield ServerSentEvent(
            data=ProgressEvent(
                stage="validating",
                status="error",
                detail="duplicate",
            ).model_dump(),
            event="error",
            retry=0,
        )
        return
    except Exception as exc:
        yield ServerSentEvent(
            data=ProgressEvent(
                stage="creating_container",
                status="error",
                detail=str(exc),
            ).model_dump(),
            event="error",
            retry=0,
        )
        return

    for stage_name in ["creating_container", "rendering_nginx", "reloading_nginx"]:
        yield ServerSentEvent(
            data=ProgressEvent(stage=stage_name, status="done").model_dump(),
            event="progress",
        )
    yield ServerSentEvent(
        data=ProgressEvent(stage="done", status="done", tg_url=tg_url).model_dump(),
        event="done",
        retry=0,
    )


@router.patch("/{instance_id}/start", response_model=ProxyInstanceResponse)
async def start_instance(
    _user_id: OwnerUserID,
    instance_id: int,
    session: AsyncSession = Depends(async_db_session),
) -> ProxyInstanceResponse:
    try:
        row = await proxy_service.start_instance(session, instance_id)
    except proxy_service.InstanceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Instance not found",
        ) from exc
    except proxy_service.InstanceStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Instance is already running",
        ) from exc
    except proxy_service.LifecycleOperationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Instance lifecycle operation failed",
        ) from exc
    return _to_response(row)
