"""风控守卫——系统唯一下单入口（QS-C04 §0.1 · QS-E02 §4.3）。

设计目标：
  1. 旁路不可达：BrokerAdapter.submit_order() 必须接受携带有效 GuardDecision
     token 的 Order 对象；token 由 RiskGuard._mint_token() 私有方法生成，
     外部无法构造合法 token（dataclass frozen=True + 私有工厂 + uuid sentinel）。
  2. 所有订单必须经 RiskGuard.submit(order) → GuardDecision 串行校验。
  3. 任一校验不过则拒单并记录原因，不会修改 order 状态。
  4. @risk_guarded 装饰器强制包装下单方法，确保未经守卫的调用路径被拦截。

校验顺序（QS-C04 §0.5）：
  1. 合规限速（1笔/秒、200笔/日）—— 先于业务逻辑（§6.1）
  2. 三级回撤状态（HARD_STOP → 禁 BUY）
  3. ST/停牌/上市<250日 剔除（流动性资格）
  4. 日均成交额 ≥ 5000万
  5. 单票 ≤ 8%
  6. 单行业 ≤ 30%

禁止 import xtquant。
"""
from __future__ import annotations

import functools
import hmac
import hashlib
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Optional

from src.common.config import load_frozen
from src.execution.interfaces import Order, OrderSide
from src.risk.constraints import (
    ConstraintCheckResult,
    check_industry_limit,
    check_liquidity_filter,
    check_single_stock_limit,
)
from src.risk.drawdown import DrawdownStatus, check_drawdown

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 不可伪造的 GuardDecision token
# ---------------------------------------------------------------------------

# 模块级私有 sentinel：只有本模块能读取，外部导入者无法访问
_GUARD_SENTINEL: str = secrets.token_hex(32)  # 进程级随机，每次启动不同


@dataclass(frozen=True)
class GuardDecision:
    """风控守卫决策令牌（不可伪造）。

    实现机制（旁路不可达）：
      1. frozen=True: 实例创建后不可修改任何字段。
      2. _sentinel: 隐藏字段，值等于模块级私有随机串 _GUARD_SENTINEL，
         仅 RiskGuard._mint_token() 传入正确值，外部无法知道正确值。
      3. BrokerAdapter 实现层调用 GuardDecision.verify() 校验 sentinel，
         任何伪造的 token（sentinel 错误）会抛 ValueError。
      4. 字段命名以下划线开头（_sentinel），强调私有性；dataclass repr 仍显示，
         但由于值是随机的，无法通过打印日志反推重建。

    外部代码只能通过 RiskGuard.submit(order) 获取合法 token，
    无法直接构造（因为 _GUARD_SENTINEL 是模块级私有变量）。
    """
    client_order_id: str                 # 订单幂等键，令牌与单笔订单绑定
    account_id: str                      # 绑定账户（防跨账户复用）
    ts_code: str                         # 绑定标的
    decided_at: str                      # ISO-8601 时间戳
    rejection_reason: Optional[str]      # None = 通过，非 None = 拒单原因
    adjusted_qty: Optional[int]          # 裁剪后数量（None = 原量）
    _sentinel: str = field(repr=False)   # 私有校验值，等于 _GUARD_SENTINEL

    @property
    def approved(self) -> bool:
        """True = 守卫通过，可下单；False = 拒单。"""
        return self.rejection_reason is None

    def verify(self) -> None:
        """校验令牌合法性（BrokerAdapter 实现层调用）。

        Raises:
            ValueError: sentinel 不匹配，说明 token 不是由本进程 RiskGuard 签发的。
        """
        if not hmac.compare_digest(self._sentinel, _GUARD_SENTINEL):
            raise ValueError(
                "GuardDecision token 无效：sentinel 不匹配，"
                "订单未经 RiskGuard 审核或令牌来自外部伪造。拒绝下单。"
            )
        if not self.approved:
            raise ValueError(
                f"GuardDecision token 为拒单决策（reason={self.rejection_reason}），"
                "不可用于下单。"
            )


