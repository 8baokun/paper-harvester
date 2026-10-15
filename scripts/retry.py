#!/usr/bin/env python3
"""HTTP 请求重试与多源降级工具。

解决审核报告 P1 问题：
- 指数退避重试（应对偶然性网络故障）
- 多源降级链（应对单源不可用）
- 区别技术（根据失败模式区别处理）
"""
import time, functools, logging
from config import MAX_RETRIES, BACKOFF_FACTOR, RETRY_STATUS

logger = logging.getLogger("paper-harvester")


def retry_with_backoff(max_retries: int = MAX_RETRIES,
                       backoff_factor: float = BACKOFF_FACTOR,
                       retryable_status: set = None):
    """指数退避重试装饰器。

    适用于 requests.Session 的 get/post 方法。
    重试条件：
    - 网络异常（ConnectionError, Timeout）
    - HTTP 状态码在 retryable_status 中（默认 429/500/502/503/504）

    不重试的情况：
    - 404（资源不存在，重试无意义）
    - 403（权限拒绝，重试无意义）
    - 200 但内容不符合预期（由调用方判断）
    """
    if retryable_status is None:
        retryable_status = RETRY_STATUS

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    resp = func(*args, **kwargs)
                    # 检查 HTTP 状态码
                    if hasattr(resp, "status_code"):
                        if resp.status_code in retryable_status and attempt < max_retries:
                            wait = backoff_factor * (2 ** attempt)
                            logger.warning(
                                f"HTTP {resp.status_code}, 第 {attempt+1} 次重试 "
                                f"(等待 {wait:.1f}s)")
                            time.sleep(wait)
                            continue
                    return resp
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries:
                        wait = backoff_factor * (2 ** attempt)
                        logger.warning(
                            f"请求异常: {e}, 第 {attempt+1} 次重试 "
                            f"(等待 {wait:.1f}s)")
                        time.sleep(wait)
                    else:
                        raise
            # 所有重试用完
            if last_exception:
                raise last_exception
            return resp  # 最后一次的响应
        return wrapper
    return decorator


def classify_error(exception_or_status) -> str:
    """分类错误类型，用于区别处理。

    Returns:
        "not_found"     → 404，资源不存在
        "forbidden"     → 403，权限拒绝
        "rate_limited"  → 429，被限流
        "server_error"  → 5xx，服务器错误
        "timeout"       → 超时
        "connection"    → 连接错误
        "not_pdf"       → 内容非 PDF
        "unknown"       → 未知错误
    """
    if isinstance(exception_or_status, int):
        status = exception_or_status
    elif hasattr(exception_or_status, "status_code"):
        status = exception_or_status.status_code
    elif isinstance(exception_or_status, Exception):
        msg = str(exception_or_status).lower()
        if "timeout" in msg or "timed out" in msg:
            return "timeout"
        elif "connection" in msg:
            return "connection"
        else:
            return "unknown"
    else:
        return "unknown"

    if status == 404:
        return "not_found"
    elif status == 403:
        return "forbidden"
    elif status == 429:
        return "rate_limited"
    elif 500 <= status < 600:
        return "server_error"
    else:
        return "unknown"


def should_retry(error_type: str) -> bool:
    """根据错误类型判断是否应该重试或换源。

    - not_found / forbidden → 不重试，换源
    - rate_limited / server_error / timeout / connection → 重试
    - not_pdf → 不重试，换源
    - unknown → 重试一次
    """
    return error_type in ("rate_limited", "server_error", "timeout",
                          "connection", "unknown")


def should_fallback(error_type: str) -> bool:
    """根据错误类型判断是否应该降级到下一个源。"""
    return error_type in ("not_found", "forbidden", "not_pdf")
