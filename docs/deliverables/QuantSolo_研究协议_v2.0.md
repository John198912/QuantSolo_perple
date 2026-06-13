# QuantSolo —《研究协议 v2.0》（全文版 · 防统计自欺宪法）

**文档编号：** QS-C02  
**版本：** v2.0  
**日期：** 2026-06-12  
**文档定位：** 宪法文档第二份（C02），四份可开工文档之第二。防统计自欺宪法，必须在写第一行回测代码前冻结。全文版（非增量补丁）。  
**与 SSOT 冲突时以基线为准已校验**：本文档所有数字已按《QuantSolo v2.0 决策基线与一致性规范》（内部 SSOT）与《统计闸门校准报告 v1.0》（QS-CAL-001）双重核对。

---

## 上下游依赖

| 方向 | 文档编号 | 说明 |
|------|----------|------|
| 上游（必须先冻结） | QS-C03 | 点时数据契约 v2.0（所有数据读取口径） |
| 上游（必须先冻结） | QS-CAL-001 | 统计闸门校准报告 v1.0（闸门数字唯一来源） |
| 平行 | QS-C01 | 设计文档 v5.0（成本模型、组合设计、五条铁律全文） |
| 下游（消费本文档） | QS-C04 | 执行与风控状态机 v1.3（状态机 15 态） |
| 下游（消费本文档） | QS-C05 | 模拟盘验收手册 v2.0（背离定义消费本文档 §7） |
| 下游（消费本文档） | QS-E04 | 软件开发功能设计文档（N_eff 预算、试验登记表） |

**交叉引用规则**（全文档体系统一）：  
- 闸门数字只引用 QS-CAL-001，不重抄推导；  
- 五条铁律全文只在 QS-C01，本文档只引用编号；  
- 背离（trigger_ic_audit）定义的**唯一权威文本**在本文档 §7，其他文档引用时只引编号；  
- 状态全集（15 运行态）只在 QS-C04；  
- 成本模型（cost_model_id）只在 QS-C01 §8。

---

## 版本演进表

| 版本 | 日期 | 主要变更摘要 |
|------|------|-------------|
| v1.0 | 2026-05 | 初版。确立三段切分（train/validation/test）、五铁律、三阶段因子筛选、BH-FDR、DSR框架、purged walk-forward。 |
| v1.1 | 2026-05 | 修复：test 四段改为一次性综合判定（堵 FWER 膨胀）；N_eff 覆盖全部 atomic_test 维度；M_registered 绑定 atomic_test_id；T_eff 统一周频+fold折减；入场 universe 拆两层；成本模型统一引用；合成权重预注册；WATCH 基准修正。 |
| v1.2 | 2026-06-初 | 见附录 A · v1.2 变更日志（D1–D18，共 18 条修复）。冻结版，堵住 v1.1 残留自由度后门。§6.2 递增 DSR 阈值（0.95/0.975/0.983/0.9875）为本版核心判定机制。 |
| v1.3 | 2026-06-11 | 受控解冻修订，承接《二次筛查审查报告 v1.0》P0/P1 项。S1：test 子段 DSR 改为全合并段计一次 DSR≥0.95；子段降为弱判定（符号/均值/离散度）。C2：variant→preprocessing 映射，N 只认 atomic_test_id。S2：WATCH 触发先做 Barra 风格归因。C4/C5：统一背离统计定义（bootstrap CI 下界+N_min）。新增子段夏普离散度 max−min≤0.8 判据。 |
| **v2.0** | **2026-06-12** | **全文合并版（非增量补丁）**。核心修订：①§6 整体重写为 B为主+A为辅闸门体系（QS-CAL-001 定标），废除 v1.2 四子段递增 DSR、v1.3 合并段 DSR≥0.95 硬判定（Monte Carlo 证明不可达，真实 SR=0.8 通过率 0.2%–3.5%）、v1.3 子段离散度判据（max−min≤0.8 在 26 周年化 SE≈1.42 下期望极差≈2.9，测的是噪声）；②新增 §N_eff 试验预算体系（全生命周期 test 评估≤6 次、强制试验登记表）；③背离（trigger_ic_audit）唯一定义改为 v1.3 §7 bootstrap CI 口径，废除「连续8周<中位数30%」旧定义；④DSR 公式细节（Harvey-Liu SR0、N_eff PCA+bootstrap+聚类、T_eff 周频+fold折减）从 v1.2 §3.5 保留并入；⑤因子流程、BH-FDR、walk-forward 等既有内容完整保留并对齐 SSOT 数字。 |

> **废除记录（Monte Carlo 证明不可达）**：  
> - v1.2 四子段递增 DSR（2024H1≥0.95 / 2024H2≥0.975 / 2025H1≥0.983 / 2025H2≥0.9875）：半年子段 T_eff≈25 周，所需样本内夏普远超现实档 0.8，与上线标准数学不兼容；  
> - v1.3 合并段 DSR≥0.95 作为**硬判定**：N_eff=6 下真实 SR=0.8 策略通过率约 43.6%（误伤率 56.4%），加之 SR=0（坏策略）通过率约 9.8%——统计无法可靠区分好坏策略，硬否决权不应由 DSR 持有；  
> - v1.3 子段夏普离散度 max−min≤0.8：26 周年化夏普 SE≈1.42，4 子段期望极差≈2.9，判据与策略真实质量无关，测量的是噪声结构，废除。  
> （以上废除均以 QS-CAL-001 § 一、二、三 Monte Carlo 结果为依据，随机种子 2026，每组 10 万次）

---

## 〇、五条不可违背的铁律

> 本文档只引用铁律编号。铁律全文唯一权威版本在 QS-C01 §（五条铁律）。违反任意一条，本轮研究结果作废。

1. **铁律①（风控凌驾）**：风控凌驾策略，下单唯一入口必经守卫。
2. **铁律②（研究纪律）**：冻结样本、试验登记、N_eff 预算（详见本文档 §4 与 §N_eff）。
3. **铁律③（点时正确性）**：一切研究数据按 visible_at 重放（走 QS-C03 点时接口）。
4. **铁律④（资金分级）**：5万（或 A2 降级路径 2.5万）→ 行为闸门（B1+B3）→ 20万（详见本文档 §6）。
5. **铁律⑤（人工巡检）**：每日人工巡检+周度对账，自动化≠无人值守。

**具体约束（全部预注册，在动手前冻结）：**

- 所有候选因子、参数网格、模型类型、**因子合成权重法**须在 `factor_registry` 预注册，确定 `M_registered` 与试验上限 `N_trials`，之后不得新增（新增须开新研究轮次、重置编号）。
- `test` 段在整个研发期一次都不能看，仅在上实盘前运行一次，看完即"烧毁"，再用须换新时间段。
- BH-FDR 与 Bonferroni 都用 `M_registered`，且 **`M_registered = count(distinct atomic_test_id where trial_type='factor')`**，t>3 仅作叠加条件，不作前置过滤。
- DSR 的裸试验数 `N_trials_raw` 取自 `factor_registry` + `research_ledger` 实际记账，禁止低报。喂入 DSR 的是经相关性折减的 `N_eff`，N_eff 的降维覆盖全部 atomic_test 维度（见 §3.5）。
- 任何特征/标签/收益计算只能经 QS-C03（点时数据契约 v2.0）点时接口（`daily_bar_asof`，传 `data_cut_id`），禁止直连原始表。

---

## 一、数据集切分（物理三段隔离 + walk-forward 关系厘清）

### 1.1 总时间轴与冻结边界

| 段 | 起止 | 用途 | 可见性 |
|----|------|------|--------|
| `train` | 2016-01-01 ~ 2021-12-31 | 因子计算、参数搜索、模型训练 | 全程可反复用 |
| `validation` | 2022-01-01 ~ 2023-12-31 | 因子三阶段筛选、超参选择、择时 A/B | 全程可反复用（上限 5 轮，见 §6.1） |
| **`test`（冻结）** | **2024-01-01 ~ 2025-12-31** | **最终一次性综合验收，全程封存** | **上实盘前跑且仅跑一次** |

