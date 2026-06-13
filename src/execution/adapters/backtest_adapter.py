"""回测适配器（QS-E02 §4.4 · QS-C01 §4）。

BacktestAdapter：基于历史数据模拟撮合，含成本模型。
成本模型参数来自 frozen.toml [cost]（印花税/佣金/过户费/滑点）。
禁止 import xtquant。
"""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from src.common.config import load_frozen
from src.execution.adapters.base import BaseExecutionAdapter
from src.execution.interfaces import (
    BrokerStatusCode,
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    Position,
)
from src.execution.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 成本模型
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CostModel:
    """交易成本模型（QS-C01 §4 · §12.2）。

    所有比例参数均为 Decimal（禁止 float 算钱）。
    """
    stamp_duty_sell: Decimal        # 印花税（卖出），默认 0.0005
    commission_rate: Decimal        # 佣金率，默认 0.00025 (万2.5)
    commission_min_cny: Decimal     # 佣金最低，默认 5 元
    transfer_fee_rate: Decimal      # 过户费率，默认 0.00001 (万0.1)
    slippage_floor: Decimal         # 回测滑点下限，默认 0.002 (0.2%)

    @classmethod
    def from_frozen(cls) -> "CostModel":
        """从 frozen.toml [cost] 加载（唯一来源）。"""
        frozen = load_frozen()
        cost = frozen["cost"]
        return cls(
            stamp_duty_sell=Decimal(str(cost["stamp_duty_sell"])),
            commission_rate=Decimal(str(cost["commission_rate"])),
            commission_min_cny=Decimal(str(cost["commission_min_cny"])),
            transfer_fee_rate=Decimal(str(cost["transfer_fee_rate"])),
            slippage_floor=Decimal(str(cost["slippage_floor"])),
        )

    def compute_commission(self, amount: Decimal) -> Decimal:
        """计算佣金（取 rate*amount 与最低佣金较大值）。"""
        commission = (amount * self.commission_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        return max(commission, self.commission_min_cny)

    def compute_stamp_duty(self, side: OrderSide, amount: Decimal) -> Decimal:
        """印花税（仅卖出收取）。"""
        if side == OrderSide.SELL:
            return (amount * self.stamp_duty_sell).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        return Decimal("0")

    def compute_transfer_fee(self, amount: Decimal) -> Decimal:
        """过户费。"""
        return (amount * self.transfer_fee_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    def compute_slippage(self, side: OrderSide, price: Decimal) -> Decimal:
        """滑点（回测）：BUY 价格上浮，SELL 价格下浮。"""
        slippage = (price * self.slippage_floor).quantize(
            Decimal("0.001"), rounding=ROUND_HALF_UP
        )
        if side == OrderSide.BUY:
            return slippage
        else:
            return -slippage

    def total_cost(self, side: OrderSide, amount: Decimal) -> Decimal:
        """交易总成本（佣金 + 印花税 + 过户费）。"""
        return (
            self.compute_commission(amount)
            + self.compute_stamp_duty(side, amount)
            + self.compute_transfer_fee(amount)
        )


# ---------------------------------------------------------------------------
# 模拟持仓账本
# ---------------------------------------------------------------------------

@dataclass
class _SimPosition:
    """模拟持仓（内部使用）。"""
    ts_code: str
    qty: int
    sellable_qty: int
    avg_cost: Decimal

    def update_buy(self, qty: int, price: Decimal) -> None:
        if qty <= 0:
            return
        total_cost = self.avg_cost * self.qty + price * qty
        self.qty += qty
        self.avg_cost = (total_cost / self.qty).quantize(Decimal("0.0001"))
        # 买入当日不可卖（T+1），sellable_qty 次日 EOD 更新

    def update_sell(self, qty: int) -> None:
        self.qty = max(0, self.qty - qty)
        self.sellable_qty = max(0, self.sellable_qty - qty)


# ---------------------------------------------------------------------------
# BacktestAdapter
# ---------------------------------------------------------------------------

class BacktestAdapter(BaseExecutionAdapter):
    """回测适配器：基于历史数据模拟撮合。

    特性：
      - 使用注入的历史价格数据（reference_prices）撮合
      - 含完整成本模型（从 frozen.toml [cost] 读取）
      - 模拟 T+1 可卖约束
      - 线程安全

    GuardDecision token 校验由父类 _pre_submit_checks() 强制执行。
    """

    def __init__(
        self,
        account_id: str,
        initial_cash: Decimal,
        reference_prices: Optional[dict[str, Decimal]] = None,
        rate_limiter: Optional[RateLimiter] = None,
        bypass_rate_limit: bool = False,
    ) -> None:
        """
        Args:
            account_id: 账户 ID。
            initial_cash: 初始资金（Decimal，元）。
            reference_prices: {ts_code: price}，回测撮合价格。
            rate_limiter: 申报限速器（None = 默认构造）。
            bypass_rate_limit: True = 回测时绕过限速（加速模拟，默认 False）。
        """
        super().__init__(account_id, rate_limiter)
        self._cash = initial_cash
        self._reference_prices: dict[str, Decimal] = reference_prices or {}
        self._positions: dict[str, _SimPosition] = {}
        self._fills: list[Fill] = []
        self._pending_orders: dict[str, Order] = {}
        self._lock = threading.Lock()
        self._bypass_rate_limit = bypass_rate_limit
        self._cost_model = CostModel.from_frozen()
        self._broker_id_counter: int = 0

    def update_reference_prices(self, prices: dict[str, Decimal]) -> None:
        """更新参考价格（逐日驱动回测时调用）。"""
        with self._lock:
            self._reference_prices.update(prices)

    def eod_release_t1(self) -> None:
        """日终将当日买入量转入可卖（T+1 释放）。"""
        with self._lock:
            for pos in self._positions.values():
                pos.sellable_qty = pos.qty

    # ------------------------------------------------------------------
    # 模板方法实现
    # ------------------------------------------------------------------

    def _pre_submit_checks(self, order: Order) -> None:
        """回测可选绕过限速，但仍强制校验 GuardDecision token。"""
        # GuardDecision token 校验不可绕过
        guard_decision = getattr(order, "_guard_decision", None)
        if guard_decision is None:
            raise ValueError(
                f"order.client_order_id={order.client_order_id} 缺少 GuardDecision token，"
                "禁止下单（回测模式同样强制）。"
            )
        guard_decision.verify()

        # 限速：回测模式可选绕过
        if not self._bypass_rate_limit:
            self._rate_limiter.check_and_consume(order.account_id)

    def _do_submit_order(self, order: Order) -> str:
        """模拟撮合：立即按参考价格全量成交。"""
        with self._lock:
            price = self._reference_prices.get(order.ts_code)
            if price is None or price <= 0:
                raise ValueError(
                    f"BacktestAdapter: {order.ts_code} 无参考价格，无法撮合。"
                )

            # 滑点调整（回测成本模型）
            slippage = self._cost_model.compute_slippage(order.side, price)
            fill_price = price + slippage

            # 成交金额
            fill_amount = fill_price * Decimal(order.qty)
            cost = self._cost_model.total_cost(order.side, fill_amount)

            if order.side == OrderSide.BUY:
                total_deduct = fill_amount + cost
                if total_deduct > self._cash:
                    raise ValueError(
                        f"BacktestAdapter: 资金不足 (需要 {total_deduct}，"
                        f"可用 {self._cash})。"
                    )
                self._cash -= total_deduct
                pos = self._positions.setdefault(
                    order.ts_code,
                    _SimPosition(
                        ts_code=order.ts_code,
                        qty=0,
                        sellable_qty=0,
                        avg_cost=Decimal("0"),
                    ),
                )
                pos.update_buy(order.qty, fill_price)
            else:
                # SELL
                pos = self._positions.get(order.ts_code)
                if pos is None or pos.sellable_qty < order.qty:
                    available = pos.sellable_qty if pos else 0
                    raise ValueError(
                        f"BacktestAdapter: T+1 可卖量不足 "
                        f"(需要 {order.qty}，可卖 {available})。"
                    )
                self._cash += fill_amount - cost
                pos.update_sell(order.qty)

            self._broker_id_counter += 1
            broker_order_id = f"BT_{self.account_id}_{self._broker_id_counter:06d}"

            fill = Fill(
                client_order_id=order.client_order_id,
                broker_order_id=broker_order_id,
                ts_code=order.ts_code,
                side=order.side,
                filled_qty=order.qty,
                fill_price=fill_price,
                fill_amount=fill_amount,
                event_ts=datetime.now(tz=timezone.utc).isoformat(),
                event_seq=self._broker_id_counter,
            )
            self._fills.append(fill)
            logger.debug(
                "backtest.fill ts_code=%s side=%s qty=%d price=%s cost=%s",
                order.ts_code, order.side.value, order.qty, fill_price, cost,
            )
            return broker_order_id

    # ------------------------------------------------------------------
    # 接口实现
    # ------------------------------------------------------------------

    def cancel_order(self, broker_order_id: str, account_id: str) -> bool:
        """回测：挂单不存在（立即成交模型），撤单无效果。"""
        return True

    def query_order(
        self, broker_order_id: str, account_id: str
    ) -> Optional[OrderStatus]:
        """回测：查询成交记录。"""
        self._validate_account(account_id)
        with self._lock:
            for fill in self._fills:
                if fill.broker_order_id == broker_order_id:
                    return OrderStatus(
                        client_order_id=fill.client_order_id,
                        broker_order_id=fill.broker_order_id,
                        ts_code=fill.ts_code,
                        side=fill.side,
                        qty=fill.filled_qty,
                        filled_qty=fill.filled_qty,
                        remaining_qty=0,
                        avg_fill_price=fill.fill_price,
                        broker_status=BrokerStatusCode.FILLED,
                        order_remark=fill.client_order_id,
                        event_ts=fill.event_ts,
                    )
        return None

    def query_positions(self, account_id: str) -> list[Position]:
        """返回当前模拟持仓。"""
        self._validate_account(account_id)
        with self._lock:
            as_of = datetime.now(tz=timezone.utc).isoformat()
            result: list[Position] = []
            for ts_code, pos in self._positions.items():
                if pos.qty <= 0:
                    continue
                price = self._reference_prices.get(ts_code, pos.avg_cost)
                result.append(Position(
                    account_id=account_id,
                    ts_code=ts_code,
                    qty=pos.qty,
                    sellable_qty=pos.sellable_qty,
                    avg_cost=pos.avg_cost,
                    market_value=(price * Decimal(pos.qty)).quantize(Decimal("0.01")),
                    as_of=as_of,
                ))
            return result

    def query_trades(self, account_id: str, trade_date: str) -> list[Fill]:
        """返回指定日期的成交记录（按 event_ts 过滤日期前缀）。"""
        self._validate_account(account_id)
        with self._lock:
            return [f for f in self._fills if f.event_ts.startswith(trade_date)]

    # ------------------------------------------------------------------
    # 回测专属属性
    # ------------------------------------------------------------------

    @property
    def cash(self) -> Decimal:
        return self._cash

    @property
    def cost_model(self) -> CostModel:
        return self._cost_model

    @property
    def all_fills(self) -> list[Fill]:
        with self._lock:
            return list(self._fills)
