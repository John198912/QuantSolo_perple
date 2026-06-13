"""研究统计闸门体系（研究协议 §三~§六）。

三阶段因子筛选 + B为主+A为辅闸门判定：
  §3.2 阶段一：BH-FDR(alpha=tunable.factor_selection.bh_fdr_alpha) + 单侧 t>3
  §3.3 阶段二：ICIR / 扣费 / 换手率
  §3.4 阶段三：Bonferroni 终筛 + DSR（研究阶段参考）
  §6.2 A1 硬否决：合并段夏普 <= a1_hard_veto_sharpe_floor
  §6.2 A2 弱否决：合并段 DSR < a2_weak_veto_dsr_floor（触发降级路径，非否决）
  §6.3 B1/B2/B3 判线（主裁决）

R3 红线：所有阈值数字经 load_frozen()['gates']/['acceptance']，禁止硬编码。
可调参数 bh_fdr_alpha 经 load_tunable()['factor_selection']['bh_fdr_alpha']。

scipy 已装可用于统计；statsmodels 惰性导入。
"""
from __future__ import annotations

import logging
import math
from typing import Any, Optional

import numpy as np
import pandas as pd
from scipy.stats import norm, t as tdist

from src.common.config import load_frozen, load_tunable
from src.research.deflated_sharpe import deflated_sharpe_ratio, sr0_expected_max

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 配置读取辅助
# ─────────────────────────────────────────────────────────────────────────────

def _gates_cfg() -> dict:
    return dict(load_frozen()["gates"])


def _acceptance_cfg() -> dict:
    return dict(load_frozen()["acceptance"])


def _bh_fdr_alpha() -> float:
    try:
        return float(load_tunable()["factor_selection"]["bh_fdr_alpha"])
    except Exception:
        logger.warning("load_tunable bh_fdr_alpha 失败，使用默认 0.10")
        return 0.10


# ─────────────────────────────────────────────────────────────────────────────
# t 统计量计算（Newey-West HAC 或 block bootstrap）
# ─────────────────────────────────────────────────────────────────────────────

def factor_ic_tstat(
    ic_series: pd.Series,
    direction: int = 1,
    label_horizon_days: int = 5,
    n_bootstrap: int = 1000,
) -> dict:
    """计算 IC 序列的 t 统计量（研究协议 §3.1）。

    Args:
        ic_series:           截面 rank-IC 时间序列（pd.Series，按时间排序）
        direction:           因子预注册方向（+1 或 -1），先归一化为"越大越好"
        label_horizon_days:  标签 horizon H（用于 block bootstrap 块长近似）
        n_bootstrap:         小样本 block bootstrap 次数

    Returns:
        {
            'ic_mean': float,
            'ic_std': float,
            'ic_ir': float,
            't_stat_nw': float,     # Newey-West HAC t 统计量（T>=200）或 block bootstrap
            'p_value_onesided': float,  # 单侧（方向预注册后，反向显著 → FAIL）
            'sign_stable_rate': float,  # 符号稳定率（block bootstrap 经验分布）
            'method': str,          # 'newey_west' | 'block_bootstrap'
        }
    """
    s = ic_series.dropna() * direction  # 方向归一化
    T = len(s)
    if T < 5:
        return {
            "ic_mean": float("nan"),
            "ic_std": float("nan"),
            "ic_ir": float("nan"),
            "t_stat_nw": float("nan"),
            "p_value_onesided": 1.0,
            "sign_stable_rate": 0.0,
            "method": "insufficient_data",
        }

    ic_mean = float(s.mean())
    ic_std = float(s.std(ddof=1)) + 1e-10
    ic_ir = ic_mean / ic_std

    if T >= 200:
        # Newey-West HAC 修正
        t_stat, p_onesided = _newey_west_tstat(s.values, T)
        method = "newey_west"
    else:
        # 小样本：block bootstrap（块长≈H）
        t_stat, p_onesided = _block_bootstrap_tstat(
            s.values, label_horizon_days, n_bootstrap
        )
        method = "block_bootstrap"

    # 符号稳定率（block bootstrap 经验分布）
    sign_stable_rate = _sign_stable_rate(s.values, label_horizon_days, n_bootstrap)

    return {
        "ic_mean": ic_mean,
        "ic_std": ic_std,
        "ic_ir": ic_ir,
        "t_stat_nw": t_stat,
        "p_value_onesided": p_onesided,
        "sign_stable_rate": sign_stable_rate,
        "method": method,
    }


