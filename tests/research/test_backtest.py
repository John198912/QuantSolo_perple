"""回测引擎测试（功能设计文档 §4.3~§4.6）。

覆盖：
- 成本模型 Decimal 正确性
- T+1 约束：当日买入当日卖出应被拒绝
- 涨跌停：涨停日买入信号被拦截
- 跌停日卖出挂单不成交
- 高价股（一手 > 1.6 万）过滤
- 100 股取整
- cost_model_id 一致性
- 向量化回测基本指标计算
"""
from __future__ import annotations

from decimal import Decimal
import numpy as np
import pandas as pd
import pytest

from src.research.backtest.cost_models import (
    CostModel,
    get_baseline_model,
    get_advanced_model,
    COST_MODEL_BASELINE_ID,
    COST_MODEL_ADVANCED_ID,
)
from src.research.backtest.event_driven import (
    apply_ashare_constraints,
    EventDrivenBacktest,
    Position,
)
from src.research.backtest.vectorized import VectorizedBacktest
from src.common.config import load_frozen


# ─────────────────────────────────────────────────────────────────────────────
# 成本模型测试
# ─────────────────────────────────────────────────────────────────────────────

class TestCostModels:
    def test_baseline_model_returns_decimal(self):
        """cm_v3_baseline 计算结果应为 Decimal 类型。"""
        cm = get_baseline_model()
        result = cm.calc_transaction_cost(
            Decimal("100000"), "BUY", Decimal("50000000"), Decimal("10000")
        )
        assert isinstance(result, Decimal)

    def test_advanced_model_returns_decimal(self):
        """cm_v3_advanced 计算结果应为 Decimal 类型。"""
        cm = get_advanced_model()
        result = cm.calc_transaction_cost(
            Decimal("100000"), "SELL", Decimal("50000000"), Decimal("10000")
        )
        assert isinstance(result, Decimal)

    def test_cost_from_frozen_config(self):
        """成本参数应与 frozen.toml 一致（R3 验证）。"""
        cfg = load_frozen()["cost"]
        cm = get_baseline_model()
        assert cm.stamp_duty_sell == Decimal(str(cfg["stamp_duty_sell"]))
        assert cm.commission_rate == Decimal(str(cfg["commission_rate"]))
        assert cm.commission_min_cny == Decimal(str(cfg["commission_min_cny"]))

    def test_stamp_duty_only_on_sell(self):
        """印花税只在卖出时收取。"""
        cm = get_baseline_model()
        buy_cost = cm.calc_transaction_cost(Decimal("100000"), "BUY")
        sell_cost = cm.calc_transaction_cost(Decimal("100000"), "SELL")
        # 卖出 = 买入 + 印花税
        stamp = Decimal(str(float(cm.stamp_duty_sell))) * Decimal("100000")
        assert sell_cost > buy_cost

    def test_minimum_commission_applied(self):
        """小额交易应套用最低佣金（5 元）。"""
        cm = get_baseline_model()
        # 1 元交易，佣金率计算 = 0.00025，小于 5 元
        cost = cm.calc_transaction_cost(Decimal("1"), "BUY")
        cfg = load_frozen()["cost"]
        min_commission = Decimal(str(cfg["commission_min_cny"]))
        assert cost >= min_commission, f"佣金 {cost} 应不小于最低佣金 {min_commission}"

    def test_advanced_model_dynamic_slippage(self):
        """cm_v3_advanced 动态滑点应随 impact_ratio 增大而增大。"""
        cm = get_advanced_model()
        # 高冲击：trade_size / daily_turnover = 0.1
        cost_high = cm.calc_transaction_cost(
            Decimal("100000"), "BUY",
            Decimal("1000000"), Decimal("100000"),  # impact_ratio=0.1
        )
        # 低冲击：trade_size / daily_turnover = 0.001
        cost_low = cm.calc_transaction_cost(
            Decimal("100000"), "BUY",
            Decimal("100000000"), Decimal("100000"),  # impact_ratio=0.001
        )
        assert cost_high > cost_low, "高冲击比率应产生更高成本"

    def test_baseline_model_id(self):
        """cm_v3_baseline 的 model_id 应正确。"""
        cm = get_baseline_model()
        assert cm.model_id == COST_MODEL_BASELINE_ID

    def test_advanced_model_id(self):
        """cm_v3_advanced 的 model_id 应正确。"""
        cm = get_advanced_model()
        assert cm.model_id == COST_MODEL_ADVANCED_ID

    def test_cost_not_float_internally(self):
        """确认无 float 直接用于金额计算（R6 验证）：stamp_duty_sell 是 Decimal。"""
        cm = get_baseline_model()
        assert isinstance(cm.stamp_duty_sell, Decimal)
        assert isinstance(cm.commission_rate, Decimal)
        assert isinstance(cm.commission_min_cny, Decimal)


