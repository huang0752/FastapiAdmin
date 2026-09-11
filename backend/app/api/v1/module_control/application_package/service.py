"""Business rules for Control application-package mappings."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.dependencies import require_platform_admin
from app.core.exceptions import CustomException

from ..model import ControlApplicationModel
from ..tenant_provision.model import ControlTenantProvisionModel
from .model import ControlApplicationPackageModel
from .schema import (
    ControlApplicationPackageCreateSchema,
    ControlApplicationPackageOutSchema,
    ControlApplicationPackageQueryParam,
    ControlApplicationPackageUpdateSchema,
)


class ControlApplicationPackageService:
    """Site-scoped package catalog managed by platform administrators."""

    def __init__(self, auth: AuthSchema) -> None:
        if auth.db is None:
            raise RuntimeError("中控应用套餐管理缺少数据库会话")
        self.auth = auth
        self.db = auth.db

    def _site_id(self) -> int:
        if self.auth.site_id is None:
            raise CustomException(msg="站点上下文缺失", code=10403, status_code=status.HTTP_403_FORBIDDEN)
        return self.auth.site_id

    async def _get_application(self, application_id: int, *, require_enabled: bool = False) -> ControlApplicationModel:
        application = await self.db.get(ControlApplicationModel, application_id)
        if application is None or application.is_deleted:
            raise CustomException(msg="应用不存在", status_code=status.HTTP_404_NOT_FOUND)
        if application.site_id != self._site_id():
            raise CustomException(msg="禁止访问其他站点的应用", code=10403, status_code=status.HTTP_403_FORBIDDEN)
        if require_enabled and (application.status != 0 or not application.provisioning_enabled):
            raise CustomException(msg="应用自动开户未启用", status_code=status.HTTP_409_CONFLICT)
        return application

    async def _get_scoped(self, package_id: int) -> ControlApplicationPackageModel:
        package = await self.db.get(ControlApplicationPackageModel, package_id)
        if package is None or package.is_deleted:
            raise CustomException(msg="应用套餐不存在", status_code=status.HTTP_404_NOT_FOUND)
        application = await self._get_application(package.application_id)
        if package.site_id != self._site_id() or package.site_id != application.site_id:
            raise CustomException(msg="禁止访问其他站点的应用套餐", code=10403, status_code=status.HTTP_403_FORBIDDEN)
        return package

    async def _ensure_unique(self, application_id: int, code: str, target_package_code: str, *, package_id: int | None = None) -> None:
        stmt = select(ControlApplicationPackageModel).where(
            ControlApplicationPackageModel.application_id == application_id,
        )
        if package_id is not None:
            stmt = stmt.where(ControlApplicationPackageModel.id != package_id)
        rows = (await self.db.execute(stmt)).scalars().all()
        if any(row.code == code for row in rows):
            raise CustomException(msg="同一应用的套餐编码已存在", status_code=status.HTTP_409_CONFLICT)
        if any(row.target_package_code == target_package_code for row in rows):
            raise CustomException(msg="同一应用的目标套餐编码已存在", status_code=status.HTTP_409_CONFLICT)

    async def _make_default(self, application_id: int, package_id: int | None = None) -> None:
        stmt = (
            update(ControlApplicationPackageModel)
            .where(
                ControlApplicationPackageModel.site_id == self._site_id(),
                ControlApplicationPackageModel.application_id == application_id,
                ControlApplicationPackageModel.is_deleted.is_(False),
            )
            .values(is_default=False)
        )
        if package_id is not None:
            stmt = stmt.where(ControlApplicationPackageModel.id != package_id)
        await self.db.execute(stmt)

    @staticmethod
    def _integrity_conflict(exc: IntegrityError) -> CustomException:
        detail = str(exc.orig or exc).lower()
        if "uq_control_application_package_one_default" in detail or (
            "unique constraint failed" in detail
            and detail.rstrip().endswith("control_application_package.application_id")
        ):
            message = "同一应用只能设置一个默认套餐"
        elif "uq_control_application_package_app_target_code" in detail or "target_package_code" in detail:
            message = "同一应用的目标套餐编码已存在"
        else:
            message = "同一应用的套餐编码已存在"
        return CustomException(msg=message, status_code=status.HTTP_409_CONFLICT)

    async def require_selectable(self, package_id: int, *, application_id: int) -> ControlApplicationPackageModel:
        package = await self._get_scoped(package_id)
        if package.application_id != application_id:
            raise CustomException(msg="套餐与应用不匹配", status_code=status.HTTP_409_CONFLICT)
        await self._get_application(application_id, require_enabled=True)
        if package.status != 0:
            raise CustomException(msg="应用套餐已停用", status_code=status.HTTP_409_CONFLICT)
        return package

    @require_platform_admin
    async def page(
        self,
        page_no: int,
        page_size: int,
        search: ControlApplicationPackageQueryParam,
    ) -> PageResultSchema[ControlApplicationPackageOutSchema]:
        filters = [
            ControlApplicationPackageModel.site_id == self._site_id(),
            ControlApplicationPackageModel.is_deleted.is_(False),
            ControlApplicationPackageModel.application_id.in_(
                select(ControlApplicationModel.id).where(
                    ControlApplicationModel.site_id == self._site_id(),
                    ControlApplicationModel.is_deleted.is_(False),
                )
            ),
        ]
        raw = vars(search)
        application_id = raw.get("application_id")
        if isinstance(application_id, tuple):
            filters.append(ControlApplicationPackageModel.application_id == application_id[1])
        code = raw.get("code")
        if isinstance(code, tuple):
            filters.append(ControlApplicationPackageModel.code.like(f"%{code[1]}%"))
        name = raw.get("name")
        if isinstance(name, tuple):
            filters.append(ControlApplicationPackageModel.name.like(f"%{name[1]}%"))
        package_status = raw.get("status")
        if isinstance(package_status, tuple):
            filters.append(ControlApplicationPackageModel.status == package_status[1])

        total = (await self.db.execute(select(func.count()).select_from(ControlApplicationPackageModel).where(*filters))).scalar_one()
        items = (
            await self.db.execute(
                select(ControlApplicationPackageModel)
                .where(*filters)
                .order_by(ControlApplicationPackageModel.sort, ControlApplicationPackageModel.id)
                .offset((page_no - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()
        return PageResultSchema(
            page_no=page_no,
            page_size=page_size,
            total=total,
            has_next=page_no * page_size < total,
            items=[ControlApplicationPackageOutSchema.model_validate(item) for item in items],
        )

    @require_platform_admin
    async def create(self, data: ControlApplicationPackageCreateSchema) -> ControlApplicationPackageOutSchema:
        application = await self._get_application(data.application_id, require_enabled=True)
        await self._ensure_unique(data.application_id, data.code, data.target_package_code)
        if data.is_default:
            await self._make_default(data.application_id)
        actor_id = self.auth.user.id if self.auth.user else None
        package = ControlApplicationPackageModel(
            **data.model_dump(),
            site_id=application.site_id,
            created_id=actor_id,
            updated_id=actor_id,
        )
        try:
            async with self.db.begin_nested():
                self.db.add(package)
                await self.db.flush()
        except IntegrityError as exc:
            raise self._integrity_conflict(exc) from exc
        return ControlApplicationPackageOutSchema.model_validate(package)

    @require_platform_admin
    async def update(self, package_id: int, data: ControlApplicationPackageUpdateSchema) -> ControlApplicationPackageOutSchema:
        package = await self._get_scoped(package_id)
        await self._get_application(package.application_id, require_enabled=True)
        values = data.model_dump(exclude_unset=True)
        next_code = values.get("code", package.code)
        next_target_code = values.get("target_package_code", package.target_package_code)
        await self._ensure_unique(package.application_id, next_code, next_target_code, package_id=package.id)
        if values.get("is_default") is True:
            await self._make_default(package.application_id, package.id)
        try:
            async with self.db.begin_nested():
                for key, value in values.items():
                    setattr(package, key, value)
                package.updated_id = self.auth.user.id if self.auth.user else None
                await self.db.flush()
        except IntegrityError as exc:
            raise self._integrity_conflict(exc) from exc
        return ControlApplicationPackageOutSchema.model_validate(package)

    @require_platform_admin
    async def delete(self, package_id: int) -> None:
        package = await self._get_scoped(package_id)
        referenced = (
            await self.db.execute(
                select(func.count())
                .select_from(ControlTenantProvisionModel)
                .where(
                    ControlTenantProvisionModel.site_id == self._site_id(),
                    ControlTenantProvisionModel.application_package_id == package.id,
                    ControlTenantProvisionModel.status == "succeeded",
                    ControlTenantProvisionModel.is_deleted.is_(False),
                )
            )
        ).scalar_one()
        if referenced:
            package.status = 1
            package.is_default = False
            package.updated_id = self.auth.user.id if self.auth.user else None
        else:
            package.is_deleted = True
            package.is_default = False
            package.deleted_time = datetime.now(UTC)
            package.deleted_id = self.auth.user.id if self.auth.user else None
        await self.db.flush()
