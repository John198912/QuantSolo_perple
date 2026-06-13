"""成本偏差归因（QS-E03 §8.3 / QS-C04 §B3 工程判线）。

实盘成本 vs 回测建模成本偏差，B3 工程判线之一：
  cost_deviation = (actual_cost - modeled_cost) / modeled_cost ≤ 30%

红线遵守：
  R3：偏差阈值取 load_frozen()['gates']['b3_cost_deviation_max']，禁止硬编码。
  R6：金额/费用全部用 Decimal，禁止 float 直接算钱。
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import pandas as pd

from src.common.config import load_frozen


# ---------------------------------------------------------------------------
# 核心函数
# ---------------------------------------------------------------------------

def calc_cost_deviation(
    execution_df: pd.DataFrame,
    backtest_cost_model: Any,
    tolerance: float | None = None,
) -> dict:
    """实盘成本 vs 回测建模成本偏差（B3 工程判线之一）。

    Args:
        execution_df:       execution_ledger 中已成交记录（DataFrame）。
                            必须包含列：client_order_id, ts_code, filled_qty,
                            avg_fill_price, side, actual_commission, actual_slippage。
                            可选：daily_turnover。
        backtest_cost_model: 回测用成本模型（实现 calc_transaction_cost()）。
        tolerance:          成本偏差容忍上限；None 时从冻结参数读取
                            gates.b3_cost_deviation_max（默认 0.30）。

    Returns:
        {
            'deviation_pct': float,       # 平均偏差率
            'b3_passed': bool,            # 是否通过 B3 判线
            'breakdown': pd.DataFrame,   # 逐笔偏差明细
        }

    注意：
        - 所有金额/费用内部均使用 Decimal 计算（R6）。
        - 最终 deviation_pct 以 float 形式对外返回（纯描述性统计，非钱）。
    """
    # 从冻结参数读取 B3 成本偏差上限（R3）
    if tolerance is None:
        frozen = load_frozen()
        tolerance = float(frozen["gates"]["b3_cost_deviation_max"])

    rows: list[dict] = []

    # 精度：分位（4 位小数）
    PREC = Decimal("0.0001")
    EPSILON = Decimal("1e-8")  # 防除以零

    for _, row in execution_df.iterrows():
        # --- 实盘成本（R6：Decimal）---
        actual_commission = Decimal(str(row.get("actual_commission", 0))).quantize(
            PREC, rounding=ROUND_HALF_UP
        )
        actual_slippage = Decimal(str(row.get("actual_slippage", 0))).quantize(
            PREC, rounding=ROUND_HALF_UP
        )
        actual_cost = actual_commission + actual_slippage

        # --- 回测建模成本（R6：Decimal）---
        filled_qty = Decimal(str(row["filled_qty"]))
        avg_fill_price = Decimal(str(row["avg_fill_price"]))
        trade_size = filled_qty * avg_fill_price

        modeled_cost_raw = backtest_cost_model.calc_transaction_cost(
            amount=float(trade_size),
            side=row["side"],
            daily_turnover=row.get("daily_turnover", 0),
            trade_size=float(trade_size),
        )
        modeled_cost = Decimal(str(modeled_cost_raw)).quantize(
            PREC, rounding=ROUND_HALF_UP
        )

        # --- 偏差率（Decimal 计算，结果以 float 存储供统计）---
        deviation = float(
            (actual_cost - modeled_cost) / (modeled_cost + EPSILON)
        )

        rows.append({
            "client_order_id": row["client_order_id"],
            "ts_code": row["ts_code"],
            "actual_cost": float(actual_cost),
            "modeled_cost": float(modeled_cost),
            "deviation_pct": deviation,
        })

    breakdown = pd.DataFrame(rows)
    avg_deviation: float = (
        float(breakdown["deviation_pct"].mean()) if not breakdown.empty else 0.0
    )
    b3_passed = avg_deviation <= tolerance

    return {
        "deviation_pct": avg_deviation,
        "b3_passed": b3_passed,
        "breakdown": breakdown,
    }