> 数据切分冻结（来源：QS-C01 §4）：train 2016–2021 / validation 2022–2023 / test 2024–2025。  
> 筛因子只能用 train+validation；test 段在 QS-C03 层面也应通过 `data_cut_id` 的 as_of 上限封锁，物理上无法被研发期查询。

### 1.2 两套切分的关系

- **固定三段**只定义"哪段数据物理可见"——`train+validation`（2016–2023）研发期可见，`test`（2024–2025）封存。
- **walk-forward 仅在 `train+validation`（2016–2023）内部滚动**，产生多个 fold 样本外 IC，作为三阶段筛选的统计依据，绝不触碰 test 段。
- **最终验收**：研发期结论基于 walk-forward fold；`test` 只在上实盘前做一次性综合确认，不参与任何调参与筛选。

### 1.3 Purged & Embargo Walk-Forward（防标签泄漏）

- **purge**：训练集中与验证标签时间窗重叠的样本剔除（label horizon = H 日，则 purge 末尾 H 日）
- **embargo**：验证集之后再隔离 E 日（E ≈ H）防序列自相关泄漏
- **滚动窗口**：训练窗 36 个月、验证窗 6 个月、步长 6 个月向前滚动，严格在 2016–2023 内滚动
- **节假日/停牌处理**：purge/embargo 的"H 日"按 `trade_calendar` 的**交易日**计，非自然日

```python
def purged_walkforward_splits(dates, train_months=36, valid_months=6,
                              step_months=6, label_horizon_days=5,
                              embargo_days=5, calendar=None):
    """生成 (train_idx, valid_idx) 序列，已 purge label 重叠样本并施加 embargo。
    铁律: 不触碰 test 段(2024-01-01 起)，仅在 train+validation(2016-2023) 内滚动。
    purge/embargo 的 H/E 按 calendar 交易日计。返回 list[(train_idx, valid_idx)]。
    """
```

---

## 二、标签（label）构造公式

### 2.1 主标签：闭合执行的净收益标签（H=5，周度调仓对齐）

\[
y_{i,t} = \frac{P^{adj,exit}_{i,t+H+1}}{P^{adj,entry}_{i,t+1}} - 1 - c_{i,t}
\]

- **信号生成时间**：t 日收盘后（只用 `visible_at <= t收盘` 信息）
- **入场时间/价格**：t+1 日，入场价 `P_entry` 默认 t+1 **VWAP**（口径声明见 §2.5）
- **持有期结束/退出**：t+H+1 日，退出价 `P_exit` 同口径
- **净收益**：扣除成本 \(c_{i,t}\)，**统一引用 QS-C01 §8 的 `cost_model_id`**（见 §2.6）
- \(P^{adj}\) 为点时前复权价（`daily_bar_asof(adjust='qfq_pit', data_cut_id=...)`，走 QS-C03）

### 2.2 标签清洗规则（区分入场端/出场端，消除幸存者偏差）

- **入场端（t+1）不可建仓 → 剔除**：t+1 停牌、t+1 涨停一字板无法买入（属 §2.4 `execution_filter_t1`，非研究 universe）
- **出场端（t+H+1）不可平仓 → 不静默剔除，进入真实撮合约束**：
  - t+H+1 停牌 → 顺延至下一可交易日可成交价计收益，不置 NaN
  - t+H+1 跌停一字板 → 用可达成成交价/顺延计收益
- **退市处理**：t+1 已知将退市 → 入场端剔除；持有期内退市 → 按退市清算价计入真实收益
- 极端收益（|y|>50%）winsorize 至 1%/99% 分位，原值留档
- 标签计算窗口必须完全落在 train 或 validation 内，跨 test 边界样本剔除
- **样本含退市股**（来源：QS-C01 §4），不得事后清洗

### 2.3 排序标签（用于 rank IC 与 LightGBM）

横截面分位排名 \(r_{i,t} = \text{rank}(y_{i,t}) / N_t \in (0,1]\)，每个调仓截面独立计算。

### 2.4 入场 universe 拆两层（防未来信息边界）

**v1.2 修正**：v1.1 把"次日一字板"写入研究 universe，但周一收盘生成信号时并不知周二是否一字板——这是未来信息泄漏。拆为两层：

```python
# 层1: signal_universe_t —— 只含 t 日收盘已知信息, 用于研究入池
def signal_universe_t(date_t):
    """剔除: t 日已 ST / t 日停牌 / 上市<250日 / t 日成交额<5000万 /
    每手>1.6万。全部基于 t 日 visible_at<=t收盘 的点时信息。"""

# 层2: execution_filter_t1 —— t+1 执行时的撮合失败处理
def execution_filter_t1(signal_list, date_t1):
    """t+1 实际不可成交者(停牌/涨停一字板)标记 fill_failed,
    现金留存或按预注册规则再分配, 不回溯剔除研究样本。"""
```

**universe 硬门槛**（来源：QS-C01 §4）：日均成交额≥5000万、剔除 ST/停牌/上市<250日；行业≤30%、单票≤8%。

### 2.5 入场价口径声明（模拟-现实鸿沟管理）

- **主口径**：t+1 VWAP（与 QS-C01 §8 调仓窗口成交假设对齐）
- **强制敏感性分析**：回测报告须同时输出 t+1 开盘价口径结果；**两者夏普差 > 5bp 时告警**，差异过大说明策略依赖不可实现的择时优势
- **VWAP 须与 QS-C01 §8 动态滑点模型口径统一**，不得用理想 VWAP 而旁路滑点

### 2.6 成本模型统一引用（防口径漂移）

单因子分组多空收益与组合 DSR **必须用同一 `cost_model_id`**，直接引用 QS-C01 §8：

**成本参数**（来源：QS-C01 §4）：印花税卖出 0.05%、佣金万2.5（**最低 5 元**）、过户费万0.1；回测滑点≥0.2%；小盘股冲击成本按真实成交额建模；100 股取整 + 现金碎片。研究协议不得自定义简化成本。

### 2.7 固定研究口径（写死消除隐性自由度，全部预注册）

- **universe**：见 §2.4 `signal_universe_t`；行业≤30%、单票≤8%
- **调仓日**：每周一收盘生成信号，T+1 执行
- **截面 IC 加权**：等权；单截面有效股票数 < 50 只时剔除
- **因子截面标准化**：rank 标准化；MAD 去极值 + 市值/行业中性化 + z-score（来源：QS-C01 §4）
- **因子合成权重法（预注册项）**：线性阶段 **ICIR 加权**；该权重法写入 `factor_registry`，不得事后调整凑夏普

---

## 三、因子三阶段筛选（全因子族检验 · 可执行伪代码）

**因子流程**（来源：QS-C01 §4）：宽进严出 15–20 候选 → 6–8 最终入选。

### 3.1 t 统计量的精确定义

- 计算 **rank IC 时间序列**（每调仓截面因子值与未来收益 Spearman 相关）
- IC 序列自相关，t 统计量用 **Newey-West (HAC) 修正**，L = floor(4×(T/100)^(2/9))
- **小样本铁律**：fold 内 IC 序列 **T < 200 时强制 block bootstrap**（块长 ≈ H）
- 因子方向预注册：负向因子先方向归一化为"越大越好"

```python
def factor_ic_tstat(factor_panel, label_rank, direction, T):
    """返回 {ic_mean, ic_std, ic_ir, t_stat_nw, p_value_onesided, sign_stable_rate}。
    direction 方向归一化后: T>=200 用 NW; T<200 用 block bootstrap(块长≈H)。
    p_value_onesided: 单侧(方向已预注册, 反向显著应 FAIL)。
    sign_stable_rate: 用 block bootstrap 经验分布评估(校正符号自相关聚集)。
    """
```

### 3.2 阶段一：统计显著（对 M_registered 做 BH-FDR + 单侧 t>3 叠加）

```python
def stage1_statistical(reg_factors, M_registered, q_fdr=0.10, t_thresh=3.0):
    """M_registered = count(distinct atomic_test_id where trial_type='factor')  # 绑定 atomic
    1) 计算各因子单侧 p_value(方向归一化, HAC 或 bootstrap)
    2) BH: 升序 p(1..M), 找最大 j 使 p(j) <= (j/M_registered)*q_fdr
    3) BH 通过集合 ∩ {t_nw > t_thresh}  <-- 单侧 t>3, 反向显著(t_nw<-t_thresh)判 FAIL
    返回通过因子集合。BH 分母用 M_registered, 不用幸存者数。
    """
```

