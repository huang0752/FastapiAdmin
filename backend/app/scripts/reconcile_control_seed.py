"""Add missing Control menus and declared package ceilings to an existing database.

Run in maintenance mode after schema upgrade. Dry-run is the default. Only the
explicit Site/package targets are changed; roles and user assignments are never
written. Menu definitions are global, package entitlements are Site-scoped.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.module_platform.menu.model import MenuModel
from app.api.v1.module_platform.package.model import PackageMenuModel, PackageModel
from app.api.v1.module_platform.site.model import SiteModel
from app.core.assembly import get_assembly
from app.core.control_features import is_control_provider

SEED_DIR = Path(__file__).parent / "seeds/control"


class ReconciliationError(RuntimeError):
    """A conflict requires review; no partial reconciliation is acceptable."""


def build_parser():
    parser = argparse.ArgumentParser(description="补齐现有中控库菜单与指定套餐上限；默认只读预览，不改角色")
    parser.add_argument("--site-id", type=int, required=True)
    parser.add_argument("--package-code", action="append", required=True, dest="package_codes")
    parser.add_argument("--apply", action="store_true", help="明确执行追加；需 sso_provider 装配")
    return parser


def _selected_seed_nodes(seed_dir):
    nodes = []

    def walk(rows, ancestors=()):
        for row in rows:
            index = len(nodes)
            nodes.append({"values": {k: v for k, v in row.items() if k != "children"}, "ancestors": ancestors, "selected": False})
            if str(row.get("permission") or "").startswith("module_control:"):
                for selected in (*ancestors, index):
                    nodes[selected]["selected"] = True
            walk(row.get("children", []), (*ancestors, index))

    walk(json.loads((seed_dir / "platform_menu.json").read_text()))
    return {index: node for index, node in enumerate(nodes) if node["selected"]}


def _matches(row, seed):
    # Page and button can intentionally share a permission; type differentiates them.
    permission = seed.get("permission")
    if permission and row["type"] == seed.get("type", 2) and row["permission"] == permission:
        return True
    for field in ("route_name", "route_path"):
        if seed.get(field) and row[field] == seed[field]:
            return True
    return False


async def reconcile_control_seed(db: AsyncSession, *, site_id: int, package_codes: list[str], apply: bool = False, assembly=None, seed_dir: Path = SEED_DIR):
    """Plan then append atomically in the caller's transaction; never commit here."""
    if site_id <= 0 or not package_codes:
        raise ReconciliationError("必须指定正数 Site ID 和套餐编码")
    if apply and not is_control_provider(assembly or get_assembly()):
        raise ReconciliationError("--apply 仅允许 sso_provider 中控装配")
    if apply and db.bind.dialect.name == "postgresql":
        # Menus lack a business-key unique constraint. Serialize against concurrent
        # menu/package writers, then re-read while holding the transaction lock.
        await db.execute(text("LOCK TABLE platform_menu, platform_package_menu IN SHARE ROW EXCLUSIVE MODE"))
    site = (await db.execute(select(SiteModel.__table__).where(SiteModel.id == site_id))).mappings().one_or_none()
    if not site or site["is_deleted"] or site["status"] != 0:
        raise ReconciliationError("目标 Site 不存在或已停用")
    package_table, menu_table, link_table = PackageModel.__table__, MenuModel.__table__, PackageMenuModel.__table__
    packages = (await db.execute(select(package_table).where(package_table.c.site_id == site_id, package_table.c.code.in_(set(package_codes))))).mappings().all()
    if {p["code"] for p in packages} != set(package_codes) or any(p["is_deleted"] or p["status"] != 0 for p in packages):
        raise ReconciliationError("目标 Site 套餐缺失或已停用；不会创建套餐或跨 Site 匹配")
    declared = {row["package_code"]: row["menus"] for row in json.loads((seed_dir / "platform_package_menu.json").read_text())}
    if not set(package_codes) <= declared.keys():
        raise ReconciliationError("套餐编码未在中控种子中声明")
    nodes = _selected_seed_nodes(seed_dir)
    if not nodes:
        raise ReconciliationError("中控种子没有 module_control 权限")
    existing = (await db.execute(select(menu_table))).mappings().all()
    existing_links = set((await db.execute(select(link_table.c.package_id, link_table.c.menu_id))).all())
    resolved = {}
    additions = []
    conflicts = []
    for index, node in nodes.items():
        seed = node["values"]
        matches = [row for row in existing if _matches(row, seed)]
        if len(matches) > 1:
            conflicts.append(f"多条菜单匹配 {seed.get('permission') or seed.get('route_name')}")
            continue
        if matches:
            row = matches[0]
            if row["is_deleted"] or row["status"] != 0 or row["scope"] != seed.get("scope", "tenant") or row["type"] != seed.get("type", 2) or row["permission"] != seed.get("permission"):
                conflicts.append(f"菜单定义/状态冲突 id={row['id']}")
            resolved[index] = row["id"]
        else:
            # Negative IDs are plan references only; database sequences allocate
            # actual primary keys. Never reuse seed numeric IDs in existing DBs.
            resolved[index] = -index - 1
            additions.append(index)
    links = set()
    for package in packages:
        permissions = {entry.get("permission") for entry in declared[package["code"]] if str(entry.get("permission") or "").startswith("module_control:")}
        for permission in permissions:
            selected = [index for index, node in nodes.items() if node["values"].get("permission") == permission]
            if not selected:
                conflicts.append(f"套餐权限未找到 {permission}")
            for index in selected:
                for parent in (*nodes[index]["ancestors"], index):
                    values = nodes[parent]["values"]
                    if values.get("scope", "tenant") != "tenant":
                        conflicts.append(f"套餐不允许 platform 权限 {permission}")
                        continue
                    if parent in resolved and (package["id"], resolved[parent]) not in existing_links:
                        links.add((package["id"], parent))
    report = {
        "mode": "apply" if apply else "dry-run",
        "site_id": site_id,
        "package_codes": sorted(set(package_codes)),
        "created_menus": len(additions),
        "added_package_links": len(links),
        "conflicts": conflicts,
        "menus": [nodes[index]["values"].get("permission") or nodes[index]["values"].get("route_name") for index in additions],
        "roles_changed": 0,
    }
    if conflicts:
        if apply:
            raise ReconciliationError("菜单/套餐冲突，未写入：" + "; ".join(conflicts))
        return report
    if not apply:
        return report
    allowed_columns = set(menu_table.c.keys()) - {"id", "uuid", "created_time", "updated_time", "deleted_time", "is_deleted", "parent_id"}
    for index in additions:
        values = {key: value for key, value in nodes[index]["values"].items() if key in allowed_columns}
        ancestors = nodes[index]["ancestors"]
        values["parent_id"] = resolved[ancestors[-1]] if ancestors else None
        resolved[index] = (await db.execute(insert(menu_table).values(**values).returning(menu_table.c.id))).scalar_one()
    for package_id, index in sorted(links):
        await db.execute(insert(link_table).values(package_id=package_id, menu_id=resolved[index]))
    return report


async def _run(args):
    from app.core.database import async_db_session

    async with async_db_session() as db:
        async with db.begin():
            report = await reconcile_control_seed(db, site_id=args.site_id, package_codes=args.package_codes, apply=args.apply)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report["conflicts"] else 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except ReconciliationError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