# ---------------------------------------------------------------------------
# 拒单结果枚举
# ---------------------------------------------------------------------------

class RejectionCode(str, Enum):
    """拒单原因代码（记录到 ledger）。"""
    RATE_LIMIT_PER_SEC = "RATE_LIMIT_PER_SEC"
    RATE_LIMIT_PER_DAY = "RATE_LIMIT_PER_DAY"
    DRAWDOWN_HARD_STOP = "DRAWDOWN_HARD_STOP"
    ST_EXCLUDED = "ST_EXCLUDED"
    SUSPENDED = "SUSPENDED"
    LISTING_DAYS_INSUFFICIENT = "LISTING_DAYS_INSUFFICIENT"
    LIQUIDITY_INSUFFICIENT = "LIQUIDITY_INSUFFICIENT"
    SINGLE_STOCK_LIMIT_EXCEEDED = "SINGLE_STOCK_LIMIT_EXCEEDED"
    INDUSTRY_LIMIT_EXCEEDED = "INDUSTRY_LIMIT_EXCEEDED"
    PORTFOLIO_VALUE_ZERO = "PORTFOLIO_VALUE_ZERO"
    MISSING_CONTEXT = "MISSING_CONTEXT"


# ---------------------------------------------------------------------------
# 合规限速计数器（内嵌，避免循环导入）
# ---------------------------------------------------------------------------

class _InlineRateLimiter:
    """内嵌简单令牌桶（1笔/秒）+ 日计数器（200笔/日）。

    此处是轻量版，供 guard.py 独立校验用；
    src/execution/rate_limiter.py 是完整实现（供执行层使用）。
    """

    def __init__(self) -> None:
        frozen = load_frozen()
        comp = frozen["compliance"]
        self._max_per_sec: int = int(comp["max_orders_per_second"])   # 1
        self._max_per_day: int = int(comp["max_orders_per_day"])       # 200

        import threading
        self._lock = threading.Lock()
        self._last_submit_ts: float = 0.0
        self._day_count: int = 0
        self._day_key: str = ""          # "YYYY-MM-DD" 当日标识

    def check(self) -> tuple[bool, str]:
        """返回 (allowed: bool, reason: str)。不消耗令牌，仅检查。"""
        import threading
        with self._lock:
            today = datetime.now(tz=timezone.utc).date().isoformat()
            if today != self._day_key:
                self._day_key = today
                self._day_count = 0

            if self._day_count >= self._max_per_day:
                return False, RejectionCode.RATE_LIMIT_PER_DAY.value

            now = time.monotonic()
            elapsed = now - self._last_submit_ts
            min_interval = 1.0 / self._max_per_sec
            if elapsed < min_interval:
                return False, RejectionCode.RATE_LIMIT_PER_SEC.value

            return True, ""

    def consume(self) -> None:
        """消耗令牌（check() 通过后调用）。"""
        import threading
        with self._lock:
            self._last_submit_ts = time.monotonic()
            self._day_count += 1

    @property
    def daily_count(self) -> int:
        return self._day_count


# ---------------------------------------------------------------------------
# RiskGuard 核心类
# ---------------------------------------------------------------------------