> **坦白说明**：对本项目 M_registered≈15–20 与单侧 t>3（p≈0.00135），BH-FDR(q=0.10) 临界值约 0.05–0.10 远超 0.00135，故阶段一实际由 **t>3 主导过滤**。BH-FDR 在此保留作为"t>3 万一漏过边缘显著因子"的安全网，并为未来 M_registered 扩大到 50+ 时提供可扩展性。

### 3.3 阶段二：经济学显著（ICIR + 扣费 + 换手率）

| 条件 | 阈值 |
|------|------|
| 单因子 ICIR (= IC_mean / IC_std) | > 0.3（强显著 > 0.5） |
| 单因子 IC 均值 | > 0.015（与 ICIR 组合判定） |
| IC 符号稳定率（block bootstrap 校正自相关后） | > 60% |
| 分组多空收益 Top−Bottom | **扣成本（cost_model_id）后 > 0**，单调性：Top>Q3>Q2>Q1>Bottom 的 4 个不等式至少 3 个成立 |
| **因子 Top 组周度换手率** | **< 50%（或扣费后净 IC > 0）** |

### 3.4 阶段三：Bonferroni 终筛 + Deflated Sharpe（研究阶段使用，与上线判定分离）

```python
def stage3_final(survivors, M_registered, sharpe_candidates,
                 N_trials_raw, n_eff_total, T_eff):
    """1) Bonferroni: p < 0.05 / M_registered  (M_registered 用 atomic 计数)
       2) DSR: 按 n_eff_total(覆盖全维度, 非仅因子) 与非正态性折减
          用于因子筛选质量评估（不再作为上线硬否决，上线判定见 §6）
       3) 经济上保留 6-8 个进入生产
    返回最终上线因子列表 (<= 8)。
    """
    # DSR 用于研究阶段因子质量评估参考
    # 上线判定以 §6 B为主+A为辅闸门为准，见 QS-CAL-001
```

> **v2.0 说明**：DSR 在 §3.4 保留作为研究阶段**因子筛选质量参考指标**，但 DSR 阈值**不再是上线决策的硬否决条件**。上线判定已重写为 §6 的 B为主+A为辅闸门体系（依据 QS-CAL-001 Monte Carlo 定标）。DSR 的硬否决权移除的理由：N_eff=6 下真实 SR=0.8 策略通过 DSR≥0.95 的概率仅约 43.6%，误伤率 56.4%——无法承担硬否决权（见 QS-CAL-001 §二）。

### 3.5 Deflated Sharpe Ratio 计算口径（Harvey-Liu SR0 + N_eff 全维度 + T_eff 周频）

本节 DSR 公式细节从 v1.2 §3.5 完整保留。

**DSR 公式：**

\[
\text{DSR} = \Phi\!\left( \frac{(\widehat{SR} - SR_0)\sqrt{T_{eff}-1}}{\sqrt{1 - \gamma_3 \widehat{SR} + \frac{\gamma_4-1}{4}\widehat{SR}^2}} \right)
\]

**(a) 通过阈值**（研究阶段因子质量参考）：`DSR >= 0.95` 强显著，`>= 0.90` 可接受边界，`< 0.90` 警示。

> v2.0 变更：此阈值仅为研究阶段参考，不作为上线硬否决。A2 弱否决线改为 DSR < 0.5（降级路径，见 §6），依据 QS-CAL-001 §二 定标。

**(b) SR0：Harvey & Liu (2015) 三参数近似：**

\[
SR_0 = \sqrt{V[\widehat{SR}]} \cdot \left[(1-\gamma_{em})\,\Phi^{-1}\!\left(1-\tfrac{1}{N_{eff}}\right) + \gamma_{em}\,\Phi^{-1}\!\left(1-\tfrac{1}{N_{eff} e}\right)\right]
\]

\(\gamma_{em}=0.5772\)，\(V[\widehat{SR}] = \frac{1 + \frac{1}{2}\widehat{SR}^2}{T_{eff}}\)。

**(c) N_eff 覆盖全部 atomic_test 维度（v1.2 核心修正 + v1.3 C2 口径对齐）：**

v1.1 仅对因子 IC 相关降维，漏算 lookback/horizon/模型/参数维度的选择偏差。v1.2 改为对**全部 atomic_test 的收益序列矩阵**降维，并取 bootstrap 保守下界；v1.3 C2 修复了 variant 重计问题（variant 归为 preprocessing 维度取值，不独立乘入）：

```python
def compute_n_eff_total(atomic_test_return_matrix, n_bootstrap=1000, alpha=0.05):
    """输入: 全部 atomic_test(因子×lookback×horizon×模型×参数)的收益/IC 序列矩阵,
    非仅最终因子。
    主估计(PCA有效维度): N_eff=(Σλ_i)²/Σ(λ_i²)
    bootstrap CI: 重采样观测行, 取 alpha 分位下界 n_eff_lower 喂入 DSR(保守端)
    下界校验(聚类): r>0.7 划同簇, N_eff_cluster=独立簇数
    裁决规则: PCA 与聚类分歧 >30% 时取更小者(更强惩罚, 防挑有利值)
    地板约束: n_eff_used = max(min(pca_lower, cluster), N_floor),
              N_floor = max(model_count, param_grid_eff) 防降到无意义
    """
    n_obs = len(atomic_test_return_matrix)
    est = []
    for _ in range(n_bootstrap):
        idx = np.random.choice(n_obs, n_obs, replace=True)
        c = atomic_test_return_matrix.iloc[idx].corr(method='spearman')
        eig = np.linalg.eigvalsh(c); eig = eig[eig > 0]
        est.append((eig.sum()**2) / (eig**2).sum())
    return {'point': np.mean(est),
            'lower': np.percentile(est, alpha*100),   # 保守端, 喂 DSR
            'ci_wide_warning': (np.percentile(est,(1-alpha)*100)
                                - np.percentile(est,alpha*100)) > 0.3*np.mean(est)}

def expected_max_sr(N_eff, T_eff, sr_mean=0.0):
    from scipy.stats import norm
    g = 0.5772156649
    v_sr = (1 + 0.5*sr_mean**2) / T_eff
    z1 = norm.ppf(1 - 1/N_eff); z2 = norm.ppf(1 - 1/(N_eff*np.e))
    return np.sqrt(v_sr) * ((1-g)*z1 + g*z2)
```

**N 计数唯一口径**（v1.3 C2 修复，写死）：
- `atomic_test_id = hash(hypothesis × formula × lookback × universe × preprocessing × label_horizon × model)`
- `preprocessing` 维度的取值域 = `factor_variant`（raw/processed/orthogonal）；`version`（公式变更）并入 `formula` 维度
- **`factor_registry.variant_count` 不再独立喂 DSR**；DSR 的 `N_trials_raw / n_eff_total` 一律由 `research_ledger` 按 `atomic_test_id` 自动统计
- **防双计断言（入研究闸门）**：校验 `count(distinct atomic_test_id)` 不大于 `Σ(version×variant×lookback×horizon×model×param)` 的笛卡尔上界，且无某维度被同时在 variant 与 preprocessing 重复计入

**(d) T_eff 统一周频 + fold 间自相关折减（v1.2 修正）：**

v1.1 误写"验证期总交易日数"，周度调仓下 8 年会从约 400 周膨胀到约 1900 日、虚高 DSR。改为：

```python
def compute_T_eff(fold_ic_series, step_months, valid_months):
    """T_eff = 非重叠的【周频】组合收益观察数(非日频交易日数)。"""
    base_T = sum(len(s) for s in fold_ic_series)  # 各 fold 周频 IC 数
    overlap = step_months/valid_months if step_months < valid_months else 1.0
    T_dedup = base_T * overlap
    if len(fold_ic_series) > 1:                    # fold 间串行相关折减
        fm = [s.mean() for s in fold_ic_series]
        rho = np.corrcoef(fm[:-1], fm[1:])[0,1]
        k = len(fold_ic_series)
        T_final = T_dedup / (1 + max(rho,0)*(k-1)/k)
        return min(T_final, base_T*1.5/k)          # 上界约束防高估
    return T_dedup
```

