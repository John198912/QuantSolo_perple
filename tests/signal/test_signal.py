"""QS-E03 §5.3 信号生成器单元测试。

覆盖：
- 过滤逻辑：ST/停牌/新股/流动性不足股票均被正确剔除
- 波动率倒数加权：高波动率股票分配较低权重
- 单票上限：迭代裁剪后所有权重 ≤ 8%
- 大盘择时：N 日确认延迟正确（不提前切换档位）
- 权重加总 = 1
- select_top_n_with_weights 纯函数性质
- merger 合并后 effective_weight 受择时上限约束
"""
from __future__ import annotations

from decimal import Decimal

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# apply_universe_filter 测试
# ---------------------------------------------------------------------------

class TestApplyUniverseFilter:
    """全市场过滤测试。"""

    def _make_universe(self):
        return ['A', 'B', 'C', 'D', 'E', 'F']

    def test_removes_st_stocks(self):
        """ST 股票被剔除。"""
        from src.signal.core_factor import apply_universe_filter
        result = apply_universe_filter(
            universe=['A', 'B'],
            st_list=['A'],
            suspension_list=[],
            listing_days={'A': 300, 'B': 300},
            avg_turnover={'A': 1e8, 'B': 1e8},
        )
        assert 'A' not in result
        assert 'B' in result

    def test_removes_suspended_stocks(self):
        """停牌股票被剔除。"""
        from src.signal.core_factor import apply_universe_filter
        result = apply_universe_filter(
            universe=['A', 'B'],
            st_list=[],
            suspension_list=['B'],
            listing_days={'A': 300, 'B': 300},
            avg_turnover={'A': 1e8, 'B': 1e8},
        )
        assert 'B' not in result
        assert 'A' in result

    def test_removes_new_listings(self):
        """上市不足 250 日的新股被剔除。"""
        from src.signal.core_factor import apply_universe_filter
        result = apply_universe_filter(
            universe=['A', 'B'],
            st_list=[],
            suspension_list=[],
            listing_days={'A': 249, 'B': 250},
            avg_turnover={'A': 1e8, 'B': 1e8},
        )
        assert 'A' not in result
        assert 'B' in result

    def test_removes_low_liquidity_stocks(self):
        """日均成交额不足 5000 万的股票被剔除。"""
        from src.signal.core_factor import apply_universe_filter
        result = apply_universe_filter(
            universe=['A', 'B'],
            st_list=[],
            suspension_list=[],
            listing_days={'A': 300, 'B': 300},
            avg_turnover={'A': 49_999_999, 'B': 50_000_000},
        )
        assert 'A' not in result
        assert 'B' in result

    def test_removes_high_price_stocks(self):
        """高价股被剔除。"""
        from src.signal.core_factor import apply_universe_filter
        result = apply_universe_filter(
            universe=['A', 'B'],
            st_list=[],
            suspension_list=[],
            listing_days={'A': 300, 'B': 300},
            avg_turnover={'A': 1e8, 'B': 1e8},
            high_price_stocks=['A'],
        )
        assert 'A' not in result
        assert 'B' in result

    def test_empty_universe(self):
        """空 universe 返回空列表。"""
        from src.signal.core_factor import apply_universe_filter
        result = apply_universe_filter(
            universe=[],
            st_list=[],
            suspension_list=[],
            listing_days={},
            avg_turnover={},
        )
        assert result == []

    def test_pure_function(self):
        """纯函数：相同输入 → 相同输出。"""
        from src.signal.core_factor import apply_universe_filter
        kwargs = dict(
            universe=['A', 'B', 'C'],
            st_list=['A'],
            suspension_list=[],
            listing_days={'A': 300, 'B': 300, 'C': 300},
            avg_turnover={'A': 1e8, 'B': 1e8, 'C': 1e8},
        )
        r1 = apply_universe_filter(**kwargs)
        r2 = apply_universe_filter(**kwargs)
        assert r1 == r2


# ---------------------------------------------------------------------------
# select_top_n_with_weights 测试
# ---------------------------------------------------------------------------

