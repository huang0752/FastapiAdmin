"""双品牌 Host/Site 静态契约；数据库仍是运行时权威来源。"""

from app.api.v1.module_platform.site.host import normalize_host

BRAND_SHORT_NAMES = {"data360": "华夏电投", "znceedi": "中能电投"}
PRODUCT_SUBDOMAINS = ("trace", "agri", "logistic")
PRODUCTION_HOSTS = {
    f"{product}.{domain}": site
    for site, domain in (("data360", "data360.org.cn"), ("znceedi", "znceedi.org.cn"))
    for product in PRODUCT_SUBDOMAINS
}
LOCALHOST_HOSTS = {
    f"{product}.{site}.localhost": site
    for site in BRAND_SHORT_NAMES
    for product in PRODUCT_SUBDOMAINS
}
HOST_TO_SITE = {**PRODUCTION_HOSTS, **LOCALHOST_HOSTS}


def resolve_site_code(raw_host: str) -> str:
    host = normalize_host(raw_host)
    try:
        return HOST_TO_SITE[host]
    except KeyError as exc:
        raise ValueError(f"请求 Host 未配置食品物流站点: {host}") from exc