**AR(1) 情形补充**（来源 QS-CAL-001 §二）：AR(1) ρ=0.2 时按 T_eff = T(1−ρ)/(1+ρ) 折减，DSR 下 SR=0.8 策略通过率降至约 35.6%，折减偏保守，可接受。

**(e) 双列审计：** `research_ledger` 同时记 `n_trials_raw`（铁律②防低报）与 `n_eff_used`（保守下界，实际喂 DSR）。

---

## 四、Trial Logging（试验次数记账 · 喂入 DSR）

```python
def log_trial(trial_type, spec, result, git_hash, data_cut_id):
    """每测试一个 atomic_test 即记一行到 research_ledger(append-only, 禁补记)。
    N_trials_raw = research_ledger 本轮累计行数。data_cut_id 为 QS-C03 口径。
    """
```

| 计数项 | 进入 N_trials_raw | 进入 N_eff 降维矩阵 | 说明 |
|--------|:--:|:--:|------|
| 每个候选因子 | ✅ | ✅ | 含同因子不同 lookback 变体 |
| 每组超参网格点 | ✅ | ✅ | grid/random search 每次评估 |
| label horizon H∈{3,5,10} 扫描 | ✅ | ✅ | 必须进 N_eff 矩阵 |
| 每个模型类型 | ✅ | ✅ | Ridge / LightGBM 各算 |
| 数据闸门校验 | ❌ | ❌ | 非策略试验 |

- **atomic_test_id 粒度**：hypothesis × formula × lookback × universe × preprocessing × label_horizon × model；`M_registered = count(distinct atomic_test_id where trial_type='factor')`
- **lookback 网格化铁律**：lookback 必须网格化预注册（如 {5,10,20,60,120}），禁止连续空间搜索
- **自律条款**：低报 N 等于自欺。N 以 research_ledger 自动统计为准，不接受人工"估计"

---

## 五、N_eff 试验预算体系（v2.0 新增章节）

> 依据：QS-CAL-001 §二.2.3「N_eff 预算封顶 6 并强制试验登记」定标裁决。本章为铁律②（研究纪律）的执行细则。

### 5.1 预算规则

**全生命周期 test 段评估次数上限：≤ 6 次**

- 每次进入 `test` 段评估的策略形态（因子组合、参数配置、模型选择）记入**试验登记表**（见 §5.2），计为消耗 1 次 N_eff 预算；
- **整个项目生命周期**（跨轮次、跨年度）`test` 段评估次数累计 ≤ 6；
- 用满 6 次后，只能动用**新的延后数据**（2026 年起的新样本），不得用旧 test 段继续评估。

**设定依据**（引用 QS-CAL-001 §二，不重抄推导）：DSR≥0.5 的门槛观测夏普随 N_eff 增长（N_eff=3→0.60，6→0.92，10→1.11，20→1.34）。若不设预算，研究者可以通过多跑试验让任何 DSR 线失效。N_eff 预算封顶 6 是在「自由度耗尽」与「项目可行性」之间取得平衡的 Monte Carlo 定标结果。

**validation 查看预算：≤ 5 轮**（保留 v1.2 规则）  
- "一轮"定义：任何导致 `factor_registry` 内容变更（新增/修改/删除因子或变体）并重新运行至少阶段一的行为，计为一轮。  
- 超 5 轮强制启用新时间段。

### 5.2 强制试验登记表

每次触碰 `test` 段前，**必须**在 `test_eval_registry` 表中提前登记，登记完成后方可运行。禁止先运行后补记。

#### 表结构

| 字段 | 类型 | 说明 |
|------|------|------|
| `eval_id` | TEXT (PK) | 唯一评估编号，格式 `TEST-{YYYY}-{NN}`，如 `TEST-2026-01` |
| `eval_date` | DATE | 登记日期（非运行日期） |
| `budget_seq` | INTEGER | 本次消耗序号（1–6），超出 6 自动 REJECT |
| `strategy_snapshot_id` | TEXT | 对应 `factor_registry` 快照 hash（git commit） |
| `factor_set` | TEXT | 上线因子列表（JSON），与预注册一致 |
| `model_type` | TEXT | 线性/LightGBM/混合 |
| `param_hash` | TEXT | 模型参数 hash（防事后替换） |
| `n_eff_used` | FLOAT | 本次喂入 DSR 的 N_eff 值 |
| `data_cut_id` | TEXT | QS-C03 数据版本 |
| `purpose` | TEXT | 本次评估目的描述（必填，≥ 20 字） |
| `approved_by` | TEXT | 自审声明（"研究者自审，独立日期确认"）|
| `run_timestamp` | TIMESTAMP | 实际运行时间戳（运行后填写） |
| `verdict` | TEXT | A1/A2/PASS（运行后填写） |
| `notes` | TEXT | 附注 |

#### 登记流程

```
【前置步骤】
1. 确认 budget_seq = 当前已消耗次数 + 1，如 > 6 则停止，不得运行 test
2. 确认 factor_registry 已冻结（git commit hash 已固定，无 dirty flag）
3. 填写 test_eval_registry 全部必填字段（eval_id, eval_date, budget_seq,
   strategy_snapshot_id, factor_set, model_type, param_hash, n_eff_used,
   data_cut_id, purpose, approved_by）
4. 将登记表提交版本控制（git commit），确保可审计
5. 代码中加双重确认注释（"# TEST EVAL AUTHORIZED: {eval_id}"）

【运行步骤】
6. 运行 test 段评估，同时写入运行日志（含 eval_id 引用）
7. 运行完成后立即在 test_eval_registry 中填写 run_timestamp 和 verdict
8. 标记 test 段"已评估"状态（不可再次用同数据重跑不同配置）

【事后步骤】
9. 将结果（含 A1/A2/B 闸门判定）写入 research_ledger
10. 如触发 A2 弱否决，启动降级路径（见 §6.2）
11. 更新项目状态机，记录 N_eff 预算剩余次数
```

#### 预算状态机

```
预算状态: [剩余次数 = 6 - budget_seq_max]
- 剩余 4–6: 绿灯，正常研究路径
- 剩余 2–3: 黄灯，每次评估须额外写明"为何此时消耗预算而非等待更多 validation 轮次"
- 剩余 1:   红灯，最后一次机会，须人工复核研究协议合规性
- 剩余 0:   锁定，只能用 2026 年后新数据段
```

---

## 六、上线判定（B为主 + A为辅闸门体系）

> **本章为 v2.0 核心重写**。所有数字严格来自 QS-CAL-001，不重抄推导，仅引用结论。  
> 废除条款记录：①v1.2 四子段递增 DSR（0.95/0.975/0.983/0.9875）—— 半年子段 T_eff≈25 周，数学不可达；②v1.3 合并段 DSR≥0.95 作为硬判定 —— Monte Carlo 证明对 SR=0.8 策略误伤率 56.4%，不可承担否决权；③v1.3 子段夏普离散度 max−min≤0.8 —— 26 周年化 SE≈1.42，期望极差≈2.9，测量噪声而非策略质量。以上废除均以 QS-CAL-001 为依据。

### 6.1 闸门体系总览

**设计哲学**（来源：QS-CAL-001 §〇执行摘要）：在 T=104 周的 test 段，没有任何离线检验能可靠区分 SR=0 与 SR=0.8 的策略。真正的统计控制来自**资金分级**：5万阶段在 25% 硬止损下最大损失被锁定在 1.25 万元，这是用有界可控的学费购买真实行为数据，让 B 类闸门在实盘分布上做最终裁决。

