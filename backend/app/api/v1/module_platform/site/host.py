from urllib.parse import urlsplit


def normalize_host(raw_host: str | None) -> str:
    """将 URL/Host 请求头规范化为不含端口的 ASCII 小写主机名。"""
    value = (raw_host or "").strip()
    if not value:
        raise ValueError("站点域名不能为空")
    parsed = urlsplit(value if "://" in value else f"//{value}")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("站点域名格式不正确")
    hostname = hostname.rstrip(".").lower()
    if not hostname or any(char.isspace() for char in hostname) or "*" in hostname:
        raise ValueError("站点域名格式不正确")
    try:
        return hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("站点域名格式不正确") from exc
