"""15 运行态状态机（QS-C04 §一 · SSOT 唯一口径）。

状态全集恰好 15 个，不多不少。
合法转换表 TRANSITIONS 按 §二 完整构建。
非法转换抛 InvalidTransitionError。

核心不变量（§0.3）：
  - 终态（FILLED / CANCELLED / REJECTED）不可再迁移。
  - UNKNOWN 不等于错误，是"危险的未知"，须通过两次一致查询归位。
  - 暂停态通过 halt_reason 字段区分语义，不单设独立状态。
"""
from __future__ import annotations

from enum import Enum
from typing import FrozenSet, Optional


# ---------------------------------------------------------------------------
# §一：OrderState 枚举（恰好 15 个，SSOT 唯一口径）
# ---------------------------------------------------------------------------

class OrderState(str, Enum):
    """15 个运行态（QS-C04 §一 1.1 权威版本）。

    序号  状态代码            含义
    ----  ----------------  ------------------------------------------------
    1     IDLE              空闲等待：等待下一交易日信号，正常循环入口
    2     TARGET_GEN        目标持仓生成：策略层输出目标权重快照
    3     RISK_CLIP         风控裁剪：risk_guard 校验+裁剪（仓位/行业/流动性）
    4     ORDER_SIZING      订单定量：差量、T+1可卖、100股取整、现金约束
    5     ORDER_INTENT      订单意图：生成带幂等键的订单意图，outbox 先落 PENDING_SEND
    6     PRE_FIRE_CHECK    提交前再审：提交前轻量复核资金/行情/一字板（防 TOCTOU）
    7     SUBMITTED         已提交：已发送至券商，等待受理回报
    8     LIVE              已受理/挂单：券商已受理，限价单排队中（正常长存活）
    9     PARTIAL           部分成交：部分数量成交，剩余挂单中
    10    CANCEL_REQUESTED  撤单已请求：撤单指令已发，等待回报（撤单在途）
    11    FILLED            全部成交 [终态]：完全成交，进对账
    12    CANCELLED         已撤 [终态]：撤单成功（含部分成交后撤剩余），进对账
    13    REJECTED          失败 [终态]：券商拒单（资金/价格/权限），进对账
    14    UNKNOWN           未知态：查不到券商状态/连接异常（危险，须归位）
    15    RECONCILE         对账：比对理论/券商/下单三方
    """

    # --- 策略/风控流水线（§二 2.1）---
    IDLE = "IDLE"                     # 1 空闲等待
    TARGET_GEN = "TARGET_GEN"         # 2 目标持仓生成
    RISK_CLIP = "RISK_CLIP"           # 3 风控裁剪
    ORDER_SIZING = "ORDER_SIZING"     # 4 订单定量
    ORDER_INTENT = "ORDER_INTENT"     # 5 订单意图
    PRE_FIRE_CHECK = "PRE_FIRE_CHECK" # 6 提交前再审

    # --- 委托生命周期（§二 2.2）---
    SUBMITTED = "SUBMITTED"           # 7 已提交
    LIVE = "LIVE"                     # 8 已受理/挂单
    PARTIAL = "PARTIAL"               # 9 部分成交
    CANCEL_REQUESTED = "CANCEL_REQUESTED"  # 10 撤单已请求

    # --- 终态（§二 2.3）---
    FILLED = "FILLED"                 # 11 全部成交（终态，进对账）
    CANCELLED = "CANCELLED"           # 12 已撤（终态，进对账）
    REJECTED = "REJECTED"             # 13 失败（终态，进对账）

    # --- 特殊态（§二 2.3）---
    UNKNOWN = "UNKNOWN"               # 14 未知态（危险）
    RECONCILE = "RECONCILE"           # 15 对账


# 终态集合（进入后不可再迁移）
TERMINAL_STATES: FrozenSet[OrderState] = frozenset({
    OrderState.FILLED,
    OrderState.CANCELLED,
    OrderState.REJECTED,
})

