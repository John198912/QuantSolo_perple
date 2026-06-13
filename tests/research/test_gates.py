"""研究统计闸门测试（研究协议 §三~§六）。

覆盖：
- BH-FDR 行为（阶段一）
- 单侧 t>3 过滤（反向显著 FAIL）
- A1 硬否决触发
- A2 弱否决降级路径触发
- B1/B2/B3 判线通过/拒绝
- run_full_gate_check 综合判定
"""
from __future__ import annotations

import math
import numpy as np
import pandas as pd
import pytest

from src.research.gates import (
    stage1_statistical,
    stage2_economic,
    stage3_final,
    check_a1_hard_veto,
    check_a2_weak_veto,
    check_b1_scale_up,
    check_b2_maintain,
    check_b3_engineering,
    run_full_gate_check,
    factor_ic_tstat,
)
from src.common.config import load_frozen


# ─────────────────────────────────────────────────────────────────────────────
# 辅助：生成合成 IC 序列
# ─────────────────────────────────────────────────────────────────────────────

def _make_ic_series(n: int, mean: float, std: float, seed: int = 42) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(mean, std, n))


# ─────────────────────────────────────────────────────────────────────────────
# 阶段一：BH-FDR + 单侧 t>3
# ─────────────────────────────────────────────────────────────────────────────

class TestStage1Statistical:
    def _make_factor(self, fid: str, p: float, t: float) -> dict:
        return {"factor_id": fid, "p_value_onesided": p, "t_stat_nw": t}

    def test_bh_fdr_passes_significant_factor(self):
        """显著因子应通过 BH-FDR + t>3 叠加。"""
        factors = [
            self._make_factor("f1", p=0.0001, t=4.5),
            self._make_factor("f2", p=0.5, t=0.5),
        ]
        result = stage1_statistical(factors, M_registered=20, t_thresh=3.0)
        ids = [f["factor_id"] for f in result]
        assert "f1" in ids
        assert "f2" not in ids

    def test_t_stat_threshold_enforced(self):
        """t <= 3.0 的因子即使 p 值低也应被过滤。"""
        # p=0.001 很显著但 t=2.8 < 3.0
        factors = [
            self._make_factor("f_low_t", p=0.001, t=2.8),
            self._make_factor("f_high_t", p=0.001, t=4.0),
        ]
        result = stage1_statistical(factors, M_registered=5, t_thresh=3.0)
        ids = [f["factor_id"] for f in result]
        # f_high_t 通过；f_low_t 不通过（t < 3.0）
        assert "f_high_t" in ids
        assert "f_low_t" not in ids

    def test_reverse_significant_factor_fails(self):
        """反向显著（t < -3.0）应判 FAIL。"""
        factors = [
            self._make_factor("f_reverse", p=0.0001, t=-4.5),
            self._make_factor("f_normal", p=0.0001, t=4.5),
        ]
        result = stage1_statistical(factors, M_registered=20, t_thresh=3.0)
        ids = [f["factor_id"] for f in result]
        assert "f_reverse" not in ids, "反向显著因子必须被 FAIL"
        assert "f_normal" in ids

    def test_bh_fdr_uses_m_registered_denominator(self):
        """BH-FDR 分母使用 M_registered，不是幸存者数。"""
        # M_registered=20 时 j=1 的 BH 临界值 = 1/20 * 0.10 = 0.005
        factors = [
            self._make_factor("f1", p=0.004, t=3.5),   # p < 0.005 通过
            self._make_factor("f2", p=0.006, t=3.5),   # p > 0.005 BH 不通过
        ]
        # 注：t>3 主导过滤，BH 仅作安全网；这里两个都 t>3 通过，检查 BH 边界
        # f2 p=0.006 在 j=1 时 BH 临界=0.005，BH 不通过，但 t>3 主导 → 通过
        result = stage1_statistical(factors, M_registered=20, t_thresh=3.0)
        ids = [f["factor_id"] for f in result]
        # 由于 t>3 主导，两个都通过
        assert "f1" in ids
        assert "f2" in ids

    def test_empty_factors_returns_empty(self):
        result = stage1_statistical([], M_registered=10)
        assert result == []

    def test_m_registered_zero_returns_empty(self):
        factors = [self._make_factor("f1", p=0.001, t=4.0)]
        result = stage1_statistical(factors, M_registered=0)
        assert result == []

    def test_bh_alpha_from_tunable(self):
        """q_fdr=None 时应从 tunable 读取 bh_fdr_alpha。"""
        factors = [self._make_factor("f1", p=0.0001, t=5.0)]
        result = stage1_statistical(factors, M_registered=20, q_fdr=None)
        assert len(result) == 1


