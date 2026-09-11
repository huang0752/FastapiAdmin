from app.core.assembly import get_assembly

SYSTEM_MANAGED_ROLE_CODES = frozenset({"owner", "admin", "member", "CONTROL_PORTAL_USER"})
GOVERNANCE_ROLE_CODES = frozenset({"owner", "admin", "member", "SUPER_ADMIN", "ADMIN", "CONTROL_PORTAL_USER"})
USER_ROLE_ASSIGNMENT_PROTECTED_CODES = GOVERNANCE_ROLE_CODES


def system_managed_role_codes() -> frozenset[str]:
    policy = get_assembly().federation_default_role
    return SYSTEM_MANAGED_ROLE_CODES | ({policy.code} if policy.mode == "declared" else set())


def user_assignment_protected_codes() -> frozenset[str]:
    return USER_ROLE_ASSIGNMENT_PROTECTED_CODES | system_managed_role_codes()
