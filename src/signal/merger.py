"""QS-E03 §11 信号合并器（核心多因子 + 趋势卫星 → 目标权重 target_weight）。

合并核心多因子信号与趋势卫星信号，输出可衔接 execution 的 TARGET_GEN 持仓目标。
core/satellite 权重取 load_tunable()['portfolio']。
输出结构对接 src/execution/order_sizing.PositionTarget。

对应文档 QuantSolo_软件功能设计文档_v1.0.md §11（模块接口契约表）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import pandas as pd

from src.common.config import load_frozen, load_tunable
from src.signal.market_timing import calc_market_timing, get_timing_exposure_cap

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MergedSignal:
    """合并后的目标持仓信号，可直接传入 execution 层差量计算。

    对接 execution/order_sizing.PositionTarget（trade_date、strategy_id 由调用方填充）。
    """
    ts_code: str
    target_weight: Decimal          # 归一化后目标权重（Decimal，禁止 float 算钱 R6）
    signal_source: str              # 'core' | 'satellite'
    timing_state: str               # 'BULL' | 'NEUTRAL' | 'BEAR'
    effective_weight: Decimal       # 乘以择时仓位上限后的有效权重


def merge_core_satellite_signals(
    core_weights: pd.Series,
    satellite_weights: Optional[pd.Series],
    hs300_close: pd.Series,
    ma_window: Optional[int] = None,
    confirmation_days: Optional[int] = None,
) -> list[MergedSignal]:
    """合并核心多因子信号与趋势卫星信号，输出目标持仓列表。

    步骤：
      1. 读取 tunable portfolio.core_weight / satellite_weight
      2. 调用 calc_market_timing 获取择时状态
      3. 按 core_weight/satellite_weight 加权合并
      4. 乘以择时仓位上限（TIMING_CAPS）得到有效权重
      5. 行业持仓上限检查（取 load_frozen()['risk']['max_position_per_industry']）

    Args:
        core_weights: 核心多因子目标权重 {ts_code: weight}（归一化后，加总=1）
        satellite_weights: 趋势卫星目标权重（None 则纯核心）
        hs300_close: 沪深 300 收盘价序列（用于大盘择时）
        ma_window: 均线窗口，None 从 tunable 读取
        confirmation_days: 确认天数，None 从 tunable 读取

    Returns:
        MergedSignal 列表，可衔接 execution.order_sizing.PositionTarget
    """
    tunable = load_tunable()
    portfolio_cfg = tunable.get('portfolio', {})
    core_w = float(portfolio_cfg.get('core_weight', 0.78))
    satellite_w = float(portfolio_cfg.get('satellite_weight', 0.22))

    # 如果无卫星信号，全部权重归核心
    if satellite_weights is None or len(satellite_weights) == 0:
        satellite_w = 0.0
        core_w = 1.0

    # 大盘择时
    timing_state = calc_market_timing(
        hs300_close,
        ma_window=ma_window,
        confirmation_days=confirmation_days,
    )
    timing_cap = get_timing_exposure_cap(timing_state)
    logger.info(
        "merge_core_satellite_signals: 择时状态=%s，仓位上限=%.2f",
        timing_state, timing_cap,
    )

    results: list[MergedSignal] = []

    # ---------- 核心多因子部分 ----------
    if len(core_weights) > 0:
        core_total = core_weights.sum()
        for ts_code, w in core_weights.items():
            if core_total > 1e-9:
                normalized = float(w) / float(core_total)
            else:
                normalized = 0.0
            combined_w = normalized * core_w
            effective_w = combined_w * timing_cap
            results.append(MergedSignal(
                ts_code=str(ts_code),
                target_weight=Decimal(str(round(combined_w, 8))),
                signal_source='core',
                timing_state=timing_state,
                effective_weight=Decimal(str(round(effective_w, 8))),
            ))

    # ---------- 趋势卫星部分 ----------
    if satellite_weights is not None and len(satellite_weights) > 0:
        sat_total = satellite_weights.sum()
        for ts_code, w in satellite_weights.items():
            if sat_total > 1e-9:
                normalized = float(w) / float(sat_total)
            else:
                normalized = 0.0
            combined_w = normalized * satellite_w
            effective_w = combined_w * timing_cap
            results.append(MergedSignal(
                ts_code=str(ts_code),
                target_weight=Decimal(str(round(combined_w, 8))),
                signal_source='satellite',
                timing_state=timing_state,
                effective_weight=Decimal(str(round(effective_w, 8))),
            ))

    return results


def apply_industry_cap(
    signals: list[MergedSignal],
    industry_map: dict[str, str],
) -> list[MergedSignal]:
    """行业持仓上限裁剪（QS-C01 §7.3）。

    单行业上限取 load_frozen()['risk']['max_position_per_industry']（R3 红线）。

    Args:
        signals: 合并后信号列表
        industry_map: {ts_code: industry_name}

    Returns:
        裁剪后的信号列表（超限行业按比例缩减）
    """
    frozen = load_frozen()
    max_industry = float(frozen['risk']['max_position_per_industry'])  # 0.30

    # 按行业汇总 effective_weight
    industry_total: dict[str, float] = {}
    for sig in signals:
        ind = industry_map.get(sig.ts_code, 'UNKNOWN')
        industry_total[ind] = industry_total.get(ind, 0.0) + float(sig.effective_weight)

    # 计算各行业缩放比例
    scale_factor: dict[str, float] = {}
    for ind, total in industry_total.items():
        if total > max_industry:
            scale_factor[ind] = max_industry / total
        else:
            scale_factor[ind] = 1.0

    # 应用缩放
    adjusted: list[MergedSignal] = []
    for sig in signals:
        ind = industry_map.get(sig.ts_code, 'UNKNOWN')
        sf = scale_factor.get(ind, 1.0)
        if sf < 1.0:
            new_eff = Decimal(str(round(float(sig.effective_weight) * sf, 8)))
            new_tw = Decimal(str(round(float(sig.target_weight) * sf, 8)))
            adjusted.append(MergedSignal(
                ts_code=sig.ts_code,
                target_weight=new_tw,
                signal_source=sig.signal_source,
                timing_state=sig.timing_state,
                effective_weight=new_eff,
            ))
        else:
            adjusted.append(sig)

    return adjusted


def signals_to_position_targets(
    signals: list[MergedSignal],
    total_portfolio_value: Decimal,
    reference_prices: dict[str, Decimal],
    strategy_id: str,
    lot_size: int = 100,
) -> "dict[str, object]":  # dict[str, PositionTarget]
    """将 MergedSignal 转换为 execution 层可消费的 PositionTarget 字典。

    对接 src/execution/order_sizing.PositionTarget。
    金额计算使用 Decimal（R6 红线）。

    Args:
        signals: 合并后信号列表
        total_portfolio_value: 组合总市值（Decimal，元）
        reference_prices: {ts_code: Decimal 参考价}
        strategy_id: 策略 ID
        lot_size: 最小申报单位（A股=100）

    Returns:
        {ts_code: PositionTarget} 字典
    """
    # 惰性导入，避免循环依赖
    from src.execution.order_sizing import PositionTarget

    result: dict[str, PositionTarget] = {}
    for sig in signals:
        price = reference_prices.get(sig.ts_code)
        if price is None or price <= Decimal('0'):
            logger.warning(
                "signals_to_position_targets: %s 无有效参考价，跳过", sig.ts_code
            )
            continue

        # 目标市值 = 有效权重 × 组合总值
        target_value = sig.effective_weight * total_portfolio_value
        # 目标股数（向下取整到 lot_size）
        raw_qty = int(target_value / price)
        target_qty = (raw_qty // lot_size) * lot_size

        result[sig.ts_code] = PositionTarget(
            ts_code=sig.ts_code,
            strategy_id=strategy_id,
            target_qty=target_qty,
            target_weight=sig.effective_weight,
        )

    return result