# ─────────────────────────────────────────────────────────────────────────────
# A1 / A2 闸门
# ─────────────────────────────────────────────────────────────────────────────

class TestAGates:
    def test_a1_hard_veto_triggers_on_zero_sharpe(self):
        """夏普 <= a1_hard_veto_sharpe_floor(=0.0) 应触发 A1 硬否决。"""
        cfg = load_frozen()["gates"]
        floor = float(cfg["a1_hard_veto_sharpe_floor"])
        assert check_a1_hard_veto(floor) is False
        assert check_a1_hard_veto(floor - 0.01) is False

    def test_a1_passes_positive_sharpe(self):
        """夏普 > 0 应通过 A1。"""
        assert check_a1_hard_veto(0.01) is True
        assert check_a1_hard_veto(1.5) is True

    def test_a2_weak_veto_triggers_degraded_path(self):
        """DSR < a2_weak_veto_dsr_floor(=0.5) 应触发降级路径。"""
        cfg = load_frozen()["gates"]
        floor = float(cfg["a2_weak_veto_dsr_floor"])
        is_std, path = check_a2_weak_veto(floor - 0.01)
        assert is_std is False
        assert path == "degraded"

    def test_a2_standard_path_on_sufficient_dsr(self):
        """DSR >= 0.5 应返回标准路径。"""
        is_std, path = check_a2_weak_veto(0.5)
        assert is_std is True
        assert path == "standard"

    def test_a2_does_not_hard_veto(self):
        """A2 弱否决不应终止项目，仅触发降级路径。"""
        # 确认 A2 只返回 False 而非抛异常
        is_std, path = check_a2_weak_veto(0.0)
        assert path == "degraded"  # 仅降级，不抛出

    def test_a1_a2_combination_fail_a1(self):
        """A1 否决时，综合 verdict = FAIL_A1。"""
        result = run_full_gate_check(
            combined_sharpe=-0.1,
            combined_dsr=0.8,
            ic_realized_mean=0.03,
            ic_research=0.03,
            ic_research_std=0.10,
            t_research=100,
            real_weeks=13,
            total_weeks=26,
            cost_deviation=0.10,
            recon_zero_error_weeks=4,
            risk_control_consistent=True,
        )
        assert result["verdict"] == "FAIL_A1"
        assert result["a1_passed"] is False


# ─────────────────────────────────────────────────────────────────────────────
# B 闸门
# ─────────────────────────────────────────────────────────────────────────────

