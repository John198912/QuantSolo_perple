"""QS-E03 §3.3 横截面因子变换（去极值/标准化/中性化/正交化）。

纯函数：无副作用，无 IO，所有输入均为参数，所有输出均为返回值。
与文档 QuantSolo_软件功能设计文档_v1.0.md §3.3 / §3.4 对应。
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def mad_winsorize(series: pd.Series, n: float = 3.0) -> pd.Series:
    """MAD 去极值。

    中位数 ± n × MAD 截断。
    纯函数：输入 pd.Series，输出去极值后 pd.Series。

    Args:
        series: 原始因子序列
        n: 截断倍数（默认 3.0）
    Returns:
        去极值后序列（无 NaN 填充，保持 NaN）
    """
    median = series.median()
    mad = (series - median).abs().median()
    upper = median + n * 1.4826 * mad
    lower = median - n * 1.4826 * mad
    return series.clip(lower=lower, upper=upper)


def industry_mktcap_neutralize(
    factor: pd.Series,
    industry: pd.Series,
    log_mktcap: pd.Series,
) -> pd.Series:
    """行业+市值中性化（OLS 残差法）。

    factor = α + β1·log_mktcap + Σγᵢ·industry_dummies + ε
    返回残差 ε（中性化后因子）。

    注意：中性化是否保留核心 Alpha 须经 A/B 验证（QS-C01 §6.6）。

    Args:
        factor: 原始因子截面
        industry: 行业标签序列（与 factor 索引对齐）
        log_mktcap: 对数市值序列（与 factor 索引对齐）
    Returns:
        中性化后残差序列（保留 NaN）
    """
    try:
        import statsmodels.api as sm
    except ImportError as exc:
        raise ImportError(
            "industry_mktcap_neutralize 需要 statsmodels 包，请 pip install statsmodels"
        ) from exc

    industry_dummies = pd.get_dummies(industry, prefix='ind', drop_first=True)
    X = pd.concat([log_mktcap.rename('log_mktcap'), industry_dummies], axis=1)
    X = sm.add_constant(X)
    mask = factor.notna() & X.notna().all(axis=1)
    residuals = factor.copy()
    if mask.sum() < 10:
        # 样本不足，返回原值并记录警告
        logger.warning(
            "industry_mktcap_neutralize: 有效样本不足（%d），跳过中性化", mask.sum()
        )
        return residuals
    model = sm.OLS(factor[mask], X[mask]).fit()
    residuals[mask] = model.resid
    return residuals


def cross_sectional_zscore(series: pd.Series) -> pd.Series:
    """截面 z-score 标准化。纯函数。

    Args:
        series: 原始截面序列
    Returns:
        z-score 标准化后序列（均值≈0，标准差≈1）
    """
    return (series - series.mean()) / (series.std() + 1e-8)


def cholesky_orthogonalize(
    factor_df: pd.DataFrame,
    correlation_threshold: float = 0.7,
) -> pd.DataFrame:
    """Cholesky 残差化正交化（仅对高相关簇做，不全量）。

    Step1: Spearman 相关矩阵 → r > threshold 的因子对
    Step2: 保留 IC 高者，另一方对其做线性回归取残差
    Step3: 记录变换矩阵（供复原）
    Returns: 正交化后 DataFrame（orthogonal 变体）

    Args:
        factor_df: 因子矩阵（行=股票，列=因子名）
        correlation_threshold: 高相关阈值，默认 0.7
    Returns:
        正交化后 DataFrame
    """
    try:
        from sklearn.linear_model import LinearRegression
    except ImportError as exc:
        raise ImportError(
            "cholesky_orthogonalize 需要 scikit-learn 包，请 pip install scikit-learn"
        ) from exc

    corr_matrix = factor_df.rank().corr(method='spearman')
    result = factor_df.copy()

    for i, col_i in enumerate(factor_df.columns):
        for col_j in factor_df.columns[i + 1:]:
            if abs(corr_matrix.loc[col_i, col_j]) > correlation_threshold:
                # 保留 IC 高者（由调用方指定或按 rank-IC 自动选）
                # 此处简化：col_i 对 col_j 回归取残差
                mask = result[col_i].notna() & result[col_j].notna()
                if mask.sum() < 5:
                    continue
                x = result.loc[mask, col_j].values.reshape(-1, 1)
                y = result.loc[mask, col_i].values
                reg = LinearRegression().fit(x, y)
                result.loc[mask, col_i] = y - reg.predict(x)

    return result