| 闸门 | 定义 | 性质 | 依据 |
|------|------|------|------|
| **A1 硬否决** | test 合并段（2024–2025，约 104 周）扣成本年化夏普 ≤ 0 | 一票否决，不上模拟盘 | QS-CAL-001 §〇 |
| **A2 弱否决** | 合并段 DSR < 0.5（按实际登记 N_eff 计，N_eff 预算封顶 6） | 不否决，触发**降级路径** | QS-CAL-001 §二 |
| **B1 加仓判线（主裁决）** | 模拟盘+5万阶段累计≥26周：实测周度 rank-IC 均值 > 研究 IC − 1.0×SE；加仓前至少 13 周来自真实 5万实盘 | 通过才允许 5万→20万加仓 | QS-CAL-001 §三 |
| **B2 维持判线** | 滚动 26 周 IC 均值 > 研究 IC − 1.645×SE 且 > 0 | 跌破触发降仓复盘 | QS-CAL-001 §三 |
| **B3 工程判线** | 成本偏差 ≤ +30%（实测/建模）、对账连续 4 周零差错、风控触发行为与状态机一致 | 与 B1 同为加仓必要条件 | QS-CAL-001 §三 |

### 6.2 A 闸门详述

#### 6.2.1 A1 硬否决

- **判定时点**：test 一次性综合评估时（消耗 N_eff 预算 1 次，须先登记 §5.2 试验登记表）
- **计算口径**：test 合并段（2024-01-01 ~ 2025-12-31，约 104 周）扣成本年化夏普；成本引用 `cost_model_id`（QS-C01 §8）
- **触发结果**：不上模拟盘，项目进入 FAIL_RESEARCH 状态；可重新开研究轮次，但须换 2026 年后新数据段
- **逻辑依据**（来源：QS-CAL-001 §二.2.3）：真实 SR=0.8 策略在 104 周上观测为负的概率约 13%，这是可接受的最低误伤率；连正收益都拿不出的候选没有理由消耗实盘学费

#### 6.2.2 A2 弱否决与降级路径

- **判定口径**：合并段 DSR（全 test 段 T_eff ≈ 100 周，计算见 §3.5），SR0 按实际登记 N_eff 计（Harvey-Liu 口径，见 §3.5(b)），**N_eff 预算封顶 6**
- **DSR 分界线：0.5**（来源：QS-CAL-001 §二.2.3）  
  - DSR≥0.5（观测夏普超过 6 次试验零假设期望最大值 0.92）：标准路径
  - DSR<0.5：降级路径（不否决项目，但起步条件收严）

**标准路径 vs 降级路径**：

| 参数 | 标准路径（DSR≥0.5） | 降级路径（DSR<0.5，A2 触发） |
|------|------|------|
| 实盘起步资金 | 5万 | 2.5万 |
| B1 行为观察窗 | 模拟盘+实盘累计≥26周（≥13周真实实盘） | 全实盘≥26周（不计模拟盘） |
| 加仓最早时点 | M9（物理下限） | M9 + 13周延后 |
| 最大学费 | 5万×25%=1.25万 | 2.5万×25%=0.625万 |

**A2 触发不意味着项目终止**——它是"用更长观察期换取更充分真实数据"的明码标价。

#### 6.2.3 test 综合判定执行流程

```
【准备】
1. 登记 §5.2 试验登记表（eval_id = TEST-{YYYY}-{NN}，budget_seq 递增）
2. 确认 factor_registry 已冻结（git commit，无 dirty flag）

【执行】
3. 一次性跑完全 test 段（2024-01-01 ~ 2025-12-31）
4. 同时输出四子段子报告（2024H1/H2/2025H1/H2）作为补充参考（不做硬阈值）
5. 计算：
   a. 合并段扣成本年化夏普 → A1 判定（≤0 则否决）
   b. 合并段 DSR（T_eff≈100周，N_eff按登记值）→ A2 判定（<0.5则降级）
6. 标记 test 段"已评估/已烧毁"

【子段参考报告（非硬判定）】
- 四子段夏普全部 > 0（方向一致性参考）
- 四子段年化收益均 > 0（参考）
- 四子段夏普均值 ≥ 0.8（与现实档验收标准对齐，参考）
注：以上子段指标为参考报告，不作否决条件，仅供研究者了解策略分布。
    原 v1.3 子段离散度 max−min≤0.8 已废除（Monte Carlo 证明测量噪声，见版本演进表）。

【登记】
7. 将结果写入 test_eval_registry（verdict 字段）和 research_ledger
8. 若 A1 通过、A2 判定完毕，进入模拟盘阶段（根据 A2 结果选标准/降级路径）
```

### 6.3 B 闸门详述（主裁决体系）

#### 6.3.1 B1 加仓判线（主裁决）

**判定条件**（来源：QS-CAL-001 §三，数字不得改动）：

\[
\bar{IC}_{realized,26w} > IC_{research} - 1.0 \times SE_{research}
\]

其中：
- \(\bar{IC}_{realized,26w}\)：实盘（模拟盘+5万真实实盘）累计≥26周的实测周度 rank-IC 均值
- \(IC_{research}\)：研究 walk-forward IC 序列均值（预注册值）
- \(SE_{research}\)：研究期 IC 序列标准误（= IC_std / √T_research）
- **加仓前至少 13 周必须来自 5万真实实盘**（成本与成交行为只有实盘可信，QS-CAL-001 §四）

**功效参考**（来源：QS-CAL-001 §三.2，26 周窗，SE(IC 均值)=0.0196）：

| 真实状态 | 真 IC | B1 通过率（> IC−1.0×SE） |
|----------|-------|-------------------------|
| 完好（IC 如研究期） | 0.030 | 84.1% |
| 衰减一半 | 0.015 | 59.4% |
| 失效 | 0.000 | 30.1% |
| 反向 | −0.010 | 14.8% |

> B 类闸门也不是万能的：26 周窗对"衰减一半"策略仍有 59% 放行。因此 B2 维持判线必须终身滚动运行（QS-CAL-001 §三.3）。

#### 6.3.2 B2 维持判线（终身滚动）

**判定条件**（终身强制运行，不随加仓完成而终止）：

\[
\bar{IC}_{realized,26w}^{rolling} > IC_{research} - 1.645 \times SE_{research} \quad \text{且} \quad \bar{IC}_{realized,26w}^{rolling} > 0
\]

- **触发动作**：任一条件跌破 → 降仓复盘（先卖卫星仓），暂停加仓，进入人工复盘流程
- **恢复条件**：连续 4 周重新达标后可申请恢复（须走状态机 QS-C04 DEGRADED→ACTIVE 流程）
- **随时间累积效果**（QS-CAL-001 §三.3）：B2 滚动监控等效于长窗检验，失效策略在 1 年内被捕获概率 >85%

#### 6.3.3 B3 工程判线（与 B1 同为加仓必要条件）

B3 不依赖统计功效，为确定性验收（来源：QS-CAL-001 §三.3）：

| 检查项 | 阈值 | 判定方式 |
|--------|------|----------|
| 成本偏差 | ≤ +30%（实测成本 / 建模成本） | 对账记录逐笔核实 |
| 对账连续零差错 | 连续 4 周 | 对账系统自动校验 |
| 风控触发行为 | 与状态机（QS-C04）一致 | 风控日志 × 状态机日志交叉比对 |

**加仓决策判定逻辑**：

```
IF A1_passed AND B1_passed AND B3_passed:
    THEN 允许 5万→20万 加仓
    NOTE: A2 结果影响的是 B1 的观察窗长度，不直接否决加仓

IF B2_triggered:
    THEN 降仓复盘，暂停加仓
    THEN 进入 QS-C04 DEGRADED 状态
```

### 6.4 验收双档标准（来源：QS-C01 §4）

| 档位 | 年化收益 | 最大回撤 | 夏普比率 | Calmar 比率 |
|------|----------|----------|----------|-------------|
| **现实档**（上实盘硬线） | 10–15% | <25% | >0.8 | >0.5 |
| **理想档** | 15–22% | <20% | >1.0 | >0.8 |
| **线性基线** | — | — | ≥0.6（进入研究下一步） | — |

- **上实盘须夏普 > 0.8**；线性 0.6–0.8 须靠 LightGBM 增量补足到 0.8
- 三级回撤（来源：QS-C01 §4）：20% 预警→降仓 50%（先卖卫星）；25% 硬止损→全清仓+冻结+人工复盘

