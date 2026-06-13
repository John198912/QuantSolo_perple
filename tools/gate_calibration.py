# QuantSolo 统计闸门校准工具 v2.0（Phase 1 交付物）
# 目标：为 B+A 方案定标
#   A 弱否决线（test 合并段）: 年化夏普 > 0 且 DSR >= theta，theta 待定标
#   B 行为闸门（模拟盘/5万阶段，主裁决）: 实盘 IC 与研究 IC 一致性检验的功效分析
# 覆盖：iid 正态 / 肥尾 t(5) / AR(1) 自相关；N_eff 预算情景
import numpy as np
from scipy.stats import norm, t as tdist

rng = np.random.default_rng(2026)
W = 52
T_FULL = 104   # 2024-2025 test 合并段（周）
N_SIM = 100_000
GAMMA = 0.5772156649

def sr0_weekly(n_eff, T):
    v = 1.0 / T
    return np.sqrt(v) * ((1 - GAMMA) * norm.ppf(1 - 1/n_eff)
                         + GAMMA * norm.ppf(1 - 1/(n_eff * np.e)))

def dsr_vec(sr_hat_w, n_eff, T, skew, kurt):
    s0 = sr0_weekly(max(n_eff, 1.0000001), T)
    denom = np.sqrt(np.maximum(1 - skew*sr_hat_w + (kurt-1)/4 * sr_hat_w**2, 1e-9))
    return norm.cdf((sr_hat_w - s0) * np.sqrt(T - 1) / denom)

def sample_moments(rets):
    m = rets.mean(1, keepdims=True)
    s = rets.std(1, ddof=1)
    z = (rets - m) / s[:, None]
    skew = (z**3).mean(1)
    kurt = (z**4).mean(1)
    return rets.mean(1)/s, skew, kurt

def gen_returns(true_sr_ann, T, kind):
    mu_w = true_sr_ann / np.sqrt(W)
    if kind == "normal":
        r = rng.normal(0, 1, size=(N_SIM, T))
    elif kind == "t5":  # 肥尾，峰度=9
        r = tdist.rvs(5, size=(N_SIM, T), random_state=rng) / np.sqrt(5/3)
    elif kind == "ar1":  # rho=0.2 正自相关
        rho = 0.2
        eps = rng.normal(0, 1, size=(N_SIM, T))
        r = np.empty_like(eps)
        r[:, 0] = eps[:, 0]
        for i in range(1, T):
            r[:, i] = rho*r[:, i-1] + np.sqrt(1-rho**2)*eps[:, i]
    return r + mu_w

print("="*90)
print("E. 弱否决线定标：合并段 [夏普>0 且 DSR>=theta] 的通过率")
print("   行：真实年化SR（0=无效策略，即希望被否决）；列：theta；面板：N_eff")
print("="*90)
true_grid = [0.0, 0.4, 0.8, 1.2, 1.5]
thetas = [0.30, 0.50, 0.70, 0.90, 0.95]
for n_eff in [3, 6, 10, 20]:
    print(f"\n--- N_eff = {n_eff}（iid 正态）  [SR0(年化期望最大null)={sr0_weekly(n_eff,T_FULL)*np.sqrt(W):.2f}] ---")
    print(f"{'真实SR':>7} | " + " | ".join(f"θ={t:<4}" for t in thetas))
    for s in true_grid:
        r = gen_returns(s, T_FULL, "normal")
        sr_w, sk, ku = sample_moments(r)
        pos = sr_w > 0
        row = []
        for th in thetas:
            ok = pos & (dsr_vec(sr_w, n_eff, T_FULL, sk, ku) >= th)
            row.append(f"{ok.mean()*100:>5.1f}%")
        print(f"{s:>7.1f} | " + " | ".join(row))

print()
print("="*90)
print("F. 分布稳健性：N_eff=6, 不同收益分布下 [夏普>0 且 DSR>=theta] 通过率")
print("="*90)
for kind, label in [("normal","iid正态"), ("t5","肥尾t(5)"), ("ar1","AR(1) rho=0.2")]:
    print(f"\n--- {label} ---")
    print(f"{'真实SR':>7} | " + " | ".join(f"θ={t:<4}" for t in thetas))
    for s in true_grid:
        r = gen_returns(s, T_FULL, kind)
        sr_w, sk, ku = sample_moments(r)
        if kind == "ar1":
            # 研究协议 T_eff 折减：T_eff = T*(1-rho)/(1+rho)
            T_use = T_FULL*(1-0.2)/(1+0.2)
        else:
            T_use = T_FULL
        pos = sr_w > 0
        row = []
        for th in thetas:
            ok = pos & (dsr_vec(sr_w, 6, T_use, sk, ku) >= th)
            row.append(f"{ok.mean()*100:>5.1f}%")
        print(f"{s:>7.1f} | " + " | ".join(row))

print()
print("="*90)
print("G. 行为闸门（B 主裁决）功效：模拟盘周度 rank-IC 一致性检验")
print("   H0: 实盘IC均值 >= 研究IC - 1*SE(研究IC)   单边 t 检验近似")
print("   研究IC=0.03, 周度IC截面std=0.10（A股中低频典型值）")
print("="*90)
ic_std = 0.10
research_ic = 0.03
for T_obs in [13, 26, 52]:
    se = ic_std/np.sqrt(T_obs)
    print(f"\n--- 观测窗 {T_obs} 周, SE(IC均值)={se:.4f} ---")
    print(f"{'真实IC':>7} | {'判定线: 实测IC均值 > 研究IC-1.0*SE':>34} | {'> 研究IC-1.645*SE':>18} | {'>0':>8}")
    for true_ic in [0.03, 0.015, 0.0, -0.01]:
        ics = rng.normal(true_ic, ic_std, size=(N_SIM, T_obs)).mean(1)
        p1 = (ics > research_ic - 1.0*se).mean()*100
        p2 = (ics > research_ic - 1.645*se).mean()*100
        p3 = (ics > 0).mean()*100
        tag = "保留(真IC=研究IC)" if true_ic==0.03 else ("衰减一半" if true_ic==0.015 else ("失效" if true_ic==0.0 else "反向"))
        print(f"{true_ic:>7.3f} | {p1:>33.1f}% | {p2:>17.1f}% | {p3:>7.1f}%   <- {tag}")

print()
print("="*90)
print("H. N_eff 预算阈值表：DSR>=0.5 等价的最低观测年化夏普（T=104 周）")
print("="*90)
print(f"{'N_eff':>6} | {'DSR>=0.5 所需观测年化SR':>24} | {'DSR>=0.7':>10} | {'DSR>=0.95':>10}")
def req_sr(n_eff, T, theta):
    lo, hi = -1.0, 2.0
    for _ in range(80):
        mid = (lo+hi)/2
        if dsr_vec(np.array([mid]), n_eff, T, np.array([0.0]), np.array([3.0]))[0] < theta: lo = mid
        else: hi = mid
    return mid*np.sqrt(W)
for n in [1, 2, 3, 4, 6, 8, 10, 15, 20]:
    print(f"{n:>6} | {req_sr(n, T_FULL, 0.5):>24.2f} | {req_sr(n, T_FULL, 0.7):>10.2f} | {req_sr(n, T_FULL, 0.95):>10.2f}")
