"""业务任务运行时异常分类。"""


class BusinessTaskRuntimeError(RuntimeError):
    """业务任务运行时基础异常。"""


class RetryableBusinessTaskError(BusinessTaskRuntimeError):
    """处理器明确声明可有限重试的临时错误。"""

    error_code = "TEMPORARY_FAILURE"


class BusinessTaskCancelled(BusinessTaskRuntimeError):
    """任务在安全检查点响应协作取消。"""

    error_code = "TASK_CANCELED"


class InvalidBackgroundActorError(BusinessTaskRuntimeError):
    """后台 actor 已失效或权限不足。"""

    error_code = "ACTOR_INVALID"
