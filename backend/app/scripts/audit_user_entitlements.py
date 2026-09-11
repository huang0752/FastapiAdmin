"""Read-only reconciliation of Control and product entitlement exports."""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

Key = tuple[str, str, str]
VALID_PRINCIPAL_ROLES = {"owner", "admin", "member"}
KNOWN_ADMIN_ROLE_CODES = {
    "ADMIN",
    "OWNER",
    "PLATFORM_GLOBAL",
    "PLATFORM_ADMIN",
    "SUPER_ADMIN",
    "SYSTEM_ADMIN",
    "TENANT_ADMIN",
    "TEST_ADMIN",
}
PLATFORM_GLOBAL_ROLE_CODES = {
    "PLATFORM_ADMIN",
    "PLATFORM_GLOBAL",
    "SUPER_ADMIN",
    "SYSTEM_ADMIN",
}


@dataclass(frozen=True, slots=True)
class AuditIssue:
    category: str
    key: Key
    detail: str


@dataclass(frozen=True, slots=True)
class AuditResult:
    issues: list[AuditIssue]
    awaiting_authorization: list[Key] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return 1 if self.issues else 0

    def export(self) -> dict:
        return {
            "ok": not self.issues,
            "issue_count": len(self.issues),
            "awaiting_authorization_count": len(self.awaiting_authorization),
            "access_ready": not self.issues and not self.awaiting_authorization,
            "issues": [
                {
                    "category": issue.category,
                    "application_code": issue.key[0],
                    "tenant_code": issue.key[1],
                    "central_user_uuid": issue.key[2],
                    "detail": issue.detail,
                }
                for issue in self.issues
            ],
        }


def _records(payload: object) -> list[dict]:
    if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
        return payload
    if isinstance(payload, dict):
        for field in ("entitlements", "records", "data"):
            value = payload.get(field)
            if isinstance(value, list) and all(isinstance(item, dict) for item in value):
                return value
    raise ValueError("导出文件必须是记录数组或包含 entitlements/records/data 数组的对象")


def _key(record: Mapping[str, object], *, application_code: str | None = None) -> Key:
    record_application = record.get("application_code")
    if application_code is not None and record_application is not None and record_application != application_code:
        raise ValueError("产品导出 application_code 与文件 product 不一致")
    values = (
        application_code or record_application,
        record.get("tenant_code"),
        record.get("central_user_uuid"),
    )
    if not all(isinstance(value, str) and value for value in values):
        raise ValueError("对账记录缺少 application_code/tenant_code/central_user_uuid")
    return values  # type: ignore[return-value]


