from fastapi import status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.module_platform.package.model import PackageModel
from app.api.v1.module_platform.tenant.model import TenantModel
from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.dependencies import require_superadmin
from app.core.exceptions import CustomException

from .host import normalize_host
from .model import SiteDomainModel, SiteModel
from .schema import SiteCreateSchema, SiteOutSchema, SiteQueryParam, SiteUpdateSchema

__all__ = ["SiteService", "normalize_host"]


class SiteService:
    def __init__(self, auth: AuthSchema | None = None) -> None:
        self.auth = auth

    @staticmethod
    async def resolve_by_host(db: AsyncSession, raw_host: str | None) -> SiteModel | None:
        try:
            host = normalize_host(raw_host)
        except ValueError:
            return None
        result = await db.execute(
            select(SiteModel)
            .join(SiteDomainModel, SiteDomainModel.site_id == SiteModel.id)
            .where(
                SiteDomainModel.host == host,
                SiteDomainModel.is_deleted.is_(False),
                SiteModel.status == 0,
                SiteModel.is_deleted.is_(False),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    def _require_auth(self) -> AuthSchema:
        if self.auth is None or self.auth.db is None:
            raise RuntimeError("站点管理缺少认证数据库会话")
        return self.auth

    async def _ensure_unique(self, *, code: str | None = None, name: str | None = None, site_id: int | None = None) -> None:
        auth = self._require_auth()
        conditions = []
        if code is not None:
            conditions.append(SiteModel.code == code)
        if name is not None:
            conditions.append(SiteModel.name == name)
        for condition in conditions:
            stmt = select(SiteModel.id).where(condition, SiteModel.is_deleted.is_(False))
            if site_id is not None:
                stmt = stmt.where(SiteModel.id != site_id)
            if (await auth.db.execute(stmt.limit(1))).scalar_one_or_none() is not None:
                raise CustomException(msg="站点编码或名称已存在", status_code=status.HTTP_400_BAD_REQUEST)

    async def _ensure_domains_available(self, domains: list, site_id: int | None = None) -> None:
        auth = self._require_auth()
        hosts = [item.host for item in domains]
        stmt = select(SiteDomainModel.host).where(
            SiteDomainModel.host.in_(hosts),
            SiteDomainModel.is_deleted.is_(False),
        )
        if site_id is not None:
            stmt = stmt.where(SiteDomainModel.site_id != site_id)
        duplicate = (await auth.db.execute(stmt.limit(1))).scalar_one_or_none()
        if duplicate is not None:
            raise CustomException(msg=f"域名已被其他站点使用: {duplicate}", status_code=status.HTTP_400_BAD_REQUEST)

    @require_superadmin
    async def create(self, data: SiteCreateSchema) -> SiteOutSchema:
        auth = self._require_auth()
        await self._ensure_unique(code=data.code, name=data.name)
        await self._ensure_domains_available(data.domains)
        values = data.model_dump(exclude={"domains"})
        site = SiteModel(**values)
        site.domains = [SiteDomainModel(host=item.host, is_primary=item.is_primary) for item in data.domains]
        auth.db.add(site)
        await auth.db.flush()
        return SiteOutSchema.model_validate(site)

    @require_superadmin
    async def detail(self, site_id: int) -> SiteOutSchema:
        auth = self._require_auth()
        site = (await auth.db.execute(select(SiteModel).where(SiteModel.id == site_id, SiteModel.is_deleted.is_(False)))).scalar_one_or_none()
        if site is None:
            raise CustomException(msg="站点不存在", status_code=status.HTTP_404_NOT_FOUND)
        return SiteOutSchema.model_validate(site)

    @require_superadmin
    async def page(self, page_no: int, page_size: int, search: SiteQueryParam) -> PageResultSchema:
        auth = self._require_auth()
        conditions = [SiteModel.is_deleted.is_(False)]
        if search.name:
            conditions.append(SiteModel.name.like(f"%{search.name[1]}%"))
        if search.code:
            conditions.append(SiteModel.code.like(f"%{search.code[1]}%"))
        if search.status:
            conditions.append(SiteModel.status == search.status[1])
        total = (await auth.db.execute(select(func.count()).select_from(SiteModel).where(*conditions))).scalar_one()
        sites = (await auth.db.execute(select(SiteModel).where(*conditions).order_by(SiteModel.id).offset((page_no - 1) * page_size).limit(page_size))).scalars().all()
        return PageResultSchema(page_no=page_no, page_size=page_size, total=total, has_next=page_no * page_size < total, items=[SiteOutSchema.model_validate(item).model_dump() for item in sites])

    @require_superadmin
    async def update(self, site_id: int, data: SiteUpdateSchema) -> SiteOutSchema:
        auth = self._require_auth()
        site = (await auth.db.execute(select(SiteModel).where(SiteModel.id == site_id, SiteModel.is_deleted.is_(False)))).scalar_one_or_none()
        if site is None:
            raise CustomException(msg="站点不存在", status_code=status.HTTP_404_NOT_FOUND)
        await self._ensure_unique(code=data.code, name=data.name, site_id=site_id)
        if data.domains is not None:
            await self._ensure_domains_available(data.domains, site_id=site_id)
            await auth.db.execute(delete(SiteDomainModel).where(SiteDomainModel.site_id == site_id))
            site.domains = [SiteDomainModel(host=item.host, is_primary=item.is_primary) for item in data.domains]
        for key, value in data.model_dump(exclude={"domains"}, exclude_unset=True).items():
            setattr(site, key, value)
        await auth.db.flush()
        return SiteOutSchema.model_validate(site)

    @require_superadmin
    async def delete(self, ids: list[int]) -> None:
        auth = self._require_auth()
        if not ids:
            raise CustomException(msg="删除对象不能为空", status_code=status.HTTP_400_BAD_REQUEST)
        tenant_count = (await auth.db.execute(select(func.count()).select_from(TenantModel).where(TenantModel.site_id.in_(ids)))).scalar_one()
        if tenant_count:
            raise CustomException(msg="站点仍有关联租户，无法删除", status_code=status.HTTP_409_CONFLICT)
        package_count = (await auth.db.execute(select(func.count()).select_from(PackageModel).where(PackageModel.site_id.in_(ids)))).scalar_one()
        if package_count:
            raise CustomException(msg="站点仍有关联套餐，无法删除", status_code=status.HTTP_409_CONFLICT)
        sites = (await auth.db.execute(select(SiteModel).where(SiteModel.id.in_(ids), SiteModel.is_deleted.is_(False)))).scalars().all()
        for site in sites:
            await auth.db.delete(site)
        await auth.db.flush()
