"""QS-E03 §3.5 因子计算引擎单元测试。

覆盖：
- 纯函数性质（相同输入→相同输出）
- MAD 去极值边界（极端值截断，中间值不变）
- 中性化残差验证（残差与 log_mktcap/行业哑变量相关性接近 0）
- z-score 输出（均值≈0，标准差≈1）
- 动量因子边界（长度不足/含 NaN 返回 None）
- 质量因子边界（confidence_tag=INSUFFICIENT 跳过）
- factor_variant 三变体生成
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# transforms 测试
# ---------------------------------------------------------------------------

class TestMadWinsorize:
    """MAD 去极值测试。"""

    def test_pure_function_determinism(self):
        """相同输入 → 相同输出（确定性）。"""
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 100.0, -50.0])
        r1 = __import__('src.factor.transforms', fromlist=['mad_winsorize']).mad_winsorize(s)
        r2 = __import__('src.factor.transforms', fromlist=['mad_winsorize']).mad_winsorize(s)
        pd.testing.assert_series_equal(r1, r2)

    def test_extreme_values_clipped(self):
        """极端值被截断到正常范围。"""
        from src.factor.transforms import mad_winsorize
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 1000.0, -1000.0])
        result = mad_winsorize(s, n=3.0)
        assert result.max() < 1000.0, "极大值应被截断"
        assert result.min() > -1000.0, "极小值应被截断"

    def test_middle_values_unchanged(self):
        """中间值不变。"""
        from src.factor.transforms import mad_winsorize
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 1000.0])
        result = mad_winsorize(s, n=3.0)
        # 中位数为 3.0，中间值 2.0, 3.0, 4.0 应保持不变
        pd.testing.assert_series_equal(result.iloc[:4], s.iloc[:4], check_names=False)

    def test_nan_preserved(self):
        """NaN 保持 NaN，不填充。"""
        from src.factor.transforms import mad_winsorize
        s = pd.Series([1.0, np.nan, 3.0, 4.0, 5.0])
        result = mad_winsorize(s)
        assert pd.isna(result.iloc[1]), "NaN 应保留"

    def test_n_parameter(self):
        """较小的 n 截断更多。"""
        from src.factor.transforms import mad_winsorize
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 20.0])
        r_n3 = mad_winsorize(s, n=3.0)
        r_n1 = mad_winsorize(s, n=1.0)
        assert r_n1.max() <= r_n3.max(), "n=1 截断应更严"


class TestCrossSectionalZscore:
    """截面 z-score 测试。"""

    def test_mean_near_zero(self):
        """均值应接近 0。"""
        from src.factor.transforms import cross_sectional_zscore
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = cross_sectional_zscore(s)
        assert abs(result.mean()) < 1e-6, f"均值应≈0，得 {result.mean()}"

    def test_std_near_one(self):
        """标准差应接近 1。"""
        from src.factor.transforms import cross_sectional_zscore
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = cross_sectional_zscore(s)
        assert abs(result.std() - 1.0) < 1e-4, f"标准差应≈1，得 {result.std()}"

    def test_pure_function(self):
        """纯函数：相同输入 → 相同输出。"""
        from src.factor.transforms import cross_sectional_zscore
        s = pd.Series([3.0, 1.0, 4.0, 1.0, 5.0])
        r1 = cross_sectional_zscore(s)
        r2 = cross_sectional_zscore(s)
        pd.testing.assert_series_equal(r1, r2)

    def test_constant_series(self):
        """常数序列：标准差接近 0，输出全 0（不崩溃）。"""
        from src.factor.transforms import cross_sectional_zscore
        s = pd.Series([5.0, 5.0, 5.0, 5.0])
        result = cross_sectional_zscore(s)
        assert result.abs().max() < 1e-3, "常数序列 z-score 应接近 0"


class TestIndustryMktcapNeutralize:
    """行业+市值中性化测试。"""

    def _make_sample_data(self, n: int = 50):
        """生成有行业效应和市值效应的模拟数据。"""
        np.random.seed(42)
        industry = pd.Series(
            ['A'] * (n // 2) + ['B'] * (n - n // 2),
            name='industry',
        )
        log_mktcap = pd.Series(np.random.normal(10, 1, n), name='log_mktcap')
        # factor 与行业/市值有相关性
        factor = (
            log_mktcap * 0.5
            + (industry == 'A').astype(float) * 2.0
            + pd.Series(np.random.normal(0, 0.5, n))
        )
        return factor, industry, log_mktcap

    def test_residual_low_correlation_with_mktcap(self):
        """中性化后残差与 log_mktcap 相关性接近 0。"""
        pytest.importorskip("statsmodels")
        from src.factor.transforms import industry_mktcap_neutralize

        factor, industry, log_mktcap = self._make_sample_data()
        residuals = industry_mktcap_neutralize(factor, industry, log_mktcap)

        corr = residuals.corr(log_mktcap)
        assert abs(corr) < 0.05, f"残差与市值相关性={corr:.4f}，应接近 0"

    def test_residual_low_correlation_with_industry(self):
        """中性化后残差与行业哑变量相关性接近 0。"""
        pytest.importorskip("statsmodels")
        from src.factor.transforms import industry_mktcap_neutralize

        factor, industry, log_mktcap = self._make_sample_data()
        residuals = industry_mktcap_neutralize(factor, industry, log_mktcap)

        industry_dummy = (industry == 'A').astype(float)
        corr = residuals.corr(industry_dummy)
        assert abs(corr) < 0.05, f"残差与行业相关性={corr:.4f}，应接近 0"

    def test_pure_function(self):
        """纯函数：相同输入 → 相同输出。"""
        pytest.importorskip("statsmodels")
        from src.factor.transforms import industry_mktcap_neutralize

        factor, industry, log_mktcap = self._make_sample_data()
        r1 = industry_mktcap_neutralize(factor, industry, log_mktcap)
        r2 = industry_mktcap_neutralize(factor, industry, log_mktcap)
        pd.testing.assert_series_equal(r1, r2)


# ---------------------------------------------------------------------------
# momentum 测试
# ---------------------------------------------------------------------------

class TestCalcMomentum:
    """动量因子测试。"""

    def test_basic_return(self):
        """基本动量计算正确。"""
        from src.factor.momentum import calc_momentum
        # close[-5] / close[-65] - 1 = 110/100 - 1 = 0.1
        close = pd.Series([100.0] * 60 + [110.0] * 5)  # len=65
        result = calc_momentum(close, lookback=60, skip_recent=5)
        assert result is not None
        assert abs(result - 0.1) < 1e-9

    def test_insufficient_data_returns_none(self):
        """长度不足返回 None。"""
        from src.factor.momentum import calc_momentum
        close = pd.Series([1.0] * 10)
        result = calc_momentum(close, lookback=60, skip_recent=5)
        assert result is None

    def test_nan_returns_none(self):
        """含 NaN 返回 None。"""
        from src.factor.momentum import calc_momentum
        close = pd.Series([1.0] * 64 + [np.nan])
        result = calc_momentum(close, lookback=60, skip_recent=5)
        assert result is None

    def test_pure_function(self):
        """纯函数：相同输入 → 相同输出。"""
        from src.factor.momentum import calc_momentum
        close = pd.Series(range(1, 70), dtype=float)
        r1 = calc_momentum(close)
        r2 = calc_momentum(close)
        assert r1 == r2


class TestCalcFactorBatch:
    """批量动量因子测试。"""

    def _make_bar_df(self, n_stocks: int = 3, n_days: int = 80):
        """生成模拟日线数据。"""
        rows = []
        for i in range(n_stocks):
            ts_code = f"{10000 + i:06d}.SZ"
            for d in range(n_days):
                rows.append({
                    'ts_code': ts_code,
                    'trade_date': f"2024-{d // 28 + 1:02d}-{d % 28 + 1:02d}",
                    'close_adj': 10.0 + d * 0.01 + i,
                })
        return pd.DataFrame(rows)

    def test_returns_dataframe(self):
        """返回 DataFrame，列齐全。"""
        from src.factor.momentum import calc_factor_batch
        bar_df = self._make_bar_df()
        result = calc_factor_batch(bar_df, as_of='2024-12-31')
        assert isinstance(result, pd.DataFrame)
        required_cols = {'ts_code', 'trade_date', 'factor_name', 'factor_value', 'factor_variant', 'computed_as_of'}
        assert required_cols.issubset(set(result.columns))

    def test_pure_function_no_io(self):
        """纯函数：相同输入两次调用结果相同。"""
        from src.factor.momentum import calc_factor_batch
        bar_df = self._make_bar_df()
        r1 = calc_factor_batch(bar_df, as_of='2024-12-31')
        r2 = calc_factor_batch(bar_df, as_of='2024-12-31')
        pd.testing.assert_frame_equal(r1.reset_index(drop=True), r2.reset_index(drop=True))

    def test_computed_as_of_matches(self):
        """computed_as_of 等于传入的 as_of。"""
        from src.factor.momentum import calc_factor_batch
        bar_df = self._make_bar_df()
        result = calc_factor_batch(bar_df, as_of='2024-06-30')
        if len(result) > 0:
            assert all(result['computed_as_of'] == '2024-06-30')

    def test_factor_variant_is_raw(self):
        """factor_variant 应为 'raw'。"""
        from src.factor.momentum import calc_factor_batch
        bar_df = self._make_bar_df()
        result = calc_factor_batch(bar_df, as_of='2024-12-31')
        if len(result) > 0:
            assert all(result['factor_variant'] == 'raw')

    def test_empty_when_insufficient_data(self):
        """数据不足时返回空 DataFrame（不崩溃）。"""
        from src.factor.momentum import calc_factor_batch
        bar_df = pd.DataFrame({
            'ts_code': ['000001.SZ'] * 5,
            'trade_date': ['2024-01-01'] * 5,
            'close_adj': [10.0] * 5,
        })
        result = calc_factor_batch(bar_df, as_of='2024-01-31')
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 0


class TestCalcVolatility:
    """低波因子测试。"""

    def test_basic_volatility(self):
        """波动率计算不为 None。"""
        from src.factor.momentum import calc_volatility
        close = pd.Series(np.random.lognormal(0, 0.01, 70))
        result = calc_volatility(close, window=60)
        assert result is not None
        assert result > 0

    def test_insufficient_data_returns_none(self):
        """数据不足返回 None。"""
        from src.factor.momentum import calc_volatility
        close = pd.Series([1.0] * 5)
        result = calc_volatility(close, window=60)
        assert result is None


# ---------------------------------------------------------------------------
# quality 测试
# ---------------------------------------------------------------------------

class TestQualityFactors:
    """质量因子测试。"""

    def test_roe_basic(self):
        """ROE = 净利润 / 净资产。"""
        from src.factor.quality import calc_roe
        result = calc_roe(20.0, 100.0)
        assert result == pytest.approx(0.2)

    def test_roe_none_on_zero_equity(self):
        """净资产为 0 返回 None。"""
        from src.factor.quality import calc_roe
        assert calc_roe(20.0, 0.0) is None

    def test_roe_none_on_none_input(self):
        """输入 None 返回 None。"""
        from src.factor.quality import calc_roe
        assert calc_roe(None, 100.0) is None
        assert calc_roe(20.0, None) is None

    def test_ocf_ratio_basic(self):
        """OCF 比率基本计算。"""
        from src.factor.quality import calc_ocf_ratio
        result = calc_ocf_ratio(25.0, 20.0)
        assert result == pytest.approx(1.25)

    def test_ocf_ratio_none_on_zero_profit(self):
        """净利润为 0 返回 None。"""
        from src.factor.quality import calc_ocf_ratio
        assert calc_ocf_ratio(25.0, 0.0) is None

    def test_gross_margin_basic(self):
        """毛利率 = (收入-成本)/收入。"""
        from src.factor.quality import calc_gross_margin
        result = calc_gross_margin(100.0, 60.0)
        assert result == pytest.approx(0.4)

    def test_debt_ratio_basic(self):
        """资产负债率 = 总负债/总资产。"""
        from src.factor.quality import calc_debt_ratio
        result = calc_debt_ratio(300.0, 1000.0)
        assert result == pytest.approx(0.3)

    def test_quality_batch_skips_insufficient(self):
        """confidence_tag='INSUFFICIENT' 的行被跳过。"""
        from src.factor.quality import calc_quality_factors_batch

        financials_df = pd.DataFrame([
            {
                'ts_code': '000001.SZ',
                'ann_date': '2024-01-01',
                'net_profit': 20.0,
                'total_equity': 100.0,
                'revenue': 100.0,
                'cogs': 60.0,
                'ocf': 25.0,
                'total_liabilities': 300.0,
                'total_assets': 1000.0,
                'confidence_tag': 'INSUFFICIENT',  # 应被跳过
            },
            {
                'ts_code': '000002.SZ',
                'ann_date': '2024-01-01',
                'net_profit': 30.0,
                'total_equity': 200.0,
                'revenue': 200.0,
                'cogs': 100.0,
                'ocf': 40.0,
                'total_liabilities': 500.0,
                'total_assets': 2000.0,
                'confidence_tag': 'OK',  # 正常处理
            },
        ])
        result = calc_quality_factors_batch(financials_df, as_of='2024-03-31')
        assert isinstance(result, pd.DataFrame)
        # 只有 000002 的行
        if len(result) > 0:
            assert '000001.SZ' not in result['ts_code'].values

    def test_quality_batch_returns_correct_columns(self):
        """批量质量因子返回正确列。"""
        from src.factor.quality import calc_quality_factors_batch

        financials_df = pd.DataFrame([{
            'ts_code': '000001.SZ',
            'ann_date': '2024-01-01',
            'net_profit': 20.0,
            'total_equity': 100.0,
            'revenue': 100.0,
            'cogs': 60.0,
            'ocf': 25.0,
            'total_liabilities': 300.0,
            'total_assets': 1000.0,
            'confidence_tag': 'OK',
        }])
        result = calc_quality_factors_batch(financials_df, as_of='2024-01-31')
        required_cols = {'ts_code', 'trade_date', 'factor_name', 'factor_value', 'factor_variant', 'computed_as_of'}
        assert required_cols.issubset(set(result.columns))

    def test_quality_batch_pure_function(self):
        """纯函数：相同输入 → 相同输出。"""
        from src.factor.quality import calc_quality_factors_batch

        financials_df = pd.DataFrame([{
            'ts_code': '000001.SZ',
            'ann_date': '2024-01-01',
            'net_profit': 20.0,
            'total_equity': 100.0,
            'revenue': 100.0,
            'cogs': 60.0,
            'ocf': 25.0,
            'total_liabilities': 300.0,
            'total_assets': 1000.0,
            'confidence_tag': 'OK',
        }])
        r1 = calc_quality_factors_batch(financials_df, as_of='2024-01-31')
        r2 = calc_quality_factors_batch(financials_df, as_of='2024-01-31')
        pd.testing.assert_frame_equal(r1.reset_index(drop=True), r2.reset_index(drop=True))


# ---------------------------------------------------------------------------
# pipeline 测试
# ---------------------------------------------------------------------------

class TestBuildFactorVariants:
    """因子三变体流水线测试。"""

    def _make_raw_df(self, n: int = 30):
        """生成宽格式原始因子 DataFrame。"""
        np.random.seed(123)
        data = {
            'ts_code': [f'{i:06d}.SZ' for i in range(n)],
            'trade_date': ['2024-03-01'] * n,
            'computed_as_of': ['2024-03-01'] * n,
            'factor_variant': ['raw'] * n,
            'momentum_60d': np.random.normal(0.05, 0.1, n),
            'roe_ttm': np.random.normal(0.12, 0.05, n),
        }
        return pd.DataFrame(data)

    def test_returns_three_variants(self):
        """返回三个变体键。"""
        pytest.importorskip("statsmodels")
        from src.factor.pipeline import build_factor_variants

        raw_df = self._make_raw_df()
        industry = pd.Series(['A'] * 15 + ['B'] * 15)
        log_mktcap = pd.Series(np.random.normal(10, 1, 30))

        result = build_factor_variants(raw_df, industry, log_mktcap)
        assert set(result.keys()) == {'raw', 'processed', 'orthogonal'}

    def test_raw_variant_unchanged(self):
        """raw 变体应为原始输入。"""
        pytest.importorskip("statsmodels")
        from src.factor.pipeline import build_factor_variants

        raw_df = self._make_raw_df()
        industry = pd.Series(['A'] * 15 + ['B'] * 15)
        log_mktcap = pd.Series(np.random.normal(10, 1, 30))

        result = build_factor_variants(raw_df, industry, log_mktcap)
        pd.testing.assert_frame_equal(result['raw'], raw_df)

    def test_processed_variant_has_factor_cols(self):
        """processed 变体含因子列。"""
        pytest.importorskip("statsmodels")
        from src.factor.pipeline import build_factor_variants

        raw_df = self._make_raw_df()
        industry = pd.Series(['A'] * 15 + ['B'] * 15)
        log_mktcap = pd.Series(np.random.normal(10, 1, 30))

        result = build_factor_variants(raw_df, industry, log_mktcap)
        assert 'momentum_60d' in result['processed'].columns
        assert 'roe_ttm' in result['processed'].columns

    def test_pivot_factor_df(self):
        """pivot_factor_df 正确转换长格式。"""
        from src.factor.pipeline import pivot_factor_df

        long_df = pd.DataFrame([
            {'ts_code': 'A', 'factor_name': 'f1', 'factor_value': 1.0, 'factor_variant': 'raw'},
            {'ts_code': 'A', 'factor_name': 'f2', 'factor_value': 2.0, 'factor_variant': 'raw'},
            {'ts_code': 'B', 'factor_name': 'f1', 'factor_value': 3.0, 'factor_variant': 'raw'},
            {'ts_code': 'B', 'factor_name': 'f2', 'factor_value': 4.0, 'factor_variant': 'raw'},
        ])
        pivot = pivot_factor_df(long_df, 'raw')
        assert pivot.loc['A', 'f1'] == pytest.approx(1.0)
        assert pivot.loc['B', 'f2'] == pytest.approx(4.0)
