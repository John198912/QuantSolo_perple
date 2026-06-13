"""XtQuant 实盘适配器（QS-C04 §8 · QS-E02 §7.2）。

⚠️  此文件是系统唯一业务文件中允许 import xtquant 的地方。
    策略层、风控层、其他执行模块禁止 import xtquant。

职责：
  - 封装 xtquant API（迅投 miniQMT Python SDK）
  - 校验 GuardDecision token（风控守卫前置，不可绕过）
  - order_remark = client_order_id（§8.1 对账方案）
  - 申报限速检查（父类 _pre_submit_checks 已处理）

GuardDecision 旁路不可达：
  _pre_submit_checks() → guard_decision.verify()
  若 token 缺失或 sentinel 错误 → ValueError，拒绝下单。
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

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
# xtquant 可选导入（未安装时友好报错，不崩溃）
# ---------------------------------------------------------------------------

try:
    from xtquant import xttrader as _xttrader_mod
    from xtquant import xtconstant as _xtconstant
    _XTQUANT_AVAILABLE = True
except ImportError:
    _xttrader_mod = None   # type: ignore[assignment]
    _xtconstant = None     # type: ignore[assignment]
    _XTQUANT_AVAILABLE = False
    logger.warning(
        "xtquant 未安装或不可用。XtquantAdapter 可实例化但 submit_order 等方法将抛 ImportError。"
        "请在 Windows + QMT 环境下运行实盘，或使用 BacktestAdapter 进行回测。"
    )


def _require_xtquant() -> None:
    """在需要 xtquant 的方法入口调用，未安装时给出清晰错误。"""
    if not _XTQUANT_AVAILABLE:
        raise ImportError(
            "xtquant 不可用。XtquantAdapter 需要迅投 miniQMT Python SDK（仅 Windows 环境）。\n"
            "回测请使用 BacktestAdapter；模拟盘/实盘须在 QMT 客户端启动后运行。"
        )


# ---------------------------------------------------------------------------
# xtquant 状态码 → BrokerStatusCode 映射
# ---------------------------------------------------------------------------

_XT_STATUS_MAP: dict[int, BrokerStatusCode] = {
    48: BrokerStatusCode.SUBMITTED,
    49: BrokerStatusCode.LIVE,
    50: BrokerStatusCode.PARTIAL,
    51: BrokerStatusCode.FILLED,
    52: BrokerStatusCode.CANCELLED,
    53: BrokerStatusCode.REJECTED,
}


def _map_xt_status(xt_status: int) -> BrokerStatusCode:
    return _XT_STATUS_MAP.get(xt_status, BrokerStatusCode.UNKNOWN)


# ---------------------------------------------------------------------------
# XtquantAdapter
# ---------------------------------------------------------------------------

class XtquantAdapter(BaseExecutionAdapter):
    """xtquant 实盘适配器（QS-C04 §8 · order_remark 对账）。

    xtquant 相关代码仅在此类出现，其他模块禁止 import。

    GuardDecision 旁路不可达机制：
      父类 BaseExecutionAdapter.submit_order() 调用 _pre_submit_checks()，
      其中强制调用 order._guard_decision.verify()，
      令牌缺失或 sentinel 错误则抛 ValueError，绝不进入 _do_submit_order()。
    """

    def __init__(
        self,
        account_id: str,
        xttrader_instance: object,           # XtQuantTrader 实例（运行时传入）
        rate_limiter: Optional[RateLimiter] = None,
    ) -> None:
        """
        Args:
            account_id: 资金账户 ID（如 "1234567890"）。
            xttrader_instance: 已连接的 xtquant XtQuantTrader 实例。
            rate_limiter: 申报限速器（None = 使用默认参数构造）。
        """
        super().__init__(account_id, rate_limiter)
        self._xttrader = xttrader_instance

    # ------------------------------------------------------------------
    # 下单（模板方法子类实现）
    # ------------------------------------------------------------------

    def _do_submit_order(self, order: Order) -> str:
        """调用 xtquant 下单接口。

        order_remark = client_order_id（§8.1 对账键，≤64字节）。
        此时 GuardDecision token 已由父类验证，限速已消耗。
        """
        _require_xtquant()

        # 买卖方向
        if order.side == OrderSide.BUY:
            order_type_xt = _xtconstant.STOCK_BUY
        else:
            order_type_xt = _xtconstant.STOCK_SELL

        # 价格类型
        if order.limit_price is not None:
            price_type_xt = _xtconstant.FIX_PRICE
            price_val = float(order.limit_price)  # xtquant API 仅接受 float（禁止在业务计算中用）
        else:
            price_type_xt = _xtconstant.MARKET_PRICE
            price_val = 0.0

        # order_remark = client_order_id（§8.1）
        order_remark = order.client_order_id
        assert len(order_remark.encode("utf-8")) <= 64, (
            f"client_order_id 超 64 字节限制: {order_remark!r}"
        )

        broker_order_id = self._xttrader.order_stock(
            account=self.account_id,
            stock_code=order.ts_code,
            order_type=order_type_xt,
            order_volume=order.qty,
            price_type=price_type_xt,
            price=price_val,
            order_remark=order_remark,     # ← 对账键（QS-C04 §8.1）
        )

        logger.info(
            "xtquant.order_stock OK broker_order_id=%s client_order_id=%s "
            "ts_code=%s side=%s qty=%d order_remark=%s",
            broker_order_id,
            order.client_order_id,
            order.ts_code,
            order.side.value,
            order.qty,
            order_remark,
        )
        return str(broker_order_id)

    # ------------------------------------------------------------------
    # 撤单
    # ------------------------------------------------------------------

    def cancel_order(self, broker_order_id: str, account_id: str) -> bool:
        """撤单（QS-C04 §二 CANCEL_REQUESTED → 等回报）。"""
        _require_xtquant()
        self._validate_account(account_id)
        result = self._xttrader.cancel_order_stock(
            account=self.account_id,
            order_id=int(broker_order_id),
        )
        ok = result == 0  # 0 = 成功
        logger.info(
            "xtquant.cancel_order broker_order_id=%s ok=%s",
            broker_order_id,
            ok,
        )
        return ok

    # ------------------------------------------------------------------
    # 查询委托
    # ------------------------------------------------------------------

    def query_order(
        self, broker_order_id: str, account_id: str
    ) -> Optional[OrderStatus]:
        """查询单笔委托（UNKNOWN 归位，§二 2.3）。"""
        _require_xtquant()
        self._validate_account(account_id)
        orders = self._xttrader.query_stock_orders(self.account_id)
        for o in orders:
            if str(o.order_id) == broker_order_id:
                return self._parse_order_status(o)
        return None

    def query_by_order_remark(
        self, client_order_id: str, account_id: str
    ) -> Optional[OrderStatus]:
        """通过 order_remark 反查委托（§8.1，outbox 恢复第一路径）。

        两次查询一致才归位（§二 2.3）：调用方须在心跳中调用两次并比对。

        Raises:
            ValueError: order_remark 匹配多条委托（须人工处理）。
        """
        _require_xtquant()
        self._validate_account(account_id)
        orders = self._xttrader.query_stock_orders(self.account_id)
        candidates = [o for o in orders if o.order_remark == client_order_id]
        if len(candidates) == 0:
            return None
        if len(candidates) > 1:
            raise ValueError(
                f"order_remark={client_order_id} 匹配 {len(candidates)} 条委托，"
                "须人工处理（暂停 MANUAL_REVIEW）。"
            )
        return self._parse_order_status(candidates[0])

    # ------------------------------------------------------------------
    # 持仓 / 成交查询
    # ------------------------------------------------------------------

    def query_positions(self, account_id: str) -> list[Position]:
        """查询全仓持仓。"""
        _require_xtquant()
        self._validate_account(account_id)
        raw_positions = self._xttrader.query_stock_positions(self.account_id)
        from datetime import datetime, timezone as _tz
        as_of = datetime.now(tz=_tz.utc).isoformat()
        results: list[Position] = []
        for p in raw_positions:
            results.append(Position(
                account_id=self.account_id,
                ts_code=p.stock_code,
                qty=int(p.volume),
                sellable_qty=int(p.can_use_volume),
                avg_cost=Decimal(str(p.avg_price)) if p.avg_price else Decimal("0"),
                market_value=Decimal(str(p.market_value)) if p.market_value else Decimal("0"),
                as_of=as_of,
            ))
        return results

    def query_trades(self, account_id: str, trade_date: str) -> list[Fill]:
        """查询指定交易日成交明细（对账使用）。"""
        _require_xtquant()
        self._validate_account(account_id)
        # TODO: xtquant 的历史成交查询接口（query_stock_trades）
        # 需传入日期范围，具体签名见 xtquant SDK 文档
        # 此处留结构化骨架，待真实 SDK 文档确认后填充
        # TODO(实现): raw_trades = self._xttrader.query_stock_trades(self.account_id, trade_date)
        # return [self._parse_fill(t) for t in raw_trades]
        logger.warning(
            "xtquant.query_trades: TODO — 待接入真实 xtquant SDK 历史成交接口 "
            "(account=%s trade_date=%s)",
            account_id,
            trade_date,
        )
        return []

    def health_check(self) -> bool:
        """检查 QMT 终端连接状态。"""
        if not _XTQUANT_AVAILABLE:
            return False
        try:
            return bool(self._xttrader.is_connected())
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 解析工具
    # ------------------------------------------------------------------

    def _parse_order_status(self, xt_order: object) -> OrderStatus:
        """xtquant 委托对象 → 统一 OrderStatus。"""
        filled = getattr(xt_order, "traded_volume", 0)
        total = getattr(xt_order, "order_volume", 0)
        remaining = max(0, total - filled)
        avg_price = getattr(xt_order, "traded_price", None)

        return OrderStatus(
            client_order_id=getattr(xt_order, "order_remark", ""),
            broker_order_id=str(getattr(xt_order, "order_id", "")),
            ts_code=getattr(xt_order, "stock_code", ""),
            side=(
                OrderSide.BUY
                if getattr(xt_order, "order_type", None) == (
                    _xtconstant.STOCK_BUY if _xtconstant else None
                )
                else OrderSide.SELL
            ),
            qty=int(total),
            filled_qty=int(filled),
            remaining_qty=int(remaining),
            avg_fill_price=Decimal(str(avg_price)) if avg_price else None,
            broker_status=_map_xt_status(
                int(getattr(xt_order, "order_status", -1))
            ),
            order_remark=getattr(xt_order, "order_remark", None),
            event_ts=str(getattr(xt_order, "order_time", "")),
        )
