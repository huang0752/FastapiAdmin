"""Preview and explicitly enqueue historical Control user entitlements.

The command is read-only unless ``--enqueue`` or ``--retry-failed`` is supplied.
It never calls a product endpoint directly; mutations only prepare the existing
transactional outbox and publish committed task IDs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.module_control.model import (
    ControlApplicationModel,
    ControlTenantApplicationModel,
    ControlUserApplicationGrantModel,
)
from app.api.v1.module_control.user_entitlement.service import (
    ControlUserEntitlementCommandService,
    publish_entitlement_tasks,
)
from app.api.v1.module_platform.tenant.model import TenantModel, TenantUserModel
from app.api.v1.module_system.user.model import UserModel
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session


class _SafeArgumentParser(argparse.ArgumentParser):
    def parse_args(self, args=None, namespace=None):
        parsed = super().parse_args(args, namespace)
        parsed.preview = not (parsed.enqueue or parsed.retry_failed or parsed.status)
        return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(description="预览或显式补同步中控用户应用授权")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--preview", action="store_true", help="只读输出待处理授权（默认）")
    modes.add_argument("--enqueue", action="store_true", help="显式创建并发布未初始化授权任务")
    modes.add_argument("--retry-failed", action="store_true", help="显式创建并发布失败授权的新代际任务")
    modes.add_argument("--status", action="store_true", help="只读输出授权同步状态汇总")
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--after-id", type=int, default=0)
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if not 1 <= args.batch_size <= 1000:
        raise ValueError("--batch-size 必须在 1 到 1000 之间")
    if args.after_id < 0:
        raise ValueError("--after-id 不能小于 0")


def needs_backfill(grant: object) -> bool:
    """Only legacy/uninitialized rows qualify; prepared generations are immutable."""
    if getattr(grant, "last_event_id", None):
        return False
    version = int(getattr(grant, "sync_version", 0) or 0)
    return version in {0, 1} and getattr(grant, "desired_state", None) == "active" and getattr(grant, "sync_status", None) == "pending" and getattr(grant, "status", None) == 0


def eligible_for_mode(grant: object, *, retry_failed: bool) -> bool:
    if retry_failed:
        return getattr(grant, "sync_status", None) == "failed"
    return needs_backfill(grant)


def page_coordinates(rows: list[object], *, processed_grant_ids: list[int]) -> dict[str, object]:
    """Return an explicit audit trail and a cursor that advances past scanned rows."""
    return {
        "processed_grant_ids": processed_grant_ids,
        "next_after_id": rows[-1].grant.id if rows else None,
    }


def control_preview_record(
    *,
    grant: object,
    opening: object,
    application: object,
    user: object,
    membership: object,
) -> dict[str, object]:
    """Build the cross-system record using the product tenant identity."""
    membership_role = getattr(membership, "role", None)
    return {
        "grant_id": grant.id,
        "application_code": application.code,
        "tenant_code": opening.target_tenant_code,
        "central_user_uuid": user.uuid,
        "central_membership_role": membership_role,
        "principal_role": membership_role,
        "desired_state": grant.desired_state,
        "sync_status": grant.sync_status,
        "sync_version": grant.sync_version,
        "needs_backfill": needs_backfill(grant),
    }


@dataclass(frozen=True, slots=True)
class _GrantRow:
    grant: ControlUserApplicationGrantModel
    opening: ControlTenantApplicationModel
    application: ControlApplicationModel
    tenant: TenantModel
    user: UserModel
    membership: TenantUserModel | None

    def export(self) -> dict:
        return control_preview_record(
            grant=self.grant,
            opening=self.opening,
            application=self.application,
            user=self.user,
            membership=self.membership,
        )


async def _load_rows(
    db: AsyncSession,
    *,
    batch_size: int,
    after_id: int,
    failed_only: bool = False,
    uninitialized_only: bool = False,
) -> list[_GrantRow]:
    conditions = [
        ControlUserApplicationGrantModel.id > after_id,
        ControlUserApplicationGrantModel.is_deleted.is_(False),
        ControlTenantApplicationModel.is_deleted.is_(False),
        ControlApplicationModel.is_deleted.is_(False),
        UserModel.is_deleted.is_(False),
    ]
    if failed_only:
        conditions.append(ControlUserApplicationGrantModel.sync_status == "failed")
    if uninitialized_only:
        conditions.append(
            and_(
                ControlUserApplicationGrantModel.last_event_id.is_(None),
                or_(
                    ControlUserApplicationGrantModel.sync_version == 0,
                    ControlUserApplicationGrantModel.sync_version == 1,
                ),
                ControlUserApplicationGrantModel.desired_state == "active",
                ControlUserApplicationGrantModel.sync_status == "pending",
                ControlUserApplicationGrantModel.status == 0,
            )
        )
    statement = (
        select(
            ControlUserApplicationGrantModel,
            ControlTenantApplicationModel,
            ControlApplicationModel,
            TenantModel,
            UserModel,
            TenantUserModel,
        )
        .join(
            ControlTenantApplicationModel,
            ControlTenantApplicationModel.id == ControlUserApplicationGrantModel.tenant_application_id,
        )
        .join(
            ControlApplicationModel,
            ControlApplicationModel.id == ControlTenantApplicationModel.application_id,
        )
        .join(TenantModel, TenantModel.id == ControlUserApplicationGrantModel.tenant_id)
        .join(UserModel, UserModel.id == ControlUserApplicationGrantModel.user_id)
        .outerjoin(
            TenantUserModel,
            and_(
                TenantUserModel.user_id == ControlUserApplicationGrantModel.user_id,
                TenantUserModel.tenant_id == ControlUserApplicationGrantModel.tenant_id,
            ),
        )
        .where(*conditions)
        .order_by(ControlUserApplicationGrantModel.id)
        .limit(batch_size)
    )
    return [_GrantRow(*row) for row in (await db.execute(statement)).all()]


def require_central_membership(row: object) -> TenantUserModel:
    membership = getattr(row, "membership", None)
    if membership is None or getattr(membership, "role", None) not in {
        "owner",
        "admin",
        "member",
    }:
        grant_id = getattr(getattr(row, "grant", None), "id", "unknown")
        raise RuntimeError(f"授权 {grant_id} 缺少有效中央租户成员关系，拒绝补同步")
    return membership


async def _actor_for(db: AsyncSession, row: _GrantRow) -> UserModel:
    """Resolve an existing active tenant administrator as the auditable task actor."""
    actor = await db.scalar(
        select(UserModel)
        .join(TenantUserModel, TenantUserModel.user_id == UserModel.id)
        .where(
            TenantUserModel.tenant_id == row.grant.tenant_id,
            TenantUserModel.role.in_(("owner", "admin")),
            UserModel.status == 0,
            UserModel.is_deleted.is_(False),
        )
        .order_by(UserModel.is_superuser.desc(), UserModel.id)
        .limit(1)
    )
    if actor is None:
        raise RuntimeError(f"授权 {row.grant.id} 所属租户没有可审计的 active owner/admin，拒绝补同步")
    return actor


async def _prepare(rows: list[_GrantRow], *, retry_failed: bool) -> tuple[list[int], list[int]]:
    task_ids: list[int] = []
    processed_grant_ids: list[int] = []
    for row in rows:
        require_central_membership(row)
        async with async_db_session() as db:
            current_membership = await db.scalar(
                select(TenantUserModel)
                .where(
                    TenantUserModel.user_id == row.grant.user_id,
                    TenantUserModel.tenant_id == row.grant.tenant_id,
                )
                .with_for_update()
            )
            if current_membership is None or current_membership.role not in {
                "owner",
                "admin",
                "member",
            }:
                raise RuntimeError(f"授权 {row.grant.id} 缺少有效中央租户成员关系，拒绝补同步")
            current = await db.scalar(
                select(ControlUserApplicationGrantModel)
                .where(
                    ControlUserApplicationGrantModel.id == row.grant.id,
                    ControlUserApplicationGrantModel.is_deleted.is_(False),
                )
                .with_for_update()
            )
            if current is None or not eligible_for_mode(current, retry_failed=retry_failed):
                await db.rollback()
                continue
            actor = await _actor_for(db, row)
            auth = AuthSchema(
                db=db,
                user=actor,
                tenant_id=row.grant.tenant_id,
                site_id=row.grant.site_id,
            )
            command = ControlUserEntitlementCommandService(auth)
            if retry_failed:
                task = await command.retry(row.grant.id)
            else:
                task = await command.set_desired_state(
                    row.grant.id,
                    current.desired_state,
                    mode="backfill",
                )
            await db.commit()
            task_ids.append(task.id)
            processed_grant_ids.append(row.grant.id)
    return task_ids, processed_grant_ids


async def run(args: argparse.Namespace) -> dict:
    validate_args(args)
    async with async_db_session() as db:
        if args.status:
            rows = await _load_rows(
                db,
                batch_size=args.batch_size,
                after_id=args.after_id,
            )
            counts: dict[str, int] = {}
            for row in rows:
                counts[row.grant.sync_status] = counts.get(row.grant.sync_status, 0) + 1
            return {
                "mode": "status",
                "count": len(rows),
                "by_sync_status": counts,
                **page_coordinates(
                    rows,
                    processed_grant_ids=[row.grant.id for row in rows],
                ),
            }
        rows = await _load_rows(
            db,
            batch_size=args.batch_size,
            after_id=args.after_id,
            failed_only=args.retry_failed,
            uninitialized_only=args.enqueue,
        )
        preview = [row.export() for row in rows]
    if args.preview:
        return {
            "mode": "preview",
            "count": len(preview),
            "entitlements": preview,
            **page_coordinates(
                rows,
                processed_grant_ids=[row.grant.id for row in rows],
            ),
        }

    task_ids, processed_grant_ids = await _prepare(rows, retry_failed=args.retry_failed)
    await publish_entitlement_tasks(task_ids)
    return {
        "mode": "retry-failed" if args.retry_failed else "enqueue",
        "count": len(task_ids),
        "task_ids": task_ids,
        **page_coordinates(rows, processed_grant_ids=processed_grant_ids),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(run(args))
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