### 6.5 失败判定伪代码（整合 test 判定 + WATCH 门禁）

```python
def research_round_verdict(stage_results, trial_log) -> str:
    """返回 'PASS_PROD'/'PASS_LINEAR_ONLY'/'FAIL_RESEARCH'/'WATCH'/'VOID'

    VOID(作废,重置编号): 任一铁律违反(test提前查看/未预注册/N低报被审计/逐段补考)

    WATCH(过拟合预警): 触发时机=阶段一通过后立即检查。
      - 基准: fold-to-fold IC 衰减(非 train-vs-val):
        相邻 walk-forward fold 间 OOS IC 衰减 > 50%, 或首个 OOS fold IC < 末 fold 的 30%
      - WATCH 触发时，先调用 QS-C01 §15 Barra 风格暴露监控对衰减 fold 做归因(v1.3 S2):
        * 风格归因贡献 > 50% → 标注 WATCH_STYLE_CYCLE，转追加预注册稳健性测试后重判
        * 残差衰减为主 → 维持 WATCH 处置（降级/shadow/追加测试）
      - WATCH 不得人工口头豁免直接 PASS_PROD

    FAIL_RESEARCH: 阶段三通过<4个 或 冗余检测后独立因子<6 或
                   线性不满足§5.1 或 A1 硬否决触发

    PASS_LINEAR_ONLY: 线性满足§5.1且夏普>=0.8, 但 LGB 未通过§5.2
    PASS_PROD: 线性达标 + (夏普>=0.8 或 LGB补足) + 择时A/B + 中性化A/B
               + 冗余检测通过 + FMB 已留档 + A1 通过
    """
```

### 6.6 失败退出与跨轮次治理

- **FAIL_RESEARCH 后**：允许开新轮次，新增因子重置 M_registered 与 N_trials_raw，不得复用已看过的 validation 反复挑选至通过
- **validation 查看预算**：单一时间段 validation **最多 5 轮**（见 §5.1）
- **test 再评估**：须换 2026 年以后新时间段，且必须重新登记试验登记表

---

## 七、显著背离定义（trigger_ic_audit 唯一口径）

> **本节为 v2.0 唯一权威定义**。其他文档（QS-C05 模拟盘手册、QS-C01 设计文档 §13）引用时只引编号，不得自定义替代口径。  
> **废除旧定义**：「连续 8 周 IC < 中位数 30%」定义已正式废除。该定义无统计基础，在小样本下几乎不可激活或过度敏感，与项目实际 IC 观测序列长度不兼容。（来源：QS-C01 §3 唯一术语裁决）

### 7.1 统一背离判定公式（v1.3 C4/C5 修复）

\[
\text{背离}(trigger\_ic\_audit) \iff realized\_ic < CI_{lower}^{research}(\alpha=0.05) \;\;\textbf{且}\;\; n_{fills} \ge N_{min}
\]

其中：
- **`CI_lower^research`**：研究 walk-forward IC 序列的 **block bootstrap 5% 分位下界**（块长≈H，复用 §3.1 bootstrap）——即滚动 26 周实测 IC 均值落在研究期 IC bootstrap 90% CI 下界之外即触发 IC 审计
- **`realized_ic`**：实盘/模拟盘累计成交推导的滚动 26 周 IC（QS-C04 状态机 §7.1 反哺 + QS-C05 模拟盘 realized_ic）
- **`N_min`**（最小样本门槛，与加仓周期对齐，解决小样本无法激活）：
  - 回挂审计触发：`n_fills ≥ 50`
  - 加仓门槛判定：`n_fills ≥ 50` 且 `持有周期 ≥ N 周（N=8–12，见 QS-C05 §七）`

### 7.2 5万小仓位阶段处理

**5万阶段（约5只持仓）样本不足 50 成交时**：  
- 背离统计项标注 `INSUFFICIENT_NOT_BLOCKING`，不阻塞加仓决策
- 记录原因：样本不足，B1/B3 工程门槛仍为必要条件
- 一旦 n_fills ≥ 50 自动激活统计背离判定

### 7.3 触发动作

```python
def check_ic_audit_trigger(realized_ic_series, research_ic_series,
                            n_fills, N_min=50, alpha=0.05, block_size=5):
    """
    返回: ('TRIGGERED'|'INSUFFICIENT'|'NORMAL', reason_str)

    TRIGGERED:  满足背离条件 → 触发事后泄漏审计(§7.4) + 若在加仓场景则暂停加仓
    INSUFFICIENT: n_fills < N_min → 标注 INSUFFICIENT_NOT_BLOCKING, 继续记录
    NORMAL:     realized_ic >= CI_lower → 正常，继续监控
    """
    if n_fills < N_min:
        return ('INSUFFICIENT', f'n_fills={n_fills} < N_min={N_min}')
    # block bootstrap CI
    ci_lower = block_bootstrap_ci(research_ic_series,
                                  alpha=alpha, block_size=block_size,
                                  quantile='lower')
    realized_mean = realized_ic_series.rolling(26).mean().iloc[-1]
    if realized_mean < ci_lower:
        return ('TRIGGERED', f'realized={realized_mean:.4f} < CI_lower={ci_lower:.4f}')
    return ('NORMAL', f'realized={realized_mean:.4f} >= CI_lower={ci_lower:.4f}')
```

### 7.4 事后泄漏审计（实盘回挂条款）

因子上线后，实盘真实 IC 显著低于研究 walk-forward IC 时（满足 §7.1 判定条件），触发该研究轮次事后泄漏审计：

1. 排查该研究轮次是否存在未发现的数据泄漏（检查 dirty flag 历史、data_cut_id 一致性）
2. 对比实盘成本与建模成本（检查 B3 成本偏差是否超限）
3. 复核 research_ledger 中 N_trials_raw 记录是否完整
4. 如发现泄漏 → 该轮次标记 VOID_RETROACTIVE，进入重新研究流程
5. 如无泄漏 → 记录为策略自然衰减，进入 B2 降仓复盘流程

---

## 八、模型验收标准（递进式，口径已与 QS-C01 统一）

### 8.1 线性基线放行（须全部满足，组合按预注册 ICIR 加权构建）

| 指标 | 阈值 |
|------|------|
| 样本外年化收益 | > 沪深300 + 2% |
| 样本外夏普 | > 0.6（进入研究下一步） |
| 样本外最大回撤 | < 25% |
| 滚动夏普 | 近 12 月不连续 3 月为负 |
| 参数敏感性（模型级+因子级） | ±20% 下指标变化 < 30%；**因子 lookback ±20% 下 IC 符号不得翻转** |

### 8.2 上实盘硬线 + LightGBM 增量

- **上实盘须夏普 > 0.8**；线性 0.6–0.8 须靠 LightGBM 增量补足到 0.8
- **LightGBM 增量验收**（来源：QS-C01 §4，ML 预算 = LightGBM 因子合成 + LLM 舆情）：夏普>线性+0.1、Calmar>线性+0.1、特征重要性 max<40%、参数±20%下 Top10 重合>70%、训练/验证 gap<15%
- **LLM 舆情情绪因子上线门槛**（QS-C01 §1）：IC>0.03、分层单调、正交化后仍显著、扣成本为正，四项须全部满足
- **回退路径**：线性通过但 LGB 不通过 → 直接线性上线

### 8.3 择时强制 A/B

- 沪深300 vs 200日MA 择时开关强制做 A/B：开关 on vs off 夏普差需 > 0 且回撤改善
- 三档仓位（来源：QS-C01 §4）：90–100% / 60% / 30%，N 日确认延迟（N 须预注册）
- 择时 A/B 结果须写入 research_ledger

### 8.4 行业市值中性化 A/B

- 中性化 on vs off 比较，控制 Barra 风格因子暴露
- IC 改善且换手率无显著上升方可认定中性化有效

### 8.5 因子冗余检测（阻断"伪8因子"上线）

- Spearman 相关矩阵 → 层次聚类；r>0.7 **且 p<0.01** 的簇只保留 ICIR 最高者
- 报告有效独立维度（即 §3.5 N_eff）；独立因子 < 6 个 → 降级 FAIL_RESEARCH

