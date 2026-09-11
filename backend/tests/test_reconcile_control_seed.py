"""Upgrade reconciliation must be additive, scoped and dry-run by default."""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import MetaData, func, insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1.module_platform.menu.model import MenuModel
from app.api.v1.module_platform.package.model import PackageMenuModel, PackageModel
from app.api.v1.module_platform.site.model import SiteModel


@pytest.fixture
async def seed_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    metadata = MetaData()
    for model in (SiteModel, MenuModel, PackageModel, PackageMenuModel):
        model.__table__.to_metadata(metadata)
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    async with async_sessionmaker(engine)() as db:
        for site_id in (1, 2):
            await db.execute(insert(SiteModel.__table__).values(id=site_id, code=f"site{site_id}", name=f"Site {site_id}"))
            await db.execute(insert(PackageModel.__table__).values(id=site_id, site_id=site_id, code="basic", name="Basic"))
        await db.commit()
        yield db
    await engine.dispose()


@pytest.fixture
def seed_dir(tmp_path):
    menus = [
        {
            "name": "Control",
            "type": 1,
            "route_name": "Control",
            "scope": "tenant",
            "children": [
                {
                    "name": "Portal",
                    "type": 2,
                    "permission": "module_control:portal:query",
                    "scope": "tenant",
                    "children": [
                        {"name": "Query", "type": 3, "permission": "module_control:portal:query", "scope": "tenant"},
                        {"name": "Launch", "type": 3, "permission": "module_control:portal:launch", "scope": "tenant"},
                    ],
                },
                {"name": "Management", "type": 2, "permission": "module_control:application:query", "scope": "platform"},
            ],
        },
        {"name": "Unrelated", "type": 2, "permission": "business:write", "scope": "tenant"},
    ]
    (tmp_path / "platform_menu.json").write_text(json.dumps(menus))
    (tmp_path / "platform_package_menu.json").write_text(
        json.dumps([{"package_code": "basic", "menus": [{"permission": "module_control:portal:query"}, {"permission": "module_control:portal:launch"}, {"permission": "business:write"}]}])
    )
    return tmp_path


PROVIDER = SimpleNamespace(is_feature_enabled=lambda feature, default=False: feature == "sso_provider")
STANDALONE = SimpleNamespace(is_feature_enabled=lambda feature, default=False: False)


@pytest.mark.asyncio
async def test_preview_then_apply_is_idempotent_and_site_scoped(seed_db, seed_dir):
    from app.scripts.reconcile_control_seed import reconcile_control_seed

    original = await seed_db.execute(insert(MenuModel.__table__).values(name="Custom", type=2, permission="business:custom", scope="tenant").returning(MenuModel.id))
    original_id = original.scalar_one()
    await seed_db.execute(insert(PackageMenuModel.__table__).values(package_id=1, menu_id=original_id))
    await seed_db.commit()
    kwargs = {"site_id": 1, "package_codes": ["basic"], "seed_dir": seed_dir, "assembly": PROVIDER}
    preview = await reconcile_control_seed(seed_db, **kwargs)
    assert preview["created_menus"] == 5
    assert preview["added_package_links"] == 4
    assert (await seed_db.scalar(select(func.count()).select_from(MenuModel))) == 1
    applied = await reconcile_control_seed(seed_db, apply=True, **kwargs)
    await seed_db.commit()
    assert applied["created_menus"] == 5
    repeated = await reconcile_control_seed(seed_db, apply=True, **kwargs)
    assert repeated["created_menus"] == repeated["added_package_links"] == 0
    assert (await seed_db.scalar(select(func.count()).select_from(PackageMenuModel).where(PackageMenuModel.package_id == 2))) == 0
    permissions = (await seed_db.execute(select(MenuModel.permission))).scalars().all()
    assert "business:write" not in permissions
    assert "business:custom" in permissions
    assert (await seed_db.scalar(select(func.count()).select_from(PackageMenuModel).where(PackageMenuModel.menu_id == original_id))) == 1


