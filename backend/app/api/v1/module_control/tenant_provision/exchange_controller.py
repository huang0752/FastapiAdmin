"""Public BasicAuth exchange endpoint for tenant-provision claims."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ResponseSchema, SuccessResponse
from app.core.dependencies import db_getter
from app.core.exceptions import CustomException

from .schema import ControlProvisionExchangeClaimsSchema, ControlProvisionExchangeInSchema
from .ticket_service import ControlProvisionTicketService

ProvisioningExchangeRouter = APIRouter(prefix="/provisioning", tags=["中控租户开户"])
http_basic = HTTPBasic()


def _validate_sensitive_code(code: object) -> str:
    if not isinstance(code, str) or not 20 <= len(code) <= 512:
        raise CustomException(msg="开户码格式无效", status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)
    return code


def _issuer_from_exchange_url(request_url: str) -> str:
    exchange_suffix = "/control/provisioning/exchange"
    normalized = request_url.split("?", maxsplit=1)[0]
    if not normalized.endswith(exchange_suffix):
        raise RuntimeError("中控开户兑换路由与 issuer 契约不一致")
    return normalized[: -len(exchange_suffix)].rstrip("/")


@ProvisioningExchangeRouter.post("/exchange", response_model=ResponseSchema[ControlProvisionExchangeClaimsSchema])
async def exchange_provision_code(
    request: Request,
    data: ControlProvisionExchangeInSchema,
    credentials: Annotated[HTTPBasicCredentials, Depends(http_basic)],
    db: Annotated[AsyncSession, Depends(db_getter)],
) -> JSONResponse:
    claims = await ControlProvisionTicketService(db).exchange(
        client_id=credentials.username,
        client_secret=credentials.password,
        plain_code=_validate_sensitive_code(data.code),
        issuer=_issuer_from_exchange_url(str(request.url)),
    )
    return SuccessResponse(data=claims, msg="开户码兑换成功")
