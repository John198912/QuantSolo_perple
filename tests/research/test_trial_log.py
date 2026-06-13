"""试验登记与 N_eff 预算体系测试（研究协议 §四 / §五）。

覆盖：
- N_eff 预算超限拒绝（> 6 次停止）
- validation 轮次超限拒绝（> 5 轮）
- 试验登记 append-only（R2）
- DSR 单调性（N_eff 增大 → SR0 增大）
- N_eff 计算正确性
"""
from __future__ import annotations

import math
import numpy as np
import pandas as pd
import pytest
import tempfile
import os
from pathlib import Path

from src.research.trial_log import (
    get_connection,
    log_trial,
    count_trials,
    count_distinct_atomic_tests,
    get_n_eff_budget_status,
    register_test_eval,
    complete_test_eval,
    get_validation_round_status,
    log_validation_round,
    make_atomic_test_id,
)
from src.research.deflated_sharpe import (
    deflated_sharpe_ratio,
    sr0_expected_max,
    compute_n_eff_total,
    compute_dsr_from_series,
)
from src.common.config import load_frozen


@pytest.fixture
def tmp_db(tmp_path):
    """临时 SQLite 数据库路径。"""
    return str(tmp_path / "test_research_ledger.db")


# ─────────────────────────────────────────────────────────────────────────────
# N_eff 预算测试
# ─────────────────────────────────────────────────────────────────────────────

