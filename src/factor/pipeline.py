"""QS-E03 §3.4 factor_variant 三变体生成流水线。

生成三变体：
  raw:        原始未变换（SHAP 归因/人工解读）
  processed:  MAD去极值 + 行业市值中性化 + z-score（Ridge 线性训练）
  orthogonal: Cholesky 残差化（LightGBM 训练）

纯函数：无副作用，无 IO。
对应文档 QuantSolo_软件功能设计文档_v1.0.md §3.4（因子变体流水线）。
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from src.factor.transforms import (
    cholesky_orthogonalize,
    cross_sectional_zscore,
    industry_mktcap_neutralize,
    mad_winsorize,
)

logger = logging.getLogger(__name__)


def build_factor_variants(
    raw_df: pd.DataFrame,
    industry_series: pd.Series,
    log_mktcap_series: pd.Series,
    high_corr_threshold: float = 0.7,
) -> dict[str, pd.DataFrame]:
    """生成三变体（QS-C03 §4.1）。

    变体定义：
      raw:        原始未变换（SHAP 归因/人工解读）
      processed:  MAD去极值 + 行业市值中性化 + z-score（Ridge 线性训练）
      orthogonal: Cholesky 残差化（LightGBM 训练）

    Args:
        raw_df: 宽格式原始因子矩阵，行=股票（ts_code），
                非因子列包含: ts_code, trade_date, computed_as_of, factor_variant
                因子列为其余数值列
        industry_series: 行业标签序列，index 与 raw_df 对齐
        log_mktcap_series: 对数市值序列，index 与 raw_df 对齐
        high_corr_threshold: 高相关阈值，默认 0.7

    Returns:
        {'raw': df_raw, 'processed': df_processed, 'orthogonal': df_orthogonal}
    """
    _META_COLS = {'ts_code', 'trade_date', 'computed_as_of', 'factor_variant'}
    factor_cols = [c for c in raw_df.columns if c not in _META_COLS]

    if not factor_cols:
        logger.warning("build_factor_variants: 无因子列可处理，返回空变体")
        empty = pd.DataFrame()
        return {'raw': raw_df, 'processed': empty, 'orthogonal': empty}

    # ---------- processed 变体 ----------
    processed = raw_df[factor_cols].copy()
    for col in factor_cols:
        processed[col] = mad_winsorize(processed[col])

    for col in factor_cols:
        try:
            processed[col] = industry_mktcap_neutralize(
                processed[col], industry_series, log_mktcap_series
            )
        except Exception as exc:  # 中性化失败（如 statsmodels 缺失）则跳过
            logger.warning(
                "build_factor_variants: 因子 %s 中性化失败，跳过: %s", col, exc
            )

    for col in factor_cols:
        processed[col] = cross_sectional_zscore(processed[col])

    # ---------- orthogonal 变体（在 processed 基础上做 Cholesky 残差化）----------
    try:
        orthogonal = cholesky_orthogonalize(processed, high_corr_threshold)
    except ImportError as exc:
        logger.warning(
            "build_factor_variants: cholesky_orthogonalize 失败（sklearn 缺失），"
            "orthogonal 回退为 processed: %s", exc
        )
        orthogonal = processed.copy()

    return {
        'raw':        raw_df,
        'processed':  processed,
        'orthogonal': orthogonal,
    }


def pivot_factor_df(
    long_df: pd.DataFrame,
    factor_variant: str = 'raw',
) -> pd.DataFrame:
    """将长格式因子 DataFrame 转换为宽格式（行=ts_code，列=factor_name）。

    辅助函数，供 build_factor_variants 调用方使用。

    Args:
        long_df: 长格式因子 DataFrame，含 [ts_code, factor_name, factor_value, factor_variant]
        factor_variant: 要筛选的变体名称（raw/processed/orthogonal）

    Returns:
        宽格式 DataFrame，index=ts_code，columns=factor_name
    """
    filtered = long_df[long_df['factor_variant'] == factor_variant]
    pivot = filtered.pivot_table(
        index='ts_code',
        columns='factor_name',
        values='factor_value',
        aggfunc='last',
    )
    return pivot
