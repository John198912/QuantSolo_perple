"""三层隔离抽象接口（QS-E02 §4 · QS-C04 §二）。

定义平台无关的统一订单/持仓 dataclass 及 BrokerAdapter ABC。
禁止 import xtquant。策略层/风控层只依赖此文件。

层次关系：
  策略层 → interfaces.BrokerAdapter（ABC）← adapters/base.py ← adapters/xtquant_adapter.py
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------

class OrderSide(str, Enum):
    """买卖方向。"""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """订单类型。"""
    LIMIT = "LIMIT"       # 限价单
    MARKET = "MARKET"     # 市价单


class TimeInForce(str, Enum):
    """有效期类型。"""
    DAY = "DAY"   # 当日有效（15:00 自动撤）
    GTC = "GTC"   # 无限期有效（跨日挂单）


class BrokerStatusCode(str, Enum):
    """券商回报的标准化状态码（内部枚举，与 OrderState 映射）。"""
    SUBMITTED = "SUBMITTED"
    LIVE = "LIVE"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# 核心 dataclass
# ---------------------------------------------------------------------------

@dataclass
class Order:
    """完整订单意图（含幂等键与风控签名）。

    此对象在系统内部流通；策略层生成，经 RiskGuard 校验后携带
    GuardDecision token 传入 BrokerAdapter.submit_order()。

    禁止 float 算钱：price / estimated_amount 均使用 Decimal。
    """
    # 幂等键（QS-C04 §4.1）
    client_order_id: str

    # 账户/策略标识
    account_id: str
    strategy_id: str

    # 标的与方向
    ts_code: str
    side: OrderSide
    order_type: OrderType
    time_in_force: TimeInForce

    # 数量与价格
    qty: int                              # 申报数量（股）
    limit_price: Optional[Decimal] = None # 限价单价格（Decimal，元/股）

    # 对账字段（QS-C04 §8）
    # order_remark 在下单时由适配器写入 = client_order_id
    order_remark: Optional[str] = None

    # 父子单关系（拆单场景）
    parent_intent_id: Optional[str] = None
    rebalance_seq: int = 0               # 全局单调递增调仓序号

    # 风控签名（由 RiskGuard 填充，执行层校验）
    risk_signature: Optional[str] = None

    # 风控守卫决策令牌（由 RiskGuard.submit() 填充，不可伪造）
    # 类型为 GuardDecision，延迟导入避免循环
    _guard_decision: Optional[object] = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        # order_remark 默认等于 client_order_id（QS-C04 §8.1）
        if self.order_remark is None:
            self.order_remark = self.client_order_id


@dataclass(frozen=True)
class OrderStatus:
    """订单状态快照（查询结果）。"""
    client_order_id: str
    broker_order_id: Optional[str]
    ts_code: str
    side: OrderSide
    qty: int
    filled_qty: int
    remaining_qty: int
    avg_fill_price: Optional[Decimal]
    broker_status: BrokerStatusCode
    order_remark: Optional[str]
    event_ts: str                        # ISO-8601 时间戳字符串


@dataclass(frozen=True)
class Fill:
    """成交回报（单次成交事件）。"""
    client_order_id: str
    broker_order_id: str
    ts_code: str
    side: OrderSide
    filled_qty: int
    fill_price: Decimal                  # 禁止 float
    fill_amount: Decimal                 # filled_qty × fill_price（含手续费前）
    event_ts: str
    event_seq: int = 0                   # 用于乱序去重（累计成交量幂等）


@dataclass
class Position:
    """持仓快照（单只标的）。"""
    account_id: str
    ts_code: str
    qty: int                             # 总持有量（股）
    sellable_qty: int                    # T+1 可卖量（股）
    avg_cost: Decimal                    # 持仓均价（Decimal，元/股）
    market_value: Decimal                # 当前市值（Decimal，元）
    as_of: str                           # 快照时间（ISO-8601）


# ---------------------------------------------------------------------------
# BrokerAdapter ABC
# ---------------------------------------------------------------------------

class BrokerAdapter(ABC):
    """券商适配器抽象基类（QS-E02 §4.2 · 三层隔离核心接口）。

    所有平台适配器（XtquantAdapter、BacktestAdapter）必须继承此类。
    此接口平台无关，禁止 import xtquant。

    接口设计原则：
      - submit_order 必须接受 Order 对象（含 GuardDecision token）。
      - 返回类型统一（不暴露平台原生类型）。
      - 所有金额用 Decimal，禁止 float。
    """

    @abstractmethod
    def submit_order(self, order: Order) -> str:
        """提交订单至券商。

        Args:
            order: 携带有效 GuardDecision token 的订单对象。
                   适配器实现层**必须**校验 token 有效性（体现风控守卫前置）。

        Returns:
            broker_order_id（券商委托编号，字符串）。

        Raises:
            ValueError: GuardDecision token 无效或缺失。
            RuntimeError: 下单接口异常。
        """

    @abstractmethod
    def cancel_order(self, broker_order_id: str, account_id: str) -> bool:
        """撤单。

        Returns:
            True 表示撤单指令已成功发出（非最终确认）。
        """

    @abstractmethod
    def query_order(self, broker_order_id: str, account_id: str) -> Optional[OrderStatus]:
        """查询单笔委托状态（UNKNOWN 态归位使用）。

        Returns:
            OrderStatus 或 None（查不到时）。
        """

    @abstractmethod
    def query_positions(self, account_id: str) -> list[Position]:
        """查询当前全仓持仓。"""

    @abstractmethod
    def query_trades(self, account_id: str, trade_date: str) -> list[Fill]:
        """查询指定交易日的成交明细（对账使用）。

        Args:
            trade_date: 交易日字符串，格式 "YYYY-MM-DD"。
        """

    def query_by_order_remark(
        self, client_order_id: str, account_id: str
    ) -> Optional[OrderStatus]:
        """通过 order_remark 反查委托（QS-C04 §8.1，outbox 恢复第一路径）。

        默认实现返回 None（适配器可按需 override）。
        """
        return None

    def health_check(self) -> bool:
        """检查适配器连接健康状态（可 override）。"""
        return True
