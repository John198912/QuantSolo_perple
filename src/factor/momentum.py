"""QS-E03 §3.3 动量类因子计算。

纯函数：无副作用，无 IO。
对应文档 QuantSolo_软件功能设计文档_v1.0.md §3.3（动量因子）。

动量因子：lookback 日收益，剔除最近 skip_recent 日反转效应。
低波因子：60 日收益率标准差。
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def calc_momentum(
    close: pd.Series,
    lookback: int = 60,
    skip_recent: int = 5,
) -> Optional[float]:
    """动量因子：lookback 日收益，剔除最近 skip_recent 日反转效应。

    公式：close[-skip_recent] / close[-lookback] - 1
    纯函数：输入 close 序列（按日期升序），输出单值因子。

    异常处理：
      - 序列长度 < lookback + skip_recent → 返回 None
      - 包含 NaN → 返回 None

    Args:
        close: 复权收盘价序列，按日期升序
        lookback: 回看窗口（日）
        skip_recent: 剔除最近 N 日（防反转）
    Returns:
        动量因子值，或 None（数据不足/含 NaN）
    """
    required_len = lookback + skip_recent
    if len(close) < required_len:
        return None
    relevant = close.iloc[-required_len:]
    if relevant.isna().any():
        return None
    return float(relevant.iloc[-skip_recent] / relevant.iloc[0] - 1)


def calc_volatility(
    close: pd.Series,
    window: int = 60,
) -> Optional[float]:
    """低波因子：window 日收益率标准差（年化）。

    纯函数：输入 close 序列，输出单值波动率。

    Args:
        close: 复权收盘价序列，按日期升序
        window: 回看窗口（日）
    Returns:
        年化波动率，或 None（数据不足/含 NaN）
    """
    if len(close) < window + 1:
        return None
    recent = close.iloc[-(window + 1):]
    if recent.isna().any():
        return None
    returns = recent.pct_change().dropna()
    if len(returns) < window:
        return None
    return float(returns.std() * np.sqrt(252))


def calc_max_drawdown(
    close: pd.Series,
    window: int = 60,
) -> Optional[float]:
    """低波类：window 日内最大回撤（绝对值，取正）。

    纯函数：输入 close 序列，输出单值。

    Args:
        close: 复权收盘价序列，按日期升序
        window: 回看窗口（日）
    Returns:
        最大回撤（0~1，越大越差），或 None（数据不足）
    """
    if len(close) < window:
        return None
    recent = close.iloc[-window:]
    if recent.isna().any():
        return None
    rolling_max = recent.cummax()
    drawdown = (recent - rolling_max) / rolling_max
    return float(-drawdown.min())


def calc_factor_batch(
    bar_df: pd.DataFrame,
    as_of: str,
    lookback: int = 60,
    skip_recent: int = 5,
) -> pd.DataFrame:
    """批量计算截面动量因子。

    返回 DataFrame: columns = [ts_code, trade_date, factor_name, factor_value, factor_variant, computed_as_of]
    可见性保证：bar_df 必须通过 pit/query_engine 获取（visible_at <= as_of）。

    纯函数：无 IO，不写数据库。

    Args:
        bar_df: 含 (ts_code, trade_date, close_adj) 列的 DataFrame
        as_of: 计算截止日期（ISO 格式），用于标记 computed_as_of
        lookback: 动量回看窗口
        skip_recent: 剔除最近 N 日
    Returns:
        长格式因子 DataFrame
    """
    results = []
    for ts_code, group in bar_df.groupby('ts_code'):
        group = group.sort_values('trade_date')
        if len(group) < lookback + skip_recent:
            continue

        # 动量因子
        val = calc_momentum(group['close_adj'], lookback, skip_recent)
        if val is not None:
            results.append({
                'ts_code': ts_code,
                'trade_date': group['trade_date'].iloc[-1],
                'factor_name': f'momentum_{lookback}d',
                'factor_value': val,
                'factor_variant': 'raw',
                'computed_as_of': as_of,
            })

        # 低波因子（同批次计算）
        vol_val = calc_volatility(group['close_adj'], window=60)
        if vol_val is not None:
            results.append({
                'ts_code': ts_code,
                'trade_date': group['trade_date'].iloc[-1],
                'factor_name': 'volatility_60d',
                'factor_value': vol_val,
                'factor_variant': 'raw',
                'computed_as_of': as_of,
            })

        # 最大回撤因子
        mdd_val = calc_max_drawdown(group['close_adj'], window=60)
        if mdd_val is not None:
            results.append({
                'ts_code': ts_code,
                'trade_date': group['trade_date'].iloc[-1],
                'factor_name': 'max_drawdown_60d',
                'factor_value': mdd_val,
                'factor_variant': 'raw',
                'computed_as_of': as_of,
            })

    if not results:
        return pd.DataFrame(columns=[
            'ts_code', 'trade_date', 'factor_name',
            'factor_value', 'factor_variant', 'computed_as_of',
        ])
    return pd.DataFrame(results)
