"""
数据库初始化与种子数据管理。

简化策略：每张表为空时一次性插入种子数据，已有数据则跳过。
改 JSON → 清空对应表 → 重启即可。
"""

import asyncio
import json
import re
from datetime import datetime, time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.module_platform.email.model import EmailConfigModel, EmailTemplateModel
from app.api.v1.module_platform.invoice.model import InvoiceModel
from app.api.v1.module_platform.menu.model import MenuModel
from app.api.v1.module_platform.order.model import OrderModel, PaymentRecordModel, RefundModel
from app.api.v1.module_platform.package.model import PackageMenuModel, PackageModel, PackagePluginModel
from app.api.v1.module_platform.plugin.model import PluginModel, TenantPluginModel
from app.api.v1.module_platform.tenant.model import TenantModel, TenantUserModel
from app.api.v1.module_system.dept.model import DeptModel
from app.api.v1.module_system.dict.model import DictDataModel, DictTypeModel
from app.api.v1.module_system.log.model import LoginLogModel, OperationLogModel
from app.api.v1.module_system.notice.model import BusinessNotificationModel, NoticeModel, NoticeReadModel
from app.api.v1.module_system.params.model import ParamsModel
from app.api.v1.module_system.position.model import PositionModel
from app.api.v1.module_system.role.model import RoleModel
from app.api.v1.module_system.ticket.model import TicketCommentModel, TicketModel
from app.api.v1.module_system.user.model import UserModel, UserRolesModel
from app.config.path_conf import SCRIPT_DIR
from app.core.assembly import get_assembly, known_plugin_module_codes, plugin_code_candidates
from app.core.database import async_db_session, create_tables
from app.core.logger import logger
from app.plugin.module_example.demo.model import DemoModel
from app.plugin.module_task.business.task.model import BusinessTaskModel
from app.plugin.module_task.cronjob.node.model import NodeModel
from app.plugin.module_task.workflow.nodes.model import WorkflowNodeTypeModel
from app.scripts.seed_loader import resolve_seed_files


