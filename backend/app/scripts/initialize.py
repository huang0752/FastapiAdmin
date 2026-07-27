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
            if hasattr(model, "tenant_id"):
                data = [
                    item if item.get("tenant_id") is not None else {**item, "tenant_id": 1}
                    for item in data
                ]

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

                # 套餐菜单使用 package code + permission/route identity，运行时解析主键。
                if table_name == "platform_package_menu":
                    count = await db.execute(select(func.count()).select_from(model))
                    if count.scalar():
                        logger.info(f"⏭️  跳过 {table_name} 表数据初始化（表已有数据）")
                        continue
                    resolved_rows = await self.resolve_package_menu_seed(db, data)
                    db.add_all([model(**item) for item in resolved_rows])
                    await db.flush()
                    logger.info(f"✅️ 已向 {table_name} 写入 {len(resolved_rows)} 条稳定菜单关联")
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

        await self.__ensure_owner_workspace_menus(db)
        await self.__backfill_tenant_memberships(db)

    async def __ensure_owner_workspace_menus(self, db: AsyncSession) -> None:
        """幂等补齐旧数据库缺失的租户工作台必备权限。"""
        platform_root = (
            await db.execute(
                select(MenuModel).where(MenuModel.route_name == "Platform").limit(1)
            )
        ).scalar_one_or_none()
        if platform_root is None:
            return

        changed = False
        workspace = (
            await db.execute(
                select(MenuModel).where(MenuModel.route_name == "PlatformWorkspace").limit(1)
            )
        ).scalar_one_or_none()
        if workspace is None:
            workspace = MenuModel(
                name="租户工作台",
                type=2,
                icon="ri:briefcase-line",
                order=13,
                permission="module_platform:workspace:query",
                route_name="PlatformWorkspace",
                route_path="workspace",
                component_path="module_platform/self_service/index",
                title="租户工作台",
                scope="tenant",
                status=0,
                parent_id=platform_root.id,
            )
            db.add(workspace)
            await db.flush()
            changed = True

        for menu in (platform_root, workspace):
            if menu.scope != "tenant":
                menu.scope = "tenant"
                changed = True
        if workspace.parent_id != platform_root.id:
            workspace.parent_id = platform_root.id
            changed = True

        button_specs = (
            ("查询", 1, "module_platform:workspace:query"),
            ("修改品牌配置", 2, "module_platform:workspace:update"),
        )
        for name, order, permission in button_specs:
            button = (
                await db.execute(
                    select(MenuModel)
                    .where(MenuModel.type == 3, MenuModel.permission == permission)
                    .order_by(MenuModel.id)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if button is None:
                button = MenuModel(
                    name=name,
                    title=name,
                    type=3,
                    order=order,
                    permission=permission,
                    scope="tenant",
                    status=0,
                    parent_id=workspace.id,
                )
                db.add(button)
                changed = True
                continue
            if button.parent_id != workspace.id:
                button.parent_id = workspace.id
                changed = True
            if button.scope != "tenant":
                button.scope = "tenant"
                changed = True

        if changed:
            await db.flush()
            logger.info("✅️ 已增量校准租户工作台菜单与品牌配置权限")

    async def __backfill_tenant_memberships(self, db: AsyncSession) -> None:
        """回填历史成员关系，并幂等修复普通租户 owner 角色与授权。"""
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
                    role="member",
                    is_default=0 if has_any else 1,
                )
            )
            added += 1
        if added:
            await db.flush()
            logger.info(f"✅️ 已回填 {added} 条用户租户关系")

        from app.api.v1.module_platform.tenant.service import TenantService

        tenant_ids = set(
            (
                await db.execute(
                    select(TenantModel.id).where(
                        TenantModel.id != 1,
                        TenantModel.is_deleted.is_(False),
                    )
                )
            ).scalars().all()
        )
        repaired = 0
        ownerless_tenant_ids: list[int] = []
        for tenant_id in tenant_ids:
            owner_user_ids = set(
                (
                    await db.execute(
                        select(TenantUserModel.user_id).where(
                            TenantUserModel.tenant_id == tenant_id,
                            TenantUserModel.role == "owner",
                        )
                    )
                ).scalars().all()
            )
            if not owner_user_ids:
                ownerless_tenant_ids.append(tenant_id)
            for user_id in owner_user_ids:
                await TenantService.ensure_tenant_owner(db, tenant_id, user_id)
                repaired += 1
        if repaired:
            logger.info(f"✅️ 已校准 {repaired} 条租户 owner 角色与权限绑定")
        if ownerless_tenant_ids:
            logger.warning(
                f"⚠️  租户缺少显式 owner，未自动提权，请由管理员确认: {sorted(ownerless_tenant_ids)}"
            )

    async def resolve_package_menu_seed(
        self,
        db: AsyncSession,
        data: list[dict],
        *,
        tenant_scope_override_ids: set[int] | None = None,
    ) -> list[dict[str, int]]:
        """把稳定套餐编码和菜单 identity 解析成运行时关联主键。"""
        scope_override_ids = tenant_scope_override_ids or set()
        resolved_pairs: set[tuple[int, int]] = set()
        for package_seed in data:
            package_code = package_seed.get("package_code")
            identities = package_seed.get("menus")
            if not package_code or not isinstance(identities, list) or not identities:
                raise ValueError("platform_package_menu seed 需要 package_code 和非空 menus")
            package = (
                await db.execute(
                    select(PackageModel).where(PackageModel.code == package_code).limit(1)
                )
            ).scalar_one_or_none()
            if package is None:
                raise ValueError(f"platform_package_menu 未找到套餐编码: {package_code}")

            selected_ids: set[int] = set()
            for identity in identities:
                identity_keys = [
                    key
                    for key in ("permission", "route_name", "route_path")
                    if identity.get(key)
                ]
                if len(identity_keys) != 1:
                    raise ValueError(f"菜单 identity 必须且只能指定一个稳定字段: {identity}")
                key = identity_keys[0]
                menus = (
                    await db.execute(
                        select(MenuModel).where(getattr(MenuModel, key) == identity[key])
                    )
                ).scalars().all()
                if not menus:
                    raise ValueError(f"菜单 identity 无匹配: {identity}")
                if any(
                    menu.scope != "tenant" and menu.id not in scope_override_ids
                    for menu in menus
                ):
                    raise ValueError(f"套餐 seed 拒绝 platform scope 菜单: {identity}")
                selected_ids.update(menu.id for menu in menus)

            pending = set(selected_ids)
            while pending:
                menus = (
                    await db.execute(select(MenuModel).where(MenuModel.id.in_(pending)))
                ).scalars().all()
                if len(menus) != len(pending):
                    raise ValueError(f"套餐 seed 菜单父级缺失: {sorted(pending)}")
                if any(
                    menu.scope != "tenant" and menu.id not in scope_override_ids
                    for menu in menus
                ):
                    raise ValueError("套餐 seed 的父级菜单不能是 platform scope")
                selected_ids.update(menu.id for menu in menus)
                pending = {
                    menu.parent_id
                    for menu in menus
                    if menu.parent_id is not None and menu.parent_id not in selected_ids
                }

            resolved_pairs.update((package.id, menu_id) for menu_id in selected_ids)

        return [
            {"package_id": package_id, "menu_id": menu_id}
            for package_id, menu_id in sorted(resolved_pairs)
        ]

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