# 暂停态：用 halt_reason 字段区分（BREAK_GLASS / WIND_CTRL_LV2 / MANUAL_REVIEW / REJECT_BREAKER）
# v1.3 设计：暂停语义内嵌在 RECONCILE（中间态）加 halt_reason 字段，不单设独立状态
PAUSABLE_STATES: FrozenSet[OrderState] = frozenset({
    OrderState.RISK_CLIP,
    OrderState.ORDER_SIZING,
    OrderState.ORDER_INTENT,
    OrderState.PRE_FIRE_CHECK,
    OrderState.SUBMITTED,
    OrderState.LIVE,
    OrderState.PARTIAL,
    OrderState.CANCEL_REQUESTED,
    OrderState.UNKNOWN,
    OrderState.RECONCILE,
    OrderState.IDLE,
    OrderState.TARGET_GEN,
})


# ---------------------------------------------------------------------------
# §二：合法转换表 TRANSITIONS
# ---------------------------------------------------------------------------

# 事件代码（str 常量，便于 transition() 参数传入）
class OrderEvent(str, Enum):
    """驱动状态迁移的事件枚举。"""
    # 交易日流水线
    NEW_TRADING_DAY = "NEW_TRADING_DAY"           # IDLE → TARGET_GEN
    UNRECONCILED_DETECTED = "UNRECONCILED_DETECTED"  # IDLE → RECONCILE
    TARGET_WEIGHT_RECEIVED = "TARGET_WEIGHT_RECEIVED"  # TARGET_GEN → RISK_CLIP
    TARGET_INVALID = "TARGET_INVALID"             # TARGET_GEN → PAUSE(MANUAL_REVIEW)
    RISK_PASSED = "RISK_PASSED"                   # RISK_CLIP → ORDER_SIZING
    RISK_REJECTED = "RISK_REJECTED"               # RISK_CLIP → PAUSE(MANUAL_REVIEW)
    DRAWDOWN_WARN = "DRAWDOWN_WARN"               # RISK_CLIP → ORDER_SIZING(降仓)
    DRAWDOWN_HARD_STOP = "DRAWDOWN_HARD_STOP"     # RISK_CLIP → ORDER_SIZING(清仓)
    SIZING_DONE = "SIZING_DONE"                   # ORDER_SIZING → ORDER_INTENT
    SIZING_FAILED = "SIZING_FAILED"               # ORDER_SIZING → PAUSE
    IDEMPOTENT_DUPLICATE = "IDEMPOTENT_DUPLICATE" # ORDER_INTENT → (no-op/terminate)
    IDEMPOTENT_NEW = "IDEMPOTENT_NEW"             # ORDER_INTENT → PRE_FIRE_CHECK
    PRECHECK_PASSED = "PRECHECK_PASSED"           # PRE_FIRE_CHECK → SUBMITTED
    PRECHECK_FAILED = "PRECHECK_FAILED"           # PRE_FIRE_CHECK → PAUSE
    # 委托生命周期
    BROKER_ACCEPTED = "BROKER_ACCEPTED"           # SUBMITTED → LIVE
    PARTIAL_FILL = "PARTIAL_FILL"                 # SUBMITTED/LIVE/PARTIAL → PARTIAL
    FULL_FILL = "FULL_FILL"                       # SUBMITTED/LIVE/PARTIAL → FILLED
    BROKER_REJECTED = "BROKER_REJECTED"           # SUBMITTED → REJECTED
    SUBMIT_TIMEOUT = "SUBMIT_TIMEOUT"             # SUBMITTED → UNKNOWN
    CANCEL_REQUESTED_EVT = "CANCEL_REQUESTED_EVT"  # LIVE/PARTIAL → CANCEL_REQUESTED
    ORDER_TTL_EXPIRED = "ORDER_TTL_EXPIRED"       # LIVE → CANCEL_REQUESTED
    CONNECTION_LOST = "CONNECTION_LOST"           # LIVE → UNKNOWN
    CANCEL_SUCCESS_NONE = "CANCEL_SUCCESS_NONE"   # CANCEL_REQUESTED → CANCELLED(NONE)
    CANCEL_FILL_FULL = "CANCEL_FILL_FULL"         # CANCEL_REQUESTED → CANCELLED(FULL)
    CANCEL_FILL_PARTIAL = "CANCEL_FILL_PARTIAL"   # CANCEL_REQUESTED → CANCELLED(PARTIAL)
    CANCEL_REJECTED = "CANCEL_REJECTED"           # CANCEL_REQUESTED → UNKNOWN
    CANCEL_TIMEOUT = "CANCEL_TIMEOUT"             # CANCEL_REQUESTED → UNKNOWN
    # EOD 事件
    EOD_DAY_ORDER = "EOD_DAY_ORDER"               # LIVE(DAY) → CANCEL_REQUESTED
    EOD_GTC_ORDER = "EOD_GTC_ORDER"               # LIVE(GTC) → LIVE (跨日维持)
    EOD_PARTIAL_DAY = "EOD_PARTIAL_DAY"           # PARTIAL(DAY) → CANCEL_REQUESTED
    EOD_TERMINAL = "EOD_TERMINAL"                 # FILLED/CANCELLED/REJECTED → RECONCILE
    # UNKNOWN 归位
    UNKNOWN_RESOLVED = "UNKNOWN_RESOLVED"         # UNKNOWN → FILLED/PARTIAL/REJECTED/CANCELLED
    UNKNOWN_UNRESOLVED = "UNKNOWN_UNRESOLVED"     # UNKNOWN → UNKNOWN (维持)
    UNKNOWN_EXHAUSTED = "UNKNOWN_EXHAUSTED"       # UNKNOWN → PAUSE(MANUAL_REVIEW)
    # 对账闭环
    RECONCILE_PASS = "RECONCILE_PASS"             # RECONCILE → IDLE
    RECONCILE_FAIL = "RECONCILE_FAIL"             # RECONCILE → PAUSE(MANUAL_REVIEW)
    # 系统级
    BREAK_GLASS = "BREAK_GLASS"                   # ANY → PAUSE(BREAK_GLASS)
    REJECT_BREAKER = "REJECT_BREAKER"             # ANY(非IDLE) → PAUSE(REJECT_BREAKER)
    MANUAL_RESUME = "MANUAL_RESUME"               # PAUSE → RECONCILE
    CRASH_RECOVER = "CRASH_RECOVER"              # outbox 恢复 → 对应态/PAUSE


