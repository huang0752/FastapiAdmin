"""业务任务状态机与进度约束。"""

from __future__ import annotations

BUSINESS_TASK_STATUSES = frozenset(
    {"pending", "enqueue_failed", "queued", "running", "retrying", "success", "failed", "canceled"}
)
TERMINAL_STATUSES = frozenset({"success", "failed", "canceled"})

_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"queued", "enqueue_failed", "canceled"}),
    "enqueue_failed": frozenset({"queued", "canceled"}),
    "queued": frozenset({"running", "canceled"}),
    "running": frozenset({"retrying", "success", "failed", "canceled"}),
    "retrying": frozenset({"queued", "running", "failed", "canceled"}),
    "success": frozenset(),
    "failed": frozenset(),
    "canceled": frozenset(),
}


class InvalidBusinessTaskTransition(ValueError):
    """业务任务状态转换不合法。"""


class InvalidBusinessTaskProgress(ValueError):
    """业务任务进度不合法。"""


def ensure_transition(current: str, target: str, *, allow_same: bool = True) -> str:
    if current not in BUSINESS_TASK_STATUSES or target not in BUSINESS_TASK_STATUSES:
        raise InvalidBusinessTaskTransition(f"未知业务任务状态: {current} -> {target}")
    if allow_same and current == target:
        return target
    if target not in _TRANSITIONS[current]:
        raise InvalidBusinessTaskTransition(f"不允许业务任务状态转换: {current} -> {target}")
    return target


def ensure_progress(current: int, target: int) -> int:
    if not 0 <= target <= 100:
        raise InvalidBusinessTaskProgress("业务任务进度必须在 0 到 100 之间")
    if target < current:
        raise InvalidBusinessTaskProgress("业务任务进度只能单调增加")
    return target
