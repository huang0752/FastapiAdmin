from types import SimpleNamespace

import pytest
from fastapi import status

from app.api.v1.module_system.user import service as user_service_module
from app.api.v1.module_system.user.schema import (
    CurrentUserUpdateSchema,
    UserChangePasswordSchema,
    UserOutSchema,
)
from app.api.v1.module_system.user.service import UserService
from app.core.exceptions import CustomException


def test_current_user_update_normalizes_gender_and_keeps_description() -> None:
    payload = CurrentUserUpdateSchema.model_validate(
        {
            "name": "演示用户",
            "gender": 1,
            "description": "个人描述",
        }
    )

    assert payload.gender == "1"
    assert payload.description == "个人描述"


def test_user_output_never_serializes_password_hash() -> None:
    payload = UserOutSchema(password="$2b$12$secret")

    assert "password" not in payload.model_dump()


@pytest.mark.asyncio
async def test_superuser_can_update_own_whitelisted_profile_without_clearing_omitted_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(
        id=7,
        tenant_id=1,
        is_superuser=True,
        name="原姓名",
        mobile="13800000000",
        email="old@example.com",
        gender="1",
        avatar="https://example.com/old.png",
        description="原描述",
        password="$2b$12$secret",
    )
    captured: dict[str, object] = {}

    class FakeUserCRUD:
        def __init__(self, crud_auth: object) -> None:
            captured["crud_tenant_id"] = crud_auth.tenant_id

        async def get(self, **_kwargs: object) -> object:
            return user

        async def update(self, id: int, data: object) -> object:
            captured["id"] = id
            captured["data"] = data
            user.name = "新姓名"
            return user

    monkeypatch.setattr(user_service_module, "UserCRUD", FakeUserCRUD)
    auth = SimpleNamespace(user=user, db=None, tenant_id=2)

    result = await UserService(auth).update_current_info(CurrentUserUpdateSchema(name="新姓名"))

    update_data = captured["data"]
    assert update_data.model_dump(exclude_unset=True) == {"name": "新姓名"}
    assert captured["crud_tenant_id"] == 1
    assert result.name == "新姓名"
    assert user.email == "old@example.com"


@pytest.mark.asyncio
async def test_wrong_current_password_is_a_bad_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = SimpleNamespace(id=7, tenant_id=1, password="$2b$12$secret")
    captured: dict[str, object] = {}

    class FakeUserCRUD:
        def __init__(self, crud_auth: object) -> None:
            captured["crud_tenant_id"] = crud_auth.tenant_id

        async def get(self, **_kwargs: object) -> object:
            return user

    monkeypatch.setattr(user_service_module, "UserCRUD", FakeUserCRUD)
    monkeypatch.setattr(
        user_service_module.PwdUtil,
        "verify_password",
        lambda **_kwargs: False,
    )
    auth = SimpleNamespace(user=user, db=None, tenant_id=2)

    with pytest.raises(CustomException) as exc_info:
        await UserService(auth).change_password(UserChangePasswordSchema(old_password="old-pass", new_password="new-pass"))

    assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc_info.value.msg == "原密码输入错误"
    assert captured["crud_tenant_id"] == 1


@pytest.mark.asyncio
async def test_duplicate_current_email_is_a_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_user = SimpleNamespace(id=7, tenant_id=1, is_superuser=False)
    other_user = SimpleNamespace(id=8)

    class FakeUserCRUD:
        def __init__(self, _auth: object) -> None:
            pass

        async def get(self, **kwargs: object) -> object:
            if kwargs.get("email") == "used@example.com":
                return other_user
            return current_user

    monkeypatch.setattr(user_service_module, "UserCRUD", FakeUserCRUD)
    auth = SimpleNamespace(user=current_user, db=None, tenant_id=2)

    with pytest.raises(CustomException) as exc_info:
        await UserService(auth).update_current_info(CurrentUserUpdateSchema(email="used@example.com"))

    assert exc_info.value.status_code == status.HTTP_409_CONFLICT
    assert exc_info.value.msg == "该数据已存在"
