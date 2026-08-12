import secrets
import string
from dataclasses import dataclass

_SUFFIX_ALPHABET = string.ascii_uppercase + string.digits


@dataclass(frozen=True, slots=True)
class UsageCertificateIdentity:
    number: str
    token: str


def build_usage_certificate_identity(tenant_code: str) -> UsageCertificateIdentity:
    suffix = "".join(secrets.choice(_SUFFIX_ALPHABET) for _ in range(6))
    return UsageCertificateIdentity(
        number=f"FA-SW-{tenant_code.upper()}-{suffix}",
        token=secrets.token_urlsafe(32),
    )
