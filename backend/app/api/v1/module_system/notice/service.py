from datetime import datetime

from app.core.base_schema import AuthSchema, BatchSetAvailable
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.utils.excel_util import ExcelUtil

from .crud import BusinessNotificationCRUD, NoticeCRUD
from .model import NoticeReadModel
from .schema import (
    BusinessNotificationCreateSchema,
    BusinessNotificationOutSchema,
    BusinessNotificationQueryParam,
    BusinessNotificationUpdateSchema,
    NoticeCreateSchema,
    NoticeOutSchema,
    NoticeQueryParam,
    NoticeUpdateSchema,
    PanelDataOut,
    PanelMessageItem,
)


class NoticeService:
    """
    公告管理服务

    提供公告 CRUD、状态切换、已启用公告分页查询、消息面板、Excel 导出等业务能力。
    """

    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth

    async def detail(self, id: int) -> NoticeOutSchema:
        return await NoticeCRUD(self.auth).get_or_404(id=id, out_schema=NoticeOutSchema)

    async def get_list(
        self,
        search: NoticeQueryParam | None = None,
        order_by: list[dict] | None = None,
    ) -> list[NoticeOutSchema]:
        notice_obj_list = await NoticeCRUD(self.auth).get_list(search=vars(search) if search else None, order_by=order_by)
        return [NoticeOutSchema.model_validate(notice_obj) for notice_obj in notice_obj_list]

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: NoticeQueryParam | None = None,
        order_by: list[dict] | None = None,
    ) -> dict:
        offset = (page_no - 1) * page_size
        return await NoticeCRUD(self.auth).page(
            offset=offset,
            limit=page_size,
            order_by=order_by or [{"id": "asc"}],
            search=vars(search) if search else None,
            out_schema=NoticeOutSchema,
        )

    async def available_page(self) -> dict:
        return await NoticeCRUD(self.auth).page(
            offset=0,
            limit=10,
            order_by=[{"id": "asc"}],
            search={"status": 0},
            out_schema=NoticeOutSchema,
        )

    async def create(self, data: NoticeCreateSchema) -> NoticeOutSchema:
        notice = await NoticeCRUD(self.auth).get(notice_title=data.notice_title)
        if notice:
            raise CustomException(msg="创建失败，该数据已存在")
        notice_obj = await NoticeCRUD(self.auth).create(data=data)
        return NoticeOutSchema.model_validate(notice_obj)

    async def update(self, id: int, data: NoticeUpdateSchema) -> NoticeOutSchema:
        _ = await NoticeCRUD(self.auth).get_or_404(id=id, msg="更新失败，该数据不存在")
        exist_notice = await NoticeCRUD(self.auth).get(notice_title=data.notice_title)
        if exist_notice and exist_notice.id != id:
            raise CustomException(msg="更新失败，标题已存在")
        notice_obj = await NoticeCRUD(self.auth).update(id=id, data=data)
        return NoticeOutSchema.model_validate(notice_obj)

    async def delete(self, ids: list[int]) -> None:
        if len(ids) < 1:
            raise CustomException(msg="删除失败，删除对象不能为空")
        notices = await NoticeCRUD(self.auth).get_list(search={"id": ("in", ids)})
        notice_map = {n.id: n for n in notices}
        for nid in ids:
            if nid not in notice_map:
                raise CustomException(msg="删除失败，该数据不存在")
        await NoticeCRUD(self.auth).delete(ids=ids)

    async def set_available(self, data: BatchSetAvailable) -> None:
        await NoticeCRUD(self.auth).set(ids=data.ids, status=data.status)

    @staticmethod
    def export(notice_list: list[dict]) -> bytes:
        mapping_dict = {
            "id": "编号",
            "notice_title": "公告标题",
            "notice_type": "公告类型（1通知 2公告）",
            "notice_content": "公告内容",
            "status": "状态",
            "description": "备注",
            "created_time": "创建时间",
            "updated_time": "更新时间",
            "created_id": "创建者ID",
            "updated_id": "更新者ID",
        }
        data = notice_list.copy()
        for item in data:
            item["status"] = "启用" if item.get("status") == 0 else "停用"
            item["notice_type"] = "通知" if item.get("notice_type") == "1" else "公告"
        return ExcelUtil.export_list2excel(list_data=data, mapping_dict=mapping_dict)

    async def latest(self, limit: int = 5) -> list[NoticeOutSchema]:
        notices = await NoticeCRUD(self.auth).get_list(
            search={"status": 0},
            order_by=[{"created_time": "desc"}],
            preload=[],
        )
        return [NoticeOutSchema.model_validate(n) for n in notices[:limit]]

    async def mark_read(self, notice_id: int) -> None:
        from datetime import datetime

        from sqlalchemy import select

        await NoticeCRUD(self.auth).get_or_404(id=notice_id, msg="该公告不存在")

        exist_stmt = select(NoticeReadModel).where(
            NoticeReadModel.user_id == self.auth.user.id,
            NoticeReadModel.notice_id == notice_id,
        )
        result = await self.auth.db.execute(exist_stmt)
        if result.scalar_one_or_none():
            return

        read_record = NoticeReadModel(
            user_id=self.auth.user.id,
            notice_id=notice_id,
            read_time=datetime.now(),
        )
        self.auth.db.add(read_record)
        await self.auth.db.flush()

    async def mark_all_read(self) -> int:
        from datetime import datetime

        from sqlalchemy import insert, select

        read_ids_stmt = select(NoticeReadModel.notice_id).where(NoticeReadModel.user_id == self.auth.user.id)
        read_ids_result = await self.auth.db.execute(read_ids_stmt)
        read_ids = {row[0] for row in read_ids_result.fetchall()}

        notices = await NoticeCRUD(self.auth).get_list(
            search={"status": 0},
            preload=[],
        )
        all_ids = {notice.id for notice in notices}

        unread_ids = all_ids - read_ids
        if not unread_ids:
            return 0

        now = datetime.now()
        if unread_ids:
            await self.auth.db.execute(
                insert(NoticeReadModel),
                [{"user_id": self.auth.user.id, "notice_id": nid, "read_time": now} for nid in unread_ids],
            )
        await self.auth.db.flush()
        return len(unread_ids)

    async def get_unread_count(self) -> int:
        from sqlalchemy import func, select

        notices = await NoticeCRUD(self.auth).get_list(
            search={"status": 0},
            preload=[],
        )
        notice_ids = [notice.id for notice in notices]
        total_count = len(notice_ids)
        if not notice_ids:
            return 0

        read_stmt = select(func.count()).select_from(NoticeReadModel).where(
            NoticeReadModel.user_id == self.auth.user.id,
            NoticeReadModel.notice_id.in_(notice_ids),
        )
        read_result = await self.auth.db.execute(read_stmt)
        read_count = read_result.scalar() or 0

        return max(0, total_count - read_count)

    async def panel_data(self) -> PanelDataOut:
        notices = await self.latest(limit=5)

        messages = []
        try:
            from app.api.v1.module_system.log.crud import OperationLogCRUD

            logs = await OperationLogCRUD(self.auth).get_list(
                order_by=[{"created_time": "desc"}],
                preload=[],
            )
            for log_entry in logs:
                messages.append(
                    PanelMessageItem(
                        id=log_entry.id,
                        title=log_entry.request_path or "系统操作",
                        content=f"{log_entry.request_method} {log_entry.request_path}",
                        time=log_entry.created_time.strftime("%Y-%m-%d %H:%M") if log_entry.created_time else "",
                        type="system",
                    )
                )
                if len(messages) >= 5:
                    break
        except Exception as e:
            logger.warning(f"获取面板消息数据失败（操作日志表可能不存在），已跳过: {e}")

        pendings = await BusinessNotificationService(self.auth).pending_for_panel(limit=10)

        return PanelDataOut(
            notices=notices,
            messages=messages,
            pendings=pendings,
        )