### 8.6 Fama-MacBeth 留档（P2 非阻断）

PASS_PROD 前须运行 FMB 回归并留档，报告各因子控制其他因子后的边际 t 值；不作为 FAIL 硬条件，但用于识别"伪独立因子"。

---

## 九、可复现与冻结管理

- 每次回测写 `research_ledger`（append-only，禁人工补记）：git commit hash + **data_cut_id（QS-C03）** + **dirty flag** + 随机种子 + 依赖锁 + 参数 + trial id + 结果 + `n_trials_raw` + `n_eff_used`
- **dirty flag 定义**：以下任一为真则 `dirty=true`——`git status --porcelain` 非空（有未提交变更）/ 实际参数与预注册不符 / 数据版本（data_cut_id）与声明不一致
- **实盘回挂条款**：因子上线后，实盘真实 IC 显著低于研究 walk-forward IC 时（满足 §7.1 判定），触发该研究轮次的事后审计（排查未发现的泄漏），反向校验协议有效性
- 本协议冻结后变更须版本号递增并记录变更理由与日期
- test 段访问须双重确认（代码注释 + 运行日志留痕），运行后标记全段"已烧毁"
- **pytest 回归场景数**（来源：QS-C01 §3）：**13 场景**，与 QS-C03 点时契约 v2.0 一致，不得少于 13

---

## 十、组合构成与风控参数（引用 QS-C01）

本节不重抄 QS-C01 §4 内容，仅列引用索引，供研究协议内部交叉核对。

| 参数 | 值 | 来源 |
|------|-----|------|
| 核心多因子权重 | 75–80%，周度调仓，Top10–15只 | QS-C01 §4 |
| 趋势卫星权重 | 20–25%，唐奇安20突破+2×ATR跟踪止损，持仓2–10天 | QS-C01 §4 |
| 大盘择时总开关 | 沪深300 vs 200日MA，三档 90–100%/60%/30%，N日确认延迟 | QS-C01 §4 |
| 单票上限 | ≤8% | QS-C01 §4 |
| 单行业上限 | ≤30% | QS-C01 §4 |
| 日均成交额门槛 | ≥5000万 | QS-C01 §4 |
| 剔除条件 | ST/停牌/上市<250日 | QS-C01 §4 |
| 三级回撤阈值 | 20%预警→降仓50%；25%硬止损→全清仓+冻结 | QS-C01 §4 |
| 本金保全底线 | 25%硬止损，不可妥协 | QS-C01 §1 |

**状态机**：15 个运行态（含 HALT/BREAK_GLASS），唯一口径见 QS-C04；本文档不重列状态枚举。

---

## 十一、三个月上线路线图（引用 QS-C01）

本节不重抄 QS-C01 §7 内容，仅列关键研究协议里程碑节点：

| 阶段 | 研究协议相关节点 |
|------|----------------|
| M1（数据基建） | AKShare/Tushare/BaoStock 三源管道上线；13 场景 pytest 全绿；visible_at 重放验证通过 |
| M2（研究+执行） | 因子库(15–20候选)+线性基线(夏普≥0.6)+walk-forward；**test 段 A1/A2 评估（消耗 N_eff 预算第 1 次，须先登记 §5.2）** |
| M3（模拟盘上线） | 模拟盘行为数据开始累积（B1 观察窗起计）；IC 审计 trigger 监控上线 |
| M9+（加仓决策） | B1 + B3 同时满足后，5万→20万加仓（最早 M9，26 周物理下限） |

> M3 末"上线可用"= 模拟盘全链路上线，开始累积 B 类行为数据。真实 5万实盘自 M4 起（合规报备完成+模拟盘 4 周零差错后）。（来源：QS-C01 §7）

---

## 十二、研究闸门放行自检清单（进入实盘闸门前必过）

> v2.0 完整清单，整合 v1.2、v1.3 增量项及 v2.0 新增项。

**研究阶段**：
- [ ] 所有因子/参数/模型/合成权重法已预注册，M_registered(=atomic 计数) 与 N_trials_raw 已冻结
- [ ] 数据全程经 QS-C03 点时接口（daily_bar_asof + data_cut_id），无直连原始表
- [ ] 标签已闭合 T+1 执行；signal_universe_t 与 execution_filter_t1 已分层；成本用统一 cost_model_id
- [ ] 入场价 VWAP 已做"VWAP vs 开盘价"敏感性分析（差>5bp 告警）
- [ ] purged/embargo walk-forward 已施加（交易日口径），仅在 2016–2023 内滚动
- [ ] 三阶段用 M_registered（atomic 计数，非幸存者）作 BH/Bonferroni 分母
- [ ] t 统计量单侧、方向已预注册；T<200 用 block bootstrap；符号稳定率已校正自相关
- [ ] 阶段二用 ICIR 复合 + 扣费多空收益 + 换手率<50%
- [ ] DSR 公式：SR0 用 Harvey-Liu；N_eff 覆盖全 atomic 维度且取 bootstrap 下界；T_eff 用周频并 fold 间折减
- [ ] （v1.3 C2）DSR 的 N 只认 research_ledger atomic_test_id 计数；variant 仅作 preprocessing 维度，未独立乘入；防双计断言通过
- [ ] 因子冗余检测通过（r>0.7 且 p<0.01），独立因子≥6；FMB 已留档
- [ ] H∈{3,5,10} 稳健性扫描已做且计入 N_trials_raw 与 N_eff 矩阵
- [ ] WATCH 检查已在阶段一后执行（fold-to-fold 基准）；如触发先做 Barra 风格归因（v1.3 S2）；如触发未口头豁免
- [ ] 最终上线因子 6–8 个，verdict ∈ {PASS_PROD, PASS_LINEAR_ONLY}

**N_eff 预算与 test 判定**：
- [ ] （v2.0 §5）test 评估已登记 §5.2 试验登记表（eval_id + budget_seq + strategy_snapshot_id + n_eff_used），登记先于运行
- [ ] 全生命周期 test 评估次数 ≤ 6（budget_seq ≤ 6），超出则不运行
- [ ] test 段一次性综合判定（全段，不逐段补考），全程未被研发期查看
- [ ] A1 硬否决：合并段扣成本年化夏普 > 0（数字来自 QS-CAL-001）
- [ ] A2 弱否决：合并段 DSR 计算完毕，判定是否触发降级路径（DSR < 0.5）
- [ ] test 段已标记"已烧毁"，research_ledger 写入本次评估记录

**背离监控**：
- [ ] （v1.3 C4/C5，v2.0 §7）显著背离用统一定义（research IC bootstrap CI 下界 + N_min=50）；旧定义「连续8周<中位数30%」已废除
- [ ] 5万阶段不足样本时标注 INSUFFICIENT_NOT_BLOCKING
- [ ] IC 审计触发监控已上线（trigger_ic_audit 函数部署到实盘监控流水线）

**工程合规**：
- [ ] 程序化交易合规：开户勾选程序化交易、报备材料准备完毕（来源：QS-C01 §4）
- [ ] B3 工程判线已配置：成本偏差监控 + 对账系统 + 风控行为日志
- [ ] 模拟盘手册 QS-C05 已同步 v2.0 背离定义（消费本文档 §7）

---

## 附录 A · v1.2 变更日志（相对 v1.1，完整保留）