# TRANSITIONS: {(from_state, event): to_state}
# 注意：IDEMPOTENT_DUPLICATE 事件下 ORDER_INTENT 终止（no-op），不产生新状态迁移，
#       在代码中特殊处理。
TRANSITIONS: dict[tuple[OrderState, OrderEvent], OrderState] = {
    # --- 2.1 策略/风控流水线（§二 2.1）---
    (OrderState.IDLE, OrderEvent.NEW_TRADING_DAY):
        OrderState.TARGET_GEN,
    (OrderState.IDLE, OrderEvent.UNRECONCILED_DETECTED):
        OrderState.RECONCILE,

    (OrderState.TARGET_GEN, OrderEvent.TARGET_WEIGHT_RECEIVED):
        OrderState.RISK_CLIP,
    (OrderState.TARGET_GEN, OrderEvent.TARGET_INVALID):
        OrderState.RECONCILE,           # 进对账中间态，由 halt_reason=MANUAL_REVIEW 标记

    (OrderState.RISK_CLIP, OrderEvent.RISK_PASSED):
        OrderState.ORDER_SIZING,
    (OrderState.RISK_CLIP, OrderEvent.RISK_REJECTED):
        OrderState.RECONCILE,
    (OrderState.RISK_CLIP, OrderEvent.DRAWDOWN_WARN):
        OrderState.ORDER_SIZING,        # 降仓路径
    (OrderState.RISK_CLIP, OrderEvent.DRAWDOWN_HARD_STOP):
        OrderState.ORDER_SIZING,        # 清仓路径（系统续运行，非 BREAK_GLASS）

    (OrderState.ORDER_SIZING, OrderEvent.SIZING_DONE):
        OrderState.ORDER_INTENT,
    (OrderState.ORDER_SIZING, OrderEvent.SIZING_FAILED):
        OrderState.RECONCILE,

    # ORDER_INTENT 幂等键已存在 → 本意图终止（特殊处理，见 OrderStateMachine）
    (OrderState.ORDER_INTENT, OrderEvent.IDEMPOTENT_NEW):
        OrderState.PRE_FIRE_CHECK,

    (OrderState.PRE_FIRE_CHECK, OrderEvent.PRECHECK_PASSED):
        OrderState.SUBMITTED,
    (OrderState.PRE_FIRE_CHECK, OrderEvent.PRECHECK_FAILED):
        OrderState.RECONCILE,

    # --- 2.2 委托生命周期（§二 2.2）---
    (OrderState.SUBMITTED, OrderEvent.BROKER_ACCEPTED):
        OrderState.LIVE,
    (OrderState.SUBMITTED, OrderEvent.PARTIAL_FILL):
        OrderState.PARTIAL,
    (OrderState.SUBMITTED, OrderEvent.FULL_FILL):
        OrderState.FILLED,
    (OrderState.SUBMITTED, OrderEvent.BROKER_REJECTED):
        OrderState.REJECTED,
    (OrderState.SUBMITTED, OrderEvent.SUBMIT_TIMEOUT):
        OrderState.UNKNOWN,

    (OrderState.LIVE, OrderEvent.FULL_FILL):
        OrderState.FILLED,
    (OrderState.LIVE, OrderEvent.PARTIAL_FILL):
        OrderState.PARTIAL,
    (OrderState.LIVE, OrderEvent.CANCEL_REQUESTED_EVT):
        OrderState.CANCEL_REQUESTED,
    (OrderState.LIVE, OrderEvent.ORDER_TTL_EXPIRED):
        OrderState.CANCEL_REQUESTED,
    (OrderState.LIVE, OrderEvent.CONNECTION_LOST):
        OrderState.UNKNOWN,
    (OrderState.LIVE, OrderEvent.EOD_DAY_ORDER):
        OrderState.CANCEL_REQUESTED,
    (OrderState.LIVE, OrderEvent.EOD_GTC_ORDER):
        OrderState.LIVE,                # GTC 跨日维持，自环

    (OrderState.PARTIAL, OrderEvent.CANCEL_REQUESTED_EVT):
        OrderState.CANCEL_REQUESTED,
    (OrderState.PARTIAL, OrderEvent.FULL_FILL):
        OrderState.FILLED,
    (OrderState.PARTIAL, OrderEvent.EOD_PARTIAL_DAY):
        OrderState.CANCEL_REQUESTED,

    (OrderState.CANCEL_REQUESTED, OrderEvent.CANCEL_SUCCESS_NONE):
        OrderState.CANCELLED,
    (OrderState.CANCEL_REQUESTED, OrderEvent.CANCEL_FILL_FULL):
        OrderState.CANCELLED,
    (OrderState.CANCEL_REQUESTED, OrderEvent.CANCEL_FILL_PARTIAL):
        OrderState.CANCELLED,
    (OrderState.CANCEL_REQUESTED, OrderEvent.CANCEL_REJECTED):
        OrderState.UNKNOWN,
    (OrderState.CANCEL_REQUESTED, OrderEvent.CANCEL_TIMEOUT):
        OrderState.UNKNOWN,

    # --- 2.3 UNKNOWN 归位（§二 2.3）---
    (OrderState.UNKNOWN, OrderEvent.UNKNOWN_RESOLVED):
        OrderState.FILLED,              # 运行时可能是 PARTIAL/REJECTED/CANCELLED，见备注
    (OrderState.UNKNOWN, OrderEvent.UNKNOWN_UNRESOLVED):
        OrderState.UNKNOWN,             # 维持，等下次心跳
    (OrderState.UNKNOWN, OrderEvent.UNKNOWN_EXHAUSTED):
        OrderState.RECONCILE,

    # --- EOD 终态进对账（§二 2.3）---
    (OrderState.FILLED, OrderEvent.EOD_TERMINAL):
        OrderState.RECONCILE,
    (OrderState.CANCELLED, OrderEvent.EOD_TERMINAL):
        OrderState.RECONCILE,
    (OrderState.REJECTED, OrderEvent.EOD_TERMINAL):
        OrderState.RECONCILE,

    # --- 对账闭环（§二 2.4）---
    (OrderState.RECONCILE, OrderEvent.RECONCILE_PASS):
        OrderState.IDLE,
    (OrderState.RECONCILE, OrderEvent.RECONCILE_FAIL):
        OrderState.RECONCILE,           # 差异无法解释时停留对账+发告警，由 halt_reason 标记

    # --- 系统级（break-glass / 断路器 / 人工恢复）---
    # MANUAL_RESUME: 暂停（任意 halt_reason）→ 先走 RECONCILE 对齐
    (OrderState.RECONCILE, OrderEvent.MANUAL_RESUME):
        OrderState.IDLE,
}