class BusinessNotificationService:
    """业务通知/待办/预警服务。"""

    def __init__(self, auth: AuthSchema) -> None:
        self.auth = auth
        self.crud = BusinessNotificationCRUD(auth)

    async def create(self, data: BusinessNotificationCreateSchema) -> BusinessNotificationOutSchema:
        obj = await self.crud.create(data=data)
        return BusinessNotificationOutSchema.model_validate(obj)

    async def page(
        self,
        page_no: int,
        page_size: int,
        search: BusinessNotificationQueryParam | None = None,
        order_by: list[dict] | None = None,
    ) -> dict:
        return await self.crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"created_time": "desc"}],
            search=vars(search) if search else None,
            out_schema=BusinessNotificationOutSchema,
        )

    async def handle(self, id: int, data: BusinessNotificationUpdateSchema) -> BusinessNotificationOutSchema:
        values = data.model_dump()
        values["handled_time"] = datetime.now() if data.handled else None
        obj = await self.crud.update(id=id, data=values)
        return BusinessNotificationOutSchema.model_validate(obj)

    async def pending_for_panel(self, limit: int = 10) -> list[dict]:
        items = await self.crud.get_list(
            search={"handled": False},
            order_by=[{"created_time": "desc"}],
            preload=[],
        )
        result = []
        for item in items[:limit]:
            result.append(
                {
                    "id": item.id,
                    "module": item.module,
                    "biz_type": item.biz_type,
                    "biz_id": item.biz_id,
                    "title": item.title,
                    "content": item.content,
                    "level": item.level,
                    "action_url": item.action_url,
                    "handled": item.handled,
                    "time": item.created_time.strftime("%Y-%m-%d %H:%M") if item.created_time else "",
                }
            )
        return result
