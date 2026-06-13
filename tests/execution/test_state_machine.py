"""15 态状态机测试（QS-C04 §一，§二）。

覆盖：
  - 枚举恰好 15 个
  - 合法转换通过
  - 非法转换抛 InvalidTransitionError
  - 终态（FILLED/CANCELLED/REJECTED）不可再转出
  - order_sizing 差量/取整/T+1
  - 幂等键去重
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from src.execution.state_machine import (
    TERMINAL_STATES,
    TRANSITIONS,
    InvalidTransitionError,
    OrderEvent,
    OrderState,
    OrderStateMachine,
    all_states,
    is_valid_transition,
)
from src.execution.order_sizing import (
    CurrentPosition,
    PositionTarget,
    SizingResult,
    compute_single_delta,
    round_to_lot,
)
from src.execution.idempotency import (
    IdempotencyRecord,
    _InMemoryIdempotencyCache,
    generate_client_order_id,
    make_idempotency_record,
)


# ---------------------------------------------------------------------------
# 1. 枚举恰好 15 个
# ---------------------------------------------------------------------------

def test_order_state_count_is_15():
    """OrderState 枚举必须恰好 15 个（QS-C04 §一 SSOT 唯一口径）。"""
    states = all_states()
    assert len(states) == 15, f"OrderState 应有 15 个，实际 {len(states)}: {states}"
    expected = {
        "IDLE", "TARGET_GEN", "RISK_CLIP", "ORDER_SIZING", "ORDER_INTENT",
        "PRE_FIRE_CHECK", "SUBMITTED", "LIVE", "PARTIAL", "CANCEL_REQUESTED",
        "FILLED", "CANCELLED", "REJECTED", "UNKNOWN", "RECONCILE",
    }
    actual_names = {s.value for s in states}
    assert actual_names == expected, f"状态名称不匹配: {actual_names ^ expected}"


def test_terminal_states_are_three():
    """终态恰好 3 个：FILLED、CANCELLED、REJECTED。"""
    assert TERMINAL_STATES == frozenset({
        OrderState.FILLED,
        OrderState.CANCELLED,
        OrderState.REJECTED,
    })


# ---------------------------------------------------------------------------
# 2. 合法转换通过（完整主链路）
# ---------------------------------------------------------------------------

def test_happy_path_idle_to_reconcile():
    """完整主链路：IDLE → TARGET_GEN → RISK_CLIP → ORDER_SIZING →
    ORDER_INTENT → PRE_FIRE_CHECK → SUBMITTED → LIVE → FILLED → RECONCILE → IDLE。
    """
    sm = OrderStateMachine()
    assert sm.state == OrderState.IDLE

    sm.transition(OrderEvent.NEW_TRADING_DAY)
    assert sm.state == OrderState.TARGET_GEN

    sm.transition(OrderEvent.TARGET_WEIGHT_RECEIVED)
    assert sm.state == OrderState.RISK_CLIP

    sm.transition(OrderEvent.RISK_PASSED)
    assert sm.state == OrderState.ORDER_SIZING

    sm.transition(OrderEvent.SIZING_DONE)
    assert sm.state == OrderState.ORDER_INTENT

    sm.transition(OrderEvent.IDEMPOTENT_NEW)
    assert sm.state == OrderState.PRE_FIRE_CHECK

    sm.transition(OrderEvent.PRECHECK_PASSED)
    assert sm.state == OrderState.SUBMITTED

    sm.transition(OrderEvent.BROKER_ACCEPTED)
    assert sm.state == OrderState.LIVE

    sm.transition(OrderEvent.FULL_FILL)
    assert sm.state == OrderState.FILLED

    # 终态进对账
    sm.transition(OrderEvent.EOD_TERMINAL)
    assert sm.state == OrderState.RECONCILE

    sm.transition(OrderEvent.RECONCILE_PASS)
    assert sm.state == OrderState.IDLE

    # 迁移历史长度应等于事件数
    assert len(sm.history) == 10


def test_partial_fill_path():
    """部分成交路径：SUBMITTED → PARTIAL → CANCEL_REQUESTED → CANCELLED → RECONCILE → IDLE。"""
    sm = OrderStateMachine(initial_state=OrderState.SUBMITTED)
    sm.transition(OrderEvent.PARTIAL_FILL)
    assert sm.state == OrderState.PARTIAL

    sm.transition(OrderEvent.CANCEL_REQUESTED_EVT)
    assert sm.state == OrderState.CANCEL_REQUESTED

    sm.transition(OrderEvent.CANCEL_FILL_PARTIAL)
    assert sm.state == OrderState.CANCELLED

    sm.transition(OrderEvent.EOD_TERMINAL)
    assert sm.state == OrderState.RECONCILE

    sm.transition(OrderEvent.RECONCILE_PASS)
    assert sm.state == OrderState.IDLE


def test_broker_reject_path():
    """券商拒单路径：SUBMITTED → REJECTED → RECONCILE → IDLE。"""
    sm = OrderStateMachine(initial_state=OrderState.SUBMITTED)
    sm.transition(OrderEvent.BROKER_REJECTED)
    assert sm.state == OrderState.REJECTED

    sm.transition(OrderEvent.EOD_TERMINAL)
    assert sm.state == OrderState.RECONCILE

    sm.transition(OrderEvent.RECONCILE_PASS)
    assert sm.state == OrderState.IDLE


def test_unknown_recovery():
    """UNKNOWN 态归位：SUBMITTED → UNKNOWN → FILLED（via transition_unknown_resolved）。"""
    sm = OrderStateMachine(initial_state=OrderState.SUBMITTED)
    sm.transition(OrderEvent.SUBMIT_TIMEOUT)
    assert sm.state == OrderState.UNKNOWN

    # 连续查询一致后归位
    new_state = sm.transition_unknown_resolved(OrderState.FILLED)
    assert new_state == OrderState.FILLED
    assert sm.state == OrderState.FILLED


def test_unknown_unresolved_stays_unknown():
    """UNKNOWN_UNRESOLVED 事件应维持 UNKNOWN 态。"""
    sm = OrderStateMachine(initial_state=OrderState.UNKNOWN)
    sm.transition(OrderEvent.UNKNOWN_UNRESOLVED)
    assert sm.state == OrderState.UNKNOWN


def test_idempotent_duplicate_no_op():
    """IDEMPOTENT_DUPLICATE 事件下 ORDER_INTENT 不迁移（no-op）。"""
    sm = OrderStateMachine(initial_state=OrderState.ORDER_INTENT)
    result = sm.transition(OrderEvent.IDEMPOTENT_DUPLICATE)
    assert result == OrderState.ORDER_INTENT
    assert sm.state == OrderState.ORDER_INTENT


def test_gtc_order_live_self_loop():
    """GTC 订单 EOD：LIVE → LIVE（自环，跨日维持）。"""
    sm = OrderStateMachine(initial_state=OrderState.LIVE)
    sm.transition(OrderEvent.EOD_GTC_ORDER)
    assert sm.state == OrderState.LIVE


def test_break_glass_from_any_non_terminal():
    """BREAK_GLASS 可从任意非终态触发，进入 RECONCILE。"""
    for state in OrderState:
        if state in TERMINAL_STATES:
            continue
        sm = OrderStateMachine(initial_state=state)
        sm.trigger_break_glass()
        assert sm.state == OrderState.RECONCILE, f"BREAK_GLASS from {state} should -> RECONCILE"


# ---------------------------------------------------------------------------
# 3. 非法转换抛 InvalidTransitionError
# ---------------------------------------------------------------------------

def test_invalid_transition_raises():
    """非法状态转换应抛 InvalidTransitionError。"""
    sm = OrderStateMachine(initial_state=OrderState.IDLE)
    with pytest.raises(InvalidTransitionError):
        sm.transition(OrderEvent.BROKER_ACCEPTED)  # IDLE 无法直接 BROKER_ACCEPTED


def test_invalid_transition_live_to_target_gen():
    """LIVE 态无法直接跳到 NEW_TRADING_DAY。"""
    sm = OrderStateMachine(initial_state=OrderState.LIVE)
    with pytest.raises(InvalidTransitionError):
        sm.transition(OrderEvent.NEW_TRADING_DAY)


def test_multiple_invalid_transitions():
    """多个非法转换均应抛 InvalidTransitionError。"""
    invalid_pairs = [
        (OrderState.IDLE, OrderEvent.FULL_FILL),
        (OrderState.TARGET_GEN, OrderEvent.BROKER_ACCEPTED),
        (OrderState.RISK_CLIP, OrderEvent.FULL_FILL),
        (OrderState.ORDER_SIZING, OrderEvent.PARTIAL_FILL),
        (OrderState.PRE_FIRE_CHECK, OrderEvent.FULL_FILL),
        (OrderState.SUBMITTED, OrderEvent.SIZING_DONE),
    ]
    for state, event in invalid_pairs:
        sm = OrderStateMachine(initial_state=state)
        assert not is_valid_transition(state, event), f"{state}+{event} should be invalid"
        with pytest.raises(InvalidTransitionError):
            sm.transition(event)


# ---------------------------------------------------------------------------
# 4. 终态不可再转出（EOD_TERMINAL 例外）
# ---------------------------------------------------------------------------

def test_terminal_states_cannot_transition_except_eod():
    """终态（FILLED/CANCELLED/REJECTED）只允许 EOD_TERMINAL，其他事件抛异常。"""
    for terminal in TERMINAL_STATES:
        sm = OrderStateMachine(initial_state=terminal)

        # EOD_TERMINAL 应通过
        sm_copy = OrderStateMachine(initial_state=terminal)
        sm_copy.transition(OrderEvent.EOD_TERMINAL)
        assert sm_copy.state == OrderState.RECONCILE

        # 其他事件应被拒绝
        for event in [OrderEvent.NEW_TRADING_DAY, OrderEvent.BROKER_ACCEPTED, OrderEvent.FULL_FILL]:
            sm2 = OrderStateMachine(initial_state=terminal)
            with pytest.raises(InvalidTransitionError):
                sm2.transition(event)


def test_terminal_is_terminal_property():
    """is_terminal() 在终态应返回 True，非终态返回 False。"""
    for state in OrderState:
        sm = OrderStateMachine(initial_state=state)
        if state in TERMINAL_STATES:
            assert sm.is_terminal()
        else:
            assert not sm.is_terminal()


# ---------------------------------------------------------------------------
# 5. order_sizing：差量/取整/T+1
# ---------------------------------------------------------------------------

def test_round_to_lot():
    """round_to_lot 应向下取整到 100 股整数倍。"""
    assert round_to_lot(0) == 0
    assert round_to_lot(50) == 0
    assert round_to_lot(100) == 100
    assert round_to_lot(150) == 100
    assert round_to_lot(199) == 100
    assert round_to_lot(200) == 200
    assert round_to_lot(1234) == 1200


def test_sizing_buy_basic():
    """差量为正（买入）：正确计算并取整。"""
    result = compute_single_delta(
        ts_code="000001.SZ",
        strategy_id="s01",
        target_qty=500,
        current_qty=0,
        sellable_qty=0,
        reference_price=Decimal("10.00"),
        available_cash=Decimal("10000"),
    )
    assert result.side == "BUY"
    assert result.order_qty == 500
    assert result.raw_delta == 500
    assert not result.sellable_constrained


def test_sizing_sell_t1_constraint():
    """卖出差量受 T+1 约束：可卖量不足时应裁剪。"""
    result = compute_single_delta(
        ts_code="000001.SZ",
        strategy_id="s01",
        target_qty=0,
        current_qty=1000,
        sellable_qty=300,   # T+1 只能卖 300
        reference_price=Decimal("10.00"),
        available_cash=Decimal("0"),
    )
    assert result.side == "SELL"
    assert result.order_qty == 300  # 300 已是 100 整数倍
    assert result.sellable_constrained is True


def test_sizing_hold_zero_delta():
    """差量为零，应返回 HOLD。"""
    result = compute_single_delta(
        ts_code="000001.SZ",
        strategy_id="s01",
        target_qty=500,
        current_qty=500,
        sellable_qty=500,
        reference_price=Decimal("10.00"),
        available_cash=Decimal("10000"),
    )
    assert result.side == "HOLD"
    assert result.order_qty == 0


def test_sizing_buy_cash_constraint():
    """可用资金不足时应裁剪买入量。"""
    result = compute_single_delta(
        ts_code="000001.SZ",
        strategy_id="s01",
        target_qty=1000,
        current_qty=0,
        sellable_qty=0,
        reference_price=Decimal("10.00"),
        available_cash=Decimal("3000"),  # 只够买 300 股（含 2% 保留 = 2940）
        cash_reservation_ratio=Decimal("0.02"),
    )
    # usable = 3000 * 0.98 = 2940, max_qty = 294, round_to_lot = 200
    assert result.side == "BUY"
    assert result.order_qty <= 300
    assert result.order_qty % 100 == 0  # 100 股整数倍


def test_sizing_sell_lot_rounding():
    """卖出差量取整：不足 100 股时应 HOLD（不卖出）。"""
    result = compute_single_delta(
        ts_code="000001.SZ",
        strategy_id="s01",
        target_qty=0,
        current_qty=50,  # 只有 50 股（不足一手）
        sellable_qty=50,
        reference_price=Decimal("10.00"),
        available_cash=Decimal("0"),
    )
    assert result.side == "HOLD"
    assert result.order_qty == 0


# ---------------------------------------------------------------------------
# 6. 幂等键去重
# ---------------------------------------------------------------------------

def test_generate_client_order_id_deterministic():
    """相同输入生成相同幂等键（deterministic HMAC）。"""
    id1 = generate_client_order_id(
        account_id="acc01", strategy_id="s01",
        trade_date="2024-03-01", ts_code="000001.SZ",
        side="BUY", rebalance_seq=1,
    )
    id2 = generate_client_order_id(
        account_id="acc01", strategy_id="s01",
        trade_date="2024-03-01", ts_code="000001.SZ",
        side="BUY", rebalance_seq=1,
    )
    assert id1 == id2
    assert len(id1) == 64  # 32 字节 hex = 64 字符


def test_generate_client_order_id_different_seq():
    """不同 rebalance_seq 应生成不同幂等键。"""
    id1 = generate_client_order_id("acc01", "s01", "2024-03-01", "000001.SZ", "BUY", 1)
    id2 = generate_client_order_id("acc01", "s01", "2024-03-01", "000001.SZ", "BUY", 2)
    assert id1 != id2


def test_in_memory_idempotency_cache_dedup():
    """内存缓存：首次注册返回 True，重复注册返回 False。"""
    cache = _InMemoryIdempotencyCache()
    key = str(uuid.uuid4())

    assert cache.register(key) is True   # 首次
    assert cache.register(key) is False  # 重复
    assert cache.exists(key) is True
    assert cache.size() == 1


def test_in_memory_cache_clear():
    """clear() 应清空缓存。"""
    cache = _InMemoryIdempotencyCache()
    key = str(uuid.uuid4())
    cache.register(key)
    assert cache.size() == 1
    cache.clear()
    assert cache.size() == 0


def test_idempotency_store_with_sqlite(tmp_path):
    """IdempotencyStore 写入 SQLite，UNIQUE 约束防重复。"""
    import sqlite3 as sqlite3_mod
    from src.execution.idempotency import IdempotencyStore

    db_path = tmp_path / "test_idempotency.db"
    store = IdempotencyStore(db_path)

    key = generate_client_order_id("acc01", "s01", "2024-03-01", "000001.SZ", "BUY", 42)
    record = make_idempotency_record(key, "acc01", "s01", "000001.SZ", "BUY", "2024-03-01", 42)

    assert store.register(record) is True   # 首次
    assert store.register(record) is False  # 重复（INSERT OR IGNORE）
    assert store.exists(key) is True
    retrieved = store.get(key)
    assert retrieved is not None
    assert retrieved.client_order_id == key
    store.close()


def test_state_machine_history_tracking():
    """OrderStateMachine 应记录每次迁移的完整历史。"""
    sm = OrderStateMachine()
    sm.transition(OrderEvent.NEW_TRADING_DAY)
    sm.transition(OrderEvent.TARGET_WEIGHT_RECEIVED)

    history = sm.history
    assert len(history) == 2
    assert history[0] == (OrderState.IDLE, OrderEvent.NEW_TRADING_DAY, OrderState.TARGET_GEN)
    assert history[1] == (OrderState.TARGET_GEN, OrderEvent.TARGET_WEIGHT_RECEIVED, OrderState.RISK_CLIP)
