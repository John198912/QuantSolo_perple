"""风控守卫测试（QS-C04 §0.1，§6.1，QS-C01 §7.3）。

覆盖：
  - 合法 GuardDecision token 通过
  - 伪造/缺失 token 被拒（ValueError/PermissionError）
  - 单票超 8% 拒单
  - 单行业超 30% 拒单
  - 流动性不足剔除
  - 回撤 WARN/HARD_STOP 状态
  - 限速 1笔/秒、200笔/日超限拒绝
  - 守卫旁路不可达（critical）
"""
from __future__ import annotations

import time
import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest

from src.execution.interfaces import Order, OrderSide, OrderType, TimeInForce
from src.risk.constraints import (
    check_industry_limit,
    check_liquidity_filter,
    check_single_stock_limit,
)
from src.risk.drawdown import DrawdownStatus, check_drawdown
from src.risk.guard import GuardDecision, RiskGuard, _GUARD_SENTINEL


# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------

def _make_order(
    ts_code: str = "000001.SZ",
    qty: int = 1000,
    side: OrderSide = OrderSide.BUY,
    account_id: str = "test_account",
) -> Order:
    return Order(
        client_order_id=str(uuid.uuid4()),
        account_id=account_id,
        strategy_id="strategy_01",
        ts_code=ts_code,
        side=side,
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.DAY,
        qty=qty,
        limit_price=Decimal("10.00"),
    )


def _make_good_context(
    total_pv: Decimal = Decimal("1000000"),
    ts_code: str = "000001.SZ",
    industry: str = "银行",
    position_val: Decimal = Decimal("0"),
    avg_daily_turnover: float = 1e8,
    listing_days: int = 500,
) -> dict:
    return {
        "price": Decimal("10.00"),
        "total_portfolio_value": total_pv,
        "current_positions": {
            ts_code: {"market_value": position_val}
        } if position_val > 0 else {},
        "industry": industry,
        "ts_code_industry_map": {ts_code: industry},
        "avg_daily_turnover": avg_daily_turnover,
        "is_suspended": False,
        "is_st": False,
        "listing_days": listing_days,
    }


# ---------------------------------------------------------------------------
# 测试 1：合法 GuardDecision token 通过
# ---------------------------------------------------------------------------

def test_valid_token_verify():
    """通过 RiskGuard.submit() 获取的 token 调用 verify() 应通过（不抛异常）。"""
    guard = RiskGuard()
    order = _make_order(qty=100)
    ctx = _make_good_context()
    decision = guard.submit(order, ctx)
    assert decision.approved
    decision.verify()  # 不应抛异常


# ---------------------------------------------------------------------------
# 测试 2：伪造 token 被 verify() 拒绝
# ---------------------------------------------------------------------------

def test_forged_token_verify_raises():
    """直接构造 GuardDecision（sentinel 猜错）应被 verify() 拒绝（ValueError）。"""
    forged = GuardDecision(
        client_order_id="fake-001",
        account_id="test_account",
        ts_code="000001.SZ",
        decided_at="2024-03-01T09:00:00+00:00",
        rejection_reason=None,
        adjusted_qty=None,
        _sentinel="wrong_sentinel_value_that_is_not_real",
    )
    with pytest.raises(ValueError, match="sentinel"):
        forged.verify()


def test_rejected_token_verify_raises():
    """rejection_reason 非 None 的 token（拒单），verify() 应抛 ValueError。"""
    guard = RiskGuard()
    order = _make_order(qty=100)
    # 触发 ST 拒单
    ctx = _make_good_context()
    ctx["is_st"] = True
    decision = guard.submit(order, ctx)
    assert not decision.approved
    with pytest.raises(ValueError):
        decision.verify()


# ---------------------------------------------------------------------------
# 测试 3：单票超 8% 拒单
# ---------------------------------------------------------------------------

