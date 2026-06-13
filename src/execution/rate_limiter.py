"""申报限速器（QS-C04 §6.1 硬约束）。

合规依据：沪深北《程序化交易管理实施细则》2025-07-07 施行。
高频认定线：300笔/秒 或 2万笔/日。
系统内部限额（远低于监管线）：
  - 1笔/秒（令牌桶）
  - 200笔/日（日计数器）

**硬约束**：不可绕过（break-glass 例外，其直接操作不经此限速器）。
检查时机：每次状态迁移中先于业务逻辑检查（§0.7 · §6.1）。

参数来源：frozen.toml [compliance] 段，运行时不可修改。
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from src.common.config import load_frozen

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------

class RateLimitExceeded(Exception):
    """超出限速时抛出（触发 MANUAL_REVIEW 暂停）。"""

    def __init__(self, reason: str, account_id: Optional[str] = None) -> None:
        msg = f"申报限速超限: {reason}"
        if account_id:
            msg += f" (account_id={account_id})"
        super().__init__(msg)
        self.reason = reason
        self.account_id = account_id


# ---------------------------------------------------------------------------
# 令牌桶（Token Bucket）实现
# ---------------------------------------------------------------------------

class TokenBucket:
    """令牌桶限速（线程安全）。

    每秒生成 rate 个令牌，最多积攒 capacity 个。
    acquire() 消耗一个令牌，无可用令牌时返回 False（不阻塞）。
    """

    def __init__(self, rate: float, capacity: int) -> None:
        """
        Args:
            rate: 每秒生成令牌数（如 1.0 = 1笔/秒）。
            capacity: 令牌桶最大容量（突发上限）。
        """
        self._rate = rate
        self._capacity = capacity
        self._tokens: float = float(capacity)
        self._last_refill: float = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        """按时间差补充令牌（调用方持锁）。"""
        now = time.monotonic()
        elapsed = now - self._last_refill
        added = elapsed * self._rate
        self._tokens = min(self._capacity, self._tokens + added)
        self._last_refill = now

    def acquire(self) -> bool:
        """尝试消耗一个令牌。

        Returns:
            True 表示成功获取令牌；False 表示令牌不足（超速）。
        """
        with self._lock:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True
            return False

    def available(self) -> float:
        """当前可用令牌数（仅用于监控，不保证原子性）。"""
        with self._lock:
            self._refill()
            return self._tokens


# ---------------------------------------------------------------------------
# 日计数器
# ---------------------------------------------------------------------------

class DailyCounter:
    """日申报笔数计数器（自动按自然日重置）。"""

    def __init__(self, max_per_day: int) -> None:
        self._max = max_per_day
        self._count: int = 0
        self._day_key: str = ""
        self._lock = threading.Lock()

    def _maybe_reset(self) -> None:
        """若日期变更则重置（调用方持锁）。"""
        today = datetime.now(tz=timezone.utc).date().isoformat()
        if today != self._day_key:
            self._day_key = today
            self._count = 0

    def check(self) -> bool:
        """检查今日计数是否未超限（不消耗计数）。"""
        with self._lock:
            self._maybe_reset()
            return self._count < self._max

    def increment(self) -> bool:
        """尝试增加计数。

        Returns:
            True 表示成功（未超限）；False 表示已到上限。
        """
        with self._lock:
            self._maybe_reset()
            if self._count >= self._max:
                return False
            self._count += 1
            return True

    @property
    def count(self) -> int:
        with self._lock:
            self._maybe_reset()
            return self._count

    @property
    def max_per_day(self) -> int:
        return self._max


# ---------------------------------------------------------------------------
# RateLimiter：令牌桶 + 日计数器组合
# ---------------------------------------------------------------------------

class RateLimiter:
    """申报限速器（QS-C04 §6.1 硬约束）。

    每账户独立实例；参数从 frozen.toml [compliance] 读取。

    行为：
      - 超秒速（令牌桶耗尽）→ 拒绝，抛 RateLimitExceeded（"PER_SECOND"）
      - 超日上限（DailyCounter 满）→ 拒绝，抛 RateLimitExceeded（"PER_DAY"）

    检查时机：在 RiskGuard.submit() 和 BrokerAdapter.submit_order() 中，
               先于所有业务逻辑调用。

    用法::

        limiter = RateLimiter()
        limiter.check_and_consume(account_id="your_account")
        # 通过后再发单
    """

    def __init__(
        self,
        max_per_sec: Optional[int] = None,
        max_per_day: Optional[int] = None,
    ) -> None:
        """
        Args:
            max_per_sec: 每秒最大申报笔数（None = 从 frozen.toml 读取）。
            max_per_day: 每日最大申报笔数（None = 从 frozen.toml 读取）。
        """
        if max_per_sec is None or max_per_day is None:
            frozen = load_frozen()
            comp = frozen["compliance"]
            max_per_sec = max_per_sec or int(comp["max_orders_per_second"])  # 1
            max_per_day = max_per_day or int(comp["max_orders_per_day"])      # 200

        self._max_per_sec = max_per_sec
        self._max_per_day = max_per_day

        # 令牌桶：rate = max_per_sec，capacity = max_per_sec（不允许突发积累）
        self._bucket = TokenBucket(rate=float(max_per_sec), capacity=max_per_sec)
        self._daily = DailyCounter(max_per_day)

    def check(self, account_id: Optional[str] = None) -> tuple[bool, str]:
        """纯检查（不消耗令牌/计数）。

        Returns:
            (allowed: bool, reason: str)  reason 为空时 allowed=True。
        """
        if not self._daily.check():
            return False, "PER_DAY"
        if self._bucket.available() < 1.0:
            return False, "PER_SECOND"
        return True, ""

    def check_and_consume(self, account_id: Optional[str] = None) -> None:
        """检查并消耗令牌（先于业务逻辑调用）。

        Raises:
            RateLimitExceeded: 超限时抛出。
        """
        # 先检查日上限（便宜）
        if not self._daily.check():
            logger.warning(
                "rate_limit_exceeded reason=PER_DAY count=%d max=%d account=%s",
                self._daily.count,
                self._daily.max_per_day,
                account_id,
            )
            raise RateLimitExceeded("PER_DAY", account_id)

        # 再尝试令牌桶
        if not self._bucket.acquire():
            logger.warning(
                "rate_limit_exceeded reason=PER_SECOND account=%s",
                account_id,
            )
            raise RateLimitExceeded("PER_SECOND", account_id)

        # 两者均通过，递增日计数
        self._daily.increment()
        logger.debug("rate_limit_ok account=%s daily_count=%d", account_id, self._daily.count)

    # 兼容旧接口（QS-E02 设计文档中使用 wait_if_needed）
    def wait_if_needed(self, account_id: Optional[str] = None) -> None:
        """别名：等同 check_and_consume（保持与文档签名兼容）。"""
        self.check_and_consume(account_id)

    @property
    def daily_count(self) -> int:
        """当日已申报笔数。"""
        return self._daily.count

    @property
    def max_per_day(self) -> int:
        return self._max_per_day

    @property
    def max_per_sec(self) -> int:
        return self._max_per_sec

    @property
    def available_tokens(self) -> float:
        """当前令牌桶可用令牌数（监控用）。"""
        return self._bucket.available()


# ---------------------------------------------------------------------------
# 每账户 RateLimiter 注册表（多账户场景）
# ---------------------------------------------------------------------------

class RateLimiterRegistry:
    """按 account_id 管理独立 RateLimiter 实例。"""

    def __init__(self) -> None:
        self._limiters: dict[str, RateLimiter] = {}
        self._lock = threading.Lock()

    def get(self, account_id: str) -> RateLimiter:
        """获取（或延迟创建）账户对应的 RateLimiter。"""
        with self._lock:
            if account_id not in self._limiters:
                self._limiters[account_id] = RateLimiter()
            return self._limiters[account_id]

    def check_and_consume(self, account_id: str) -> None:
        self.get(account_id).check_and_consume(account_id)
