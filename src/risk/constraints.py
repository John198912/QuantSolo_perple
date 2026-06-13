"""仓位/行业/流动性硬约束的纯函数校验集合（QS-C04 §六 · QS-C01 §7.3）。

所有参数均来自 frozen.toml，禁止在此硬编码数值。
禁止 import xtquant。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from src.common.config import load_frozen


# ---------------------------------------------------------------------------
# 结果 dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConstraintCheckResult:
    """约束检查结果。

    Attributes:
        passed: True 表示通过，False 表示拒单/需裁剪。
        rejection_reason: 拒绝原因代码（passed=False 时非 None）。
        adjusted_qty: 建议裁剪后数量（None 表示不调整/直接拒绝）。
    """
    passed: bool
    rejection_reason: Optional[str] = None
    adjusted_qty: Optional[int] = None


# ---------------------------------------------------------------------------
# 参数加载（单次加载，之后引用 _C）
# ---------------------------------------------------------------------------

def _load_constraints() -> dict:
    """从 frozen.toml [risk] 加载约束参数，返回普通 dict 便于直接索引。"""
    frozen = load_frozen()
    risk = frozen["risk"]
    return {
        "single_stock_max": float(risk["max_position_per_stock"]),    # 0.08
        "industry_max": float(risk["max_position_per_industry"]),      # 0.30
        "min_daily_turnover": float(risk["min_daily_turnover_cny"]),   # 5000万
        "min_listing_days": int(risk["min_list_days"]),                 # 250
        "exclude_st": bool(risk["exclude_st"]),                         # True
    }


def _constraints() -> dict:
    """懒加载约束字典（允许测试时 mock load_frozen）。"""
    return _load_constraints()


# ---------------------------------------------------------------------------
# 纯函数检查
# ---------------------------------------------------------------------------

def check_single_stock_limit(
    ts_code: str,
    order_qty: int,
    price: Decimal,
    total_portfolio_value: Decimal,
    current_positions: dict[str, dict],
) -> ConstraintCheckResult:
    """单票仓位上限检查（QS-C01 §7.3）。

    Args:
        ts_code: 股票代码（仅用于查询 current_positions）。
        order_qty: 本次申报数量（股）。
        price: 参考价格（Decimal，元/股）。禁止传 float。
        total_portfolio_value: 组合总市值（Decimal，元）。
        current_positions: {ts_code: {"market_value": Decimal}} 当前持仓字典。

    Returns:
        ConstraintCheckResult。若超限且仍可买入部分，adjusted_qty 给出裁剪量（100 股整数倍）；
        若裁剪后数量 ≤ 0，则 passed=False 直接拒单。
    """
    c = _constraints()
    max_pct = Decimal(str(c["single_stock_max"]))

    price_d = Decimal(str(price))
    total_d = Decimal(str(total_portfolio_value))
    if total_d <= 0:
        return ConstraintCheckResult(False, "PORTFOLIO_VALUE_ZERO")

    current_val = Decimal(str(current_positions.get(ts_code, {}).get("market_value", 0)))
    new_val = current_val + Decimal(order_qty) * price_d
    new_pct = new_val / total_d

    if new_pct > max_pct:
        max_val = total_d * max_pct
        remaining_capacity = max_val - current_val
        if remaining_capacity <= 0:
            return ConstraintCheckResult(False, "SINGLE_STOCK_LIMIT_EXCEEDED", adjusted_qty=0)
        max_qty_raw = int(remaining_capacity / price_d)
        max_qty = (max_qty_raw // 100) * 100  # 取整 100 股
        if max_qty <= 0:
            return ConstraintCheckResult(False, "SINGLE_STOCK_LIMIT_EXCEEDED", adjusted_qty=0)
        return ConstraintCheckResult(True, None, adjusted_qty=max_qty)

    return ConstraintCheckResult(True)


def check_industry_limit(
    industry: str,
    order_qty: int,
    price: Decimal,
    total_portfolio_value: Decimal,
    current_positions: dict[str, dict],
    ts_code_industry_map: dict[str, str],
) -> ConstraintCheckResult:
    """行业集中度上限检查（≤30%，QS-C01 §7.3）。

    Args:
        industry: 本次订单所属行业（申万一级等）。
        order_qty: 本次申报数量（股）。
        price: 参考价格（Decimal）。
        total_portfolio_value: 组合总市值（Decimal）。
        current_positions: {ts_code: {"market_value": Decimal}} 当前持仓。
        ts_code_industry_map: {ts_code: industry_name} 行业映射。

    Returns:
        ConstraintCheckResult（行业超限时直接拒单，不做裁剪）。
    """
    c = _constraints()
    max_pct = Decimal(str(c["industry_max"]))

    price_d = Decimal(str(price))
    total_d = Decimal(str(total_portfolio_value))
    if total_d <= 0:
        return ConstraintCheckResult(False, "PORTFOLIO_VALUE_ZERO")

    industry_val = sum(
        Decimal(str(pos.get("market_value", 0)))
        for code, pos in current_positions.items()
        if ts_code_industry_map.get(code) == industry
    )
    new_val = industry_val + Decimal(order_qty) * price_d
    new_pct = new_val / total_d

    if new_pct > max_pct:
        return ConstraintCheckResult(False, "INDUSTRY_LIMIT_EXCEEDED")
    return ConstraintCheckResult(True)


def check_liquidity_filter(
    ts_code: str,  # noqa: ARG001  — 保留用于日志/扩展
    avg_daily_turnover: float,
    is_suspended: bool,
    is_st: bool,
    listing_days: int,
) -> ConstraintCheckResult:
    """流动性与资格过滤（每笔下单前，QS-C01 §7.3）。

    检查顺序（任一不过即拒单）：
      1. ST 股票剔除
      2. 停牌剔除
      3. 上市天数 < 250 剔除（次新股）
      4. 日均成交额 < 5000 万剔除

    Args:
        ts_code: 股票代码（用于上下文/日志，不参与计算）。
        avg_daily_turnover: 近期日均成交额（元）。
        is_suspended: 是否停牌。
        is_st: 是否 ST / *ST / 退市风险。
        listing_days: 自上市至今的自然日数。

    Returns:
        ConstraintCheckResult。
    """
    c = _constraints()

    if c["exclude_st"] and is_st:
        return ConstraintCheckResult(False, "ST_EXCLUDED")
    if is_suspended:
        return ConstraintCheckResult(False, "SUSPENDED")
    if listing_days < c["min_listing_days"]:
        return ConstraintCheckResult(False, "LISTING_DAYS_INSUFFICIENT")
    if avg_daily_turnover < c["min_daily_turnover"]:
        return ConstraintCheckResult(False, "LIQUIDITY_INSUFFICIENT")

    return ConstraintCheckResult(True)


def check_all_constraints(
    ts_code: str,
    order_qty: int,
    price: Decimal,
    total_portfolio_value: Decimal,
    current_positions: dict[str, dict],
    industry: str,
    ts_code_industry_map: dict[str, str],
    avg_daily_turnover: float,
    is_suspended: bool,
    is_st: bool,
    listing_days: int,
) -> ConstraintCheckResult:
    """顺序执行所有约束检查，第一个失败即返回。

    检查顺序（QS-C04 §0.5 设计原则：先快后慢，先剔除后数值）：
      1. 流动性/资格过滤（ST/停牌/次新股/流动性）
      2. 单票仓位上限（≤8%）
      3. 行业集中度上限（≤30%）

    Returns:
        最终 ConstraintCheckResult（passed=True 或首个失败结果）。
        若单票检查返回 adjusted_qty，后续行业检查仍基于 adjusted_qty 重新算。
    """
    # 1. 流动性与资格
    result = check_liquidity_filter(
        ts_code, avg_daily_turnover, is_suspended, is_st, listing_days
    )
    if not result.passed:
        return result

    # 2. 单票上限
    result = check_single_stock_limit(
        ts_code, order_qty, price, total_portfolio_value, current_positions
    )
    if not result.passed:
        return result

    # 若单票有裁剪，用裁剪后数量做行业检查
    effective_qty = result.adjusted_qty if result.adjusted_qty is not None else order_qty

    # 3. 行业集中度
    result_ind = check_industry_limit(
        industry, effective_qty, price, total_portfolio_value,
        current_positions, ts_code_industry_map,
    )
    if not result_ind.passed:
        return result_ind

    # 透传可能的 adjusted_qty
    return ConstraintCheckResult(True, None, result.adjusted_qty)
