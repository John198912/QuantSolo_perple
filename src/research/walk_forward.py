"""Purged & Embargo Walk-Forward 切分器（研究协议 §1.3）。

防标签泄漏的滚动窗口切分：
- purge：训练集末尾与验证标签时间窗重叠的样本剔除（label_horizon 交易日）
- embargo：验证集之后再隔离 E 日（约等于 label_horizon）防序列自相关泄漏
- 滚动窗口：训练 36 月 / 验证 6 月 / 步长 6 月，严格在 train+validation(2016-2023) 内
- purge/embargo 的 H/E 按 trade_calendar 交易日计，非自然日

R3：切分边界取 load_frozen()['data_split']，禁止硬编码。
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from src.common.config import load_frozen

logger = logging.getLogger(__name__)


def _get_split_cfg() -> dict:
    """获取冻结切分参数。"""
    return dict(load_frozen()["data_split"])


def purged_walkforward_splits(
    dates: pd.DatetimeIndex | list[str],
    train_months: int = 36,
    valid_months: int = 6,
    step_months: int = 6,
    label_horizon_days: int = 5,
    embargo_days: int = 5,
    calendar: Optional[pd.DatetimeIndex] = None,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """生成 (train_idx, valid_idx) 序列，已 purge label 重叠样本并施加 embargo。

    铁律：不触碰 test 段（load_frozen()['data_split']['test_start'] 起），
          仅在 train+validation (2016-2023) 内滚动。

    Args:
        dates:               样本日期序列（pd.DatetimeIndex 或 str list，已排序）
        train_months:        训练窗口月数（默认 36）
        valid_months:        验证窗口月数（默认 6）
        step_months:         步长月数（默认 6）
        label_horizon_days:  标签 horizon H（交易日），用于 purge 末尾 H 日
        embargo_days:        embargo 天数 E（交易日，约等于 H）
        calendar:            交易日历（DatetimeIndex）；None 时用自然日近似

    Returns:
        list of (train_idx, valid_idx)：numpy array of integer indices。
        train_idx 已剔除与验证标签重叠的末尾样本（purged）。
        valid_idx 已施加前端 embargo（移除验证集最前 E 交易日）。

    Note:
        - purge/embargo 按 calendar 交易日计，若 calendar=None 则按自然日近似（测试用）
        - 验证集最早不早于训练窗口结束；最晚不晚于 test_start 前一日
    """
    cfg = _get_split_cfg()
    # 冻结边界
    allowed_end_str = cfg["validation_end"]  # "2023-12-31"
    test_start_str = cfg["test_start"]       # "2024-01-01"
    train_start_str = cfg["train_start"]     # "2016-01-01"

    # 统一转为 DatetimeIndex
    if not isinstance(dates, pd.DatetimeIndex):
        dates_idx = pd.to_datetime(dates)
    else:
        dates_idx = dates

    dates_arr = np.array(dates_idx)
    n = len(dates_arr)
    if n == 0:
        return []

    allowed_end = pd.Timestamp(allowed_end_str)
    test_start = pd.Timestamp(test_start_str)
    train_start = pd.Timestamp(train_start_str)

    # 过滤：只在 train_start ~ allowed_end 内滚动
    all_indices = np.arange(n)
    valid_mask = (dates_arr >= np.datetime64(train_start)) & (dates_arr <= np.datetime64(allowed_end))
    valid_global_indices = all_indices[valid_mask]
    valid_dates = dates_arr[valid_mask]

    if len(valid_global_indices) == 0:
        return []

    splits: list[tuple[np.ndarray, np.ndarray]] = []

    # 构造交易日历用于 purge/embargo 计数
    if calendar is not None:
        cal_sorted = np.sort(np.array(pd.DatetimeIndex(calendar)))
    else:
        # 无交易日历时使用自然日近似（测试场景）
        cal_sorted = None

    def trading_days_ahead(ref_date: np.datetime64, n_days: int) -> np.datetime64:
        """在 ref_date 之后的第 n_days 个交易日（或自然日）。"""
        if cal_sorted is None:
            return ref_date + np.timedelta64(n_days, "D")
        future = cal_sorted[cal_sorted > ref_date]
        if len(future) >= n_days:
            return future[n_days - 1]
        return future[-1] if len(future) > 0 else ref_date

    def trading_days_before(ref_date: np.datetime64, n_days: int) -> np.datetime64:
        """在 ref_date 之前的第 n_days 个交易日（或自然日）。"""
        if cal_sorted is None:
            return ref_date - np.timedelta64(n_days, "D")
        past = cal_sorted[cal_sorted < ref_date]
        if len(past) >= n_days:
            return past[-n_days]
        return past[0] if len(past) > 0 else ref_date

    # 滚动起始点：以 train_start 为基准，按 step_months 推进
    cursor = pd.Timestamp(train_start_str)
    while True:
        train_end = cursor + pd.DateOffset(months=train_months)
        valid_start = train_end
        valid_end = valid_start + pd.DateOffset(months=valid_months)

        # 不得超过验证集边界
        if valid_start > allowed_end:
            break
        if valid_end > allowed_end + pd.DateOffset(days=1):
            valid_end = allowed_end + pd.DateOffset(days=1)

        # 不得进入 test 段
        if valid_start >= test_start:
            break

        train_end_ts = np.datetime64(train_end)
        valid_start_ts = np.datetime64(valid_start)
        valid_end_ts = np.datetime64(valid_end)
        cursor_ts = np.datetime64(cursor)

        # ── train indices ──
        raw_train_mask = (valid_dates >= np.datetime64(cursor)) & (valid_dates < train_end_ts)
        raw_train_local = np.where(raw_train_mask)[0]

        # purge：剔除训练集末尾 label_horizon_days 交易日
        # 即剔除 valid_start - H 交易日 ~ valid_start 之间的样本
        purge_cutoff = trading_days_before(valid_start_ts, label_horizon_days)
        purge_mask = valid_dates[raw_train_local] >= purge_cutoff
        train_local_purged = raw_train_local[~purge_mask]
        train_global = valid_global_indices[train_local_purged]

        # ── valid indices ──
        raw_valid_mask = (valid_dates >= valid_start_ts) & (valid_dates < valid_end_ts)
        raw_valid_local = np.where(raw_valid_mask)[0]

        # embargo：验证集前端去掉 embargo_days 交易日
        embargo_cutoff = trading_days_ahead(valid_start_ts, embargo_days)
        embargo_mask = valid_dates[raw_valid_local] < embargo_cutoff
        valid_local_embargoed = raw_valid_local[~embargo_mask]
        valid_global = valid_global_indices[valid_local_embargoed]

        if len(train_global) > 0 and len(valid_global) > 0:
            splits.append((train_global, valid_global))

        cursor += pd.DateOffset(months=step_months)

    logger.info(
        "purged_walkforward_splits 生成 %d 折，label_horizon=%d, embargo=%d 交易日",
        len(splits), label_horizon_days, embargo_days,
    )
    return splits


def check_no_leakage(
    splits: list[tuple[np.ndarray, np.ndarray]],
    dates: pd.DatetimeIndex | list[str],
    label_horizon_days: int = 5,
) -> bool:
    """断言所有 fold 无标签泄漏（train 末尾日期 + H < valid 最早日期）。

    Args:
        splits:              purged_walkforward_splits 返回值
        dates:               原始日期序列
        label_horizon_days:  标签 horizon H

    Returns:
        True = 无泄漏；False = 存在泄漏（并记录 ERROR 日志）。
    """
    if not isinstance(dates, pd.DatetimeIndex):
        dates_idx = pd.to_datetime(dates)
    else:
        dates_idx = dates

    ok = True
    for i, (train_idx, valid_idx) in enumerate(splits):
        if len(train_idx) == 0 or len(valid_idx) == 0:
            continue
        train_max_date = dates_idx[train_idx].max()
        valid_min_date = dates_idx[valid_idx].min()
        gap = (valid_min_date - train_max_date).days
        if gap < label_horizon_days:
            logger.error(
                "Fold %d 存在潜在标签泄漏！train_max=%s valid_min=%s gap=%d < H=%d",
                i, train_max_date.date(), valid_min_date.date(), gap, label_horizon_days,
            )
            ok = False
    return ok


def compute_T_eff(
    fold_ic_series: list[pd.Series],
    step_months: int = 6,
    valid_months: int = 6,
) -> float:
    """T_eff = 非重叠的【周频】组合收益观察数（非日频交易日数）。

    研究协议 §3.5(d)：T_eff 统一周频 + fold 间自相关折减。

    Args:
        fold_ic_series: 各 fold 的周度 IC 时间序列（pd.Series 列表）
        step_months:    走前步长（月）
        valid_months:   验证窗口（月）

    Returns:
        T_eff（float，周频有效观测数）
    """
    if not fold_ic_series:
        return 0.0

    base_T = sum(len(s) for s in fold_ic_series)  # 各 fold 周频 IC 数

    # 重叠比例折减
    overlap = step_months / valid_months if step_months < valid_months else 1.0
    T_dedup = base_T * overlap

    if len(fold_ic_series) > 1:
        # fold 间串行相关折减
        fold_means = [float(s.mean()) for s in fold_ic_series]
        rho = float(np.corrcoef(fold_means[:-1], fold_means[1:])[0, 1])
        if np.isnan(rho):
            rho = 0.0
        k = len(fold_ic_series)
        T_final = T_dedup / (1 + max(rho, 0.0) * (k - 1) / k)
        # 上界约束防高估
        T_final = min(T_final, base_T * 1.5 / k)
        return float(T_final)

    return float(T_dedup)