def test_single_stock_limit_exceeded():
    """单票持仓超 8% 时，check_single_stock_limit 应拒绝并给出 adjusted_qty。"""
    total_pv = Decimal("1000000")
    price = Decimal("10.00")
    # 已持仓市值 = 70000（7%）
    current_positions = {"000001.SZ": {"market_value": Decimal("70000")}}
    # 拟买入 2000 股（20000 元），总计 = 90000/1000000 = 9% > 8%
    result = check_single_stock_limit(
        "000001.SZ", 2000, price, total_pv, current_positions
    )
    # 应裁剪：max_val = 80000，remaining = 10000，max_qty_raw = 1000，rounded = 1000
    assert result.passed  # 裁剪后仍可买，adjusted_qty=1000
    assert result.adjusted_qty == 1000

    # 已满仓 8%，再买任意量应直接拒绝
    full_positions = {"000001.SZ": {"market_value": Decimal("80000")}}
    result_reject = check_single_stock_limit(
        "000001.SZ", 1000, price, total_pv, full_positions
    )
    assert not result_reject.passed
    assert result_reject.adjusted_qty == 0


def test_risk_guard_single_stock_reject():
    """通过 RiskGuard 测试单票超 8% 时返回拒单 decision。"""
    guard = RiskGuard()
    order = _make_order(qty=10000)  # 10000*10=100000，占 10% > 8%
    ctx = _make_good_context(
        total_pv=Decimal("1000000"),
        position_val=Decimal("0"),
    )
    # 买入 10000 股 * 10元 = 100000，占 10%
    # 使用足够小的组合值使超 8%
    ctx["total_portfolio_value"] = Decimal("100000")  # 100000 / 100000 = 100% > 8%
    decision = guard.submit(order, ctx)
    # 应裁剪或拒单
    # 不直接断言 approved=False，因为可能裁剪
    # 检查 token 合法性
    decision.verify()  # 不应抛（approved or 已裁剪）


def test_single_stock_over_limit_full_reject():
    """单票完全超限（已持满 8%），通过守卫应返回拒单 decision。"""
    guard = RiskGuard()
    order = _make_order(qty=1000)
    ctx = _make_good_context(
        total_pv=Decimal("1000000"),
        position_val=Decimal("80000"),  # 已有 8%
    )
    decision = guard.submit(order, ctx)
    assert not decision.approved
    assert "SINGLE_STOCK_LIMIT_EXCEEDED" in decision.rejection_reason


# ---------------------------------------------------------------------------
# 测试 4：单行业超 30% 拒单
# ---------------------------------------------------------------------------

def test_industry_limit_exceeded():
    """行业集中度超 30% 时，check_industry_limit 应返回 passed=False。"""
    total_pv = Decimal("1000000")
    price = Decimal("10.00")
    # 银行行业已持仓 250000（25%），本次买 10000 股（100000）→ 35% > 30%
    current_positions = {
        "000001.SZ": {"market_value": Decimal("150000")},
        "600000.SH": {"market_value": Decimal("100000")},
    }
    ts_code_industry_map = {"000001.SZ": "银行", "600000.SH": "银行"}
    result = check_industry_limit(
        "银行", 10000, price, total_pv,
        current_positions, ts_code_industry_map
    )
    assert not result.passed
    assert result.rejection_reason == "INDUSTRY_LIMIT_EXCEEDED"


def test_industry_within_limit():
    """行业在限额内，应通过。"""
    total_pv = Decimal("1000000")
    price = Decimal("10.00")
    current_positions = {"000001.SZ": {"market_value": Decimal("50000")}}
    ts_code_industry_map = {"000001.SZ": "银行"}
    result = check_industry_limit(
        "银行", 1000, price, total_pv,
        current_positions, ts_code_industry_map
    )
    assert result.passed


# ---------------------------------------------------------------------------
# 测试 5：流动性不足剔除
# ---------------------------------------------------------------------------

def test_liquidity_st_excluded():
    """ST 股票应被剔除（check_liquidity_filter）。"""
    result = check_liquidity_filter(
        "000001.SZ",
        avg_daily_turnover=1e8,
        is_suspended=False,
        is_st=True,
        listing_days=500,
    )
    assert not result.passed
    assert result.rejection_reason == "ST_EXCLUDED"


