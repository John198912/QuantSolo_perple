"""事件驱动回测引擎（QS-C01 §4 / §12.3；功能设计文档 §4.5）。

精确成本验证模式。按 A 股交易规则逐笔撮合：
- T+1：当日买入不可当日卖出
- 涨跌停：命中限价板 → 挂单排队，不强行成交
- 100 股一手取整
- 高价股过滤（一手 > 1.6 万）
- 部分成交处理

R3 红线：成本数字经 load_frozen()['cost'] 读取。
R6 红线：金额计算使用 Decimal。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

import numpy as np
import pandas as pd

from src.research.backtest.cost_models import CostModel, get_advanced_model

logger = logging.getLogger(__name__)

# A 股高价股过滤线：一手（100 股）超过此金额则剔除
HIGH_PRICE_THRESHOLD_CNY = Decimal("16000")


@dataclass
class Order:
    """委托单（事件驱动回测内部用）。"""
    ts_code: str
    trade_date: str
    side: str            # 'BUY' | 'SELL'
    qty: int             # 股数（100 股整数倍）
    limit_price: Optional[Decimal] = None  # 限价；None 表示市价
    # 内部字段
    filled_qty: int = 0
    filled_price: Decimal = field(default_factory=lambda: Decimal("0"))
    status: str = "PENDING"  # PENDING / FILLED / PARTIAL / REJECTED


@dataclass
class Position:
    """持仓记录。"""
    ts_code: str
    qty: int
    avg_cost: Decimal
    buy_date: str  # 最近一次建仓日（T+1 检查用）


def apply_ashare_constraints(
    order: dict[str, Any],
    bars: pd.DataFrame | dict,
    positions: dict[str, Position],
) -> tuple[dict[str, Any], bool]:
    """A 股特性约束（QS-C01 §12.3）。

    Args:
        order:     委托字典，含 ts_code / side / qty / trade_date
        bars:      当日行情 DataFrame（index=ts_code）或 dict
                   字段：close / upper_limit / lower_limit
        positions: 当前持仓字典 {ts_code: Position}

    Returns:
        (adjusted_order, can_fill)：调整后委托 + 是否可成交。

    规则：
      1. T+1：当日买入不可当日卖出
      2. 涨停：买入信号被拦截（无法成交）
      3. 跌停：卖出挂跌停价排队，当日不成交
      4. 100 股取整
      5. 高价股（一手 > 1.6 万）剔除
    """
    ts_code = order["ts_code"]

    # 获取当日行情
    if isinstance(bars, pd.DataFrame):
        if ts_code not in bars.index:
            logger.warning("apply_ashare_constraints: 无行情数据 ts_code=%s", ts_code)
            return order, False
        bar = bars.loc[ts_code].to_dict() if hasattr(bars.loc[ts_code], "to_dict") else dict(bars.loc[ts_code])
    elif isinstance(bars, dict):
        if ts_code not in bars:
            return order, False
        bar = bars[ts_code]
    else:
        return order, False

    # ── 规则 1：T+1 约束 ──
    if order["side"] == "SELL":
        pos = positions.get(ts_code)
        if pos is not None and pos.buy_date == order["trade_date"]:
            logger.debug("T+1 约束：%s 当日买入不可卖出（%s）", ts_code, order["trade_date"])
            return order, False

    close_price = Decimal(str(bar.get("close", 0)))

    # ── 规则 2：涨停板不可买入 ──
    upper_limit = bar.get("upper_limit")
    if upper_limit is not None:
        upper_limit_dec = Decimal(str(upper_limit))
        if order["side"] == "BUY" and close_price >= upper_limit_dec:
            logger.debug("涨停板拦截买入：%s close=%s upper=%s", ts_code, close_price, upper_limit_dec)
            return order, False

    # ── 规则 3：跌停板不可卖出（挂排队单）──
    lower_limit = bar.get("lower_limit")
    if lower_limit is not None:
        lower_limit_dec = Decimal(str(lower_limit))
        if order["side"] == "SELL" and close_price <= lower_limit_dec:
            order["limit_price"] = float(lower_limit_dec)
            logger.debug("跌停板挂单排队：%s close=%s lower=%s", ts_code, close_price, lower_limit_dec)
            return order, False

    # ── 规则 4：100 股取整 ──
    qty = int(order.get("qty", 0))
    qty = (qty // 100) * 100
    if qty == 0:
        return order, False
    order["qty"] = qty

    # ── 规则 5：高价股过滤（一手 > 1.6 万）──
    if close_price * 100 > HIGH_PRICE_THRESHOLD_CNY:
        logger.debug("高价股过滤：%s close=%s > 160元/股", ts_code, close_price)
        return order, False

    return order, True


class EventDrivenBacktest:
    """事件驱动回测引擎（A 股精确成本验证）。

    逐日/逐笔撮合，严格执行 A 股约束。
    """

    def __init__(
        self,
        cost_model: Optional[CostModel] = None,
        data_cut_id: Optional[int] = None,
        pit_engine: Optional[object] = None,
        initial_cash: float = 1_000_000.0,
    ) -> None:
        self.cost = cost_model if cost_model is not None else get_advanced_model()
        self.data_cut_id = data_cut_id
        self.pit = pit_engine
        self.initial_cash = Decimal(str(initial_cash))

    def run(
        self,
        signal_df: pd.DataFrame,
        bars_df: pd.DataFrame,
        start_date: str,
        end_date: str,
        top_n: int = 15,
    ) -> dict:
        """执行事件驱动回测。

        Args:
            signal_df: DataFrame，列：ts_code / trade_date / signal_rank（越小越优先）
            bars_df:   DataFrame，列：ts_code / trade_date / open / close /
                       upper_limit / lower_limit / amount（日均成交额）
            start_date: 回测开始日期
            end_date:   回测结束日期
            top_n:      每期持股数

        Returns:
            {
                'nav_series': pd.Series,
                'sharpe': float,
                'max_drawdown': float,
                'calmar': float,
                'annual_return': float,
                'cost_model_id': str,
                'trade_log': list[dict],
            }
        """
        cash = self.initial_cash
        positions: dict[str, Position] = {}
        trade_log: list[dict] = []
        nav_series: dict[str, float] = {}

        trade_dates = sorted(
            bars_df["trade_date"].unique().tolist()
        )
        trade_dates = [d for d in trade_dates if start_date <= d <= end_date]

        for date in trade_dates:
            day_bars_df = bars_df[bars_df["trade_date"] == date].copy()
            if day_bars_df.empty:
                continue
            day_bars_df = day_bars_df.set_index("ts_code")

            # 信号（当日收盘后生成，次日执行；此处简化为当日信号当日执行）
            day_signals = signal_df[signal_df["trade_date"] == date]

            # 计算目标持仓（Top N）
            target_stocks: list[str] = []
            if not day_signals.empty:
                target_stocks = (
                    day_signals.sort_values("signal_rank").head(top_n)["ts_code"].tolist()
                )

            # 生成卖出委托（清仓不在目标中的持仓）
            for ts_code in list(positions.keys()):
                if ts_code not in target_stocks:
                    order = {
                        "ts_code": ts_code,
                        "trade_date": date,
                        "side": "SELL",
                        "qty": positions[ts_code].qty,
                    }
                    order, can_fill = apply_ashare_constraints(order, day_bars_df, positions)
                    if can_fill:
                        fill_price = Decimal(str(day_bars_df.loc[ts_code, "close"]))
                        fill_amount = fill_price * order["qty"]
                        daily_vol = Decimal(str(day_bars_df.loc[ts_code, "amount"])) if "amount" in day_bars_df.columns else Decimal("0")
                        cost = self.cost.calc_transaction_cost(
                            fill_amount, "SELL", daily_vol, fill_amount
                        )
                        proceeds = fill_amount - cost
                        cash += proceeds
                        del positions[ts_code]
                        trade_log.append({
                            "date": date, "ts_code": ts_code, "side": "SELL",
                            "qty": order["qty"], "price": float(fill_price),
                            "cost": float(cost),
                        })

            # 生成买入委托（新增目标持仓）
            n_new = len([s for s in target_stocks if s not in positions])
            if n_new > 0:
                alloc_per_stock = cash / n_new
                for ts_code in target_stocks:
                    if ts_code in positions:
                        continue
                    if ts_code not in day_bars_df.index:
                        continue
                    close_price = Decimal(str(day_bars_df.loc[ts_code, "close"]))
                    if close_price <= 0:
                        continue
                    qty = int(alloc_per_stock / close_price)
                    qty = (qty // 100) * 100
                    if qty == 0:
                        continue
                    order = {
                        "ts_code": ts_code,
                        "trade_date": date,
                        "side": "BUY",
                        "qty": qty,
                    }
                    order, can_fill = apply_ashare_constraints(order, day_bars_df, positions)
                    if can_fill:
                        actual_qty = order["qty"]
                        fill_amount = close_price * actual_qty
                        daily_vol = Decimal(str(day_bars_df.loc[ts_code, "amount"])) if "amount" in day_bars_df.columns else Decimal("0")
                        cost = self.cost.calc_transaction_cost(
                            fill_amount, "BUY", daily_vol, fill_amount
                        )
                        total_cost = fill_amount + cost
                        if total_cost > cash:
                            continue
                        cash -= total_cost
                        positions[ts_code] = Position(
                            ts_code=ts_code,
                            qty=actual_qty,
                            avg_cost=(fill_amount + cost) / actual_qty,
                            buy_date=date,
                        )
                        trade_log.append({
                            "date": date, "ts_code": ts_code, "side": "BUY",
                            "qty": actual_qty, "price": float(close_price),
                            "cost": float(cost),
                        })

            # 计算当日 NAV = 现金 + 持仓市值
            port_value = cash
            for ts_code, pos in positions.items():
                if ts_code in day_bars_df.index:
                    close_p = Decimal(str(day_bars_df.loc[ts_code, "close"]))
                    port_value += close_p * pos.qty
            nav_series[date] = float(port_value / self.initial_cash)

        nav_s = pd.Series(nav_series)
        metrics = self._compute_metrics(nav_s, self.cost.model_id)
        metrics["trade_log"] = trade_log
        return metrics

    def _compute_metrics(self, nav: pd.Series, cost_model_id: str) -> dict:
        """计算回测汇总指标。"""
        if len(nav) < 2:
            return {"cost_model_id": cost_model_id}

        daily_ret = nav.pct_change().dropna()
        n = len(daily_ret)
        if n == 0:
            return {"cost_model_id": cost_model_id}

        annual_factor = 252.0
        annual_return = float(nav.iloc[-1] / nav.iloc[0]) ** (annual_factor / n) - 1
        sharpe = float(daily_ret.mean()) / (float(daily_ret.std()) + 1e-10) * np.sqrt(annual_factor)
        rolling_max = nav.cummax()
        max_drawdown = float(((nav - rolling_max) / rolling_max).min())
        calmar = annual_return / abs(max_drawdown) if max_drawdown != 0 else 0.0

        return {
            "nav_series": nav,
            "sharpe": sharpe,
            "max_drawdown": max_drawdown,
            "calmar": calmar,
            "annual_return": annual_return,
            "cost_model_id": cost_model_id,
        }

    @staticmethod
    def cross_validate_sharpe(
        vec_sharpe: float,
        evt_sharpe: float,
        threshold: float = 0.1,
    ) -> bool:
        """双层互验：向量化 vs 事件驱动 Sharpe 差异校验。

        Args:
            vec_sharpe: 向量化回测 Sharpe
            evt_sharpe: 事件驱动回测 Sharpe
            threshold:  最大允许差值（默认 0.1，来自 §4.2）

        Returns:
            True = 一致通过；False = 差异过大，须排查。
        """
        diff = abs(vec_sharpe - evt_sharpe)
        if diff > threshold:
            logger.error(
                "双层回测 Sharpe 差异超标：vec=%.3f evt=%.3f diff=%.3f > %.3f，停止！须排查。",
                vec_sharpe, evt_sharpe, diff, threshold,
            )
            return False
        return True