| 编号 | 优先级 | 章节 | v1.1 残留缺陷 | v1.2 修复 | 审查来源 |
|------|--------|------|-----------|-----------|---------| 
| D1 | P0 | §6.2/铁律2 | test 四段逐段补考致 FWER 膨胀至 ~18.5% | 改一次性综合判定 + 递增阈值(0.05/k) + 均值兜底 | R1-C1, R3-P0 |
| D2 | P0 | §3.5(c)/§4/铁律4 | N_eff 仅折减因子相关，漏算 lookback/horizon/模型/参数 | N_eff 覆盖全部 atomic_test 收益矩阵 + 地板约束 | R2, R3-P0 |
| D3 | P1 | §3.2/§4/铁律3 | M_registered 未绑定 atomic_test_id，可按因子名低报 | 强制 M_registered = distinct atomic_test_id 计数 | R3-P1 |
| D4 | P1 | §3.5(d) | T_eff 误用日频，8年从~400周膨胀到~1900日 | 统一周频组合观察数 + fold 间自相关折减 + 上界 | R1-C4, R3-P1 |
| D5 | P1 | §2.4 | 次日一字板写入研究 universe，含未来信息 | 拆 signal_universe_t / execution_filter_t1 两层 | R3-P1 |
| D6 | P1 | §2.6 | 标签成本只列三项，与设计文档成本不一致 | 统一引用 cost_model_id（含最低5元/过户费/取整） | R3-P1 |
| D7 | P1 | §2.7 | 因子合成权重法未预注册，可事后调权重凑夏普 | ICIR 加权写入 factor_registry 预注册固定口径 | R2 |
| D8 | P1 | §6 | WATCH 用 train IC 作基准不当，几乎全因子误触发 | 改 fold-to-fold IC 衰减基准 + 明确触发时机 | R1-7, R2 |
| D9 | P1 | §6 | WATCH 可人工口头豁免直接 PASS_PROD，门禁偏软 | 只能降级/shadow/追加测试，不得直接放行 | R3-P2 |
| D10 | P2 | §3.5(c) | N_eff PCA 点估计短样本不稳，且双法分歧无裁决 | bootstrap CI 取下界 + 双法分歧取更保守者 | R1-C2, R2 |
| D11 | P2 | §2.5 | VWAP 入场价系统性乐观 | 主口径保留 + 强制开盘价敏感性分析（差>5bp 告警） | R1-C5, R2 |
| D12 | P2 | §3.3 | 阶段二未考虑换手率，高换手因子扣费后失效 | 增加 Top 组周度换手率<50% 条件 | R1-opt1 |
| D13 | P2 | §3.2 | BH-FDR 在 t>3 下冗余但文档未承认 | 加坦白说明（t>3 主导，BH 作安全网与可扩展性） | R1-C3 |
| D14 | P3 | §3.1/§3.3 | IC 符号稳定率未校正自相关聚集 | 用 block bootstrap 经验分布评估符号稳定率 | R1-6 |
| D15 | P3 | §5.1 | 参数敏感性仅模型级 | 下沉到因子级（lookback±20% 符号不翻转） | R1-opt5 |
| D16 | P3 | §5.6 | FMB 仅注明未强制 | 列为 PASS_PROD 前置留档项（非 FAIL 硬条件） | R1-opt4, R2 |
| D17 | P3 | §6.1/§7 | "一轮"定义、dirty flag、单调性、聚类阈值显著性模糊 | 各补一句明确定义 | R1-8/9/10/11 |
| D18 | P3 | §7 | 缺研究 IC 与实盘 IC 一致性回挂 | 新增实盘回挂事后审计条款 | R2 |

**未采纳的审查建议（及理由）**：
- 贝叶斯收缩 IC 估计：对单人 20h/周项目增量复杂度偏高，N_eff 已部分缓解相关因子问题，列长期 backlog。
- 市场状态条件 IC 强制：有价值但 validation 仅 2 年、牛熊样本不足以稳健估计条件 IC，列为可选监控而非闸门。
- Jonckheere-Terpstra 趋势检验：单人项目用"4 不等式至少 3 成立"已足够，全检验过重。
- 偏相关替代 r>0.7：20 因子×100 观测下偏相关自身估计不稳，改为 r>0.7 且 p<0.01。

---

## 附录 B · v1.3 变更日志（相对 v1.2，完整保留）

> 触发文件：《二次筛查审查报告 v1.0》P0/P1 项。  
> 说明：原《二次筛查审查报告 v1.0》文件未随本项目文档一同提供，以下要点据 v1.3 正文及附录 A 上下文重建。**[基线缺失，据增量重建]**

**报告定位**（[基线缺失，据增量重建]）：该报告系对 v1.2 冻结版的专项二次审查，识别了 v1.2 中剩余的 P0/P1 级缺陷，触发 v1.3 受控解冻修订。主要发现：①v1.2 test 子段 DSR 递增阈值与小样本 T_eff 数学不兼容（S1，P0）；②DSR N 计数与点时契约口径漂移（C2，P0）；③WATCH 误报跨制度风格切换（S2，P1）；④显著背离无统计定义小样本无法激活（C4/C5，P1）。

| 编号 | 级别 | 章节 | v1.2 缺陷 | v1.3 修复 | 来源 |
|------|------|------|-----------|-----------|------|
| S1 | P0 | §6.2/§3.5 | test 子段 DSR≥0.9875 与小样本 T_eff 数学不兼容、与现实档 0.8 冲突 | 全 test 合并段算一次 DSR≥0.95；子段降弱判定（符号/均值/离散度） | 二次筛查报告 v1.0 R2 |
| C2 | P0 | §3.5 | DSR 的 N 计数与点时契约口径漂移（variant 独立乘入）不可复现 | variant 归为 preprocessing 维度取值；N 只认 atomic_test_id；防双计断言 | 二次筛查报告 v1.0 R2 |
| S2 | P1 | §6 | WATCH 误报跨制度风格切换为过拟合 | 触发先做 Barra 风格归因再裁决 | 二次筛查报告 v1.0 R2 |
| C4/C5 | P1 | §7 | 显著背离无统计定义、小样本无法激活 | 统一 bootstrap CI 下界 + N_min 定义；5万阶段非阻塞标注 | 二次筛查报告 v1.0 R2 |

---

## 附录 C · v2.0 变更日志（相对 v1.3）

| 编号 | 级别 | 章节 | v1.3 状态 | v2.0 修订 | 依据 |
|------|------|------|-----------|-----------|------|
| V1 | P0 | §6 整体 | B为辅+A为主，DSR 承担上线否决权 | 整体重写为 B为主+A为辅，废除 DSR 硬否决，重建 A1/A2/B1/B2/B3 体系 | QS-CAL-001 Monte Carlo |
| V2 | P0 | §6.2 | v1.2 四子段递增 DSR（0.95/0.975/0.983/0.9875） | 废除（Monte Carlo 证明不可达，真实 SR=0.8 通过率 0.2%–3.5%） | QS-CAL-001 §一 |
| V3 | P0 | §6.2 | v1.3 合并段 DSR≥0.95 作为硬判定 | 降格为参考指标；A2 改用 DSR<0.5 触发降级（不否决） | QS-CAL-001 §二 |
| V4 | P0 | §6.2.2 | v1.3 子段离散度 max−min≤0.8 | 废除（26周 SE≈1.42，期望极差≈2.9，测量噪声） | QS-CAL-001 §一 |
| V5 | P0 | §5（新增） | 无 N_eff 预算专章 | 新增 §5 N_eff 试验预算体系（test ≤6 次 + 试验登记表） | QS-CAL-001 §二.2.3 |
| V6 | P0 | §7 | v1.3 background 定义（含旧「连续8周<中位数30%」） | 废除旧定义，v2.0 §7 确立为唯一权威口径（bootstrap CI 下界） | QS-C01 §3 |
| V7 | P1 | §3.4 | DSR≥0.95 作为 stage3 硬通过阈值 | 降格为研究阶段质量参考，不作上线硬否决 | QS-CAL-001 §二 |
| V8 | P1 | §6.3 | B 类判线分散在各文档 | 集中到 §6.3，附功效表参考（来源 QS-CAL-001 §三） | QS-CAL-001 §三 |
| V9 | P2 | 全文 | v1.2/v1.3 分散引用成本/组合参数 | 统一改为引用 QS-C01 编号，不重抄数字 | QS-C01 §4 |
| V10 | P2 | 文档头 | 无版本演进表 | 新增版本演进表（v1.0→v1.1→v1.2→v1.3→v2.0） | 合并规范 |

---

*本协议为 QuantSolo 宪法文档第二份（QS-C02），v2.0 全文版（非增量补丁）。冻结后再变更须版本号递增并记录于本文档版本演进表与 research_ledger。下一份宪法文档：QS-C03 点时数据契约 v2.0。*

*文档编号：QS-C02 · 版本：v2.0 · 日期：2026-06-12 · 编制：QuantSolo 研究部*