@pytest.mark.asyncio
async def test_existing_custom_menu_is_preserved(seed_db, seed_dir):
    from app.scripts.reconcile_control_seed import reconcile_control_seed

    menu_id = (
        await seed_db.execute(insert(MenuModel.__table__).values(name="My portal", type=2, permission="module_control:portal:query", scope="tenant", hidden=True).returning(MenuModel.id))
    ).scalar_one()
    await reconcile_control_seed(seed_db, site_id=1, package_codes=["basic"], seed_dir=seed_dir, assembly=PROVIDER, apply=True)
    row = (await seed_db.execute(select(MenuModel.__table__).where(MenuModel.id == menu_id))).mappings().one()
    assert row["name"] == "My portal" and row["hidden"] is True and row["parent_id"] is None


@pytest.mark.asyncio
async def test_apply_rejects_standalone_and_conflicting_deleted_permission(seed_db, seed_dir):
    from app.scripts.reconcile_control_seed import ReconciliationError, reconcile_control_seed

    kwargs = {"site_id": 1, "package_codes": ["basic"], "seed_dir": seed_dir}
    with pytest.raises(ReconciliationError, match="provider"):
        await reconcile_control_seed(seed_db, assembly=STANDALONE, apply=True, **kwargs)
    await seed_db.execute(insert(MenuModel.__table__).values(name="Deleted", type=2, permission="module_control:portal:query", scope="tenant", is_deleted=True))
    await seed_db.commit()
    with pytest.raises(ReconciliationError, match="冲突"):
        await reconcile_control_seed(seed_db, assembly=PROVIDER, apply=True, **kwargs)
    assert (await seed_db.scalar(select(func.count()).select_from(MenuModel))) == 1


def test_cli_requires_explicit_site_and_package_defaults_to_preview():
    from app.scripts.reconcile_control_seed import build_parser

    args = build_parser().parse_args(["--site-id", "1", "--package-code", "basic"])
    assert args.apply is False


@pytest.mark.asyncio
async def test_framework_control_seed_reconciles_without_food_or_role_grants(seed_db):
    from app.scripts.reconcile_control_seed import reconcile_control_seed

    report = await reconcile_control_seed(seed_db, site_id=1, package_codes=["basic"], assembly=PROVIDER, apply=True)
    assert report["conflicts"] == []
    assert report["created_menus"] > 0
    assert report["roles_changed"] == 0
    repeated = await reconcile_control_seed(seed_db, site_id=1, package_codes=["basic"], assembly=PROVIDER, apply=True)
    assert repeated["created_menus"] == repeated["added_package_links"] == 0
    granted = (await seed_db.execute(select(MenuModel.permission).join(PackageMenuModel, PackageMenuModel.menu_id == MenuModel.id))).scalars().all()
    assert "module_control:user_grant:retry" in granted
    assert "module_control:application:create" not in granted
    assert all(permission is None or permission.startswith("module_control:") for permission in granted)


@pytest.mark.asyncio
async def test_platform_permission_in_package_fails_without_partial_writes(seed_db, seed_dir):
    from app.scripts.reconcile_control_seed import ReconciliationError, reconcile_control_seed

    package_seed = json.loads((seed_dir / "platform_package_menu.json").read_text())
    package_seed[0]["menus"].append({"permission": "module_control:application:query"})
    (seed_dir / "platform_package_menu.json").write_text(json.dumps(package_seed))
    with pytest.raises(ReconciliationError, match="platform"):
        await reconcile_control_seed(seed_db, site_id=1, package_codes=["basic"], seed_dir=seed_dir, assembly=PROVIDER, apply=True)
    assert (await seed_db.scalar(select(func.count()).select_from(MenuModel))) == 0
    assert (await seed_db.scalar(select(func.count()).select_from(PackageMenuModel))) == 0