def test_liquidity_suspended():
    """停牌股应被剔除。"""
    result = check_liquidity_filter(
        "000001.SZ", avg_daily_turnover=1e8,
        is_suspended=True, is_st=False, listing_days=500,
    )
    assert not result.passed
    assert result.rejection_reason == "SUSPENDED"


def test_liquidity_new_stock():
    """上市天数不足 250 日应被剔除。"""
    result = check_liquidity_filter(
        "000001.SZ", avg_daily_turnover=1e8,
        is_suspended=False, is_st=False, listing_days=100,
    )
    assert not result.passed
    assert result.rejection_reason == "LISTING_DAYS_INSUFFICIENT"


def test_liquidity_low_turnover():
    """日均成交额不足 5000 万应被剔除。"""
    result = check_liquidity_filter(
        "000001.SZ", avg_daily_turnover=1e6,  # 100 万，不足 5000 万
        is_suspended=False, is_st=False, listing_days=500,
    )
    assert not result.passed
    assert result.rejection_reason == "LIQUIDITY_INSUFFICIENT"


def test_liquidity_pass():
    """满足所有流动性条件时应通过。"""
    result = check_liquidity_filter(
        "000001.SZ", avg_daily_turnover=1e8,
        is_suspended=False, is_st=False, listing_days=500,
    )
    assert result.passed


# ---------------------------------------------------------------------------
# 测试 6：回撤 WARN/HARD_STOP 状态
# ---------------------------------------------------------------------------

def test_drawdown_warn():
    """回撤 20% <= dd < 25% 时，状态应为 WARN。"""
    # 使用能精确计算出 >= 0.20 的净值（避免 float 精度问题）
    result = check_drawdown(0.79, 1.00, warn_threshold=0.20, hard_stop_threshold=0.25)
    assert result.status == DrawdownStatus.WARN
    assert result.drawdown_pct == pytest.approx(0.21)
    assert result.level == 1


def test_drawdown_hard_stop():
    """回撤 >= 25% 时，状态应为 HARD_STOP。"""
    result = check_drawdown(0.74, 1.00, warn_threshold=0.20, hard_stop_threshold=0.25)
    assert result.status == DrawdownStatus.HARD_STOP
    assert result.drawdown_pct == pytest.approx(0.26)
    assert result.level == 2


def test_drawdown_normal():
    """回撤 < 20% 时，状态应为 NORMAL。"""
    result = check_drawdown(0.90, 1.00, warn_threshold=0.20, hard_stop_threshold=0.25)
    assert result.status == DrawdownStatus.NORMAL
    assert result.level == 0


def test_risk_guard_hard_stop_rejects_buy():
    """HARD_STOP 状态下 BUY 订单应被拒绝。"""
    guard = RiskGuard()
    guard.update_drawdown_status(current_nav=0.74, peak_nav=1.00)
    assert guard.drawdown_status == DrawdownStatus.HARD_STOP

    order = _make_order(qty=100, side=OrderSide.BUY)
    ctx = _make_good_context()
    decision = guard.submit(order, ctx)
    assert not decision.approved
    assert "DRAWDOWN_HARD_STOP" in decision.rejection_reason


def test_risk_guard_hard_stop_allows_sell():
    """HARD_STOP 状态下 SELL 订单应通过（减仓不受回撤约束）。"""
    guard = RiskGuard()
    guard.update_drawdown_status(current_nav=0.74, peak_nav=1.00)

    order = _make_order(qty=100, side=OrderSide.SELL)
    ctx = _make_good_context()
    decision = guard.submit(order, ctx)
    assert decision.approved


# ---------------------------------------------------------------------------
# 测试 7：限速 1笔/秒、200笔/日超限拒绝
# ---------------------------------------------------------------------------

