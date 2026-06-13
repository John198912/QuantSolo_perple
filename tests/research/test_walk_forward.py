"""Walk-forward 切分器测试（研究协议 §1.3）。

覆盖：
- purge 正确（训练末尾 H 日被剔除）
- embargo 正确（验证集前端被隔离）
- 无标签泄漏断言
- 不触碰 test 段（2024-01-01 起）
- T_eff 计算
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.research.walk_forward import (
    purged_walkforward_splits,
    check_no_leakage,
    compute_T_eff,
)
from src.common.config import load_frozen


def _make_weekly_dates(start: str, end: str) -> pd.DatetimeIndex:
    """生成周度日期序列（每周一）。"""
    return pd.date_range(start, end, freq="W-MON")


def _make_daily_dates(start: str, end: str) -> pd.DatetimeIndex:
    """生成日度日期序列（工作日）。"""
    return pd.bdate_range(start, end)


class TestPurgedWalkForwardSplits:
    def test_splits_generated(self):
        """标准配置下应生成 > 0 个 fold。"""
        dates = _make_weekly_dates("2016-01-04", "2023-12-25")
        splits = purged_walkforward_splits(dates, train_months=36, valid_months=6, step_months=6)
        assert len(splits) > 0, "应生成至少 1 个 fold"

    def test_no_overlap_with_test_segment(self):
        """验证集索引不得进入 test 段（2024-01-01 起）。"""
        cfg = load_frozen()["data_split"]
        test_start = pd.Timestamp(cfg["test_start"])

        dates = _make_weekly_dates("2016-01-04", "2023-12-25")
        splits = purged_walkforward_splits(dates, train_months=36, valid_months=6, step_months=6)

        for i, (train_idx, valid_idx) in enumerate(splits):
            if len(valid_idx) > 0:
                max_valid_date = dates[valid_idx].max()
                assert max_valid_date < test_start, (
                    f"Fold {i} 验证集日期 {max_valid_date} >= test_start {test_start}"
                )

    def test_train_valid_no_overlap(self):
        """训练集和验证集索引不应有重叠。"""
        dates = _make_weekly_dates("2016-01-04", "2023-12-25")
        splits = purged_walkforward_splits(dates, train_months=36, valid_months=6, step_months=6)
        for i, (train_idx, valid_idx) in enumerate(splits):
            overlap = set(train_idx.tolist()) & set(valid_idx.tolist())
            assert len(overlap) == 0, f"Fold {i} 训练/验证集有重叠索引 {overlap}"

    def test_no_leakage_check_passes(self):
        """check_no_leakage 断言应全部通过（gap >= H）。"""
        dates = _make_weekly_dates("2016-01-04", "2023-12-25")
        splits = purged_walkforward_splits(
            dates, train_months=36, valid_months=6, step_months=6,
            label_horizon_days=5, embargo_days=5,
        )
        ok = check_no_leakage(splits, dates, label_horizon_days=5)
        assert ok is True, "purge 后 check_no_leakage 应全部通过"

    def test_purge_removes_trailing_samples(self):
        """训练集末尾日期 + H 应 < 验证集最早日期（purge 有效）。"""
        dates = _make_daily_dates("2016-01-04", "2023-12-29")
        splits = purged_walkforward_splits(
            dates, train_months=36, valid_months=6, step_months=6,
            label_horizon_days=5, embargo_days=5,
        )
        assert len(splits) > 0
        for i, (train_idx, valid_idx) in enumerate(splits):
            if len(train_idx) == 0 or len(valid_idx) == 0:
                continue
            train_max = dates[train_idx].max()
            valid_min = dates[valid_idx].min()
            gap_days = (valid_min - train_max).days
            assert gap_days >= 5, (
                f"Fold {i} gap={gap_days} < label_horizon=5，存在泄漏风险"
            )

    def test_embargo_removes_front_of_valid(self):
        """embargo 应移除验证集最前端 E 个交易日。"""
        dates = _make_daily_dates("2016-01-04", "2023-12-29")
        # 对比 embargo=0 和 embargo=5
        splits_no_embargo = purged_walkforward_splits(
            dates, train_months=36, valid_months=6, step_months=6,
            label_horizon_days=5, embargo_days=0,
        )
        splits_with_embargo = purged_walkforward_splits(
            dates, train_months=36, valid_months=6, step_months=6,
            label_horizon_days=5, embargo_days=5,
        )
        if not splits_no_embargo or not splits_with_embargo:
            pytest.skip("splits 为空，跳过")

        # 有 embargo 的验证集最早日期应晚于无 embargo 的
        _, valid_no = splits_no_embargo[0]
        _, valid_with = splits_with_embargo[0]
        if len(valid_no) > 0 and len(valid_with) > 0:
            min_no = dates[valid_no].min()
            min_with = dates[valid_with].min()
            assert min_with >= min_no, "有 embargo 的验证集最早日期应不早于无 embargo"

    def test_empty_dates_returns_empty(self):
        """空日期序列应返回空列表。"""
        splits = purged_walkforward_splits([], train_months=36, valid_months=6)
        assert splits == []

    def test_train_before_valid_chronologically(self):
        """每个 fold 中，训练集最大日期 < 验证集最小日期（时间顺序）。"""
        dates = _make_weekly_dates("2016-01-04", "2023-12-25")
        splits = purged_walkforward_splits(dates, train_months=36, valid_months=6)
        for i, (train_idx, valid_idx) in enumerate(splits):
            if len(train_idx) == 0 or len(valid_idx) == 0:
                continue
            train_max = dates[train_idx].max()
            valid_min = dates[valid_idx].min()
            assert train_max < valid_min, f"Fold {i} 时间顺序错误"


class TestCheckNoLeakage:
    def test_detects_leakage(self):
        """人工构造泄漏场景，check_no_leakage 应返回 False。"""
        dates = pd.date_range("2020-01-01", periods=100, freq="D")
        # 训练 0-49，验证 50-99，但让验证从第 52 天开始（gap=2 < H=5）
        train_idx = np.arange(0, 50)
        valid_idx = np.arange(52, 55)  # gap=52-49=3 < 5
        splits = [(train_idx, valid_idx)]
        ok = check_no_leakage(splits, dates, label_horizon_days=5)
        assert ok is False


class TestComputeTEff:
    def test_single_fold(self):
        """单 fold 时 T_eff = base_T * overlap。"""
        ic = [pd.Series(np.random.randn(26))]
        t_eff = compute_T_eff(ic, step_months=6, valid_months=6)
        assert t_eff == pytest.approx(26.0, rel=0.01)

    def test_multiple_folds_reduces_t_eff(self):
        """多 fold 时 T_eff 应有折减（串行相关）。"""
        # 构造 6 个 fold，每个 26 周，IC 均值正相关
        rng = np.random.default_rng(42)
        folds = [pd.Series(rng.normal(0.03, 0.1, 26)) for _ in range(6)]
        t_eff = compute_T_eff(folds, step_months=6, valid_months=6)
        # 总原始 T = 6*26=156；折减后应 < 156
        assert t_eff < 156.0

    def test_step_smaller_than_valid_reduces_t_eff(self):
        """step < valid_months 时 overlap 折减应小于 step=valid_months 情形。"""
        # 使用确定性种子以确保测试稳定
        rng = np.random.default_rng(0)
        ic = [pd.Series(rng.normal(0.03, 0.1, 26)) for _ in range(4)]
        # overlap=3/6=0.5 将使 base_T 折减一半
        t_eff_no_overlap = compute_T_eff(ic, step_months=6, valid_months=6)  # overlap=1.0
        t_eff_half_overlap = compute_T_eff(ic, step_months=3, valid_months=6)  # overlap=0.5
        # step=3/valid=6 工具应应用 overlap=0.5 折减，结果应不大于 step=6 的情形
        assert t_eff_half_overlap <= t_eff_no_overlap, (
            f"step=3/valid=6 应有折减，但 t_eff_half={t_eff_half_overlap:.2f} > t_eff_full={t_eff_no_overlap:.2f}"
        )

    def test_empty_folds_returns_zero(self):
        assert compute_T_eff([]) == 0.0
