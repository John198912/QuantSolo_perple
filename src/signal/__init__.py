"""QS-E03 §5 信号生成器包导出。

子模块：
  core_factor     - 核心多因子信号（宇宙过滤/综合得分/Top-N选股）
  market_timing   - 大盘择时信号（BULL/NEUTRAL/BEAR 三态）
  merger          - 核心+卫星信号合并 → 目标权重（TARGET_GEN）
"""
from __future__ import annotations

from src.signal.core_factor import (
    apply_universe_filter,
    calc_composite_score,
    select_top_n_with_weights,
)
from src.signal.market_timing import (
    TIMING_CAPS,
    calc_market_timing,
    get_timing_exposure_cap,
)
from src.signal.merger import (
    MergedSignal,
    apply_industry_cap,
    merge_core_satellite_signals,
    signals_to_position_targets,
)

__all__ = [
    # core_factor
    "apply_universe_filter",
    "calc_composite_score",
    "select_top_n_with_weights",
    # market_timing
    "calc_market_timing",
    "get_timing_exposure_cap",
    "TIMING_CAPS",
    # merger
    "MergedSignal",
    "merge_core_satellite_signals",
    "apply_industry_cap",
    "signals_to_position_targets",
]