class TestSelectTopNWithWeights:
    """Top-N 选股 + 波动率倒数加权测试。"""

    def _make_scores_and_vol(self, n: int = 20):
        np.random.seed(42)
        codes = [f'{i:06d}.SZ' for i in range(n)]
        scores = pd.Series(np.random.rand(n), index=codes)
        # 高波动率（前10只）和低波动率（后10只）
        vols = pd.Series(
            [0.4] * (n // 2) + [0.1] * (n // 2),
            index=codes,
        )
        return scores, vols

    def test_weights_sum_to_one(self):
        """权重加总应等于 1（±1e-6）。"""
        from src.signal.core_factor import select_top_n_with_weights
        scores, vols = self._make_scores_and_vol(20)
        weights = select_top_n_with_weights(scores, vols, top_n=10)
        assert abs(weights.sum() - 1.0) < 1e-6, f"权重加总={weights.sum():.8f}，应=1"

    def test_weights_sum_to_one_small_n(self):
        """n=5 时权重加总=1。"""
        from src.signal.core_factor import select_top_n_with_weights
        scores, vols = self._make_scores_and_vol(10)
        weights = select_top_n_with_weights(scores, vols, top_n=5)
        assert abs(weights.sum() - 1.0) < 1e-6

    def test_single_stock_max_cap(self):
        """单票权重不超过 8%。"""
        from src.signal.core_factor import select_top_n_with_weights
        # 极端情况：只有 1 只股票时权重=1，超过上限
        # 正常情况：15只均匀，每只约 6.7%
        n = 20
        codes = [f'{i:06d}.SZ' for i in range(n)]
        scores = pd.Series(np.arange(n, dtype=float), index=codes)
        vols = pd.Series([0.2] * n, index=codes)
        weights = select_top_n_with_weights(scores, vols, top_n=15, single_stock_max=0.08)
        assert weights.max() <= 0.08 + 1e-9, f"单票最大权重={weights.max():.4f}，应≤0.08"

    def test_high_vol_lower_weight(self):
        """高波动率股票获得较低权重。"""
        from src.signal.core_factor import select_top_n_with_weights
        codes = ['A', 'B', 'C', 'D']
        # 让两只波动率差异悬殊
        scores = pd.Series([0.8, 0.7, 0.6, 0.5], index=codes)
        vols = pd.Series([0.5, 0.5, 0.1, 0.1], index=codes)
        weights = select_top_n_with_weights(scores, vols, top_n=4, single_stock_max=0.9)
        # C, D（低波动率）应比 A, B（高波动率）权重高
        assert weights['C'] > weights['A'], "低波动率股票应有更高权重"
        assert weights['D'] > weights['B'], "低波动率股票应有更高权重"

    def test_returns_series_named_target_weight(self):
        """返回 Series 命名为 target_weight。"""
        from src.signal.core_factor import select_top_n_with_weights
        scores, vols = self._make_scores_and_vol(10)
        weights = select_top_n_with_weights(scores, vols, top_n=5)
        assert weights.name == 'target_weight'

    def test_pure_function(self):
        """纯函数：相同输入 → 相同输出。"""
        from src.signal.core_factor import select_top_n_with_weights
        scores, vols = self._make_scores_and_vol(20)
        w1 = select_top_n_with_weights(scores, vols, top_n=10)
        w2 = select_top_n_with_weights(scores, vols, top_n=10)
        pd.testing.assert_series_equal(w1, w2)

    def test_weight_all_non_negative(self):
        """所有权重非负。"""
        from src.signal.core_factor import select_top_n_with_weights
        scores, vols = self._make_scores_and_vol(20)
        weights = select_top_n_with_weights(scores, vols, top_n=10)
        assert (weights >= 0).all(), "所有权重应 >= 0"

    def test_exactly_top_n_selected(self):
        """恰好选 top_n 只。"""
        from src.signal.core_factor import select_top_n_with_weights
        scores, vols = self._make_scores_and_vol(20)
        weights = select_top_n_with_weights(scores, vols, top_n=7)
        assert len(weights) == 7, f"应选 7 只，得 {len(weights)}"


# ---------------------------------------------------------------------------
# calc_composite_score 测试
# ---------------------------------------------------------------------------

class TestCalcCompositeScore:
    """综合得分测试。"""

    def _make_factor_df(self):
        return pd.DataFrame([
            {'ts_code': 'A', 'factor_name': 'f1', 'processed_value': 1.0},
            {'ts_code': 'A', 'factor_name': 'f2', 'processed_value': 0.5},
            {'ts_code': 'B', 'factor_name': 'f1', 'processed_value': 0.2},
            {'ts_code': 'B', 'factor_name': 'f2', 'processed_value': 0.8},
            {'ts_code': 'C', 'factor_name': 'f1', 'processed_value': 0.5},
            {'ts_code': 'C', 'factor_name': 'f2', 'processed_value': 0.5},
        ])

    def test_returns_series(self):
        """返回 pd.Series。"""
        from src.signal.core_factor import calc_composite_score
        result = calc_composite_score(
            self._make_factor_df(),
            factor_weights={'f1': 0.5, 'f2': 0.5},
            lgbm_score=None,
        )
        assert isinstance(result, pd.Series)

    def test_pure_linear_mode(self):
        """lgbm_score=None 时纯线性模式。"""
        from src.signal.core_factor import calc_composite_score
        r1 = calc_composite_score(
            self._make_factor_df(),
            factor_weights={'f1': 0.5, 'f2': 0.5},
            lgbm_score=None,
        )
        r2 = calc_composite_score(
            self._make_factor_df(),
            factor_weights={'f1': 0.5, 'f2': 0.5},
            lgbm_score=None,
        )
        pd.testing.assert_series_equal(r1, r2)

    def test_higher_f1_gets_higher_rank_with_f1_weight(self):
        """f1 权重较高时，f1 值大的股票排名高。"""
        from src.signal.core_factor import calc_composite_score
        result = calc_composite_score(
            self._make_factor_df(),
            factor_weights={'f1': 1.0, 'f2': 0.0},
            lgbm_score=None,
        )
        # A(f1=1.0) > C(f1=0.5) > B(f1=0.2)
        assert result['A'] > result['C'] > result['B']


# ---------------------------------------------------------------------------
# calc_market_timing 测试
# ---------------------------------------------------------------------------

class TestCalcMarketTiming:
    """大盘择时测试。"""

    def _make_close(self, n: int = 250, trend: float = 0.0):
        """生成模拟沪深300收盘价序列。"""
        np.random.seed(0)
        returns = np.random.normal(trend, 0.01, n)
        close = 3000.0 * np.cumprod(1 + returns)
        return pd.Series(close)

    def test_returns_valid_state(self):
        """返回值为三态之一。"""
        from src.signal.market_timing import calc_market_timing
        close = self._make_close(250)
        result = calc_market_timing(close)
        assert result in ('BULL', 'NEUTRAL', 'BEAR')

    def test_insufficient_data_returns_neutral(self):
        """数据不足返回 NEUTRAL。"""
        from src.signal.market_timing import calc_market_timing
        close = pd.Series([3000.0] * 10)
        result = calc_market_timing(close, ma_window=200, confirmation_days=3)
        assert result == 'NEUTRAL'

    def test_bull_state(self):
        """价格持续在 MA 上方 → BULL。"""
        from src.signal.market_timing import calc_market_timing
        # 构造：前 200 日 MA=3000，最近 5 日价格=3500（持续在上方）
        close = pd.Series([3000.0] * 200 + [3500.0] * 5)
        result = calc_market_timing(close, ma_window=200, confirmation_days=3)
        assert result == 'BULL'

    def test_bear_state(self):
        """价格持续在 MA 下方 → BEAR。"""
        from src.signal.market_timing import calc_market_timing
        # 构造：前 200 日 MA=3000，最近 5 日价格=2500（持续在下方）
        close = pd.Series([3000.0] * 200 + [2500.0] * 5)
        result = calc_market_timing(close, ma_window=200, confirmation_days=3)
        assert result == 'BEAR'

    def test_confirmation_delay(self):
        """N 日确认延迟：仅 1 天在上方不算 BULL（confirmation_days=3）。"""
        from src.signal.market_timing import calc_market_timing
        # 构造：前 200 日=3000，最后 1 天在上方，前 2 天在下方
        close = pd.Series([3000.0] * 198 + [2900.0, 2900.0, 3500.0])
        result = calc_market_timing(close, ma_window=200, confirmation_days=3)
        # 最近 3 天：2900（下方）、2900（下方）、3500（上方）→ 非全部上方 → 非 BULL
        assert result != 'BULL'

    def test_pure_function(self):
        """纯函数：相同输入 → 相同输出。"""
        from src.signal.market_timing import calc_market_timing
        close = self._make_close(250)
        r1 = calc_market_timing(close, ma_window=200, confirmation_days=3)
        r2 = calc_market_timing(close, ma_window=200, confirmation_days=3)
        assert r1 == r2

    def test_timing_caps_complete(self):
        """TIMING_CAPS 覆盖三态。"""
        from src.signal.market_timing import TIMING_CAPS
        assert 'BULL' in TIMING_CAPS
        assert 'NEUTRAL' in TIMING_CAPS
        assert 'BEAR' in TIMING_CAPS

    def test_bull_cap_highest(self):
        """BULL 仓位上限 >= NEUTRAL >= BEAR。"""
        from src.signal.market_timing import TIMING_CAPS
        assert TIMING_CAPS['BULL'] >= TIMING_CAPS['NEUTRAL'] >= TIMING_CAPS['BEAR']

    def test_get_timing_exposure_cap(self):
        """未知状态回退到 NEUTRAL 仓位。"""
        from src.signal.market_timing import get_timing_exposure_cap, TIMING_CAPS
        assert get_timing_exposure_cap('UNKNOWN') == TIMING_CAPS['NEUTRAL']


# ---------------------------------------------------------------------------
# merger 测试
# ---------------------------------------------------------------------------

class TestMergeCoreSignals:
    """信号合并器测试。"""

    def _make_weights(self, codes: list[str], equal: bool = True) -> pd.Series:
        n = len(codes)
        if equal:
            return pd.Series([1.0 / n] * n, index=codes)
        return pd.Series(range(1, n + 1), dtype=float, index=codes)

    def _make_hs300_bull(self, n: int = 210):
        """构造 BULL 市场的沪深300序列。"""
        return pd.Series([3000.0] * 200 + [3500.0] * (n - 200))

    def _make_hs300_bear(self, n: int = 210):
        """构造 BEAR 市场的沪深300序列。"""
        return pd.Series([3000.0] * 200 + [2500.0] * (n - 200))

    def test_returns_list_of_merged_signals(self):
        """返回 MergedSignal 列表。"""
        from src.signal.merger import merge_core_satellite_signals, MergedSignal
        core_w = self._make_weights(['A', 'B', 'C'])
        result = merge_core_satellite_signals(core_w, None, self._make_hs300_bull())
        assert isinstance(result, list)
        assert all(isinstance(s, MergedSignal) for s in result)

    def test_effective_weight_capped_by_timing(self):
        """effective_weight 受择时仓位上限约束。"""
        from src.signal.merger import merge_core_satellite_signals
        from src.signal.market_timing import TIMING_CAPS

        core_w = self._make_weights(['A', 'B', 'C'])
        # BEAR 市：有效仓位上限 0.30
        bear_close = self._make_hs300_bear()
        results = merge_core_satellite_signals(
            core_w, None, bear_close,
            ma_window=200, confirmation_days=3,
        )
        bear_cap = TIMING_CAPS['BEAR']
        for sig in results:
            assert float(sig.effective_weight) <= bear_cap + 1e-9, (
                f"BEAR 市有效权重 {float(sig.effective_weight):.4f} 超过上限 {bear_cap}"
            )

    def test_timing_state_in_signal(self):
        """MergedSignal.timing_state 字段正确。"""
        from src.signal.merger import merge_core_satellite_signals
        core_w = self._make_weights(['A', 'B'])
        results = merge_core_satellite_signals(
            core_w, None, self._make_hs300_bull(),
            ma_window=200, confirmation_days=3,
        )
        assert all(sig.timing_state in ('BULL', 'NEUTRAL', 'BEAR') for sig in results)

    def test_signal_source_core(self):
        """纯核心模式：signal_source 均为 core。"""
        from src.signal.merger import merge_core_satellite_signals
        core_w = self._make_weights(['A', 'B'])
        results = merge_core_satellite_signals(core_w, None, self._make_hs300_bull())
        assert all(sig.signal_source == 'core' for sig in results)

    def test_signal_source_mixed(self):
        """有卫星时：包含 core 和 satellite。"""
        from src.signal.merger import merge_core_satellite_signals
        core_w = self._make_weights(['A', 'B'])
        sat_w = self._make_weights(['X', 'Y'])
        results = merge_core_satellite_signals(core_w, sat_w, self._make_hs300_bull())
        sources = {sig.signal_source for sig in results}
        assert 'core' in sources
        assert 'satellite' in sources

    def test_target_weight_is_decimal(self):
        """target_weight 类型为 Decimal（R6 红线）。"""
        from src.signal.merger import merge_core_satellite_signals
        core_w = self._make_weights(['A', 'B'])
        results = merge_core_satellite_signals(core_w, None, self._make_hs300_bull())
        for sig in results:
            assert isinstance(sig.target_weight, Decimal), (
                f"target_weight 应为 Decimal，得 {type(sig.target_weight)}"
            )

    def test_industry_cap_applied(self):
        """行业上限裁剪后单行业权重不超 30%。"""
        from src.signal.merger import merge_core_satellite_signals, apply_industry_cap

        # 构造 5 只同行业股票，core 权重均等
        codes = ['A', 'B', 'C', 'D', 'E']
        core_w = pd.Series([0.15, 0.15, 0.15, 0.15, 0.15], index=codes)
        industry_map = {c: 'INDUSTRY_X' for c in codes}

        bull_close = self._make_hs300_bull()
        signals = merge_core_satellite_signals(core_w, None, bull_close,
                                               ma_window=200, confirmation_days=3)
        adjusted = apply_industry_cap(signals, industry_map)

        # 该行业有效权重之和 <= 30%
        industry_total = sum(float(sig.effective_weight) for sig in adjusted if industry_map.get(sig.ts_code) == 'INDUSTRY_X')
        assert industry_total <= 0.30 + 1e-6, (
            f"行业权重 {industry_total:.4f} 超过上限 0.30"
        )
