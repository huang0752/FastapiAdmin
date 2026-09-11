"""Public Basic-auth exchange endpoint for user-entitlement claims."""

import hashlib
import ipaddress
import json
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi_limiter.depends import RateLimiter
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.module_control.schema import ControlUserApplicationGrantOutSchema
from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_schema import AuthSchema
from app.core.dependencies import AuthPermission, db_getter
from app.core.exceptions import CustomException

from .schema import ControlUserAccessClaims, ControlUserCreateIn, ControlUserCreateOut
from .service import ControlUserCreateService, publish_entitlement_tasks
from .ticket_service import ControlUserEntitlementTicketService

UserEntitlementExchangeRouter = APIRouter(prefix="/user-entitlements", tags=["中控用户授权"])
ControlUserRouter = APIRouter(prefix="/users", tags=["中控用户授权"])
http_basic = HTTPBasic()


@ControlUserRouter.post(
    "",
    response_model=ResponseSchema[ControlUserCreateOut],
    dependencies=[
        Depends(AuthPermission(["module_system:user:create"])),
        Depends(AuthPermission(["module_control:user_grant:update"])),
    ],
)
async def create_control_user(
    data: ControlUserCreateIn,
    background_tasks: BackgroundTasks,
    auth: Annotated[AuthSchema, Depends(AuthPermission())],
) -> JSONResponse:
    outcome = await ControlUserCreateService(auth).create(data)
    background_tasks.add_task(publish_entitlement_tasks, outcome.task_ids)
    return SuccessResponse(
        data=ControlUserCreateOut(
            user=outcome.user,
            entitlements=[
                ControlUserApplicationGrantOutSchema.model_validate(grant)
                for grant in outcome.grants
            ],
        ),
        msg="用户和产品授权已提交",
    )


def _limit_key(namespace: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"control-user-entitlement:{namespace}:{digest}"


async def _control_peer_identifier(request: Request) -> str:
    """Use the direct peer, trusting overwritten XFF only from the loopback proxy."""
    return _limit_key("peer", _control_peer_value(request))


def _control_peer_value(request: Request) -> str:
    peer_host = request.client.host if request.client is not None else "unknown-peer"
    try:
        peer_ip = ipaddress.ip_address(peer_host)
    except ValueError:
        return peer_host
    mapped_ipv4 = getattr(peer_ip, "ipv4_mapped", None)
    is_loopback = peer_ip.is_loopback or bool(mapped_ipv4 and mapped_ipv4.is_loopback)
    if not is_loopback:
        return peer_ip.compressed
    forwarded_for = request.headers.get("x-forwarded-for", "").strip()
    try:
        return ipaddress.ip_address(forwarded_for).compressed
    except ValueError:
        return peer_ip.compressed


async def _control_client_identifier(request: Request) -> str:
    """Key the normal bucket from Basic username without retaining credentials."""
    try:
        credentials = await http_basic(request)
    except (HTTPException, ValueError):
        credentials = None
    if credentials is not None:
        return _limit_key("client", credentials.username)
    return _limit_key("client-fallback-peer", _control_peer_value(request))


async def _read_sensitive_code(request: Request) -> str:
    """Parse the bearer code without FastAPI ever echoing the request body."""
    content_type = request.headers.get("content-type", "").split(";", maxsplit=1)[0].strip().lower()
    if content_type != "application/json":
        raise CustomException(msg="用户授权请求无效", status_code=status.HTTP_400_BAD_REQUEST)

    declared_length = request.headers.get("content-length")
    if declared_length is not None:
        try:
            parsed_length = int(declared_length)
        except ValueError:
            raise CustomException(msg="用户授权请求无效", status_code=status.HTTP_400_BAD_REQUEST) from None
        if parsed_length < 0 or parsed_length > 1024:
            raise CustomException(msg="用户授权请求无效", status_code=status.HTTP_400_BAD_REQUEST)

    raw_body = bytearray()
    try:
        async for chunk in request.stream():
            if len(raw_body) + len(chunk) > 1024:
                raise ValueError
            raw_body.extend(chunk)
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        raise CustomException(msg="用户授权请求无效", status_code=status.HTTP_400_BAD_REQUEST) from None
    if not isinstance(payload, dict) or set(payload) != {"code"}:
        raise CustomException(msg="用户授权请求无效", status_code=status.HTTP_400_BAD_REQUEST)
    code = payload["code"]
    if not isinstance(code, str) or not 20 <= len(code) <= 512:
        raise CustomException(msg="用户授权请求无效", status_code=status.HTTP_400_BAD_REQUEST)
    return code


@UserEntitlementExchangeRouter.post(
    "/exchange",
    response_model=ResponseSchema[ControlUserAccessClaims],
    dependencies=[
        Depends(RateLimiter(times=120, seconds=60, identifier=_control_peer_identifier)),
        Depends(RateLimiter(times=10, seconds=60, identifier=_control_client_identifier)),
    ],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["code"],
                        "properties": {
                            "code": {"type": "string", "minLength": 20, "maxLength": 512},
                        },
                    }
                }
            },
        }
    },
)
async def exchange_user_entitlement(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials, Depends(http_basic)],
    db: Annotated[AsyncSession, Depends(db_getter)],
) -> JSONResponse:
    request.state.skip_operation_log = True
    code = await _read_sensitive_code(request)
    result = await ControlUserEntitlementTicketService.exchange(
        db,
        client_id=credentials.username,
        client_secret=credentials.password,
        code=code,
        issuer=str(request.base_url).rstrip("/"),
    )
    return SuccessResponse(data=result, msg="用户访问资格票据兑换成功")