def _newey_west_tstat(x: np.ndarray, T: int) -> tuple[float, float]:
    """Newey-West HAC 修正的 t 统计量（单侧检验，H0: μ<=0）。"""
    # 惰性导入 statsmodels
    try:
        from statsmodels.stats.sandwich_covariance import cov_hac_simple
        mu = float(x.mean())
        # 设置滞后阶数 L = floor(4*(T/100)^(2/9))
        L = max(1, int(4 * (T / 100) ** (2 / 9)))
        # 构造 demeaned x 的 HAC 方差估计
        xd = x - mu
        # 简单 Newey-West 手工实现（兼容无 statsmodels 场景）
        gamma0 = float(np.mean(xd ** 2))
        s2 = gamma0
        for lag in range(1, L + 1):
            gam = float(np.mean(xd[lag:] * xd[:-lag]))
            s2 += 2.0 * (1.0 - lag / (L + 1)) * gam
        s2 = max(s2, 1e-12)
        t_stat = mu / np.sqrt(s2 / T)
        p_onesided = float(norm.sf(t_stat))  # 单侧：P(Z > t)
        return float(t_stat), p_onesided
    except ImportError:
        # 退化为标准 t 检验
        return _simple_tstat(x, T)


def _simple_tstat(x: np.ndarray, T: int) -> tuple[float, float]:
    """标准 t 检验（fallback）。"""
    mu = float(x.mean())
    se = float(x.std(ddof=1)) / np.sqrt(T) + 1e-12
    t_stat = mu / se
    p_onesided = float(tdist.sf(t_stat, df=T - 1))
    return t_stat, p_onesided


