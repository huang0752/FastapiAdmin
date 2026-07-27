
from typing import Any

from sqlalchemy import select

from app.api.v1.module_system.user.model import UserModel
from app.core.base_schema import AuthSchema, PageResultSchema
from app.core.exceptions import CustomException
from app.utils.excel_util import ExcelUtil

from .crud import TicketCommentCRUD, TicketCRUD
from .schema import (
    TicketBatchSchema,
    TicketCommentCreateSchema,
    TicketCommentOutSchema,
    TicketCreateSchema,
    TicketOutSchema,
    TicketQueryParam,
    TicketUpdateSchema,
)

_TICKET_STATUS_TRANSITIONS = {
    0: {1, 3},
    1: {2, 3},
    2: {3},
    3: {0},
}

_TICKET_STATUS_LABELS = {
    0: "待处理",
    1: "处理中",
    2: "已完成",
    3: "已关闭",
}


class TicketService:
    """工单管理服务"""

    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth

    def _validate_status_transition(self, ticket, new_status: int) -> None:
        old_status = ticket.status if ticket.status is not None else 0
        old_label = _TICKET_STATUS_LABELS.get(old_status, str(old_status))
        new_label = _TICKET_STATUS_LABELS.get(new_status, str(new_status))

        if new_status not in _TICKET_STATUS_TRANSITIONS.get(old_status, set()):
            raise CustomException(msg=f"不允许从{old_label}转换为{new_label}")

        is_super = self.auth.user and self.auth.user.is_superuser
        is_creator = self.auth.user and ticket.created_id == self.auth.user.id
        is_assignee = self.auth.user and ticket.assigned_id == self.auth.user.id

        if new_status == 0:
            if not is_super:
                raise CustomException(msg="仅超管可以重新打开已关闭的工单")
        elif old_status == 0 and new_status == 1:
            if not (is_super or is_creator or is_assignee):
                raise CustomException(msg="仅创建人、处理人或超管可以受理工单")
        elif old_status == 0 and new_status == 3:
            if not (is_super or is_creator):
                raise CustomException(msg="仅创建人或超管可以取消工单")
        elif old_status == 1 and new_status == 2:
            if not (is_super or is_assignee):
                raise CustomException(msg="仅处理人或超管可以将工单标记为已完成")
        elif old_status == 1 and new_status == 3:
            if not (is_super or is_creator or is_assignee):
                raise CustomException(msg="仅创建人、处理人或超管可以关闭工单")
        elif old_status == 2 and new_status == 3:
            if not (is_super or is_creator):
                raise CustomException(msg="仅创建人或超管可以确认关闭工单")

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: TicketQueryParam | None = None,
        order_by: list | None = None,
    ) -> dict:
        return await TicketCRUD(self.auth).page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"created_time": "desc"}],
            search=vars(search) if search else None,
            out_schema=TicketOutSchema,
        )

    async def detail(self, id: int) -> TicketOutSchema:
        return await TicketCRUD(self.auth).get_or_404(id=id, out_schema=TicketOutSchema)

    async def create(self, data: TicketCreateSchema) -> TicketOutSchema:
        obj = await TicketCRUD(self.auth).create(data=data)
        if not obj:
            raise CustomException(msg="创建工单失败")
        return TicketOutSchema.model_validate(obj)

    async def update(self, id: int, data: TicketUpdateSchema) -> TicketOutSchema:
        obj = await TicketCRUD(self.auth).get_or_404(id=id, msg="工单不存在")

        if data.status is not None:
            self._validate_status_transition(obj, data.status)

        if data.assigned_id is not None:
            user_stmt = select(UserModel).where(
                UserModel.id == data.assigned_id,
                UserModel.is_deleted.is_(False),
            )
            user_result = await self.auth.db.execute(user_stmt)
            assigned_user = user_result.scalar_one_or_none()
            if not assigned_user:
                raise CustomException(msg="指定的处理人不存在")
            if assigned_user.tenant_id != obj.tenant_id:
                raise CustomException(msg="处理人必须与工单属于同一租户")

        updated = await TicketCRUD(self.auth).update(id=id, data=data)
        if not updated:
            raise CustomException(msg="工单不存在")
        return TicketOutSchema.model_validate(updated)

    async def delete(self, ids: list[int]) -> None:
        if not ids:
            raise CustomException(msg="删除对象不能为空")
        await TicketCRUD(self.auth).delete(ids=ids)

    async def batch(self, data: TicketBatchSchema) -> None:
        if not data.ids:
            raise CustomException(msg="请选择要操作的工单")

        tickets = await TicketCRUD(self.auth).get_by_ids_crud(ids=data.ids)
        ticket_map = {t.id: t for t in tickets}
        for tid in data.ids:
            obj = ticket_map.get(tid)
            if not obj:
                raise CustomException(msg=f"工单[{tid}]不存在")
            self._validate_status_transition(obj, data.status)
        await TicketCRUD(self.auth).set_crud(ids=data.ids, status=data.status)

    async def get_list(
        self,
        search: TicketQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> list[TicketOutSchema]:
        obj_list = await TicketCRUD(self.auth).get_list(
            search=vars(search) if search else None,
            order_by=order_by or [{"created_time": "desc"}],
        )
        return [TicketOutSchema.model_validate(obj) for obj in obj_list]

    @staticmethod
    def export_list(ticket_list: list[dict[str, Any]]) -> bytes:
        """导出工单列表。"""

        mapping_dict = {
            "id": "工单编号",
            "title": "工单标题",
            "ticket_type": "工单类型",
            "summary": "工单摘要",
            "status": "工单状态",
            "description": "备注",
            "created_time": "创建时间",
            "updated_time": "更新时间",
        }
        type_labels = {
            "suggestion": "建议",
            "bug": "缺陷",
            "optimize": "优化",
            "other": "其他",
        }
        export_data = [item.copy() for item in ticket_list]
        for item in export_data:
            ticket_type = item.get("ticket_type")
            item["ticket_type"] = type_labels.get(getattr(ticket_type, "value", ticket_type), ticket_type)
            item["status"] = _TICKET_STATUS_LABELS.get(item.get("status"), item.get("status"))
        return ExcelUtil.export_list2excel(list_data=export_data, mapping_dict=mapping_dict)


class TicketCommentService:
    """带父工单校验和租户隔离的评论服务。"""

    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth

    async def page(
        self,
        ticket_id: int,
        page_no: int,
        page_size: int,
    ) -> PageResultSchema[TicketCommentOutSchema]:
        await TicketCRUD(self.auth).get_or_404(id=ticket_id, msg="工单不存在")
        return await TicketCommentCRUD(self.auth).page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=[{"created_time": "desc"}],
            search={"ticket_id": ("eq", ticket_id)},
            out_schema=TicketCommentOutSchema,
        )

    async def create(
        self,
        ticket_id: int,
        data: TicketCommentCreateSchema,
    ) -> TicketCommentOutSchema:
        ticket = await TicketCRUD(self.auth).get_or_404(id=ticket_id, msg="工单不存在")
        obj = await TicketCommentCRUD(self.auth).create(
            data=data.model_dump()
            | {"ticket_id": ticket_id, "tenant_id": ticket.tenant_id}
        )
        return TicketCommentOutSchema.model_validate(obj)
