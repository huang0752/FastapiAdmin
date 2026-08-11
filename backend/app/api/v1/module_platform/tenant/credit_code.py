USCC_ALPHABET = "0123456789ABCDEFGHJKLMNPQRTUWXY"
USCC_WEIGHTS = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)


def normalize_unified_social_credit_code(value: str | None) -> str | None:
    """去除空白并统一社会信用代码大小写；空字符串按未填写处理。"""
    normalized = value.strip().upper() if value else ""
    return normalized or None


def validate_unified_social_credit_code(value: str | None) -> str | None:
    """规范化并校验 18 位统一社会信用代码。"""
    normalized = normalize_unified_social_credit_code(value)
    if normalized is None:
        return None
    if len(normalized) != 18 or any(char not in USCC_ALPHABET for char in normalized):
        raise ValueError("统一社会信用代码格式不正确")
    total = sum(
        USCC_ALPHABET.index(char) * weight
        for char, weight in zip(normalized[:17], USCC_WEIGHTS, strict=True)
    )
    expected = USCC_ALPHABET[(31 - total % 31) % 31]
    if normalized[-1] != expected:
        raise ValueError("统一社会信用代码校验位不正确")
    return normalized