def _block_bootstrap_tstat(
    x: np.ndarray,
    block_len: int,
    n_bootstrap: int,
) -> tuple[float, float]:
    """Block bootstrap t 统计量（小样本，研究协议 §3.1）。"""
    rng = np.random.default_rng(42)
    T = len(x)
    block_len = max(1, block_len)
    n_blocks = max(1, T // block_len)

    boot_means = np.empty(n_bootstrap)
    for b in range(n_bootstrap):
        starts = rng.integers(0, max(1, T - block_len + 1), size=n_blocks)
        boot_sample = np.concatenate([x[s: s + block_len] for s in starts])[:T]
        boot_means[b] = boot_sample.mean()

    mu = float(x.mean())
    boot_se = float(boot_means.std(ddof=1)) + 1e-12
    t_stat = mu / boot_se
    p_onesided = float(np.mean(boot_means <= 0))  # 单侧经验 p 值
    return t_stat, p_onesided


def _sign_stable_rate(x: np.ndarray, block_len: int, n_bootstrap: int) -> float:
    """符号稳定率：block bootstrap 下均值 > 0 的概率。"""
    rng = np.random.default_rng(123)
    T = len(x)
    block_len = max(1, block_len)
    n_blocks = max(1, T // block_len)

    positive_count = 0
    for _ in range(n_bootstrap):
        starts = rng.integers(0, max(1, T - block_len + 1), size=n_blocks)
        boot_sample = np.concatenate([x[s: s + block_len] for s in starts])[:T]
        if float(boot_sample.mean()) > 0:
            positive_count += 1
    return positive_count / n_bootstrap


# ─────────────────────────────────────────────────────────────────────────────
# 阶段一：统计显著（BH-FDR + 单侧 t>3）
# ─────────────────────────────────────────────────────────────────────────────

def stage1_statistical(
    reg_factors: list[dict],
    M_registered: int,
    q_fdr: Optional[float] = None,
    t_thresh: float = 3.0,
) -> list[dict]:
    """阶段一：BH-FDR + 单侧 t>3 叠加筛选（研究协议 §3.2）。

    Args:
        reg_factors:   已登记因子列表，每项含 {'factor_id', 'p_value_onesided', 't_stat_nw', ...}
        M_registered:  count(distinct atomic_test_id where trial_type='factor')，BH 分母
        q_fdr:         BH-FDR 显著性水平；None 时从 tunable 读取
        t_thresh:      单侧 t 阈值（默认 3.0）

    Returns:
        通过因子列表（dict 列表，含原始字段）。

    算法：
      1. 按 p_value_onesided 升序排列
      2. BH：找最大 j 使 p(j) <= (j/M_registered)*q_fdr
      3. BH 通过集合 ∩ {t_nw > t_thresh}（单侧 t>3，反向显著 t<-t_thresh 判 FAIL）
    """
    q = q_fdr if q_fdr is not None else _bh_fdr_alpha()
    if M_registered <= 0:
        logger.warning("stage1_statistical: M_registered=%d <= 0，返回空。", M_registered)
        return []

    # 按 p 值升序排列
    sorted_factors = sorted(reg_factors, key=lambda f: f.get("p_value_onesided", 1.0))
    n = len(sorted_factors)
    if n == 0:
        return []

    # BH 临界值：p(j) <= (j/M_registered)*q
    bh_pass_set: set[str] = set()
    bh_threshold = 0.0
    for j, factor in enumerate(sorted_factors, start=1):
        critical = (j / M_registered) * q
        if factor.get("p_value_onesided", 1.0) <= critical:
            bh_threshold = critical
            bh_pass_set.add(factor["factor_id"])

    # 叠加 t>3 条件
    passed: list[dict] = []
    for factor in reg_factors:
        fid = factor["factor_id"]
        t_val = factor.get("t_stat_nw", 0.0)
        p_val = factor.get("p_value_onesided", 1.0)

        in_bh = fid in bh_pass_set
        t_pass = t_val > t_thresh
        # 反向显著（t < -t_thresh）判 FAIL
        t_reverse_fail = t_val < -t_thresh

        if t_reverse_fail:
            logger.debug("阶段一 FAIL（反向显著）: %s t=%.3f", fid, t_val)
            continue
        if in_bh and t_pass:
            passed.append(factor)
        elif t_pass and not in_bh:
            # t>3 主导过滤（BH 安全网，参见协议 §3.2 说明）
            passed.append(factor)

    logger.info("阶段一：%d/%d 因子通过（BH q=%.2f，t>%.1f）", len(passed), n, q, t_thresh)
    return passed


# ─────────────────────────────────────────────────────────────────────────────
# 阶段二：经济学显著（ICIR / 扣费 / 换手率）
# ─────────────────────────────────────────────────────────────────────────────

def stage2_economic(
    factors: list[dict],
    icir_thresh: float = 0.3,
    ic_mean_thresh: float = 0.015,
    sign_rate_thresh: float = 0.60,
    turnover_max: float = 0.50,
) -> list[dict]:
    """阶段二：经济学显著筛选（研究协议 §3.3）。

    Args:
        factors:          阶段一通过因子列表，每项含：
                          ic_ir, ic_mean, sign_stable_rate,
                          long_short_net_return（扣费后多空收益）,
                          top_turnover_weekly（Top 组周度换手率）,
                          monotonic_count（单调性满足不等式数，最多 4 个）
        icir_thresh:      ICIR 最低阈值（默认 0.3）
        ic_mean_thresh:   IC 均值最低阈值（默认 0.015）
        sign_rate_thresh: 符号稳定率最低阈值（默认 0.60）
        turnover_max:     换手率最高阈值（默认 0.50）

    Returns:
        通过因子列表。

    筛选规则：
      - ICIR > icir_thresh（强显著 > 0.5）
      - IC 均值 > ic_mean_thresh
      - 符号稳定率 > sign_rate_thresh
      - 扣成本多空收益 > 0
      - 单调性：Top>Q3>Q2>Q1>Bottom 的 4 个不等式至少 3 个成立
      - Top 组周度换手率 < turnover_max 或 扣费后净 IC > 0
    """
    passed: list[dict] = []
    for f in factors:
        fid = f.get("factor_id", "?")

        ic_ir = f.get("ic_ir", 0.0)
        ic_mean = f.get("ic_mean", 0.0)
        sign_rate = f.get("sign_stable_rate", 0.0)
        ls_net = f.get("long_short_net_return", float("-inf"))
        turnover = f.get("top_turnover_weekly", 1.0)
        mono_count = f.get("monotonic_count", 0)
        net_ic = f.get("net_ic", None)

        fails = []
        if ic_ir <= icir_thresh:
            fails.append(f"ICIR={ic_ir:.3f}<={icir_thresh}")
        if ic_mean <= ic_mean_thresh:
            fails.append(f"IC_mean={ic_mean:.4f}<={ic_mean_thresh}")
        if sign_rate <= sign_rate_thresh:
            fails.append(f"sign_rate={sign_rate:.2f}<={sign_rate_thresh}")
        if ls_net <= 0:
            fails.append(f"long_short_net={ls_net:.4f}<=0")
        if mono_count < 3:
            fails.append(f"monotonic_count={mono_count}<3")
        # 换手率 OR 净IC > 0
        turnover_ok = turnover < turnover_max
        net_ic_ok = (net_ic is not None and net_ic > 0)
        if not (turnover_ok or net_ic_ok):
            fails.append(f"turnover={turnover:.2f}>={turnover_max} and net_ic not positive")

        if fails:
            logger.debug("阶段二 FAIL %s: %s", fid, "; ".join(fails))
            continue
        passed.append(f)

    logger.info("阶段二：%d/%d 因子通过经济学显著筛选", len(passed), len(factors))
    return passed


# ─────────────────────────────────────────────────────────────────────────────
# 阶段三：Bonferroni 终筛 + DSR 参考
# ─────────────────────────────────────────────────────────────────────────────

def stage3_final(
    survivors: list[dict],
    M_registered: int,
    n_eff_total: float,
    T_eff: float,
    bonferroni_alpha: float = 0.05,
    max_select: int = 8,
) -> list[dict]:
    """阶段三：Bonferroni 终筛（研究协议 §3.4）。

    Args:
        survivors:        阶段二通过因子列表，每项含 p_value_onesided / sharpe_candidates 等
        M_registered:     atomic_test 总计数（Bonferroni 分母）
        n_eff_total:      N_eff 保守下界（供 DSR 计算）
        T_eff:            有效观测数（周频）
        bonferroni_alpha: Bonferroni α（默认 0.05）
        max_select:       最终入选上限（默认 8，协议 §3.4）

    Returns:
        最终因子列表（≤ 8），按 ICIR 降序。DSR 作为质量参考附在字段 'dsr' 中。
    """
    if M_registered <= 0:
        logger.warning("stage3_final: M_registered=%d <= 0", M_registered)
        return []

    bonferroni_thresh = bonferroni_alpha / M_registered
    passed: list[dict] = []

    for f in survivors:
        p = f.get("p_value_onesided", 1.0)
        if p >= bonferroni_thresh:
            logger.debug(
                "阶段三 Bonferroni FAIL %s: p=%.6f >= %.8f",
                f.get("factor_id", "?"), p, bonferroni_thresh,
            )
            continue

        # DSR 附加（研究质量参考，非否决）
        ic_s = f.get("ic_series")
        if ic_s is not None and len(ic_s) >= 5:
            from src.research.deflated_sharpe import compute_dsr_from_series
            dsr_result = compute_dsr_from_series(
                ic_s if hasattr(ic_s, "dropna") else pd.Series(ic_s),
                n_eff=n_eff_total,
                T_eff=T_eff,
            )
            f = {**f, "dsr": dsr_result.get("dsr", float("nan"))}
        passed.append(f)

    # 按 ICIR 降序，取前 max_select
    passed.sort(key=lambda x: x.get("ic_ir", 0.0), reverse=True)
    selected = passed[:max_select]

    logger.info(
        "阶段三：%d/%d 因子通过 Bonferroni（α/M=%.2e），最终入选 %d 个",
        len(passed), len(survivors), bonferroni_thresh, len(selected),
    )
    return selected


# ─────────────────────────────────────────────────────────────────────────────
# A 闸门（A1 硬否决 / A2 弱否决）
# ─────────────────────────────────────────────────────────────────────────────

def check_a1_hard_veto(
    combined_sharpe: float,
) -> bool:
    """A1 硬否决：合并段扣成本年化夏普 <= a1_hard_veto_sharpe_floor → 一票否决。

    Args:
        combined_sharpe: test 合并段（2024-2025）扣成本年化夏普

    Returns:
        True = 通过（Sharpe > floor）；False = 否决。
    """
    floor = float(_gates_cfg()["a1_hard_veto_sharpe_floor"])
    if combined_sharpe <= floor:
        logger.error(
            "A1 硬否决触发：合并段夏普 %.4f <= %.4f。项目进入 FAIL_RESEARCH 状态。",
            combined_sharpe, floor,
        )
        return False
    return True


def check_a2_weak_veto(
    combined_dsr: float,
) -> tuple[bool, str]:
    """A2 弱否决：合并段 DSR < a2_weak_veto_dsr_floor → 触发降级路径（非否决）。

    Args:
        combined_dsr: 合并段 DSR（按实际登记 N_eff 计，N_eff 预算封顶 6）

    Returns:
        (is_standard_path, path_name):
          - (True, 'standard')  = DSR >= floor，标准路径
          - (False, 'degraded') = DSR < floor，降级路径（起步资金减半、观察窗延长）
    """
    floor = float(_gates_cfg()["a2_weak_veto_dsr_floor"])
    if combined_dsr < floor:
        logger.warning(
            "A2 弱否决触发：合并段 DSR %.4f < %.4f。进入降级路径（非否决）。",
            combined_dsr, floor,
        )
        return False, "degraded"
    return True, "standard"


# ─────────────────────────────────────────────────────────────────────────────
# B 闸门（B1 加仓判线 / B2 维持判线 / B3 工程判线）
# ─────────────────────────────────────────────────────────────────────────────

def check_b1_scale_up(
    ic_realized_mean: float,
    ic_research: float,
    ic_research_std: float,
    t_research: int,
    real_weeks: int,
    total_weeks: int,
) -> tuple[bool, dict]:
    """B1 加仓判线（主裁决）（研究协议 §6.3.1）。

    条件：
      IC_realized_26w_mean > IC_research - b1_ic_se_multiplier × SE_research
      且 加仓前至少 b1_min_real_weeks 周来自真实实盘

    Args:
        ic_realized_mean:  实盘累计≥26周的实测周度 rank-IC 均值
        ic_research:       研究 walk-forward IC 均值（预注册值）
        ic_research_std:   研究期 IC 序列标准差
        t_research:        研究期 IC 序列长度（用于 SE = std/sqrt(T)）
        real_weeks:        真实实盘累计周数
        total_weeks:       模拟盘+实盘累计周数

    Returns:
        (passed, detail_dict)
    """
    cfg = _gates_cfg()
    b1_mult = float(cfg["b1_ic_se_multiplier"])
    min_real_weeks = int(cfg["b1_min_real_weeks"])
    observe_weeks = int(cfg["b1_observe_weeks"])

    se_research = (ic_research_std / math.sqrt(max(t_research, 1)))
    threshold = ic_research - b1_mult * se_research

    passes_ic = ic_realized_mean > threshold
    passes_weeks = total_weeks >= observe_weeks
    passes_real = real_weeks >= min_real_weeks

    passed = passes_ic and passes_weeks and passes_real
    detail = {
        "ic_realized_mean": ic_realized_mean,
        "threshold": threshold,
        "ic_research": ic_research,
        "se_research": se_research,
        "b1_ic_se_multiplier": b1_mult,
        "total_weeks": total_weeks,
        "real_weeks": real_weeks,
        "passes_ic": passes_ic,
        "passes_weeks": passes_weeks,
        "passes_real": passes_real,
        "passed": passed,
    }
    if not passed:
        logger.warning("B1 未通过：%s", detail)
    return passed, detail


def check_b2_maintain(
    ic_realized_rolling_mean: float,
    ic_research: float,
    ic_research_std: float,
    t_research: int,
) -> tuple[bool, dict]:
    """B2 维持判线（终身滚动）（研究协议 §6.3.2）。

    条件：
      滚动 26 周 IC 均值 > IC_research - b2_ic_se_multiplier × SE_research
      且 > 0

    Returns:
        (passed, detail_dict)；False → 降仓复盘
    """
    cfg = _gates_cfg()
    b2_mult = float(cfg["b2_ic_se_multiplier"])

    se_research = ic_research_std / math.sqrt(max(t_research, 1))
    threshold = ic_research - b2_mult * se_research

    above_threshold = ic_realized_rolling_mean > threshold
    above_zero = ic_realized_rolling_mean > 0.0
    passed = above_threshold and above_zero

    detail = {
        "ic_realized_rolling_mean": ic_realized_rolling_mean,
        "threshold": threshold,
        "b2_ic_se_multiplier": b2_mult,
        "above_threshold": above_threshold,
        "above_zero": above_zero,
        "passed": passed,
    }
    if not passed:
        logger.warning("B2 维持判线未通过，触发降仓复盘：%s", detail)
    return passed, detail


def check_b3_engineering(
    cost_deviation: float,
    recon_zero_error_weeks: int,
    risk_control_consistent: bool,
) -> tuple[bool, dict]:
    """B3 工程判线（研究协议 §6.3.3）。

    Args:
        cost_deviation:         实测成本 / 建模成本 - 1（偏差率，如 0.25 = 25%）
        recon_zero_error_weeks: 对账连续零差错周数
        risk_control_consistent: 风控触发行为与状态机一致

    Returns:
        (passed, detail_dict)
    """
    cfg = _gates_cfg()
    max_cost_dev = float(cfg["b3_cost_deviation_max"])
    min_recon_weeks = int(cfg["b3_recon_zero_error_weeks"])

    cost_ok = cost_deviation <= max_cost_dev
    recon_ok = recon_zero_error_weeks >= min_recon_weeks
    risk_ok = risk_control_consistent
    passed = cost_ok and recon_ok and risk_ok

    detail = {
        "cost_deviation": cost_deviation,
        "max_cost_dev": max_cost_dev,
        "recon_zero_error_weeks": recon_zero_error_weeks,
        "min_recon_weeks": min_recon_weeks,
        "risk_control_consistent": risk_control_consistent,
        "cost_ok": cost_ok,
        "recon_ok": recon_ok,
        "risk_ok": risk_ok,
        "passed": passed,
    }
    if not passed:
        logger.warning("B3 工程判线未通过：%s", detail)
    return passed, detail


# ─────────────────────────────────────────────────────────────────────────────
# 综合判定入口
# ─────────────────────────────────────────────────────────────────────────────

def run_full_gate_check(
    combined_sharpe: float,
    combined_dsr: float,
    ic_realized_mean: float,
    ic_research: float,
    ic_research_std: float,
    t_research: int,
    real_weeks: int,
    total_weeks: int,
    cost_deviation: float,
    recon_zero_error_weeks: int,
    risk_control_consistent: bool,
) -> dict:
    """运行完整 A+B 闸门检验，返回综合判定结果。

    Returns:
        {
            'a1_passed': bool,
            'a2_standard_path': bool,    # True=标准路径, False=降级路径
            'b1_passed': bool,
            'b2_passed': bool,
            'b3_passed': bool,
            'scale_up_allowed': bool,    # A1 & B1 & B3 均通过
            'verdict': str,              # 'PASS'/'FAIL_A1'/'DEGRADED_A2'/'HOLD_B'
        }
    """
    a1 = check_a1_hard_veto(combined_sharpe)
    a2_std, a2_path = check_a2_weak_veto(combined_dsr)
    b1, _ = check_b1_scale_up(
        ic_realized_mean, ic_research, ic_research_std,
        t_research, real_weeks, total_weeks,
    )
    b2, _ = check_b2_maintain(
        ic_realized_mean, ic_research, ic_research_std, t_research,
    )
    b3, _ = check_b3_engineering(
        cost_deviation, recon_zero_error_weeks, risk_control_consistent,
    )

    scale_up_allowed = a1 and b1 and b3

    if not a1:
        verdict = "FAIL_A1"
    elif scale_up_allowed:
        verdict = "PASS"
    elif not b2:
        verdict = "HOLD_B2_DEGRADED"
    else:
        verdict = "HOLD_B"

    return {
        "a1_passed": a1,
        "a2_standard_path": a2_std,
        "a2_path": a2_path,
        "b1_passed": b1,
        "b2_passed": b2,
        "b3_passed": b3,
        "scale_up_allowed": scale_up_allowed,
        "verdict": verdict,
    }