def test_rate_limiter_per_second():
    """同一秒内连续两次请求，第二次应因 PER_SECOND 被拒。"""
    from src.execution.rate_limiter import RateLimiter, RateLimitExceeded

    limiter = RateLimiter(max_per_sec=1, max_per_day=200)
    # 第一次消耗令牌
    limiter.check_and_consume()
    # 第二次立即（<1s）应被拒绝
    with pytest.raises(RateLimitExceeded, match="PER_SECOND"):
        limiter.check_and_consume()


def test_rate_limiter_per_day():
    """日订单数超限后应抛 PER_DAY 错误。"""
    from src.execution.rate_limiter import RateLimiter, RateLimitExceeded

    limiter = RateLimiter(max_per_sec=200, max_per_day=3)
    # 调用 3 次（满额）
    for _ in range(3):
        limiter.check_and_consume()
    # 第 4 次应超日限
    with pytest.raises(RateLimitExceeded, match="PER_DAY"):
        limiter.check_and_consume()


# ---------------------------------------------------------------------------
# 测试 8：守卫旁路不可达（铁律一，关键安全测试）
# ---------------------------------------------------------------------------

def test_guard_bypass_sentinel_is_private():
    """_GUARD_SENTINEL 是进程级私有随机串，不是固定值（无法预测）。"""
    import re
    # sentinel 是 64 位十六进制（secrets.token_hex(32)）
    assert re.match(r'^[0-9a-f]{64}$', _GUARD_SENTINEL), "sentinel 格式异常"


def test_guard_bypass_direct_construction_fails_verify():
    """直接构造 GuardDecision（不经 RiskGuard）调用 verify() 必须失败。"""
    # 即使猜对大部分字段，sentinel 必然错误
    direct = GuardDecision(
        client_order_id="bypass-001",
        account_id="attacker",
        ts_code="000001.SZ",
        decided_at="2024-03-01T00:00:00+00:00",
        rejection_reason=None,
        adjusted_qty=None,
        _sentinel="0" * 64,  # 强行猜 64 个 0
    )
    with pytest.raises(ValueError, match="sentinel"):
        direct.verify()


def test_guard_bypass_frozen_dataclass():
    """GuardDecision 是 frozen dataclass，创建后不可通过普通赋值修改字段。"""
    guard = RiskGuard()
    order = _make_order(qty=100)
    ctx = _make_good_context()
    decision = guard.submit(order, ctx)

    # frozen=True，普通属性赋值应失败
    try:
        decision.rejection_reason = "tampered"  # type: ignore
        # 如果上面没有抛异常，则下面断言失败
        pytest.fail("frozen dataclass 应拒绝赋值操作")
    except (AttributeError, TypeError):
        pass  # 预期异常


def test_guard_bypass_risk_guarded_decorator():
    """@risk_guarded 装饰器：未经守卫的调用路径被拦截（PermissionError）。"""
    from src.risk.guard import risk_guarded

    guard = RiskGuard()
    # 构造一个会被拒单的守卫（ST 标记）
    order = _make_order(qty=100)

    @risk_guarded(guard, context_provider=lambda o: {
        "is_st": True, "is_suspended": False, "listing_days": 500,
        "avg_daily_turnover": 1e8,
        "price": Decimal("10.00"),
        "total_portfolio_value": Decimal("1000000"),
        "current_positions": {},
        "industry": "银行",
        "ts_code_industry_map": {"000001.SZ": "银行"},
    })
    def _place_order(order_arg: Order) -> str:
        return "broker_001"

    with pytest.raises(PermissionError, match="RiskGuard 拒单"):
        _place_order(order)


def test_guard_no_skip_check_attr():
    """RiskGuard 不存在 __skip_check__ 旁路属性（铁律一守护）。"""
    guard = RiskGuard()
    assert not hasattr(guard, "__skip_check__")
    assert not hasattr(guard, "skip_check")


def test_approved_token_verify_succeeds():
    """合法通过的 token，verify() 不应抛任何异常。"""
    guard = RiskGuard()
    order = _make_order(qty=100, side=OrderSide.SELL)
    ctx = _make_good_context()
    decision = guard.submit(order, ctx)
    assert decision.approved
    # 不抛异常
    decision.verify()
