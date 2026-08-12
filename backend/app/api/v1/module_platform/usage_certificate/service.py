import base64
import io
import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select
from starlette.concurrency import run_in_threadpool

from app.api.v1.module_platform.tenant.model import TenantModel, TenantStatus
from app.config.setting import settings
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.utils.email_util import render_template_file
from app.utils.pdf_generator import html_to_pdf

from .schema import UsageCertificatePlatformItem, UsageCertificatePlatformPage, UsageCertificatePreviewOut, UsageCertificatePublicOut, UsageCertificateView


@dataclass(frozen=True, slots=True)
class UsageCertificatePdf:
    filename: str
    content: bytes


class UsageCertificateService:
    @staticmethod
    def _display(value: object | None) -> str:
        text = "" if value is None else str(value).strip()
        return text or "-"

    @staticmethod
    def _format_datetime(value: datetime | None) -> str:
        return value.strftime("%Y-%m-%d %H:%M:%S") if value else "-"

    @staticmethod
    def _safe_filename(value: str) -> str:
        cleaned = re.sub(r'[\\/:*?"<>|\s]+', "-", value.strip())
        return cleaned.strip("-") or "企业"

    @staticmethod
    def _qr_data_url(url: str | None) -> str | None:
        if not url:
            return None
        import qrcode

        image = qrcode.make(url)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

    @classmethod
    def build_view(cls, tenant: TenantModel, *, now: datetime, request_ip: str | None, include_request_ip: bool, public_origin: str, system_name: str, system_version: str) -> UsageCertificateView:
        status_ok = int(tenant.status) in {TenantStatus.ACTIVE, TenantStatus.GRACE}
        date_ok = (tenant.start_time is None or now >= tenant.start_time) and (tenant.end_time is None or now <= tenant.end_time)
        currently_valid = bool(status_ok and date_ok and not tenant.is_deleted)
        origin = public_origin.strip().rstrip("/")
        token = tenant.usage_certificate_token or ""
        verify_url = f"{origin}/#/certificate/verify/{token}" if origin and token else None
        return UsageCertificateView(
            tenant_id=tenant.id,
            certificate_no=tenant.usage_certificate_no or "-",
            enterprise_name=cls._display(tenant.name),
            tenant_code=cls._display(tenant.code),
            social_credit_code=cls._display(tenant.unified_social_credit_code),
            system_name=cls._display(system_name),
            system_version=cls._display(system_version),
            start_time=cls._format_datetime(tenant.start_time),
            end_time=cls._format_datetime(tenant.end_time),
            currently_valid=currently_valid,
            status_label="当前有效" if currently_valid else "当前无效",
            certificate_created_at=cls._format_datetime(tenant.usage_certificate_created_at),
            request_ip=cls._display(request_ip) if include_request_ip else None,
            verify_url=verify_url,
            qr_data_url=cls._qr_data_url(verify_url),
        )

    @classmethod
    def view_for_tenant(cls, tenant: TenantModel, *, request_ip: str | None = None, include_request_ip: bool = False) -> UsageCertificateView:
        return cls.build_view(tenant, now=datetime.now(), request_ip=request_ip, include_request_ip=include_request_ip, public_origin=settings.USAGE_CERTIFICATE_PUBLIC_ORIGIN, system_name=settings.TITLE, system_version=settings.VERSION)

    @classmethod
    def render_html(cls, view: UsageCertificateView) -> str:
        return render_template_file("includes/software_usage_certificate.html", {"certificate": view})

    @classmethod
    def preview(cls, tenant: TenantModel, *, request_ip: str | None) -> UsageCertificatePreviewOut:
        view = cls.view_for_tenant(tenant, request_ip=request_ip, include_request_ip=True)
        return UsageCertificatePreviewOut(certificate_no=view.certificate_no, filename=f"企业软件使用证明-{cls._safe_filename(view.enterprise_name)}.pdf", generated_at=cls._format_datetime(datetime.now()), html=cls.render_html(view))

    @classmethod
    async def pdf(cls, tenant: TenantModel, *, request_ip: str | None) -> UsageCertificatePdf:
        preview = cls.preview(tenant, request_ip=request_ip)
        try:
            content = await run_in_threadpool(html_to_pdf, preview.html)
        except Exception as exc:
            logger.exception("软件使用证明 PDF 生成失败: tenant_id={}", tenant.id)
            raise CustomException(msg="软件使用证明 PDF 生成失败，请稍后重试") from exc
        if not content.startswith(b"%PDF"):
            raise CustomException(msg="软件使用证明 PDF 生成失败，请稍后重试")
        return UsageCertificatePdf(filename=preview.filename, content=content)

    @classmethod
    async def tenant_by_id(cls, db, tenant_id: int) -> TenantModel:
        tenant = await db.get(TenantModel, tenant_id)
        if tenant is None:
            raise CustomException(msg="该数据不存在", status_code=404)
        return tenant

    @classmethod
    async def tenant_by_token(cls, db, token: str) -> TenantModel:
        tenant = (await db.execute(select(TenantModel).where(TenantModel.usage_certificate_token == token).limit(1))).scalar_one_or_none()
        if tenant is None:
            raise CustomException(msg="无法核验此证明", status_code=404)
        return tenant

    @classmethod
    async def platform_page(cls, db, *, page_no: int, page_size: int, keyword: str | None, currently_valid: bool | None) -> UsageCertificatePlatformPage:
        stmt = select(TenantModel).order_by(TenantModel.id.asc())
        if keyword:
            term = f"%{keyword.strip()}%"
            stmt = stmt.where(or_(TenantModel.name.ilike(term), TenantModel.code.ilike(term), TenantModel.usage_certificate_no.ilike(term)))
        items = []
        for tenant in (await db.execute(stmt)).scalars().all():
            view = cls.view_for_tenant(tenant)
            if currently_valid is not None and view.currently_valid is not currently_valid:
                continue
            items.append(UsageCertificatePlatformItem(**view.model_dump(exclude={"request_ip", "verify_url", "qr_data_url"})))
        total = len(items)
        start = (page_no - 1) * page_size
        return UsageCertificatePlatformPage(items=items[start:start + page_size], total=total, page_no=page_no, page_size=page_size)

    @classmethod
    def public_out(cls, tenant: TenantModel) -> UsageCertificatePublicOut:
        view = cls.view_for_tenant(tenant)
        return UsageCertificatePublicOut(**view.model_dump(include=set(UsageCertificatePublicOut.model_fields)))