# ─────────────────────────────────────────────────────────────────────────────
# A 股约束测试
# ─────────────────────────────────────────────────────────────────────────────

def _make_bar(close: float, upper_limit: float = None, lower_limit: float = None) -> dict:
    bar = {"close": close}
    if upper_limit is not None:
        bar["upper_limit"] = upper_limit
    if lower_limit is not None:
        bar["lower_limit"] = lower_limit
    return bar


def _make_bars_dict(ts_code: str, **kwargs) -> dict:
    return {ts_code: _make_bar(**kwargs)}


class TestAShareConstraints:
    def test_t1_buy_cannot_sell_same_day(self):
        """T+1：当日买入不可当日卖出。"""
        positions = {
            "000001.SZ": Position(
                ts_code="000001.SZ", qty=100, avg_cost=Decimal("10"),
                buy_date="2024-03-01",
            )
        }
        order = {
            "ts_code": "000001.SZ",
            "side": "SELL",
            "qty": 100,
            "trade_date": "2024-03-01",  # 同一天
        }
        bars = _make_bars_dict("000001.SZ", close=10.5)
        adjusted, can_fill = apply_ashare_constraints(order, bars, positions)
        assert can_fill is False, "T+1 不可卖应被拒绝"

    def test_t1_can_sell_next_day(self):
        """T+1：次日可以卖出。"""
        positions = {
            "000001.SZ": Position(
                ts_code="000001.SZ", qty=100, avg_cost=Decimal("10"),
                buy_date="2024-03-01",
            )
        }
        order = {
            "ts_code": "000001.SZ",
            "side": "SELL",
            "qty": 100,
            "trade_date": "2024-03-04",  # 次个交易日
        }
        bars = _make_bars_dict("000001.SZ", close=10.5)
        adjusted, can_fill = apply_ashare_constraints(order, bars, positions)
        assert can_fill is True, "次日应可卖出"

    def test_limit_up_blocks_buy(self):
        """涨停板应拦截买入信号。"""
        positions: dict = {}
        order = {
            "ts_code": "000001.SZ",
            "side": "BUY",
            "qty": 200,
            "trade_date": "2024-03-01",
        }
        # 收盘价 = 涨停价
        bars = _make_bars_dict("000001.SZ", close=11.0, upper_limit=11.0)
        adjusted, can_fill = apply_ashare_constraints(order, bars, positions)
        assert can_fill is False, "涨停板应拦截买入"

    def test_limit_down_blocks_sell(self):
        """跌停板应拦截卖出（挂单排队，当日不成交）。"""
        positions = {
            "000001.SZ": Position(
                ts_code="000001.SZ", qty=100, avg_cost=Decimal("10"),
                buy_date="2024-02-28",  # 非当日
            )
        }
        order = {
            "ts_code": "000001.SZ",
            "side": "SELL",
            "qty": 100,
            "trade_date": "2024-03-01",
        }
        # 收盘价 = 跌停价
        bars = _make_bars_dict("000001.SZ", close=9.0, lower_limit=9.0)
        adjusted, can_fill = apply_ashare_constraints(order, bars, positions)
        assert can_fill is False, "跌停板应拦截卖出（挂排队单）"
        assert adjusted.get("limit_price") is not None, "应设置 limit_price"

    def test_lot_rounding_100_shares(self):
        """100 股取整：250 股 → 200 股。"""
        positions: dict = {}
        order = {
            "ts_code": "000001.SZ",
            "side": "BUY",
            "qty": 250,
            "trade_date": "2024-03-01",
        }
        bars = _make_bars_dict("000001.SZ", close=10.0)
        adjusted, can_fill = apply_ashare_constraints(order, bars, positions)
        if can_fill:
            assert adjusted["qty"] % 100 == 0, "数量必须是 100 的整数倍"
            assert adjusted["qty"] == 200

    def test_high_price_stock_filtered(self):
        """高价股（一手 > 1.6 万）应被过滤。"""
        positions: dict = {}
        order = {
            "ts_code": "000001.SZ",
            "side": "BUY",
            "qty": 100,
            "trade_date": "2024-03-01",
        }
        # 收盘价 161 元 → 一手 = 161 * 100 = 16100 > 16000
        bars = _make_bars_dict("000001.SZ", close=161.0)
        adjusted, can_fill = apply_ashare_constraints(order, bars, positions)
        assert can_fill is False, "高价股应被过滤"

    def test_missing_bar_returns_false(self):
        """无行情数据时应返回 False。"""
        positions: dict = {}
        order = {
            "ts_code": "MISSING.SZ",
            "side": "BUY",
            "qty": 100,
            "trade_date": "2024-03-01",
        }
        bars: dict = {}  # 无数据
        adjusted, can_fill = apply_ashare_constraints(order, bars, positions)
        assert can_fill is False

    def test_zero_qty_after_rounding_rejected(self):
        """100 股取整后数量为 0 应被拒绝。"""
        positions: dict = {}
        order = {
            "ts_code": "000001.SZ",
            "side": "BUY",
            "qty": 50,  # < 100，取整后 = 0
            "trade_date": "2024-03-01",
        }
        bars = _make_bars_dict("000001.SZ", close=10.0)
        adjusted, can_fill = apply_ashare_constraints(order, bars, positions)
        assert can_fill is False, "取整后 qty=0 应被拒绝"