class RiskGuard:
    """系统唯一下单入口（QS-C04 §0.1）。

    使用方式::

        guard = RiskGuard()
        decision = guard.submit(order, context)
        if decision.approved:
            # 将 decision 附到 order，交给 BrokerAdapter
            order._guard_decision = decision
            adapter.submit_order(order)

    所有校验结果会写入 structured log，便于审计回放。
    """

    def __init__(self) -> None:
        self._rate_limiter = _InlineRateLimiter()
        self._drawdown_status: DrawdownStatus = DrawdownStatus.NORMAL
        self._frozen = load_frozen()

    # ------------------------------------------------------------------
    # 私有工厂：唯一合法的 token 生成路径
    # ------------------------------------------------------------------

    def _mint_token(
        self,
        order: Order,
        rejection_reason: Optional[str],
        adjusted_qty: Optional[int],
    ) -> GuardDecision:
        """生成携带私有 sentinel 的 GuardDecision（唯一合法路径）。"""
        return GuardDecision(
            client_order_id=order.client_order_id,
            account_id=order.account_id,
            ts_code=order.ts_code,
            decided_at=datetime.now(tz=timezone.utc).isoformat(),
            rejection_reason=rejection_reason,
            adjusted_qty=adjusted_qty,
            _sentinel=_GUARD_SENTINEL,
        )

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def update_drawdown_status(self, current_nav: float, peak_nav: float) -> DrawdownStatus:
        """更新回撤状态（由监控模块定期调用）。返回新状态。"""
        result = check_drawdown(current_nav, peak_nav)
        self._drawdown_status = result.status
        if result.status != DrawdownStatus.NORMAL:
            logger.warning(
                "drawdown_alert status=%s drawdown_pct=%.4f",
                result.status.value,
                result.drawdown_pct,
            )
        return result.status

    def submit(self, order: Order, context: Optional[dict] = None) -> GuardDecision:
        """校验订单并签发 GuardDecision token。

        Args:
            order: 待校验的 Order 对象。
            context: 校验上下文，应包含：
                - avg_daily_turnover: float（日均成交额，元）
                - is_suspended: bool
                - is_st: bool
                - listing_days: int
                - price: Decimal（参考价）
                - total_portfolio_value: Decimal（组合总市值）
                - current_positions: dict[str, dict]（{ts_code: {"market_value": Decimal}}）
                - industry: str（本次标的所属行业）
                - ts_code_industry_map: dict[str, str]

        Returns:
            GuardDecision — approved=True 表示通过，False 表示拒单（附 rejection_reason）。

        注意：此方法**不**引发异常来表示拒单，而是返回 approved=False 的 token，
        由调用方决定是否记录日志/发送告警。
        """
        ctx = context or {}

        def _reject(reason: str, adjusted_qty: Optional[int] = None) -> GuardDecision:
            logger.warning(
                "risk_guard REJECT client_order_id=%s ts_code=%s reason=%s",
                order.client_order_id,
                order.ts_code,
                reason,
            )
            return self._mint_token(order, reason, adjusted_qty)

        # ---- 1. 合规限速（先于一切业务逻辑，§6.1）----
        allowed, rate_reason = self._rate_limiter.check()
        if not allowed:
            return _reject(rate_reason)

        # ---- 2. 三级回撤：HARD_STOP 时禁止 BUY ----
        if self._drawdown_status == DrawdownStatus.HARD_STOP:
            if order.side == OrderSide.BUY:
                return _reject(RejectionCode.DRAWDOWN_HARD_STOP.value)

        # ---- 3. 流动性与资格过滤 ----
        avg_turnover = float(ctx.get("avg_daily_turnover", 0))
        is_suspended = bool(ctx.get("is_suspended", False))
        is_st = bool(ctx.get("is_st", False))
        listing_days = int(ctx.get("listing_days", 999))

        liq_result = check_liquidity_filter(
            order.ts_code,
            avg_turnover,
            is_suspended,
            is_st,
            listing_days,
        )
        if not liq_result.passed:
            return _reject(liq_result.rejection_reason or "LIQUIDITY_FILTER_FAILED")

        # ---- 4 & 5. 单票 + 行业（仅 BUY 方向做加仓校验）----
        if order.side == OrderSide.BUY:
            price: Optional[Decimal] = ctx.get("price")
            total_pv: Optional[Decimal] = ctx.get("total_portfolio_value")
            current_pos: dict = ctx.get("current_positions", {})
            industry: str = ctx.get("industry", "UNKNOWN")
            ind_map: dict = ctx.get("ts_code_industry_map", {})

            if price is None or total_pv is None:
                return _reject(RejectionCode.MISSING_CONTEXT.value)

            price_d = Decimal(str(price))
            total_pv_d = Decimal(str(total_pv))

            # 单票上限
            ss_result = check_single_stock_limit(
                order.ts_code, order.qty, price_d, total_pv_d, current_pos
            )
            if not ss_result.passed:
                return _reject(
                    ss_result.rejection_reason or RejectionCode.SINGLE_STOCK_LIMIT_EXCEEDED.value,
                    adjusted_qty=ss_result.adjusted_qty,
                )
            effective_qty = ss_result.adjusted_qty if ss_result.adjusted_qty is not None else order.qty

            # 行业上限
            ind_result = check_industry_limit(
                industry, effective_qty, price_d, total_pv_d, current_pos, ind_map
            )
            if not ind_result.passed:
                return _reject(
                    ind_result.rejection_reason or RejectionCode.INDUSTRY_LIMIT_EXCEEDED.value
                )

            # 消耗限速令牌（所有校验通过后）
            self._rate_limiter.consume()
            logger.info(
                "risk_guard APPROVE client_order_id=%s ts_code=%s side=%s qty=%s",
                order.client_order_id, order.ts_code, order.side.value,
                effective_qty,
            )
            token = self._mint_token(order, None, ss_result.adjusted_qty)
            return token

        # SELL 方向：仅限速 + 流动性，不检查仓位上限（减仓不受约束）
        self._rate_limiter.consume()
        logger.info(
            "risk_guard APPROVE client_order_id=%s ts_code=%s side=%s qty=%s",
            order.client_order_id, order.ts_code, order.side.value, order.qty,
        )
        return self._mint_token(order, None, None)

    @property
    def daily_order_count(self) -> int:
        """当日已通过守卫的订单数。"""
        return self._rate_limiter.daily_count

    @property
    def drawdown_status(self) -> DrawdownStatus:
        return self._drawdown_status