class InitializeData:
    """初始化数据库和基础数据"""

    _DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
    _DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    _TIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}(\.\d+)?$")

    # 按依赖关系排序：先基础表，再关联表
    prepare_init_models: list[type] = [
        # ── 平台管理：基础表 ──
        PackageModel,
        TenantModel,
        PluginModel,
        MenuModel,
        # ── 系统管理：基础表 ──
        ParamsModel,
        DeptModel,
        RoleModel,
        DictTypeModel,
        DictDataModel,
        PositionModel,
        UserModel,
        # ── 平台管理：依赖用户的表 ──
        EmailConfigModel,
        EmailTemplateModel,
        OrderModel,
        InvoiceModel,
        PaymentRecordModel,
        RefundModel,
        # ── 关联表 ──
        UserRolesModel,
        TenantUserModel,
        PackageMenuModel,
        PackagePluginModel,
        TenantPluginModel,
        # ── 其他系统/业务表 ──
        NoticeModel,
        NoticeReadModel,
        BusinessNotificationModel,
        TicketModel,
        TicketCommentModel,
        # ── 日志表（追加写入） ──
        LoginLogModel,
        OperationLogModel,
        # ── 插件表 ──
        NodeModel,
        BusinessTaskModel,
        WorkflowNodeTypeModel,
        DemoModel,
    ]

    # 树形模型：JSON 含嵌套 children，需递归创建对象
    _RECURSIVE_TABLES: set[str] = {"platform_menu", "sys_dept"}

    def __init__(self) -> None:
        self._seed_files_by_table = resolve_seed_files()
        self._assembly = get_assembly()
        self._disabled_module_codes = {
            module_code
            for module_code in known_plugin_module_codes()
            if not self._assembly.is_plugin_enabled(module_code)
        }
        self._disabled_plugin_codes = {
            plugin_code
            for module_code in self._disabled_module_codes
            for plugin_code in plugin_code_candidates(module_code)
        }
        self._disabled_plugin_seed_ids: set[int] = set()
        self._plugin_seed_id_map: dict[int, int] = {}

    async def init_db(self) -> None:
        """建表并导入种子数据"""
        try:
            await create_tables()
        except asyncio.exceptions.TimeoutError:
            logger.error("❌️ 数据库表结构初始化超时")
            raise

        async with async_db_session() as session:
            async with session.begin():
                await self.__init_data(session)

    async def __init_data(self, db: AsyncSession) -> None:
        """按依赖顺序初始化各表种子数据"""
        dict_type_mapping: dict[str, Any] = {}  # dict_type → DictTypeModel 实例

        init_models = [
            model for model in self.prepare_init_models
            if model.__tablename__ in self._seed_files_by_table
        ]

        for model in init_models:
            table_name = model.__tablename__

            data = await self.__load_json(table_name)
            if not data:
                logger.info(f"⏭️  跳过 {table_name} 表，无初始化数据")
                continue

            try:
                # 树形表（platform_menu / sys_dept）：递归创建含 children 的对象
                if table_name in self._RECURSIVE_TABLES:
                    count = await db.execute(select(func.count()).select_from(model))
                    if count.scalar():
                        logger.info(f"⏭️  跳过 {table_name} 表数据初始化（表已有数据）")
                        continue
                    objs = self.__create_objects_with_children(data, model)
                    db.add_all(objs)
                    await db.flush()
                    logger.info(f"✅️ 已向 {table_name} 写入初始化数据")
                    continue

                # 字典类型表：存储类型映射供字典数据使用
                if table_name == "sys_dict_type":
                    count = await db.execute(select(func.count()).select_from(model))
                    if count.scalar():
                        logger.info(f"⏭️  跳过 {table_name} 表数据初始化（表已有数据）")
                        continue
                    objs = []
                    for item in data:
                        obj = model(**item)
                        objs.append(obj)
                        dict_type_mapping[item["dict_type"]] = obj
                    db.add_all(objs)
                    await db.flush()
                    logger.info(f"✅️ 已向 {table_name} 写入初始化数据")
                    continue

                # 字典数据表：关联 dict_type_id
                if table_name == "sys_dict_data":
                    count = await db.execute(select(func.count()).select_from(model))
                    if count.scalar():
                        logger.info(f"⏭️  跳过 {table_name} 表数据初始化（表已有数据）")
                        continue
                    objs = []
                    for item in data:
                        dict_type_str = item.get("dict_type")
                        if dict_type_str not in dict_type_mapping:
                            logger.warning(f"⚠️  未找到字典类型 {dict_type_str}，跳过")
                            continue
                        item["dict_type_id"] = dict_type_mapping[dict_type_str].id
                        objs.append(model(**item))
                    db.add_all(objs)
                    await db.flush()
                    logger.info(f"✅️ 已向 {table_name} 写入初始化数据")
                    continue

                # 日志表：追加写入，已有数据跳过
                if table_name in ("sys_login_log", "sys_operation_log"):
                    count = await db.execute(select(func.count()).select_from(model))
                    if count.scalar():
                        logger.info(f"⏭️  跳过 {table_name} 表数据初始化（表已有数据）")
                        continue
                    objs = [model(**item) for item in data]
                    db.add_all(objs)
                    await db.flush()
                    logger.info(f"✅️ 已向 {table_name} 写入 {len(objs)} 条")
                    continue

                # 普通表：空表时插入，已有数据跳过
                count = await db.execute(select(func.count()).select_from(model))
                if count.scalar():
                    logger.info(f"⏭️  跳过 {table_name} 表数据初始化（表已有数据）")
                    continue
                objs = [model(**item) for item in data]
                db.add_all(objs)
                await db.flush()
                logger.info(f"✅️ 已向 {table_name} 写入初始化数据")

            except Exception:
                logger.error(f"❌️ 初始化 {table_name} 表数据失败")
                raise

        await self.__backfill_tenant_memberships(db)

    async def __backfill_tenant_memberships(self, db: AsyncSession) -> None:
        """回填历史用户的租户关系，保证会话租户校验不会误杀旧数据。"""
        stmt = (
            select(UserModel)
            .where(
                UserModel.tenant_id.is_not(None),
                UserModel.is_deleted.is_(False),
            )
        )
        users = (await db.execute(stmt)).scalars().all()
        added = 0
        for user in users:
            exists_stmt = (
                select(TenantUserModel)
                .where(
                    TenantUserModel.user_id == user.id,
                    TenantUserModel.tenant_id == user.tenant_id,
                )
                .limit(1)
            )
            if (await db.execute(exists_stmt)).scalar_one_or_none():
                continue
            has_any_stmt = select(TenantUserModel).where(TenantUserModel.user_id == user.id).limit(1)
            has_any = (await db.execute(has_any_stmt)).scalar_one_or_none()
            db.add(
                TenantUserModel(
                    user_id=user.id,
                    tenant_id=user.tenant_id,
                    role="owner" if user.is_superuser else "member",
                    is_default=0 if has_any else 1,
                )
            )
            added += 1
        if added:
            await db.flush()
            logger.info(f"✅️ 已回填 {added} 条用户租户关系")

    @staticmethod
    def __create_objects_with_children(data: list[dict], model_class: type) -> list:
        """递归创建树形模型实例，处理嵌套 children 并注入 parent_id"""

        def _create(obj_data: dict) -> Any:
            children_data = obj_data.pop("children", [])

            # JSON 中子节点 parent_id 通常为 null，先按原始值创建
            obj = model_class(**obj_data)

            if children_data:
                obj.children = [_create(child) for child in children_data]

            return obj

        return [_create(item) for item in data]

    async def __load_json(self, filename: str) -> list[dict]:
        """读取并解析种子数据 JSON 文件"""
        json_path = self._seed_files_by_table.get(filename) or SCRIPT_DIR / f"{filename}.json"
        if not json_path.exists():
            return []

        try:
            with open(json_path, encoding="utf-8") as f:
                raw = json.loads(f.read())
            data = self.__filter_seed_data(filename, raw)
            return [self._parse_date_strings(item) for item in data]
        except json.JSONDecodeError as e:
            logger.error(f"❌️ 解析 {json_path} 失败: {e!s}")
            raise
        except Exception as e:
            logger.error(f"❌️ 读取 {json_path} 失败: {e!s}")
            raise

    def __filter_seed_data(self, table_name: str, data: list[dict]) -> list[dict]:
        """按当前 assembly 裁剪 legacy seed 中的可选插件数据。"""
        if table_name == "platform_menu":
            return self.__filter_menu_tree(data)
        if not self._disabled_module_codes:
            return data
        if table_name == "platform_plugin":
            return self.__filter_platform_plugins(data)
        if table_name in {"platform_package_plugin", "platform_tenant_plugin"}:
            return self.__filter_plugin_relations(data)
        return data

    def __filter_menu_tree(self, data: list[dict]) -> list[dict]:
        return self._assembly.filter_menu_tree(data)

    def __filter_platform_plugins(self, data: list[dict]) -> list[dict]:
        filtered: list[dict] = []
        self._disabled_plugin_seed_ids = set()
        self._plugin_seed_id_map = {}

        for original_id, item in enumerate(data, start=1):
            if item.get("code") in self._disabled_plugin_codes:
                self._disabled_plugin_seed_ids.add(original_id)
                continue
            self._plugin_seed_id_map[original_id] = len(filtered) + 1
            filtered.append(item)
        return filtered

    def __filter_plugin_relations(self, data: list[dict]) -> list[dict]:
        filtered: list[dict] = []
        for item in data:
            original_plugin_id = item.get("plugin_id")
            if original_plugin_id in self._disabled_plugin_seed_ids:
                continue
            plugin_id = self._plugin_seed_id_map.get(original_plugin_id, original_plugin_id)
            filtered.append({**item, "plugin_id": plugin_id})
        return filtered

    @classmethod
    def _parse_date_strings(cls, data: dict) -> dict:
        """递归转换 JSON 中的日期时间字符串为 datetime 对象（兼容 PostgreSQL）"""
        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                if cls._DATETIME_RE.match(value):
                    result[key] = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
                elif cls._DATE_RE.match(value):
                    result[key] = datetime.strptime(value, "%Y-%m-%d").date()
                elif cls._TIME_RE.match(value):
                    result[key] = time.fromisoformat(value)
                else:
                    result[key] = value
            elif isinstance(value, dict):
                result[key] = cls._parse_date_strings(value)
            else:
                result[key] = value
        return result
