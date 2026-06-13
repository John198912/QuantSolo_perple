"""QS-E03 §3 因子计算引擎包导出。

子模块：
  transforms  - 横截面变换（去极值/标准化/中性化/正交化）
  momentum    - 动量类因子
  quality     - 质量类因子
  pipeline    - factor_variant 三变体生成流水线
"""
from __future__ import annotations

from src.factor.transforms import (
    cholesky_orthogonalize,
    cross_sectional_zscore,
    industry_mktcap_neutralize,
    mad_winsorize,
)
from src.factor.momentum import (
    calc_factor_batch as calc_momentum_batch,
    calc_max_drawdown,
    calc_momentum,
    calc_volatility,
)
from src.factor.quality import (
    calc_debt_ratio,
    calc_gross_margin,
    calc_ocf_ratio,
    calc_quality_factors_batch,
    calc_roe,
)
from src.factor.pipeline import (
    build_factor_variants,
    pivot_factor_df,
)

__all__ = [
    # transforms
    "mad_winsorize",
    "industry_mktcap_neutralize",
    "cross_sectional_zscore",
    "cholesky_orthogonalize",
    # momentum
    "calc_momentum",
    "calc_volatility",
    "calc_max_drawdown",
    "calc_momentum_batch",
    # quality
    "calc_roe",
    "calc_gross_margin",
    "calc_ocf_ratio",
    "calc_debt_ratio",
    "calc_quality_factors_batch",
    # pipeline
    "build_factor_variants",
    "pivot_factor_df",
]