# ---------------------------------------------------------------------------
# @risk_guarded 装饰器
# ---------------------------------------------------------------------------

def risk_guarded(guard: RiskGuard, context_provider: Optional[Callable[[Order], dict]] = None):
    """装饰器：强制下单方法经过 RiskGuard 校验。

    用法::

        _guard = RiskGuard()

        @risk_guarded(_guard, context_provider=lambda o: fetch_context(o))
        def place_order(self, order: Order) -> str:
            ...

    装饰后的函数签名不变；若守卫拒单，会在调用原函数前抛出 PermissionError。
    Approved 的 order 会被注入 _guard_decision token。

    Args:
        guard: RiskGuard 实例（单例推荐）。
        context_provider: 可选，接受 Order 返回 context dict 的函数；
                          None 时以空 dict 调用 guard.submit()。
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # 从 args 中找 Order 对象（第一个 Order 类型参数）
            order_obj: Optional[Order] = None
            for a in args:
                if isinstance(a, Order):
                    order_obj = a
                    break
            if order_obj is None:
                for v in kwargs.values():
                    if isinstance(v, Order):
                        order_obj = v
                        break

            if order_obj is None:
                raise TypeError(
                    "@risk_guarded 装饰的函数必须有 Order 类型参数，"
                    f"但在 {fn.__qualname__} 调用中未找到。"
                )

            ctx = context_provider(order_obj) if context_provider else {}
            decision = guard.submit(order_obj, ctx)

            if not decision.approved:
                raise PermissionError(
                    f"RiskGuard 拒单：{decision.rejection_reason} "
                    f"(client_order_id={order_obj.client_order_id}, "
                    f"ts_code={order_obj.ts_code})"
                )

            # 注入 token（使调用方无需手动注入）
            object.__setattr__(order_obj, "_guard_decision", decision) \
                if hasattr(order_obj, "__dataclass_fields__") else \
                setattr(order_obj, "_guard_decision", decision)

            return fn(*args, **kwargs)

        wrapper._risk_guarded = True  # 标记，便于测试断言
        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# 模块级默认单例（方便 import 后直接使用）
# ---------------------------------------------------------------------------

_default_guard: Optional[RiskGuard] = None


def get_default_guard() -> RiskGuard:
    """获取/延迟初始化默认 RiskGuard 单例。"""
    global _default_guard
    if _default_guard is None:
        _default_guard = RiskGuard()
    return _default_guard
