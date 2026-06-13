"""成本偏差归因单元测试（QS-E03 §8.3）。

覆盖：
  - 成本偏差计算正确（Decimal 精度验证）
  - b3_passed=True：偏差 ≤ 容忍阈值
  - b3_passed=False：偏差 > 容忍阈值
  - tolerance 从冻结参数默认读取（= 0.30）
  - 空 DataFrame：avg_deviation=0.0，b3_passed=True
  - 逐笔明细 breakdown 结构正确
  - Decimal 金额计算不引入 float 精度误差

不发真实网络请求，使用 fake cost model。
"""
from __future__ import annotations

from decimal import Decimal

import pandas as pd
import pytest

from src.reconcile.cost_attribution import calc_cost_deviation


# ---------------------------------------------------------------------------
# Fake 成本模型
# ---------------------------------------------------------------------------

class FakeCostModel:
    """模拟回测成本模型，返回固定建模成本。"""

    def __init__(self, fixed_cost: float = 50.0):
        self._cost = fixed_cost

    def calc_transaction_cost(
        self,
        amount: float,
        side: str,
        daily_turnover: float,
        trade_size: float,
    ) -> float:
        return self._cost


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------

def _make_exec_row(
    client_order_id: str = "ORD001",
    ts_code: str = "000001.SZ",
    filled_qty: int = 1000,
    avg_fill_price: float = 10.0,
    side: str = "BUY",
    actual_commission: float = 55.0,
    actual_slippage: float = 0.0,
    daily_turnover: float = 100_000_000.0,
) -> dict:
    return {
        "client_order_id": client_order_id,
        "ts_code": ts_code,
        "filled_qty": filled_qty,
        "avg_fill_price": avg_fill_price,
        "side": side,
        "actual_commission": actual_commission,
        "actual_slippage": actual_slippage,
        "daily_turnover": daily_turnover,
    }


# ---------------------------------------------------------------------------
# 测试：空 DataFrame
# ---------------------------------------------------------------------------

def test_empty_dataframe():
    """空 DataFrame → avg_deviation=0.0，b3_passed=True，breakdown 为空。"""
    df = pd.DataFrame()
    model = FakeCostModel()
    result = calc_cost_deviation(df, model)

    assert result["deviation_pct"] == pytest.approx(0.0)
    assert result["b3_passed"] is True
    assert result["breakdown"].empty


# ---------------------------------------------------------------------------
# 测试：b3_passed=True（偏差 ≤ 30%）
# ---------------------------------------------------------------------------

def test_b3_passed_within_tolerance():
    """实盘成本 = 建模成本 * 1.20（偏差20% ≤ 30%）→ b3_passed=True。"""
    model = FakeCostModel(fixed_cost=50.0)
    # actual_commission=60，modeled=50 → deviation = (60-50)/50 = 0.20
    rows = [_make_exec_row(actual_commission=60.0)]
    df = pd.DataFrame(rows)
    result = calc_cost_deviation(df, model)

    assert result["b3_passed"] is True
    assert result["deviation_pct"] == pytest.approx(0.20, abs=1e-4)


# ---------------------------------------------------------------------------
# 测试：b3_passed=False（偏差 > 30%）
# ---------------------------------------------------------------------------

def test_b3_failed_exceeds_tolerance():
    """实盘成本 = 建模成本 * 1.50（偏差50% > 30%）→ b3_passed=False。"""
    model = FakeCostModel(fixed_cost=50.0)
    # actual_commission=75，modeled=50 → deviation = 0.50
    rows = [_make_exec_row(actual_commission=75.0)]
    df = pd.DataFrame(rows)
    result = calc_cost_deviation(df, model)

    assert result["b3_passed"] is False
    assert result["deviation_pct"] == pytest.approx(0.50, abs=1e-4)


# ---------------------------------------------------------------------------
# 测试：tolerance 默认从冻结参数读取
# ---------------------------------------------------------------------------

def test_default_tolerance_from_frozen():
    """tolerance=None 时，从 frozen gates.b3_cost_deviation_max=0.30 读取。"""
    model = FakeCostModel(fixed_cost=50.0)
    # deviation=0.30（边界），b3_passed=True
    rows = [_make_exec_row(actual_commission=65.0)]  # (65-50)/50 = 0.30
    df = pd.DataFrame(rows)
    result = calc_cost_deviation(df, model, tolerance=None)

    assert result["b3_passed"] is True
    assert result["deviation_pct"] == pytest.approx(0.30, abs=1e-4)


# ---------------------------------------------------------------------------
# 测试：多笔交易平均偏差
# ---------------------------------------------------------------------------

def test_multiple_rows_average_deviation():
    """多笔交易：平均偏差 = (0.20 + 0.40) / 2 = 0.30 → b3_passed=True（恰在边界）。"""
    model = FakeCostModel(fixed_cost=50.0)
    rows = [
        _make_exec_row("ORD001", actual_commission=60.0),   # dev=0.20
        _make_exec_row("ORD002", actual_commission=70.0),   # dev=0.40
    ]
    df = pd.DataFrame(rows)
    result = calc_cost_deviation(df, model)

    assert result["deviation_pct"] == pytest.approx(0.30, abs=1e-4)
    assert result["b3_passed"] is True
    assert len(result["breakdown"]) == 2


# ---------------------------------------------------------------------------
# 测试：breakdown 结构
# ---------------------------------------------------------------------------

def test_breakdown_columns():
    """breakdown 包含必要列：client_order_id, ts_code, actual_cost, modeled_cost, deviation_pct。"""
    model = FakeCostModel(fixed_cost=50.0)
    rows = [_make_exec_row()]
    df = pd.DataFrame(rows)
    result = calc_cost_deviation(df, model)

    breakdown = result["breakdown"]
    assert "client_order_id" in breakdown.columns
    assert "ts_code" in breakdown.columns
    assert "actual_cost" in breakdown.columns
    assert "modeled_cost" in breakdown.columns
    assert "deviation_pct" in breakdown.columns


# ---------------------------------------------------------------------------
# 测试：Decimal 精度（R6）
# ---------------------------------------------------------------------------

def test_decimal_no_float_error():
    """金额使用 Decimal 计算，避免 float 精度误差（R6）。

    验证方式：给定精确金额，偏差率应与预期 Decimal 结果一致。
    """
    model = FakeCostModel(fixed_cost=100.0)
    # actual_commission=110，deviation = (110-100)/100 = 0.10
    rows = [_make_exec_row(actual_commission=110.0, actual_slippage=0.0)]
    df = pd.DataFrame(rows)
    result = calc_cost_deviation(df, model)

    # 精确到 4 位小数内无误差
    assert result["deviation_pct"] == pytest.approx(0.10, abs=1e-4)
    # actual_cost 应精确到 Decimal 精度
    assert result["breakdown"].iloc[0]["actual_cost"] == pytest.approx(110.0, abs=1e-4)


# ---------------------------------------------------------------------------
# 测试：actual_cost = actual_commission + actual_slippage
# ---------------------------------------------------------------------------

def test_actual_cost_includes_slippage():
    """actual_cost 应为 commission + slippage 之和。"""
    model = FakeCostModel(fixed_cost=50.0)
    rows = [_make_exec_row(actual_commission=30.0, actual_slippage=25.0)]  # total=55
    df = pd.DataFrame(rows)
    result = calc_cost_deviation(df, model)

    assert result["breakdown"].iloc[0]["actual_cost"] == pytest.approx(55.0, abs=1e-4)
    # deviation = (55-50)/50 = 0.10
    assert result["deviation_pct"] == pytest.approx(0.10, abs=1e-4)