class TestNEffBudget:
    def test_initial_budget_is_full(self, tmp_db):
        """初始状态下预算剩余 = n_eff_budget_total（=6）。"""
        cfg = load_frozen()["gates"]
        budget_total = int(cfg["n_eff_budget_total"])
        status = get_n_eff_budget_status(tmp_db)
        assert status["budget_total"] == budget_total
        assert status["remaining"] == budget_total
        assert status["status"] == "GREEN"

    def test_budget_decrements_on_registration(self, tmp_db):
        """每次登记 test 评估，预算减少 1。"""
        for i in range(3):
            register_test_eval(
                eval_date=f"2026-0{i+1}-01",
                strategy_snapshot_id=f"abc{i}",
                factor_set=["f1"],
                model_type="linear",
                param_hash=f"ph{i}",
                n_eff_used=4.0,
                data_cut_id="dc1",
                purpose=f"测试第 {i+1} 次评估，目的说明长度满足二十字要求。",
                db_path=tmp_db,
            )
        status = get_n_eff_budget_status(tmp_db)
        cfg = load_frozen()["gates"]
        assert status["remaining"] == int(cfg["n_eff_budget_total"]) - 3

    def test_budget_exhausted_raises_error(self, tmp_db):
        """预算用完后再登记应抛出 RuntimeError。"""
        cfg = load_frozen()["gates"]
        budget_total = int(cfg["n_eff_budget_total"])
        for i in range(budget_total):
            register_test_eval(
                eval_date=f"2026-01-{i+1:02d}",
                strategy_snapshot_id=f"snap{i}",
                factor_set=["f1"],
                model_type="linear",
                param_hash=f"ph{i}",
                n_eff_used=4.0,
                data_cut_id="dc1",
                purpose=f"第 {i+1} 次评估，目的说明需满足二十字要求如此。",
                db_path=tmp_db,
            )
        # 第 7 次应被拒绝
        with pytest.raises(RuntimeError, match="N_eff 预算已用完"):
            register_test_eval(
                eval_date="2026-07-01",
                strategy_snapshot_id="snap_over",
                factor_set=["f1"],
                model_type="linear",
                param_hash="ph_over",
                n_eff_used=4.0,
                data_cut_id="dc1",
                purpose="超出预算的第七次评估，目的说明需满足二十字要求如此。",
                db_path=tmp_db,
            )

    def test_purpose_too_short_raises_error(self, tmp_db):
        """purpose < 20 字应抛出 ValueError。"""
        with pytest.raises(ValueError, match="purpose 必须 ≥ 20 字"):
            register_test_eval(
                eval_date="2026-01-01",
                strategy_snapshot_id="snap",
                factor_set=["f1"],
                model_type="linear",
                param_hash="ph",
                n_eff_used=4.0,
                data_cut_id="dc1",
                purpose="太短",  # < 20 字
                db_path=tmp_db,
            )

    def test_budget_status_machine(self, tmp_db):
        """预算状态机：GREEN/YELLOW/RED/LOCKED。"""
        cfg = load_frozen()["gates"]
        budget_total = int(cfg["n_eff_budget_total"])

        # 初始 GREEN（remaining >= 4）
        s = get_n_eff_budget_status(tmp_db)
        assert s["status"] == "GREEN"

        # 消耗至 remaining=3 → YELLOW
        for i in range(budget_total - 3):
            register_test_eval(
                eval_date=f"2026-{i+1:02d}-01",
                strategy_snapshot_id=f"snap{i}",
                factor_set=["f1"],
                model_type="linear",
                param_hash=f"ph{i}",
                n_eff_used=4.0,
                data_cut_id="dc1",
                purpose=f"第 {i+1} 次评估测试，目的描述要满足最少二十字的要求。",
                db_path=tmp_db,
            )
        s = get_n_eff_budget_status(tmp_db)
        assert s["remaining"] == 3
        assert s["status"] == "YELLOW"

    def test_complete_test_eval_updates_verdict(self, tmp_db):
        """complete_test_eval 应更新 verdict 字段。"""
        reg = register_test_eval(
            eval_date="2026-01-01",
            strategy_snapshot_id="snap",
            factor_set=["f1"],
            model_type="linear",
            param_hash="ph",
            n_eff_used=4.0,
            data_cut_id="dc1",
            purpose="这是一次完整的测试段评估，目的描述满足二十字要求。",
            db_path=tmp_db,
        )
        eval_id = reg["eval_id"]
        complete_test_eval(eval_id, "2026-01-02T10:00:00", "PASS", db_path=tmp_db)

        conn = get_connection(tmp_db)
        try:
            row = conn.execute(
                "SELECT verdict, run_timestamp FROM test_eval_registry WHERE eval_id=?",
                (eval_id,)
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == "PASS"
        assert row[1] is not None


# ─────────────────────────────────────────────────────────────────────────────
# validation 轮次测试
# ─────────────────────────────────────────────────────────────────────────────

class TestValidationBudget:
    def test_initial_validation_budget(self, tmp_db):
        """初始 validation 查看轮次 = validation_view_budget（=5）。"""
        cfg = load_frozen()["gates"]
        budget = int(cfg["validation_view_budget"])
        status = get_validation_round_status(tmp_db)
        assert status["budget_total"] == budget
        assert status["remaining"] == budget
        assert not status["exceeded"]

    def test_validation_round_log(self, tmp_db):
        """记录一轮 validation 查看后，used+1，remaining-1。"""
        log_validation_round("首轮因子筛选，新增 momentum_20 等 5 个因子。", "abc123", "dc1", db_path=tmp_db)
        status = get_validation_round_status(tmp_db)
        assert status["used"] == 1
        assert not status["exceeded"]

    def test_validation_budget_exceeded_raises(self, tmp_db):
        """超过 5 轮后应抛出 RuntimeError。"""
        cfg = load_frozen()["gates"]
        budget = int(cfg["validation_view_budget"])
        for i in range(budget):
            log_validation_round(
                f"第 {i+1} 轮因子筛选，新增修改了若干个因子配置。",
                f"hash{i}", "dc1", db_path=tmp_db
            )
        with pytest.raises(RuntimeError, match="validation 查看轮次已用完"):
            log_validation_round("第六轮，已超出预算。", "hash_over", "dc1", db_path=tmp_db)


# ─────────────────────────────────────────────────────────────────────────────
# Trial 记录测试
# ─────────────────────────────────────────────────────────────────────────────

class TestTrialLog:
    def test_log_trial_appends(self, tmp_db):
        """log_trial 应向 research_ledger 追加记录。"""
        spec = {
            "atomic_test_id": "abc123",
            "factor_id": "momentum_20",
        }
        row_id = log_trial("factor", spec, {"ic_mean": 0.03}, "git_abc", "dc1", db_path=tmp_db)
        assert row_id is not None
        assert count_trials(db_path=tmp_db) == 1

    def test_log_trial_append_only(self, tmp_db):
        """多次 log_trial 应累计行数（append-only）。"""
        for i in range(5):
            spec = {"atomic_test_id": f"test_{i}", "factor_id": f"f{i}"}
            log_trial("factor", spec, None, "git_abc", "dc1", db_path=tmp_db)
        assert count_trials(db_path=tmp_db) == 5

    def test_log_trial_missing_atomic_id_raises(self, tmp_db):
        """spec 缺少 atomic_test_id 应抛出 ValueError。"""
        with pytest.raises(ValueError, match="atomic_test_id"):
            log_trial("factor", {"factor_id": "f1"}, None, "git", "dc1", db_path=tmp_db)

    def test_count_distinct_atomic_tests(self, tmp_db):
        """相同 atomic_test_id 重复记录时，distinct 计数不重复。"""
        for _ in range(3):
            log_trial("factor", {"atomic_test_id": "same_id"}, None, "git", "dc1", db_path=tmp_db)
        log_trial("factor", {"atomic_test_id": "diff_id"}, None, "git", "dc1", db_path=tmp_db)
        assert count_distinct_atomic_tests("factor", db_path=tmp_db) == 2

    def test_make_atomic_test_id_deterministic(self):
        """相同参数生成相同 atomic_test_id（确定性）。"""
        id1 = make_atomic_test_id("h", "f", 20, "all", "raw", 5, "linear")
        id2 = make_atomic_test_id("h", "f", 20, "all", "raw", 5, "linear")
        assert id1 == id2

    def test_make_atomic_test_id_sensitive_to_params(self):
        """不同参数生成不同 atomic_test_id。"""
        id_5 = make_atomic_test_id("h", "f", 5, "all", "raw", 5, "linear")
        id_20 = make_atomic_test_id("h", "f", 20, "all", "raw", 5, "linear")
        assert id_5 != id_20


# ─────────────────────────────────────────────────────────────────────────────
# DSR 单调性测试（研究协议 §3.5）
# ─────────────────────────────────────────────────────────────────────────────

class TestDSRMonotonicity:
    def test_dsr_increases_with_sharpe(self):
        """SR_hat 增大时 DSR 应单调递增（使用未饱和区间）。"""
        n_eff, t_eff = 6, 100
        # 在 DSR 尚未饱和为 1.0 的区间内测试（远低于饱和点）
        sr_vals = [0.05, 0.1, 0.3, 0.5, 0.7]
        dsr_vals = [deflated_sharpe_ratio(sr, n_eff, t_eff) for sr in sr_vals]
        for i in range(len(dsr_vals) - 1):
            assert dsr_vals[i] < dsr_vals[i + 1], (
                f"DSR 应随 SR 递增，但 DSR({sr_vals[i]})={dsr_vals[i]:.6f} "
                f">= DSR({sr_vals[i+1]})={dsr_vals[i+1]:.6f}"
            )

    def test_sr0_increases_with_n_eff(self):
        """N_eff 增大时 SR0（零假设期望最大 Sharpe）应单调递增。"""
        t_eff = 100
        n_vals = [2, 4, 6, 10, 20]
        sr0_vals = [sr0_expected_max(n, t_eff) for n in n_vals]
        for i in range(len(sr0_vals) - 1):
            assert sr0_vals[i] < sr0_vals[i + 1], (
                f"SR0 应随 N_eff 递增，但 SR0({n_vals[i]})={sr0_vals[i]:.4f} "
                f">= SR0({n_vals[i+1]})={sr0_vals[i+1]:.4f}"
            )

    def test_dsr_decreases_with_n_eff(self):
        """固定 SR_hat 时，N_eff 增大 → SR0 增大 → DSR 降低（更严格的惩罚）。"""
        sr_hat = 1.0
        t_eff = 100
        n_vals = [3, 6, 10, 20]
        dsr_vals = [deflated_sharpe_ratio(sr_hat, n, t_eff) for n in n_vals]
        for i in range(len(dsr_vals) - 1):
            assert dsr_vals[i] > dsr_vals[i + 1], (
                f"N_eff 增大时 DSR 应递减：DSR(N={n_vals[i]})={dsr_vals[i]:.4f} "
                f"<= DSR(N={n_vals[i+1]})={dsr_vals[i+1]:.4f}"
            )

    def test_dsr_in_range(self):
        """DSR 应在 [0, 1] 范围内。"""
        for sr in [-1.0, 0.0, 0.5, 1.0, 2.0]:
            dsr = deflated_sharpe_ratio(sr, n_eff=6, T_eff=100)
            assert 0.0 <= dsr <= 1.0, f"DSR={dsr} 超出 [0,1] 范围（sr={sr}）"

    def test_compute_dsr_from_series(self):
        """从 IC 序列计算 DSR 应返回合理值。"""
        rng = np.random.default_rng(42)
        ic = pd.Series(rng.normal(0.03, 0.10, 100))
        result = compute_dsr_from_series(ic, n_eff=6.0)
        assert "dsr" in result
        assert 0.0 <= result["dsr"] <= 1.0
        assert result["sr_hat"] == pytest.approx(float(ic.mean() / ic.std(ddof=1)), rel=0.01)

    def test_compute_n_eff_total(self):
        """compute_n_eff_total 应返回合理 N_eff（1 <= n_eff_used <= n_tests）。"""
        rng = np.random.default_rng(42)
        n_obs, n_tests = 50, 10
        # 构造低相关矩阵（近似独立，N_eff ≈ n_tests）
        mat = pd.DataFrame(rng.normal(0, 1, (n_obs, n_tests)))
        result = compute_n_eff_total(mat, n_bootstrap=100)
        assert 1.0 <= result["n_eff_used"] <= n_tests + 1
        assert result["lower"] <= result["point"]