# UNKNOWN_RESOLVED 实际目标态由调用方根据查询结果决定，
# 此处 TRANSITIONS 仅记录占位路径 FILLED（最常见终止路径）。
# OrderStateMachine.transition_unknown_resolved() 提供多目标重载。

# break-glass 可从任意状态触发，不在上表中穷举，由 OrderStateMachine.trigger_break_glass() 处理


# ---------------------------------------------------------------------------
# InvalidTransitionError
# ---------------------------------------------------------------------------

class InvalidTransitionError(Exception):
    """非法状态转换异常。"""

    def __init__(
        self,
        from_state: OrderState,
        event: OrderEvent,
        *,
        detail: Optional[str] = None,
    ) -> None:
        msg = (
            f"非法状态转换：{from_state.value} --[{event.value}]--> ??"
        )
        if detail:
            msg += f"  ({detail})"
        super().__init__(msg)
        self.from_state = from_state
        self.event = event


# ---------------------------------------------------------------------------
# OrderStateMachine
# ---------------------------------------------------------------------------

class OrderStateMachine:
    """驱动单笔订单在 15 态中迁移。

    实例化时传入初始状态（默认 IDLE）；
    每次调用 transition(event) 返回新状态，非法转换抛 InvalidTransitionError。

    典型用法::

        sm = OrderStateMachine()
        sm.transition(OrderEvent.NEW_TRADING_DAY)       # → TARGET_GEN
        sm.transition(OrderEvent.TARGET_WEIGHT_RECEIVED)# → RISK_CLIP
        ...
    """

    def __init__(self, initial_state: OrderState = OrderState.IDLE) -> None:
        self._state = initial_state
        self._history: list[tuple[OrderState, OrderEvent, OrderState]] = []

    @property
    def state(self) -> OrderState:
        return self._state

    @property
    def history(self) -> list[tuple[OrderState, OrderEvent, OrderState]]:
        """迁移历史列表：[(from_state, event, to_state), ...]。"""
        return list(self._history)

    def transition(self, event: OrderEvent) -> OrderState:
        """执行状态迁移。

        Args:
            event: 驱动事件。

        Returns:
            迁移后的新状态。

        Raises:
            InvalidTransitionError: 当前状态+事件组合不在合法转换表中。
            ValueError: 终态不可再迁移（FILLED/CANCELLED/REJECTED 例外：可接受 EOD_TERMINAL）。
        """
        from_state = self._state

        # 终态限制：只允许 EOD_TERMINAL（→ RECONCILE）
        if from_state in TERMINAL_STATES and event != OrderEvent.EOD_TERMINAL:
            raise InvalidTransitionError(
                from_state, event,
                detail=f"{from_state.value} 为终态，仅允许 EOD_TERMINAL 事件进入对账。",
            )

        # ORDER_INTENT + IDEMPOTENT_DUPLICATE → 本意图终止（no-op，不改变状态）
        if from_state == OrderState.ORDER_INTENT and event == OrderEvent.IDEMPOTENT_DUPLICATE:
            # 不迁移，调用方应将本意图标记为 dropped，不产生新委托
            return from_state

        # BREAK_GLASS 可从任意非终态触发
        if event == OrderEvent.BREAK_GLASS:
            new_state = OrderState.RECONCILE  # 进对账中间态，halt_reason=BREAK_GLASS
            self._record(from_state, event, new_state)
            return new_state

        # REJECT_BREAKER（5分钟内 ≥3 笔 REJECTED）
        if event == OrderEvent.REJECT_BREAKER:
            if from_state != OrderState.IDLE:
                new_state = OrderState.RECONCILE  # halt_reason=REJECT_BREAKER
                self._record(from_state, event, new_state)
                return new_state

        key = (from_state, event)
        if key not in TRANSITIONS:
            raise InvalidTransitionError(from_state, event)

        new_state = TRANSITIONS[key]
        self._record(from_state, event, new_state)
        return new_state

    def transition_unknown_resolved(
        self, resolved_state: OrderState
    ) -> OrderState:
        """UNKNOWN 归位到真实状态（连续两次查询结果一致后调用）。

        Args:
            resolved_state: 实际查询到的状态（FILLED/PARTIAL/REJECTED/CANCELLED）。

        Raises:
            InvalidTransitionError: 当前不是 UNKNOWN 态，或 resolved_state 非法。
        """
        valid_targets = {
            OrderState.FILLED,
            OrderState.PARTIAL,
            OrderState.REJECTED,
            OrderState.CANCELLED,
        }
        if self._state != OrderState.UNKNOWN:
            raise InvalidTransitionError(
                self._state,
                OrderEvent.UNKNOWN_RESOLVED,
                detail=f"当前状态 {self._state.value} 不是 UNKNOWN，无法归位。",
            )
        if resolved_state not in valid_targets:
            raise InvalidTransitionError(
                self._state,
                OrderEvent.UNKNOWN_RESOLVED,
                detail=f"归位目标 {resolved_state.value} 不合法，须为 FILLED/PARTIAL/REJECTED/CANCELLED。",
            )
        self._record(OrderState.UNKNOWN, OrderEvent.UNKNOWN_RESOLVED, resolved_state)
        return resolved_state

    def trigger_break_glass(self) -> OrderState:
        """触发物理熔断，任意状态 → RECONCILE（halt_reason=BREAK_GLASS）。"""
        return self.transition(OrderEvent.BREAK_GLASS)

    def _record(
        self, from_state: OrderState, event: OrderEvent, to_state: OrderState
    ) -> None:
        """记录迁移历史并更新当前状态。"""
        self._history.append((from_state, event, to_state))
        self._state = to_state

    def is_terminal(self) -> bool:
        return self._state in TERMINAL_STATES

    def is_live_order(self) -> bool:
        """是否处于活跃委托状态（需要关注券商回报）。"""
        return self._state in {
            OrderState.SUBMITTED,
            OrderState.LIVE,
            OrderState.PARTIAL,
            OrderState.CANCEL_REQUESTED,
            OrderState.UNKNOWN,
        }

    def __repr__(self) -> str:
        return f"OrderStateMachine(state={self._state.value}, steps={len(self._history)})"


# ---------------------------------------------------------------------------
# 模块级工具函数
# ---------------------------------------------------------------------------

def is_valid_transition(from_state: OrderState, event: OrderEvent) -> bool:
    """检查状态转换是否合法（不引发异常版本）。"""
    if from_state in TERMINAL_STATES and event != OrderEvent.EOD_TERMINAL:
        return False
    if event in (OrderEvent.BREAK_GLASS, OrderEvent.REJECT_BREAKER):
        return True
    if from_state == OrderState.ORDER_INTENT and event == OrderEvent.IDEMPOTENT_DUPLICATE:
        return True
    return (from_state, event) in TRANSITIONS


def all_states() -> list[OrderState]:
    """返回所有 15 个状态（按定义顺序）。"""
    return list(OrderState)
