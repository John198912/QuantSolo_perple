"""QS-E03 §5.2 大盘择时信号。

calc_market_timing 返回 'BULL'/'NEUTRAL'/'BEAR'，带 N 日确认延迟。
参数取 load_tunable()['market_timing']。
对应文档 QuantSolo_软件功能设计文档_v1.0.md §5.2（大盘择时）。
"""
from __future__ import annotations

import logging

import pandas as pd

from src.common.config import load_tunable

logger = logging.getLogger(__name__)


# 仓位上限映射（QS-C01 §5.4）
TIMING_CAPS: dict[str, float] = {
    'BULL':    1.00,   # 90-100%（取上限）
    'NEUTRAL': 0.60,
    'BEAR':    0.30,
}


def calc_market_timing(
    hs300_close: pd.Series,
    ma_window: int | None = None,
    confirmation_days: int | None = None,
) -> str:
    """大盘择时总开关（QS-C01 §5.4）。

    沪深 300 vs 200 日 MA → 三档仓位上限。
    ma_window / confirmation_days 若传 None，则从 load_tunable()['market_timing'] 读取。

    Args:
        hs300_close: 沪深 300 日收盘价序列（按日期升序）
        ma_window: 均线窗口（日），默认取 tunable market_timing.ma_window=200
        confirmation_days: N 日确认延迟（防假信号），默认取 tunable market_timing.confirm_days=3

    Returns:
        'BULL' | 'NEUTRAL' | 'BEAR'

    算法：
      1. 计算 ma_window 日 MA
      2. 连续 confirmation_days 天在 MA 上方 → BULL
      3. 连续 confirmation_days 天在 MA 下方 → BEAR
      4. 其他 → NEUTRAL
    """
    tunable = load_tunable()
    mt_cfg = tunable.get('market_timing', {})

    if ma_window is None:
        ma_window = int(mt_cfg.get('ma_window', 200))
    if confirmation_days is None:
        confirmation_days = int(mt_cfg.get('confirm_days', 3))

    if len(hs300_close) < ma_window + confirmation_days:
        logger.debug(
            "calc_market_timing: 数据不足（需 %d，有 %d），返回 NEUTRAL",
            ma_window + confirmation_days, len(hs300_close),
        )
        return 'NEUTRAL'

    ma = hs300_close.rolling(ma_window).mean()
    recent_close = hs300_close.iloc[-confirmation_days:]
    recent_ma = ma.iloc[-confirmation_days:]

    # 确保近期 MA 没有 NaN（窗口未满时）
    if recent_ma.isna().any():
        return 'NEUTRAL'

    above = (recent_close > recent_ma).all()
    below = (recent_close < recent_ma).all()

    if above:
        return 'BULL'
    elif below:
        return 'BEAR'
    return 'NEUTRAL'


def get_timing_exposure_cap(timing_state: str) -> float:
    """根据择时状态返回最大仓位上限。

    Args:
        timing_state: 'BULL' | 'NEUTRAL' | 'BEAR'
    Returns:
        仓位上限（0~1），未知状态返回 NEUTRAL 的 0.60
    """
    return TIMING_CAPS.get(timing_state, TIMING_CAPS['NEUTRAL'])