def audit_exports(
    control_records: Iterable[Mapping[str, object]],
    product_exports: Mapping[str, Iterable[Mapping[str, object]]],
) -> AuditResult:
    issues: list[AuditIssue] = []
    awaiting_authorization: list[Key] = []
    products: dict[Key, list[Mapping[str, object]]] = {}
    for application_code, records in product_exports.items():
        for record in records:
            key = _key(record, application_code=application_code)
            products.setdefault(key, []).append(record)

    controls: dict[Key, list[Mapping[str, object]]] = {}
    for record in control_records:
        controls.setdefault(_key(record), []).append(record)

    for key, records in controls.items():
        if len(records) > 1:
            issues.append(AuditIssue("duplicate", key, "Control 存在重复授权记录"))
        control = records[0]
        if control.get("sync_status") != "succeeded":
            issues.append(
                AuditIssue(
                    "sync_status_drift",
                    key,
                    "Control 授权同步尚未成功",
                )
            )
        matches = products.get(key, [])
        if not matches:
            issues.append(AuditIssue("missing", key, "产品缺少授权记录"))
            continue
        if len(matches) > 1:
            issues.append(AuditIssue("duplicate", key, "产品存在重复 entitlement"))
        product = matches[0]
        expected_active = control.get("desired_state") == "active"
        if product.get("active") is not expected_active:
            issues.append(AuditIssue("state_drift", key, "产品 active 与 Control 期望状态不一致"))
        if product.get("applied_version") != control.get("sync_version"):
            issues.append(AuditIssue("version_drift", key, "产品 applied_version 与 Control 版本不一致"))
        roles = product.get("role_codes")
        leaked = product.get("admin_permission_count")
        normalized_roles = {str(role).strip().upper() for role in roles} if isinstance(roles, list) else set()
        central_role = control.get("central_membership_role")
        principal_role = product.get("principal_role")
        if central_role not in VALID_PRINCIPAL_ROLES:
            issues.append(AuditIssue("principal_role_drift", key, "Control 中央成员身份无效"))
        if control.get("central_membership_role") is not None and control.get("principal_role") is not None and control.get("central_membership_role") != control.get("principal_role"):
            issues.append(AuditIssue("principal_role_drift", key, "Control 成员身份字段不一致"))
        if principal_role not in VALID_PRINCIPAL_ROLES or principal_role != central_role:
            issues.append(AuditIssue("principal_role_drift", key, "产品 principal_role 与中央成员身份不一致"))
        admin_permissions = product.get("admin_permissions")
        admin_evidence_invalid = not isinstance(leaked, int) or leaked < 0 or not isinstance(admin_permissions, list)
        management_roles = normalized_roles & KNOWN_ADMIN_ROLE_CODES
        is_superuser = product.get("is_superuser")
        platform_global_role_count = product.get("platform_global_role_count")
        platform_global_role_codes = product.get("platform_global_role_codes")
        if admin_evidence_invalid:
            issues.append(AuditIssue("admin_permission_leak", key, "产品管理权限证据缺失或无效"))
        if is_superuser is not False:
            issues.append(AuditIssue("admin_permission_leak", key, "产品用户暴露平台超级用户身份"))
        platform_evidence_invalid = (
            not isinstance(platform_global_role_count, int)
            or platform_global_role_count < 0
            or not isinstance(platform_global_role_codes, list)
            or platform_global_role_count != len(platform_global_role_codes)
            or not all(isinstance(role, str) and bool(role.strip()) for role in platform_global_role_codes)
        )
        if platform_evidence_invalid or bool(platform_global_role_count) or bool(platform_global_role_codes):
            issues.append(
                AuditIssue(
                    "admin_permission_leak",
                    key,
                    "产品平台全局角色证据无效或非空",
                )
            )
        if management_roles & PLATFORM_GLOBAL_ROLE_CODES:
            issues.append(AuditIssue("admin_permission_leak", key, "租户用户暴露平台级角色"))
        if central_role == "member" and (principal_role in {"owner", "admin"} or bool(management_roles) or bool(leaked) or bool(admin_permissions)):
            issues.append(AuditIssue("admin_permission_leak", key, "普通成员暴露管理权限"))
        elif central_role == "admin" and "OWNER" in management_roles:
            issues.append(AuditIssue("admin_permission_leak", key, "租户管理员越权持有 owner 角色"))
        if not expected_active:
            continue
        safely_waiting = (
            product.get("default_role_mode") == "manual"
            and product.get("access_state") == "awaiting_role"
            and central_role == principal_role == "member"
            and roles == []
            and product.get("active") is True
            and all(
                type(product.get(name)) is int and product[name] == 0
                for name in ("user_role_effective_menu_count", "effective_business_menu_count", "admin_permission_count", "platform_global_role_count")
            )
            and admin_permissions == []
            and platform_global_role_codes == []
            and is_superuser is False
        )
        if safely_waiting:
            awaiting_authorization.append(key)
            continue
        if not isinstance(roles, list) or not roles:
            issues.append(AuditIssue("missing_role", key, "active entitlement 未绑定任何角色"))
            continue
        has_user_role = "USER" in normalized_roles
        user_menu_count = product.get("user_role_effective_menu_count")
        if has_user_role and (not isinstance(user_menu_count, int) or user_menu_count < 1):
            issues.append(AuditIssue("user_menu_missing", key, "USER 没有有效业务菜单"))
        effective_business_menu_count = product.get("effective_business_menu_count")
        if central_role in {"owner", "admin"}:
            required_role = central_role.upper()
            if required_role not in normalized_roles or not isinstance(leaked, int) or leaked < 1 or not isinstance(admin_permissions, list) or not admin_permissions:
                issues.append(
                    AuditIssue(
                        "governance_access_drift",
                        key,
                        f"{central_role} 缺少对应治理角色或有效治理权限",
                    )
                )
        if central_role == "member" and not has_user_role and (not isinstance(effective_business_menu_count, int) or effective_business_menu_count < 1):
            issues.append(AuditIssue("user_menu_missing", key, "人工业务角色没有有效业务菜单"))

    for key in products.keys() - controls.keys():
        issues.append(AuditIssue("orphan", key, "产品存在 Control 未声明的授权记录"))
    issue_keys = {issue.key for issue in issues}
    return AuditResult(issues=issues, awaiting_authorization=[key for key in awaiting_authorization if key not in issue_keys])


def _load(path: Path) -> list[dict]:
    return _records(json.loads(path.read_text(encoding="utf-8")))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="只读对账 Control 与产品的用户授权")
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument(
        "--product",
        action="append",
        default=[],
        metavar="CODE=PATH",
        help="产品代码及只读导出文件，可重复指定",
    )
    parser.add_argument(
        "--required-product",
        action="append",
        default=[],
        metavar="CODE",
        help="额外必须提供导出的产品代码，可重复指定；Control 账本为空时必须显式指定对账范围",
    )
    parser.add_argument("--fail-on-drift", action="store_true")
    return parser


def _product_paths(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        code, separator, raw_path = value.partition("=")
        if not separator or not code.strip() or code != code.strip() or not raw_path.strip() or code in result:
            raise ValueError("--product 必须使用唯一的 CODE=PATH 格式")
        result[code] = Path(raw_path)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        control_records = _load(args.control)
        product_paths = _product_paths(args.product)
        required_products = {_key(record)[0] for record in control_records}
        if any(not code.strip() or code != code.strip() for code in args.required_product):
            raise ValueError("--required-product 必须是非空产品代码且不能包含首尾空格")
        required_products.update(args.required_product)
        if not required_products:
            raise ValueError("Control 账本为空，请使用 --required-product 显式指定对账产品范围")
        missing = required_products - product_paths.keys()
        if missing:
            raise ValueError(f"缺少必须对账的产品导出: {','.join(sorted(missing))}")
        products = {code: _load(path) for code, path in product_paths.items()}
        result = audit_exports(control_records, products)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result.export(), ensure_ascii=False, sort_keys=True))
    return result.exit_code if args.fail_on_drift else 0


if __name__ == "__main__":
    raise SystemExit(main())
