import asyncio
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.v1.module_platform.site.model import SiteDomainModel, SiteModel
from app.api.v1.module_platform.tenant.model import TenantModel, TenantUserModel
from app.api.v1.module_system.user.login_identifier import sync_user_login_identifiers
from app.api.v1.module_system.user.model import UserLoginIdentifierModel, UserModel
from app.core.database import async_db_session
from app.utils.hash_bcrpy_util import PwdUtil


def _mobile(seed: str, offset: int = 0) -> str:
    return f"13{(int(seed, 16) + offset) % 1_000_000_000:09d}"


def _create_user(
    test_client: TestClient,
    auth_headers: dict[str, str],
    *,
    username: str,
    email: str,
    mobile: str,
    password: str = "Login123",
):
    return test_client.post(
        "/system/user/create",
        headers=auth_headers,
        json={
            "username": username,
            "password": password,
            "name": username,
            "email": email,
            "mobile": mobile,
        },
    )


def _login(test_client: TestClient, identifier: str, password: str = "Login123"):
    return test_client.post(
        "/system/auth/login",
        data={"username": identifier, "password": password, "login_type": "PC端"},
    )


async def _create_other_site_user(*, email: str, mobile: str) -> tuple[int, str]:
    suffix = uuid.uuid4().hex[:8]
    host = f"login-{suffix}.example.test"
    async with async_db_session() as db:
        site = SiteModel(code=f"login_{suffix}", name=f"登录站点{suffix}", status=0)
        site.domains = [SiteDomainModel(host=host, is_primary=True)]
        db.add(site)
        await db.flush()
        tenant = TenantModel(name=f"登录租户{suffix}", code=f"L{suffix.upper()}", site_id=site.id)
        db.add(tenant)
        await db.flush()
        user = UserModel(
            username=f"other_{suffix}",
            password=PwdUtil.hash_password("Login123"),
            name=f"other_{suffix}",
            email=email,
            mobile=mobile,
            tenant_id=tenant.id,
            status=0,
            is_superuser=False,
        )
        db.add(user)
        await db.flush()
        db.add(
            TenantUserModel(
                user_id=user.id,
                tenant_id=tenant.id,
                role="owner",
                is_default=1,
            )
        )
        await db.flush()
        await sync_user_login_identifiers(db, user)
        await db.commit()
        return tenant.id, host


def test_password_login_accepts_username_email_and_mobile(
    test_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    suffix = uuid.uuid4().hex[:8]
    username = f"login_{suffix}"
    email = f"LOGIN_{suffix}@Example.COM"
    mobile = _mobile(suffix)

    created = _create_user(
        test_client,
        auth_headers,
        username=username,
        email=email,
        mobile=mobile,
    )
    assert created.status_code == 200, created.text

    assert _login(test_client, username.upper()).status_code == 200
    assert _login(test_client, f"  {email.lower()}  ").status_code == 200
    assert _login(test_client, mobile).status_code == 200


def test_fresh_database_backfills_seed_login_identifiers(test_client: TestClient) -> None:
    async def _count_admin_identifiers() -> int:
        async with async_db_session() as db:
            count = await db.execute(
                select(func.count())
                .select_from(UserLoginIdentifierModel)
                .join(UserModel, UserModel.id == UserLoginIdentifierModel.user_id)
                .where(UserModel.username == "admin", UserLoginIdentifierModel.site_id == 1)
            )
            return int(count.scalar_one())

    assert asyncio.run(_count_admin_identifiers()) == 3


def test_same_site_rejects_duplicate_login_identifier(
    test_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    suffix = uuid.uuid4().hex[:8]
    shared_email = f"shared_{suffix}@example.com"

    first = _create_user(
        test_client,
        auth_headers,
        username=f"first_{suffix}",
        email=shared_email,
        mobile=_mobile(suffix),
    )
    assert first.status_code == 200, first.text

    second = _create_user(
        test_client,
        auth_headers,
        username=f"second_{suffix}",
        email=shared_email.upper(),
        mobile=_mobile(suffix, 1),
    )

    assert second.status_code == 409, second.text
    assert "登录标识已被占用" in second.text


def test_current_profile_change_replaces_email_and_mobile_identifiers(
    test_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    suffix = uuid.uuid4().hex[:8]
    username = f"profile_{suffix}"
    old_email = f"old_{suffix}@example.com"
    old_mobile = _mobile(suffix)
    new_email = f"new_{suffix}@example.com"
    new_mobile = _mobile(suffix, 1)
    created = _create_user(
        test_client,
        auth_headers,
        username=username,
        email=old_email,
        mobile=old_mobile,
    )
    assert created.status_code == 200, created.text
    logged_in = _login(test_client, username)
    assert logged_in.status_code == 200, logged_in.text
    user_headers = {"Authorization": f"Bearer {logged_in.json()['data']['access_token']}"}

    updated = test_client.put(
        "/system/user/current/info/update",
        headers=user_headers,
        json={"name": username, "email": new_email.upper(), "mobile": new_mobile},
    )
    assert updated.status_code == 200, updated.text

    assert _login(test_client, new_email.lower()).status_code == 200
    assert _login(test_client, new_mobile).status_code == 200
    assert _login(test_client, old_email).status_code != 200
    assert _login(test_client, old_mobile).status_code != 200


def test_unknown_identifier_and_wrong_password_return_the_same_public_error(
    test_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    suffix = uuid.uuid4().hex[:8]
    username = f"error_{suffix}"
    created = _create_user(
        test_client,
        auth_headers,
        username=username,
        email=f"error_{suffix}@example.com",
        mobile=_mobile(suffix),
    )
    assert created.status_code == 200, created.text

    unknown = _login(test_client, f"missing_{suffix}")
    wrong_password = _login(test_client, username, password="WrongPassword")

    assert unknown.status_code == 400
    assert wrong_password.status_code == 400
    assert unknown.json()["msg"] == "账号或密码错误"
    assert wrong_password.json()["msg"] == "账号或密码错误"


def test_identifier_can_repeat_across_sites_but_membership_collision_is_rejected(
    test_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    suffix = uuid.uuid4().hex[:8]
    shared_email = f"site_{suffix}@example.com"
    first = _create_user(
        test_client,
        auth_headers,
        username=f"home_{suffix}",
        email=shared_email,
        mobile=_mobile(suffix),
    )
    assert first.status_code == 200, first.text
    first_user_id = first.json()["data"]["id"]

    other_tenant_id, other_host = asyncio.run(
        _create_other_site_user(email=shared_email.upper(), mobile=_mobile(suffix, 1))
    )
    other_login = _login(test_client, shared_email.lower())
    assert other_login.status_code == 200, other_login.text
    other_site_login = test_client.post(
        "/system/auth/login",
        headers={"host": other_host},
        data={"username": shared_email.upper(), "password": "Login123", "login_type": "PC端"},
    )
    assert other_site_login.status_code == 200, other_site_login.text

    membership = test_client.post(
        f"/platform/tenant/{other_tenant_id}/users",
        headers=auth_headers,
        json={"user_id": first_user_id, "role": "member", "is_default": 0},
    )
    assert membership.status_code == 409, membership.text
    assert "登录标识已被占用" in membership.text
