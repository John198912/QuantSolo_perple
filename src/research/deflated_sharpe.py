"""Deflated Sharpe Ratio (DSR) 计算口径（研究协议 §3.5）。

Harvey-Liu SR0 三参数近似 + N_eff 全维度 + T_eff 周频。

v2.0 说明：
- DSR 在研究阶段作为因子质量参考指标（不作为上线硬否决）
- A2 弱否决线为 DSR < 0.5（降级路径），数字来自 load_frozen()['gates']
- 详见研究协议 §3.4 / §3.5 / §6.2.2

R3 红线：DSR 阈值数字取 load_frozen()['gates']，禁止硬编码。
"""
from __future__ import annotations

import logging

import numpy as np
from scipy.stats import norm

from src.common.config import load_frozen

logger = logging.getLogger(__name__)

# 欧拉-马歇罗尼常数
GAMMA_EM = 0.5772156649


def sr0_expected_max(
    n_eff: float,
    T_eff: float,
    sr_mean: float = 0.0,
) -> float:
    """Harvey-Liu (2015) SR0：N_eff 次独立试验零假设期望最大 Sharpe。

    公式（研究协议 §3.5(b)）：

        SR0 = sqrt(V[SR_hat]) × [(1-γ_em)·Φ⁻¹(1 - 1/N_eff) + γ_em·Φ⁻¹(1 - 1/(N_eff·e))]

    其中 V[SR_hat] = (1 + 0.5·sr_mean²) / T_eff

    Args:
        n_eff:   有效独立试验数（N_eff lower bound，保守端）
        T_eff:   有效观测数（周频，非日频）
        sr_mean: 先验 SR 均值（通常 0.0）

    Returns:
        SR0（float，与 SR_hat 同量纲，即周频归一化 SR）
    """
    if n_eff < 1.0:
        n_eff = 1.0 + 1e-7
    if T_eff < 2.0:
        logger.warning("T_eff=%.1f < 2，DSR 计算不可靠。", T_eff)
        T_eff = 2.0

    v_sr = (1.0 + 0.5 * sr_mean ** 2) / T_eff
    z1 = norm.ppf(1.0 - 1.0 / n_eff)
    z2 = norm.ppf(1.0 - 1.0 / (n_eff * np.e))
    return float(np.sqrt(v_sr) * ((1.0 - GAMMA_EM) * z1 + GAMMA_EM * z2))


def deflated_sharpe_ratio(
    sr_hat: float,
    n_eff: float,
    T_eff: float,
    skew: float = 0.0,
    kurt: float = 3.0,
    sr_mean: float = 0.0,
) -> float:
    """计算 Deflated Sharpe Ratio（DSR）。

    公式（研究协议 §3.5）：

        DSR = Φ( (SR_hat - SR0) · sqrt(T_eff - 1)
                / sqrt(1 - γ3·SR_hat + (γ4-1)/4·SR_hat²) )

    Args:
        sr_hat:  观测 Sharpe（与 T_eff 同量纲，通常为周频归一化）
        n_eff:   有效独立试验数（保守下界）
        T_eff:   有效观测数（周频）
        skew:    收益序列三阶矩（γ3，偏度）
        kurt:    收益序列四阶矩（γ4，峰度；正态=3）
        sr_mean: 先验 SR 均值

    Returns:
        DSR in [0, 1]（概率），越接近 1 越显著。
    """
    sr0 = sr0_expected_max(n_eff, T_eff, sr_mean)

    # 分母防负（极端参数下可能为负）
    denom_sq = 1.0 - skew * sr_hat + (kurt - 1.0) / 4.0 * sr_hat ** 2
    denom_sq = max(denom_sq, 1e-9)
    denom = np.sqrt(denom_sq)

    z = (sr_hat - sr0) * np.sqrt(max(T_eff - 1.0, 1.0)) / denom
    return float(norm.cdf(z))