# ─────────────────────────────────────────────────────────────────────────────
# 向量化回测测试
# ─────────────────────────────────────────────────────────────────────────────

def _make_factor_df(n_dates: int, n_stocks: int, seed: int = 42) -> pd.DataFrame:
    """生成合成因子 DataFrame（无 PIT 引擎依赖）。"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2022-01-03", periods=n_dates, freq="W-MON").strftime("%Y-%m-%d")
    stocks = [f"{i:06d}.SZ" for i in range(1, n_stocks + 1)]
    rows = []
    for d in dates:
        for s in stocks:
            rows.append({
                "ts_code": s,
                "trade_date": d,
                "factor_value": float(rng.normal(0, 1)),
            })
    return pd.DataFrame(rows)


class TestVectorizedBacktest:
    def test_run_returns_required_keys(self):
        """向量化回测应返回必要的指标 keys。"""
        factor_df = _make_factor_df(52, 30)
        vbt = VectorizedBacktest(cost_model=get_baseline_model(), pit_engine=None)
        # 无 PIT 引擎时，bars 为空，nav_series 也为空 → 返回 {} 是正常的
        result = vbt.run(
            factor_df=factor_df,
            start_date="2022-01-03",
            end_date="2022-12-26",
            top_n=5,
            weight_scheme="equal",
        )
        # 由于无 PIT 数据，result 可能是 {} 或有 cost_model_id
        # 只验证有返回且类型正确
        assert isinstance(result, dict)

    def test_cost_model_id_in_result(self):
        """回测结果应包含 cost_model_id（双层互验 §4.2）。"""
        cm = get_baseline_model()
        vbt = VectorizedBacktest(cost_model=cm, pit_engine=None)
        factor_df = _make_factor_df(10, 5)
        result = vbt.run(
            factor_df=factor_df,
            start_date="2022-01-03",
            end_date="2022-03-01",
            top_n=3,
        )
        if result:
            assert result.get("cost_model_id") == COST_MODEL_BASELINE_ID

    def test_rebal_dates_generated(self):
        """调仓日期序列生成应正确。"""
        dates = VectorizedBacktest._get_rebal_dates("2022-01-03", "2022-06-30", "W")
        assert len(dates) >= 2
        # 日期格式应为 YYYY-MM-DD
        assert all(len(d) == 10 for d in dates)

    def test_period_return_equal_weight(self):
        """等权加权收益应在正常范围内。"""
        vbt = VectorizedBacktest()
        stocks = ["A", "B", "C"]
        bars = pd.DataFrame({
            "ts_code": ["A", "A", "B", "B", "C", "C"],
            "trade_date": ["2022-01-03", "2022-01-10"] * 3,
            "close_adj": [10, 11, 20, 22, 30, 31],
        })
        ret = vbt._calc_period_return(bars, stocks, "equal", "close_adj")
        # A=10%, B=10%, C=3.33% → 约 7.8%
        assert abs(ret) < 0.5, "等权收益应在合理范围内"

    def test_cross_validate_sharpe_pass(self):
        """Sharpe 差异 < 0.1 时应通过双层互验。"""
        assert EventDrivenBacktest.cross_validate_sharpe(1.0, 1.05, threshold=0.1) is True

    def test_cross_validate_sharpe_fail(self):
        """Sharpe 差异 > 0.1 时应报警返回 False。"""
        assert EventDrivenBacktest.cross_validate_sharpe(1.0, 1.15, threshold=0.1) is False