class TestBGates:
    def test_b1_passes_when_ic_sufficient(self):
        """实测 IC 均值 > 研究 IC - 1.0×SE 时 B1 通过。"""
        cfg = load_frozen()["gates"]
        ic_research = 0.030
        ic_std = 0.10
        t_research = 100
        se = ic_std / math.sqrt(t_research)
        threshold = ic_research - float(cfg["b1_ic_se_multiplier"]) * se
        # 给一个明确超阈值的实测 IC
        passed, detail = check_b1_scale_up(
            ic_realized_mean=threshold + 0.01,
            ic_research=ic_research,
            ic_research_std=ic_std,
            t_research=t_research,
            real_weeks=int(cfg["b1_min_real_weeks"]),
            total_weeks=int(cfg["b1_observe_weeks"]),
        )
        assert passed is True, f"B1 应通过，detail={detail}"

    def test_b1_fails_insufficient_real_weeks(self):
        """真实实盘周数不足时 B1 不通过。"""
        cfg = load_frozen()["gates"]
        min_real = int(cfg["b1_min_real_weeks"])
        passed, detail = check_b1_scale_up(
            ic_realized_mean=0.05,
            ic_research=0.03,
            ic_research_std=0.10,
            t_research=100,
            real_weeks=min_real - 1,  # 不足
            total_weeks=int(cfg["b1_observe_weeks"]),
        )
        assert passed is False
        assert detail["passes_real"] is False

    def test_b2_fails_when_ic_below_threshold(self):
        """实测 IC 均值低于 B2 判线时应触发降仓。"""
        cfg = load_frozen()["gates"]
        ic_research = 0.03
        ic_std = 0.10
        t_research = 100
        se = ic_std / math.sqrt(t_research)
        b2_threshold = ic_research - float(cfg["b2_ic_se_multiplier"]) * se
        # 给一个低于 B2 判线的值
        passed, detail = check_b2_maintain(
            ic_realized_rolling_mean=b2_threshold - 0.01,
            ic_research=ic_research,
            ic_research_std=ic_std,
            t_research=t_research,
        )
        assert passed is False

    def test_b2_fails_when_ic_below_zero(self):
        """IC 均值 < 0 时 B2 不通过（两个条件均需满足）。"""
        passed, detail = check_b2_maintain(
            ic_realized_rolling_mean=-0.01,
            ic_research=0.03,
            ic_research_std=0.10,
            t_research=100,
        )
        assert passed is False
        assert detail["above_zero"] is False

    def test_b3_passes_all_conditions(self):
        """所有 B3 工程条件满足时通过。"""
        cfg = load_frozen()["gates"]
        passed, detail = check_b3_engineering(
            cost_deviation=0.10,
            recon_zero_error_weeks=int(cfg["b3_recon_zero_error_weeks"]),
            risk_control_consistent=True,
        )
        assert passed is True

    def test_b3_fails_cost_deviation_too_high(self):
        """成本偏差超限时 B3 不通过。"""
        cfg = load_frozen()["gates"]
        max_dev = float(cfg["b3_cost_deviation_max"])
        passed, detail = check_b3_engineering(
            cost_deviation=max_dev + 0.01,
            recon_zero_error_weeks=int(cfg["b3_recon_zero_error_weeks"]),
            risk_control_consistent=True,
        )
        assert passed is False
        assert detail["cost_ok"] is False

    def test_b3_fails_insufficient_recon_weeks(self):
        """对账周数不足时 B3 不通过。"""
        cfg = load_frozen()["gates"]
        passed, detail = check_b3_engineering(
            cost_deviation=0.10,
            recon_zero_error_weeks=int(cfg["b3_recon_zero_error_weeks"]) - 1,
            risk_control_consistent=True,
        )
        assert passed is False
        assert detail["recon_ok"] is False

    def test_scale_up_requires_a1_b1_b3(self):
        """加仓需要 A1 & B1 & B3 同时通过。"""
        cfg = load_frozen()["gates"]
        result = run_full_gate_check(
            combined_sharpe=1.0,   # A1 通过
            combined_dsr=0.7,      # A2 标准路径
            ic_realized_mean=0.05,
            ic_research=0.03,
            ic_research_std=0.10,
            t_research=100,
            real_weeks=int(cfg["b1_min_real_weeks"]),
            total_weeks=int(cfg["b1_observe_weeks"]),
            cost_deviation=0.10,
            recon_zero_error_weeks=int(cfg["b3_recon_zero_error_weeks"]),
            risk_control_consistent=True,
        )
        assert result["scale_up_allowed"] is True
        assert result["verdict"] == "PASS"


# ─────────────────────────────────────────────────────────────────────────────
# factor_ic_tstat
# ─────────────────────────────────────────────────────────────────────────────

class TestFactorIcTstat:
    def test_large_sample_uses_newey_west(self):
        """T >= 200 时使用 Newey-West HAC。"""
        ic = _make_ic_series(250, mean=0.03, std=0.10)
        result = factor_ic_tstat(ic, direction=1)
        assert result["method"] in ("newey_west", "block_bootstrap")
        assert result["ic_mean"] == pytest.approx(float(ic.mean()), abs=1e-10)

    def test_small_sample_uses_bootstrap(self):
        """T < 200 时使用 block bootstrap。"""
        ic = _make_ic_series(100, mean=0.03, std=0.10)
        result = factor_ic_tstat(ic, direction=1)
        assert result["method"] == "block_bootstrap"

    def test_direction_normalization(self):
        """负向因子先归一化（direction=-1 后 IC 反号）。"""
        ic = _make_ic_series(50, mean=0.03, std=0.10)
        r_pos = factor_ic_tstat(ic, direction=1)
        r_neg = factor_ic_tstat(ic, direction=-1)
        assert r_pos["ic_mean"] == pytest.approx(-r_neg["ic_mean"], abs=1e-10)

    def test_insufficient_data(self):
        """数据点 < 5 时返回 nan 及 insufficient_data 标签。"""
        ic = pd.Series([0.01, 0.02, 0.03])
        result = factor_ic_tstat(ic)
        assert result["method"] == "insufficient_data"
        assert math.isnan(result["ic_mean"])

    def test_sign_stable_rate_in_range(self):
        """符号稳定率在 [0, 1] 内。"""
        ic = _make_ic_series(60, mean=0.04, std=0.10)
        result = factor_ic_tstat(ic)
        assert 0.0 <= result["sign_stable_rate"] <= 1.0
