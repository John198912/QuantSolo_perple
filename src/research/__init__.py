"""研究子包（QS-C02 研究协议）。

包含：
  - backtest: 向量化 / 事件驱动回测引擎、成本模型
  - walk_forward: Purged & Embargo Walk-Forward 切分器
  - deflated_sharpe: DSR 计算（Harvey-Liu + N_eff + T_eff）
  - gates: 三阶段因子筛选 + B为主+A为辅闸门
  - trial_log: 试验登记 + N_eff 预算管理
"""
from __future__ import annotations

from src.research.walk_forward import (
    purged_walkforward_splits,
    check_no_leakage,
    compute_T_eff,
)
from src.research.deflated_sharpe import (
    deflated_sharpe_ratio,
    sr0_expected_max,
    compute_n_eff_total,
    compute_dsr_from_series,
    get_dsr_thresholds,
)
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
from src.research.trial_log import (
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

__all__ = [
    # walk_forward
    "purged_walkforward_splits",
    "check_no_leakage",
    "compute_T_eff",
    # deflated_sharpe
    "deflated_sharpe_ratio",
    "sr0_expected_max",
    "compute_n_eff_total",
    "compute_dsr_from_series",
    "get_dsr_thresholds",
    # gates
    "stage1_statistical",
    "stage2_economic",
    "stage3_final",
    "check_a1_hard_veto",
    "check_a2_weak_veto",
    "check_b1_scale_up",
    "check_b2_maintain",
    "check_b3_engineering",
    "run_full_gate_check",
    "factor_ic_tstat",
    # trial_log
    "log_trial",
    "count_trials",
    "count_distinct_atomic_tests",
    "get_n_eff_budget_status",
    "register_test_eval",
    "complete_test_eval",
    "get_validation_round_status",
    "log_validation_round",
    "make_atomic_test_id",
]
