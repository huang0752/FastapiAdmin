from app.api.v1.module_system.user.schema import UserOutSchema, UserUpdateSchema


def test_superuser_flag_is_output_only():
    assert "is_superuser" not in UserUpdateSchema.model_fields
    assert "is_superuser" in UserOutSchema.model_fields
    assert UserOutSchema.model_construct(is_superuser=True).model_dump()["is_superuser"] is True
    assert UserOutSchema.model_construct().model_dump()["is_superuser"] is False
