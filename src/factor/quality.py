"""QS-E03 §3.3 质量类因子计算。

纯函数：无副作用，无 IO。
对应文档 QuantSolo_软件功能设计文档_v1.0.md §3.3（质量因子）。

质量因子：ROE、毛利率、OCF/净利润、资产负债率（来源 financials_pit）。
"""
from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


def calc_roe(
    net_profit: Optional[float],
    total_equity: Optional[float],
) -> Optional[float]:
    """ROE = 净利润 / 平均净资产。单值纯函数。

    Args:
        net_profit: 净利润
        total_equity: 净资产（平均值或期末值）
    Returns:
        ROE，或 None（数据缺失/无效）
    """
    if net_profit is None or total_equity is None or total_equity == 0:
        return None
    return net_profit / total_equity


def calc_gross_margin(
    revenue: Optional[float],
    cogs: Optional[float],
) -> Optional[float]:
    """毛利率 = (营收 - 营业成本) / 营收。单值纯函数。

    Args:
        revenue: 营业收入
        cogs: 营业成本
    Returns:
        毛利率（0~1），或 None（数据缺失/无效）
    """
    if revenue is None or cogs is None or revenue == 0:
        return None
    return (revenue - cogs) / revenue


def calc_ocf_ratio(
    ocf: Optional[float],
    net_profit: Optional[float],
) -> Optional[float]:
    """经营现金流/净利润质量因子。

    Args:
        ocf: 经营活动产生的现金流量净额
        net_profit: 净利润
    Returns:
        OCF/净利润比率，或 None（数据缺失/无效）
    """
    if ocf is None or net_profit is None or net_profit == 0:
        return None
    return ocf / net_profit


def calc_debt_ratio(
    total_liabilities: Optional[float],
    total_assets: Optional[float],
) -> Optional[float]:
    """资产负债率 = 总负债 / 总资产。单值纯函数。

    Args:
        total_liabilities: 总负债
        total_assets: 总资产
    Returns:
        资产负债率（0~1），或 None（数据缺失/无效）
    """
    if total_liabilities is None or total_assets is None or total_assets == 0:
        return None
    return total_liabilities / total_assets


def calc_quality_factors_batch(
    financials_df: pd.DataFrame,
    as_of: str,
) -> pd.DataFrame:
    """批量计算质量因子截面。

    financials_df 必须已经过 PIT 查询（visible_at <= as_of）。
    confidence_tag='INSUFFICIENT' 的行跳过（QS-C03 §6）。

    纯函数：无 IO，不写数据库。

    Args:
        financials_df: 含财务指标的 PIT DataFrame，来自 financials_pit_asof
        as_of: 计算截止日期（ISO 格式）
    Returns:
        长格式因子 DataFrame：[ts_code, trade_date, factor_name, factor_value, factor_variant, computed_as_of]
    """
    results = []
    for _, row in financials_df.iterrows():
        if row.get('confidence_tag') == 'INSUFFICIENT':
            continue

        # ROE
        roe_val = calc_roe(row.get('net_profit'), row.get('total_equity'))
        # 毛利率
        gm_val = calc_gross_margin(row.get('revenue'), row.get('cogs'))
        # OCF 质量
        ocf_val = calc_ocf_ratio(row.get('ocf'), row.get('net_profit'))
        # 资产负债率
        dr_val = calc_debt_ratio(row.get('total_liabilities'), row.get('total_assets'))

        trade_date = row.get('ann_date', as_of[:10])

        for fname, fval in [
            ('roe_ttm', roe_val),
            ('gross_margin', gm_val),
            ('ocf_ratio', ocf_val),
            ('debt_ratio', dr_val),
        ]:
            if fval is not None:
                results.append({
                    'ts_code': row['ts_code'],
                    'trade_date': trade_date,
                    'factor_name': fname,
                    'factor_value': fval,
                    'factor_variant': 'raw',
                    'computed_as_of': as_of,
                })

    if not results:
        return pd.DataFrame(columns=[
            'ts_code', 'trade_date', 'factor_name',
            'factor_value', 'factor_variant', 'computed_as_of',
        ])
    return pd.DataFrame(results)
