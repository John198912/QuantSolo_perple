"""差量计算（QS-C04 §二 ORDER_SIZING · QS-E02 §7.3）。

职责：
  1. 差量计算：目标持仓 - 当前持仓
  2. T+1 可卖约束：当日买入的不可卖出
  3. 100 股整数倍取整（A 股最小申报单位）
  4. 现金 reservation：预留资金防超额
  5. 子策略隔离：仅对指定 strategy_id 算差量

金额一律 Decimal，禁止 float 算钱。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal
from typing import Optional


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

LOT_SIZE = 100          # A 股最小申报单位（股）
MIN_LOT = 100           # 最小下单量（一手）


# ---------------------------------------------------------------------------
# 输入/输出 dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PositionTarget:
    """单只标的目标持仓（策略层输出）。"""
    ts_code: str
    strategy_id: str
    target_qty: int              # 目标持有股数（股）
    target_weight: Decimal       # 目标权重（用于记录，不参与差量计算）


@dataclass(frozen=True)
class CurrentPosition:
    """当前持仓快照（来自 position_ledger）。"""
    ts_code: str
    strategy_id: str
    current_qty: int             # 当前持有股数（股）
    sellable_qty: int            # T+1 可卖量（今日买入的不可卖）
    avg_cost: Decimal            # 持仓均价（元/股）


@dataclass(frozen=True)
class SizingResult:
    """差量计算结果（单只标的）。"""
    ts_code: str
    strategy_id: str
    side: str                    # "BUY" | "SELL" | "HOLD"
    order_qty: int               # 最终申报数量（100股整数倍）
    raw_delta: int               # 原始差量（未取整）
    sellable_constrained: bool   # 是否被 T+1 约束裁剪
    cash_reserved: Decimal       # 本单预留现金（元）
    detail: str                  # 说明信息


@dataclass
class PortfolioSizingInput:
    """组合差量计算输入。"""
    account_id: str
    strategy_id: str
    trade_date: str                              # "YYYY-MM-DD"
    targets: dict[str, PositionTarget]           # {ts_code: PositionTarget}
    positions: dict[str, CurrentPosition]        # {ts_code: CurrentPosition}
    reference_prices: dict[str, Decimal]         # {ts_code: price}（Decimal）
    available_cash: Decimal                      # 可用资金（Decimal，元）
    cash_reservation_ratio: Decimal = Decimal("0.02")  # 预留 2% 现金 buffer


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------

def round_to_lot(qty: int, lot_size: int = LOT_SIZE) -> int:
    """向下取整到 lot_size 整数倍（A 股最小申报 100 股）。"""
    if qty <= 0:
        return 0
    return (qty // lot_size) * lot_size


def compute_single_delta(
    ts_code: str,
    strategy_id: str,
    target_qty: int,
    current_qty: int,
    sellable_qty: int,
    reference_price: Decimal,
    available_cash: Decimal,
    cash_reservation_ratio: Decimal = Decimal("0.02"),
) -> SizingResult:
    """计算单只标的的申报差量。

    Args:
        ts_code: 股票代码。
        strategy_id: 策略 ID（子策略隔离）。
        target_qty: 策略目标持仓（股）。
        current_qty: 当前持仓（股）。
        sellable_qty: T+1 可卖量（股）。
        reference_price: 参考价（Decimal，元/股）。
        available_cash: 可用资金（Decimal，元）。
        cash_reservation_ratio: 现金预留比例（默认 2%）。

    Returns:
        SizingResult，包含 side/order_qty/cash_reserved 等信息。
    """
    raw_delta = target_qty - current_qty

    if raw_delta == 0:
        return SizingResult(
            ts_code=ts_code,
            strategy_id=strategy_id,
            side="HOLD",
            order_qty=0,
            raw_delta=0,
            sellable_constrained=False,
            cash_reserved=Decimal("0"),
            detail="差量为零，无需调仓。",
        )

    if raw_delta > 0:
        # BUY：差量取整 + 现金 reservation 校验
        rounded_qty = round_to_lot(raw_delta)
        if rounded_qty < MIN_LOT:
            return SizingResult(
                ts_code=ts_code,
                strategy_id=strategy_id,
                side="HOLD",
                order_qty=0,
                raw_delta=raw_delta,
                sellable_constrained=False,
                cash_reserved=Decimal("0"),
                detail=f"差量 {raw_delta} 取整后 < {MIN_LOT} 股，忽略。",
            )

        estimated_cost = Decimal(rounded_qty) * reference_price
        # 保留现金 buffer
        usable_cash = available_cash * (Decimal("1") - cash_reservation_ratio)
        if estimated_cost > usable_cash:
            # 按可用资金倒推最大可买量
            max_qty_by_cash = int(usable_cash / reference_price)
            adjusted_qty = round_to_lot(max_qty_by_cash)
            if adjusted_qty < MIN_LOT:
                return SizingResult(
                    ts_code=ts_code,
                    strategy_id=strategy_id,
                    side="HOLD",
                    order_qty=0,
                    raw_delta=raw_delta,
                    sellable_constrained=False,
                    cash_reserved=Decimal("0"),
                    detail=f"现金不足（available={available_cash}），无法买入最小手。",
                )
            cash_reserved = Decimal(adjusted_qty) * reference_price
            return SizingResult(
                ts_code=ts_code,
                strategy_id=strategy_id,
                side="BUY",
                order_qty=adjusted_qty,
                raw_delta=raw_delta,
                sellable_constrained=False,
                cash_reserved=cash_reserved,
                detail=f"现金约束裁剪：原差量 {raw_delta} → {adjusted_qty} 股。",
            )

        cash_reserved = estimated_cost
        return SizingResult(
            ts_code=ts_code,
            strategy_id=strategy_id,
            side="BUY",
            order_qty=rounded_qty,
            raw_delta=raw_delta,
            sellable_constrained=False,
            cash_reserved=cash_reserved,
            detail=f"买入 {rounded_qty} 股（原差量 {raw_delta}）。",
        )

    else:
        # SELL：差量绝对值取整 + T+1 约束
        raw_sell = abs(raw_delta)
        rounded_sell = round_to_lot(raw_sell)
        if rounded_sell < MIN_LOT:
            return SizingResult(
                ts_code=ts_code,
                strategy_id=strategy_id,
                side="HOLD",
                order_qty=0,
                raw_delta=raw_delta,
                sellable_constrained=False,
                cash_reserved=Decimal("0"),
                detail=f"卖出差量 {raw_sell} 取整后 < {MIN_LOT} 股，忽略。",
            )

        # T+1 约束：不可卖出超过 sellable_qty
        sellable_constrained = False
        if rounded_sell > sellable_qty:
            rounded_sell = round_to_lot(sellable_qty)
            sellable_constrained = True
            if rounded_sell < MIN_LOT:
                return SizingResult(
                    ts_code=ts_code,
                    strategy_id=strategy_id,
                    side="HOLD",
                    order_qty=0,
                    raw_delta=raw_delta,
                    sellable_constrained=True,
                    cash_reserved=Decimal("0"),
                    detail=f"T+1 约束：可卖量 {sellable_qty} 不足一手，不卖出。",
                )

        return SizingResult(
            ts_code=ts_code,
            strategy_id=strategy_id,
            side="SELL",
            order_qty=rounded_sell,
            raw_delta=raw_delta,
            sellable_constrained=sellable_constrained,
            cash_reserved=Decimal("0"),
            detail=(
                f"卖出 {rounded_sell} 股（原差量 -{raw_sell}"
                + ("，T+1约束裁剪" if sellable_constrained else "")
                + "）。"
            ),
        )


def compute_portfolio_sizing(inp: PortfolioSizingInput) -> list[SizingResult]:
    """组合差量计算（按 strategy_id 过滤）。

    按以下顺序处理：
      1. 先计算所有 SELL（减仓先行，释放资金）
      2. 再计算所有 BUY（用已释放资金）

    Args:
        inp: PortfolioSizingInput 组合输入。

    Returns:
        SizingResult 列表（含 HOLD 结果，便于完整记录）。
    """
    results: list[SizingResult] = []
    remaining_cash = inp.available_cash

    # 过滤当前策略的 targets
    strategy_targets = {
        code: t for code, t in inp.targets.items()
        if t.strategy_id == inp.strategy_id
    }

    # --- PASS 1：SELL 先行 ---
    for ts_code, target in strategy_targets.items():
        pos = inp.positions.get(ts_code, CurrentPosition(
            ts_code=ts_code,
            strategy_id=inp.strategy_id,
            current_qty=0,
            sellable_qty=0,
            avg_cost=Decimal("0"),
        ))
        price = inp.reference_prices.get(ts_code, Decimal("0"))
        if price <= 0:
            continue

        raw_delta = target.target_qty - pos.current_qty
        if raw_delta >= 0:
            continue  # BUY/HOLD，第二趟处理

        result = compute_single_delta(
            ts_code=ts_code,
            strategy_id=inp.strategy_id,
            target_qty=target.target_qty,
            current_qty=pos.current_qty,
            sellable_qty=pos.sellable_qty,
            reference_price=price,
            available_cash=remaining_cash,
            cash_reservation_ratio=inp.cash_reservation_ratio,
        )
        results.append(result)
        # 卖出后增加可用资金（估算，实际结算 T+1）
        if result.side == "SELL":
            remaining_cash += Decimal(result.order_qty) * price

    # --- PASS 2：BUY ---
    for ts_code, target in strategy_targets.items():
        pos = inp.positions.get(ts_code, CurrentPosition(
            ts_code=ts_code,
            strategy_id=inp.strategy_id,
            current_qty=0,
            sellable_qty=0,
            avg_cost=Decimal("0"),
        ))
        price = inp.reference_prices.get(ts_code, Decimal("0"))
        if price <= 0:
            continue

        raw_delta = target.target_qty - pos.current_qty
        if raw_delta <= 0:
            continue  # SELL/HOLD，已处理

        result = compute_single_delta(
            ts_code=ts_code,
            strategy_id=inp.strategy_id,
            target_qty=target.target_qty,
            current_qty=pos.current_qty,
            sellable_qty=pos.sellable_qty,
            reference_price=price,
            available_cash=remaining_cash,
            cash_reservation_ratio=inp.cash_reservation_ratio,
        )
        results.append(result)
        if result.side == "BUY":
            remaining_cash -= result.cash_reserved

    # --- PASS 3：HOLD（差量为零的标的，用于完整记录）---
    for ts_code, target in strategy_targets.items():
        # 若已在前两趟处理过，不重复
        processed = {r.ts_code for r in results}
        if ts_code in processed:
            continue
        pos = inp.positions.get(ts_code, CurrentPosition(
            ts_code=ts_code,
            strategy_id=inp.strategy_id,
            current_qty=0,
            sellable_qty=0,
            avg_cost=Decimal("0"),
        ))
        price = inp.reference_prices.get(ts_code, Decimal("0"))
        if price > 0:
            result = compute_single_delta(
                ts_code=ts_code,
                strategy_id=inp.strategy_id,
                target_qty=target.target_qty,
                current_qty=pos.current_qty,
                sellable_qty=pos.sellable_qty,
                reference_price=price,
                available_cash=remaining_cash,
                cash_reservation_ratio=inp.cash_reservation_ratio,
            )
            results.append(result)

    return results


def compute_liquidation_sizing(
    positions: dict[str, CurrentPosition],
    reference_prices: dict[str, Decimal],
    strategy_id: str,
    sell_satellite_first: bool = False,
    satellite_codes: Optional[set[str]] = None,
) -> list[SizingResult]:
    """熔断/回撤触发时的平仓差量（目标持仓全为 0）。

    Args:
        positions: 当前持仓字典。
        reference_prices: 参考价格字典。
        strategy_id: 策略 ID。
        sell_satellite_first: True = 一级回撤先卖卫星仓（§6.2）。
        satellite_codes: 卫星仓标的集合（sell_satellite_first=True 时有效）。

    Returns:
        SizingResult 列表（全为 SELL）。
    """
    satellite_codes = satellite_codes or set()
    results: list[SizingResult] = []

    for ts_code, pos in positions.items():
        if pos.strategy_id != strategy_id:
            continue
        if sell_satellite_first and ts_code not in satellite_codes:
            continue  # 第一趟仅卫星仓

        price = reference_prices.get(ts_code, Decimal("0"))
        if price <= 0 or pos.sellable_qty <= 0:
            continue

        result = compute_single_delta(
            ts_code=ts_code,
            strategy_id=strategy_id,
            target_qty=0,
            current_qty=pos.current_qty,
            sellable_qty=pos.sellable_qty,
            reference_price=price,
            available_cash=Decimal("999999999"),  # 平仓不受资金约束
            cash_reservation_ratio=Decimal("0"),
        )
        results.append(result)

    return results