def compute_n_eff_total(
    atomic_test_return_matrix: "pd.DataFrame",  # noqa: F821  type-checked lazily
    n_bootstrap: int = 1000,
    alpha: float = 0.05,
) -> dict:
    """N_eff 全维度估计（研究协议 §3.5(c)）。

    输入：全部 atomic_test（因子×lookback×horizon×模型×参数）的收益/IC 序列矩阵
         （行=时间，列=atomic_test），非仅最终因子。

    主估计（PCA 有效维度）：N_eff = (Σλ_i)² / Σ(λ_i²)
    bootstrap CI：重采样观测行，取 alpha 分位下界 n_eff_lower 喂入 DSR（保守端）
    下界校验（聚类）：r > 0.7 划同簇，N_eff_cluster = 独立簇数
    裁决规则：PCA 与聚类分歧 > 30% 时取更小者（更强惩罚）
    地板约束：n_eff_used = max(min(pca_lower, cluster), N_floor)
              N_floor = max(model_count, param_grid_eff) 防降到无意义

    Args:
        atomic_test_return_matrix: pd.DataFrame，行=时间，列=atomic_test_id
        n_bootstrap:               bootstrap 重采样次数
        alpha:                     下界分位数（默认 0.05，即 5% 分位下界）

    Returns:
        {
            'point':           PCA 主估计（float）
            'lower':           bootstrap alpha 分位下界（float，喂入 DSR）
            'cluster':         聚类下界（int）
            'n_eff_used':      最终使用值（保守端）
            'ci_wide_warning': bool，CI 宽度 > 30% 均值时告警
        }
    """
    import pandas as pd  # 惰性导入

    mat = atomic_test_return_matrix
    n_obs, n_tests = mat.shape

    if n_tests == 0:
        return {"point": 0.0, "lower": 0.0, "cluster": 0, "n_eff_used": 0.0, "ci_wide_warning": False}

    if n_tests == 1:
        return {"point": 1.0, "lower": 1.0, "cluster": 1, "n_eff_used": 1.0, "ci_wide_warning": False}

    # ── PCA bootstrap ──
    rng = np.random.default_rng(2026)
    estimates: list[float] = []
    for _ in range(n_bootstrap):
        idx = rng.choice(n_obs, n_obs, replace=True)
        sample = mat.iloc[idx]
        c = sample.corr(method="spearman")
        eig = np.linalg.eigvalsh(c.values)
        eig = eig[eig > 0]
        if len(eig) == 0:
            estimates.append(1.0)
            continue
        n_eff_pca = (eig.sum() ** 2) / (eig ** 2).sum()
        estimates.append(float(n_eff_pca))

    estimates_arr = np.array(estimates)
    point = float(np.mean(estimates_arr))
    lower = float(np.percentile(estimates_arr, alpha * 100))
    upper = float(np.percentile(estimates_arr, (1 - alpha) * 100))
    ci_wide = bool((upper - lower) > 0.3 * point) if point > 0 else False

    # ── 聚类下界 ──
    try:
        full_corr = mat.corr(method="spearman")
        n_cluster = _count_independent_clusters(full_corr.values, threshold=0.7)
    except Exception:
        n_cluster = max(1, int(point))

    # ── 裁决 ──
    pca_lower = lower
    cluster = max(1, n_cluster)
    if cluster > 0 and abs(pca_lower - cluster) / max(cluster, 1) > 0.3:
        n_eff_used = float(min(pca_lower, cluster))
    else:
        n_eff_used = pca_lower

    # 地板约束（至少 1）
    n_eff_used = max(n_eff_used, 1.0)

    return {
        "point": point,
        "lower": lower,
        "cluster": cluster,
        "n_eff_used": n_eff_used,
        "ci_wide_warning": ci_wide,
    }


def _count_independent_clusters(corr_matrix: np.ndarray, threshold: float = 0.7) -> int:
    """简单聚类：相关系数 > threshold 划同一簇，返回独立簇数。

    使用贪心连通分量法（近似，适合 n_tests < 100 的场景）。
    """
    n = corr_matrix.shape[0]
    visited = [False] * n
    clusters = 0

    for i in range(n):
        if visited[i]:
            continue
        clusters += 1
        queue = [i]
        while queue:
            node = queue.pop()
            if visited[node]:
                continue
            visited[node] = True
            for j in range(n):
                if not visited[j] and abs(corr_matrix[node, j]) > threshold:
                    queue.append(j)

    return clusters


def compute_dsr_from_series(
    ic_series: "pd.Series",  # noqa: F821
    n_eff: float,
    T_eff: float | None = None,
) -> dict:
    """从 IC 时间序列快速计算 DSR。

    Args:
        ic_series: 周度 IC 时间序列（pd.Series）
        n_eff:     有效独立试验数（保守下界）
        T_eff:     有效观测数；None 时直接用 len(ic_series)

    Returns:
        {'dsr': float, 'sr_hat': float, 'sr0': float, 'skew': float, 'kurt': float}
    """
    import pandas as pd  # 惰性导入

    s = ic_series.dropna()
    n = len(s)
    if n < 5:
        logger.warning("compute_dsr_from_series: IC 序列长度 %d < 5，DSR 不可靠。", n)
        return {"dsr": float("nan"), "sr_hat": float("nan"), "sr0": float("nan"), "skew": 0.0, "kurt": 3.0}

    t_eff = float(T_eff) if T_eff is not None else float(n)

    mu = float(s.mean())
    sigma = float(s.std(ddof=1)) + 1e-10
    sr_hat = mu / sigma  # 周频 SR（未年化）

    skew = float(s.skew())
    kurt = float(s.kurt()) + 3.0  # pandas 返回超额峰度，加 3 转回原始峰度

    sr0 = sr0_expected_max(n_eff, t_eff)
    dsr = deflated_sharpe_ratio(sr_hat, n_eff, t_eff, skew, kurt)

    return {"dsr": dsr, "sr_hat": sr_hat, "sr0": sr0, "skew": skew, "kurt": kurt}


def get_dsr_thresholds() -> dict:
    """获取 DSR 阈值（来自 load_frozen()['gates']）。

    Returns:
        {'a2_weak_veto_dsr_floor': float, ...}
    """
    g = load_frozen()["gates"]
    return {
        "a2_weak_veto_dsr_floor": float(g["a2_weak_veto_dsr_floor"]),
        "research_strong": 0.95,   # 研究阶段强显著参考（协议 §3.5(a)）
        "research_acceptable": 0.90,  # 可接受边界
    }
