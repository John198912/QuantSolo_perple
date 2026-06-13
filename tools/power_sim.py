# QuantSolo 统计闸门功效模拟（S1 待办落地）
# 检验对象：
#   A. 研究协议 v1.3 §6.2.1 主判定：全 test 合并段(约104周) DSR >= 0.95
#   B. 研究协议 v1.3 §6.2.2 子段弱判定：四个半年子段(各26周)
#      (1) 子段夏普全部>0  (2) 四子段夏普均值>=0.8(年化)
#      (3) 子段夏普离散度 max-min <= 0.8(年化)
# 假设：周频 iid 正态收益（偏度0、峰度3），无自相关 —— 对判定最有利的理想化假设
import numpy as np
from scipy.stats import norm

rng = np.random.default_rng(42)
W = 52          # 周/年
T_FULL = 104    # 2年test
T_SEG = 26      # 半年子段
N_SIM = 200_000
GAMMA = 0.5772156649

def sr0_weekly(n_eff, T):
    """Bailey-Lopez de Prado 期望最大夏普(null), 周频单位; V[SR]≈1/T"""
    v = 1.0 / T
    return np.sqrt(v) * ((1 - GAMMA) * norm.ppf(1 - 1/n_eff)
                         + GAMMA * norm.ppf(1 - 1/(n_eff * np.e)))

def dsr(sr_hat_w, n_eff, T, skew=0.0, kurt=3.0):
    s0 = sr0_weekly(n_eff, T)
    denom = np.sqrt(1 - skew*sr_hat_w + (kurt-1)/4 * sr_hat_w**2)
    return norm.cdf((sr_hat_w - s0) * np.sqrt(T - 1) / denom)

def required_ann_sr_for_dsr95(n_eff, T):
    """解出 DSR=0.95 所需的观测年化夏普"""
    lo, hi = 0.0, 2.0  # weekly
    for _ in range(80):
        mid = (lo + hi) / 2
        if dsr(mid, n_eff, T) < 0.95: lo = mid
        else: hi = mid
    return mid * np.sqrt(W)

print("="*72)
print("A. 合并段 DSR>=0.95 所需观测年化夏普 (T=104周, 2024-2025 全test段)")
print("="*72)
print(f"{'N_eff':>6} | {'SR0(年化,null期望最大)':>22} | {'DSR>=0.95所需观测年化SR':>24}")
for n in [1, 2, 3, 5, 10, 20, 50, 100, 200]:
    n_ = max(n, 1.0000001)
    print(f"{n:>6} | {sr0_weekly(n_, T_FULL)*np.sqrt(W):>22.2f} | {required_ann_sr_for_dsr95(n_, T_FULL):>24.2f}")

print()
print("="*72)
print("B. 真实策略通过 DSR>=0.95 的概率 (Monte Carlo, T=104周)")
print("="*72)
true_srs = [0.5, 0.8, 1.0, 1.2, 1.5, 2.0, 2.5]
neffs = [3, 5, 10, 20, 50]
header = f"{'真实年化SR':>10} | " + " | ".join(f"N_eff={n:>3}" for n in neffs)
print(header)
for s_ann in true_srs:
    mu_w = s_ann / np.sqrt(W)
    rets = rng.normal(mu_w, 1.0, size=(N_SIM, T_FULL))
    sr_hat = rets.mean(1) / rets.std(1, ddof=1)
    row = []
    for n in neffs:
        row.append(f"{(dsr(sr_hat, n, T_FULL) >= 0.95).mean()*100:>8.1f}%")
    print(f"{s_ann:>10.1f} | " + " | ".join(row))

print()
print("="*72)
print("C. 四子段弱判定通过率 (Monte Carlo, 4 x 26周, 年化口径)")
print("="*72)
print(f"{'真实年化SR':>10} | {'全部>0':>8} | {'均值>=0.8':>9} | {'离散度<=0.8':>10} | {'三项同时':>8} | {'弱判定+DSR(N_eff=10)':>18}")
for s_ann in true_srs:
    mu_w = s_ann / np.sqrt(W)
    rets = rng.normal(mu_w, 1.0, size=(N_SIM, 4, T_SEG))
    seg_sr = rets.mean(2) / rets.std(2, ddof=1) * np.sqrt(W)  # 年化
    all_pos = (seg_sr > 0).all(1)
    mean_ok = seg_sr.mean(1) >= 0.8
    disp_ok = (seg_sr.max(1) - seg_sr.min(1)) <= 0.8
    weak = all_pos & mean_ok & disp_ok
    # 合并段 DSR（同一组收益拼接）
    full = rets.reshape(N_SIM, -1)
    sr_hat_w = full.mean(1) / full.std(1, ddof=1)
    dsr_ok = dsr(sr_hat_w, 10, T_FULL) >= 0.95
    print(f"{s_ann:>10.1f} | {all_pos.mean()*100:>7.1f}% | {mean_ok.mean()*100:>8.1f}% | "
          f"{disp_ok.mean()*100:>9.1f}% | {weak.mean()*100:>7.1f}% | {(weak&dsr_ok).mean()*100:>17.2f}%")

print()
print("="*72)
print("D. 参考：单个26周子段年化夏普估计的标准误")
print("="*72)
for s_ann in [0.8, 1.5]:
    se = np.sqrt((1 + 0.5*(s_ann/np.sqrt(W))**2) / T_SEG) * np.sqrt(W)
    print(f"真实年化SR={s_ann}: 子段夏普估计SE≈{se:.2f}(年化) -> 4子段期望极差≈{2.06*se:.2f}")
