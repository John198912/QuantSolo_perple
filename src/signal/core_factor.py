"""QS-E03 §5.2 核心多因子信号生成。

纯函数：calc_factor_batch、select_top_n_with_weights 无 IO。
波动率倒数加权使用 tunable portfolio.core_weighting。
对应文档 QuantSolo_软件功能设计文档_v1.0.md §5.2（核心因子信号）。
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from src.common.config import load_frozen, load_tunable

logger = logging.getLogger(__name__)


def apply_universe_filter(
    universe: list[str],
    st_list: list[str],
    suspension_list: list[str],
    listing_days: dict[str, int],        # ts_code → 上市天数
    avg_turnover: dict[str, float],      # ts_code → 日均成交额（元）
    high_price_stocks: Optional[list[str]] = None,  # 一手超 1.6 万股票
) -> list[str]:
    """全市场过滤（QS-C01 §7.3）。

    剔除 ST / 停牌 / 上市 < 250 日 / 日均成交额 < 5000 万 / 高价股。
    纯函数：输入列表，输出过滤后列表。

    阈值来自 load_frozen()['risk']（R3 红线：禁止硬编码）。

    Args:
        universe: 全量候选股票代码列表
        st_list: ST 股票代码列表
        suspension_list: 当日停牌股票代码列表
        listing_days: {ts_code: 已上市天数}
        avg_turnover: {ts_code: 近期日均成交额（元）}
        high_price_stocks: 一手买入超 1.6 万元的高价股列表
    Returns:
        过滤后合格股票代码列表
    """
    frozen = load_frozen()
    risk = frozen['risk']
    min_list_days = int(risk['min_list_days'])         # 250
    min_turnover = float(risk['min_daily_turnover_cny'])  # 50_000_000

    st_set = set(st_list)
    suspension_set = set(suspension_list)
    high_price_set = set(high_price_stocks) if high_price_stocks else set()

    result = []
    for ts_code in universe:
        if ts_code in st_set:
            continue
        if ts_code in suspension_set:
            continue
        if listing_days.get(ts_code, 0) < min_list_days:
            continue
        if avg_turnover.get(ts_code, 0) < min_turnover:
            continue
        if ts_code in high_price_set:
            continue
        result.append(ts_code)
    return result


def calc_composite_score(
    factor_df: pd.DataFrame,
    factor_weights: dict[str, float],
    lgbm_score: Optional[pd.Series],
    lgbm_weight: float = 0.5,
) -> pd.Series:
    """线性加权 + LightGBM 融合排序。

    fusion_rank = (1 - lgbm_weight) × linear_rank + lgbm_weight × lgbm_rank

    纯函数：无 IO。

    Args:
        factor_df: 含 (ts_code, factor_name, processed_value) 列的 DataFrame
        factor_weights: {因子名: 权重}
        lgbm_score: LightGBM 排名得分（None = 纯线性模式）
        lgbm_weight: LightGBM 权重占比

    Returns:
        pd.Series: ts_code → composite_score（越高越好）
    """
    # 线性加权得分
    pivot = factor_df.pivot(
        index='ts_code', columns='factor_name', values='processed_value'
    )
    linear_score = pd.Series(0.0, index=pivot.index)
    for fname, weight in factor_weights.items():
        if fname in pivot.columns:
            linear_score += pivot[fname].fillna(0) * weight

    # 融合排序
    linear_rank = linear_score.rank(pct=True)
    if lgbm_score is not None and len(lgbm_score) > 0:
        lgbm_rank = lgbm_score.reindex(linear_rank.index).rank(pct=True)
        merged_rank = (1 - lgbm_weight) * linear_rank + lgbm_weight * lgbm_rank
    else:
        merged_rank = linear_rank

    return merged_rank


def select_top_n_with_weights(
    scores: pd.Series,
    volatility: pd.Series,
    top_n: int = 15,
    single_stock_max: Optional[float] = None,
) -> pd.Series:
    """选 Top N 只，波动率倒数加权，单票上限裁剪。

    波动率倒数加权方式取 tunable portfolio.core_weighting。
    单票上限取 load_frozen()['risk']['max_position_per_stock']（R3 红线）。

    纯函数：无 IO，权重加总 = 1。

    Args:
        scores: {ts_code: composite_score}（越高越好）
        volatility: {ts_code: 60日年化波动率}
        top_n: 选股数量
        single_stock_max: 单票权重上限（None 则从 frozen 读取）

    Returns:
        pd.Series: ts_code → target_weight（归一化后，加总=1）
    """
    if single_stock_max is None:
        frozen = load_frozen()
        single_stock_max = float(frozen['risk']['max_position_per_stock'])  # 0.08

    tunable = load_tunable()
    core_weighting = tunable.get('portfolio', {}).get('core_weighting', 'inverse_vol')

    top_stocks = scores.nlargest(top_n).index.tolist()
    if not top_stocks:
        return pd.Series(dtype=float).rename('target_weight')

    vol_subset = volatility.reindex(top_stocks).fillna(volatility.median())

    if core_weighting == 'inverse_vol':
        # 波动率倒数加权
        inv_vol = 1.0 / (vol_subset + 1e-8)
        raw_weights = inv_vol / inv_vol.sum()
    elif core_weighting == 'equal':
        # 等权
        raw_weights = pd.Series(1.0 / len(top_stocks), index=top_stocks)
    else:
        # 默认回退到波动率倒数
        logger.warning(
            "select_top_n_with_weights: 未知 core_weighting=%s，回退到 inverse_vol",
            core_weighting,
        )
        inv_vol = 1.0 / (vol_subset + 1e-8)
        raw_weights = inv_vol / inv_vol.sum()

    # 单票上限迭代裁剪（最多 10 次）
    for _ in range(10):
        capped = raw_weights.clip(upper=single_stock_max)
        excess = raw_weights.sum() - capped.sum()
        if excess < 1e-9:
            raw_weights = capped
            break
        uncapped_mask = capped < single_stock_max
        uncapped = capped[uncapped_mask]
        if uncapped.sum() < 1e-9:
            raw_weights = capped
            break
        capped[uncapped.index] += excess * (uncapped / uncapped.sum())
        raw_weights = capped

    # 最终归一化确保加总=1
    total = raw_weights.sum()
    if total > 1e-9:
        raw_weights = raw_weights / total

    return raw_weights.rename('target_weight')
