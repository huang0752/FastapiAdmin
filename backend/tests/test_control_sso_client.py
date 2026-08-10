from sqlalchemy import UniqueConstraint

from app.api.v1.module_system.auth.model import FederatedIdentityModel
from app.api.v1.module_system.user.model import UserModel
from app.api.v1.module_system.user.schema import UserOutSchema


def test_user_model_defaults_to_local_password_login() -> None:
    user = UserModel(username="local", password="hash", name="Local", tenant_id=1)

    assert user.auth_source == "local"
    assert user.password_login_enabled is True


def test_user_output_defaults_to_local_password_login() -> None:
    output = UserOutSchema()

    assert output.auth_source == "local"
    assert output.password_login_enabled is True


def test_federated_identity_has_stable_unique_keys() -> None:
    constraints = {
        tuple(sorted(column.name for column in constraint.columns))
        for constraint in FederatedIdentityModel.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("central_user_uuid", "issuer", "site_id") in constraints
    assert ("issuer", "local_user_id", "site_id") in constraints
