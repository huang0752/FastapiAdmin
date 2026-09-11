"""Control product routes."""

from fastapi import APIRouter

from .application_package.controller import ApplicationPackageRouter
from .controller import ApplicationRouter, PortalRouter, SSORouter, TenantApplicationRouter
from .tenant_provision.controller import TenantProvisionCreateRouter, TenantProvisionRouter
from .tenant_provision.exchange_controller import ProvisioningExchangeRouter
from .user_entitlement.controller import ControlUserRouter, UserEntitlementExchangeRouter

control_router = APIRouter(prefix="/control")
control_router.include_router(ApplicationRouter)
control_router.include_router(ApplicationPackageRouter)
control_router.include_router(TenantApplicationRouter)
control_router.include_router(PortalRouter)
control_router.include_router(SSORouter)
control_router.include_router(ProvisioningExchangeRouter)
control_router.include_router(UserEntitlementExchangeRouter)
control_router.include_router(ControlUserRouter)
control_router.include_router(TenantProvisionCreateRouter)
control_router.include_router(TenantProvisionRouter)

__all__ = ["control_router"]


def build_control_router(assembly):
    """Build the effective API surface from the same switches exposed to the UI."""
    router = APIRouter()
    router.include_router(control_router)
    entitlements = assembly.is_feature_enabled("control_user_entitlements", default=False)
    portal = assembly.is_feature_enabled("app_portal", default=False)
    provisioning = assembly.is_feature_enabled("tenant_auto_provisioning", default=False)
    router.routes = [
        route for route in router.routes
        if not (
            (not entitlements and (route.path.startswith(("/control/users", "/control/user-entitlements")) or "/grants" in route.path))
            or (not portal and route.path.startswith("/control/portal"))
            or (not provisioning and route.path.startswith(("/control/tenants", "/control/tenant-provisions", "/control/provisioning")))
        )
    ]
    return router
