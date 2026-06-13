"""执行适配器抽象基类（QS-E02 §4 · 三层隔离）。

继承 BrokerAdapter（ABC），定义平台无关的模板方法。
禁止 import xtquant。

层次关系：
  interfaces.BrokerAdapter (ABC)
      └─ adapters/base.py: BaseExecutionAdapter  ← 本文件
              ├─ adapters/xtquant_adapter.py: XtquantAdapter
              └─ adapters/backtest_adapter.py: BacktestAdapter

模板方法模式：
  - submit_order() 包含风控令牌校验 + 限速检查的公共逻辑（_pre_submit_checks）
  - 子类实现 _do_submit_order() 处理平台差异
  - cancel_order / query_order / query_positions / query_trades 留给子类完全实现
"""
from __future__ import annotations

import logging
from abc import abstractmethod
from typing import Optional

from src.execution.interfaces import (
    BrokerAdapter,
    Fill,
    Order,
    OrderStatus,
    Position,
)
from src.execution.rate_limiter import RateLimiter, RateLimitExceeded

logger = logging.getLogger(__name__)


class BaseExecutionAdapter(BrokerAdapter):
    """执行适配器基类（模板方法骨架）。

    子类必须实现所有 @abstractmethod。
    公共逻辑（风控令牌校验、限速）在模板方法中统一处理。
    """

    def __init__(
        self,
        account_id: str,
        rate_limiter: Optional[RateLimiter] = None,
    ) -> None:
        """
        Args:
            account_id: 资金账户 ID。
            rate_limiter: 申报限速器（None = 使用默认参数构造）。
        """
        self.account_id = account_id
        self._rate_limiter = rate_limiter or RateLimiter()

    # ------------------------------------------------------------------
    # 模板方法：submit_order（公共前置逻辑 + 子类实现）
    # ------------------------------------------------------------------

    def submit_order(self, order: Order) -> str:
        """提交订单模板方法。

        公共前置检查（_pre_submit_checks）：
          1. GuardDecision token 校验（体现风控守卫前置，不可绕过）
          2. 申报限速检查（§6.1 硬约束）

        然后调用 _do_submit_order() 执行平台差异逻辑。

        Args:
            order: 携带有效 GuardDecision token 的 Order 对象。

        Returns:
            broker_order_id（券商委托编号）。

        Raises:
            ValueError: GuardDecision token 无效。
            RateLimitExceeded: 超出申报限速。
        """
        self._pre_submit_checks(order)
        broker_order_id = self._do_submit_order(order)
        logger.info(
            "adapter.submit_order OK client_order_id=%s broker_order_id=%s ts_code=%s",
            order.client_order_id,
            broker_order_id,
            order.ts_code,
        )
        return broker_order_id

    def _pre_submit_checks(self, order: Order) -> None:
        """前置校验（子类勿 override，保持强制执行）。

        1. GuardDecision token 校验
        2. 限速检查（§6.1，先于业务逻辑）
        """
        # 1. 风控令牌校验（不可伪造，见 guard.py GuardDecision.verify()）
        guard_decision = getattr(order, "_guard_decision", None)
        if guard_decision is None:
            raise ValueError(
                f"order.client_order_id={order.client_order_id} 缺少 GuardDecision token，"
                "禁止下单。所有订单必须经 RiskGuard.submit() 审核。"
            )
        # 调用 verify() — sentinel 错误或 approved=False 均抛 ValueError
        guard_decision.verify()

        # 2. 申报限速（§6.1 硬约束，先于业务逻辑）
        self._rate_limiter.check_and_consume(order.account_id)

    @abstractmethod
    def _do_submit_order(self, order: Order) -> str:
        """平台差异下单逻辑（子类实现）。

        调用时前置检查已通过（token 合法 + 限速通过）。

        Args:
            order: 已验证的 Order 对象。

        Returns:
            broker_order_id。
        """

    # ------------------------------------------------------------------
    # 抽象方法（子类完全实现）
    # ------------------------------------------------------------------

    @abstractmethod
    def cancel_order(self, broker_order_id: str, account_id: str) -> bool:
        """撤单。"""

    @abstractmethod
    def query_order(self, broker_order_id: str, account_id: str) -> Optional[OrderStatus]:
        """查询单笔委托状态。"""

    @abstractmethod
    def query_positions(self, account_id: str) -> list[Position]:
        """查询全仓持仓。"""

    @abstractmethod
    def query_trades(self, account_id: str, trade_date: str) -> list[Fill]:
        """查询当日成交明细。"""

    # ------------------------------------------------------------------
    # 公共工具方法（子类可复用）
    # ------------------------------------------------------------------

    def _validate_account(self, account_id: str) -> None:
        """校验 account_id 与适配器绑定账户一致。"""
        if account_id != self.account_id:
            raise ValueError(
                f"账户不匹配：适配器绑定 {self.account_id}，"
                f"调用传入 {account_id}。"
            )

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(account_id={self.account_id!r})"
