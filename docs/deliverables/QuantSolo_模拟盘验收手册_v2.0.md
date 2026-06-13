# QuantSolo —《模拟盘验收手册》v2.0（宪法文档全文版）

---

**文档编号**：QS-C05
**版本**：v2.0（全文版，非增量补丁）
**日期**：2026-06-12
**状态**：正式发布
**与 SSOT 冲突时以基线为准，已校验**

---

## 文档头部索引

| 项目 | 内容 |
|------|------|
| 编号 | QS-C05 |
| 版本 | v2.0 |
| 日期 | 2026-06-12 |
| 上游依赖 | QS-C02《研究协议 v2.0》、QS-C03《点时数据契约 v2.0》、QS-C04《执行与风控状态机 v1.3》 |
| 下游依赖 | QS-E05《执行行动指导手册》（运营参考）、QS-E06《项目实施计划》（里程碑对齐） |
| 闸门数字来源 | QS-CAL-001《统计闸门校准报告 v1.0》（数字不得改动） |
| 背离定义来源 | QS-C02 §七（bootstrap CI 口径，本文档只引用编号，不重抄） |
| 状态名权威来源 | QS-C04 §一（15 个运行态唯一口径） |
| 对账字段权威来源 | QS-C04 §八（order_remark = client_order_id） |

---

## 版本演进表

| 版本 | 日期 | 核心变更 |
|------|------|----------|
| v1.0 | 2026-05 | 初稿，基础验收框架 |
| v1.1 | 2026-05 | 补充异常演练、反哺链路 |
| v1.2 | 2026-06 | 三态 verdict（RESET/CONTINUE_WITH_FLAG/PASS）；策略逻辑改动定义收窄；真实券商观察期最小样本量+前置；T1 模糊匹配唯一性防线；T2/T5 重复次数；pytest 套件清单；压力注入硬门槛；演练 ledger 隔离；合规监管合计口径；cost_model_id 断言；ic_audit mock 验证；分阶段门禁 A/B/C；冻结联调候选版 |
| **v2.0** | **2026-06-12** | **版本锁定更新为点时契约 v2.0、研究协议 v2.0、状态机 v1.3（QS-C02/C03/C04）；背离定义改用 QS-C02 §七 bootstrap CI 口径（删除「连续8周<中位数30%」旧定义）；pytest 场景数统一 13；验收清单与 B1/B2/B3 行为闸门（引用 QS-CAL-001 数字）对齐；模拟盘周数计入 B1 26周观测窗；加仓前至少13周真实5万实盘；模拟盘4周零差错=转5万实盘前置条件；M3末模拟盘全链路上线验收口径；状态名/告警名/对账字段与 QS-C04 v1.3 全面对齐；全文版（非增量补丁）** |

---

## 〇、验收哲学（FAIL-CLOSED · v2.0 精炼）

1. **工程验收 ≠ 策略验收**：模拟盘只证明"不会下错单、不会对账漂移、异常能正确处置"，**不证明策略赚钱**。
2. **模拟盘 4 周零差错 = 转 5 万实盘前置条件**（v2.0 新增）：在 M3 末全链路上线基础上，须达到连续 4 个清洁交易周（每周日终对账零未解释差异）方可转入 5 万真实实盘。此为 QS-CAL-001 B3 工程判线"对账连续4周零差错"条件的模拟盘实现。
3. **M3 末「上线可用」= 模拟盘全链路上线**（对齐 SSOT §7）：数据→信号→风控→模拟执行→对账→告警闭环跑通，开始累积 B 类行为数据。真实 5 万实盘自 M4 起（合规报备完成 + 模拟盘 4 周零差错后）。
4. **加仓看"行为一致"而非短期收益**：实盘 IC/夏普与回测无显著背离（见下条背离定义）+ 对账零错误，才是加仓门槛。
5. **失败判定锚点是"系统行为是否正确"，而非"结果是否有差异"**：
   - **系统行为错误**（重复成交/未知态盲目下单/超卖/越限继续提交）→ 一次都不容忍，**立即 RESET**。
   - **系统行为正确、仅外部环境异常**（如券商超时进 UNKNOWN 但系统正确停单+告警+未盲目下单）→ 进 `CONTINUE_WITH_FLAG`，留痕但不推倒重来。
   - 设计意图：标准不是"无法执行的绝对零容忍"，而是"可执行的客观分级"——避免操作者为凑满天数放宽对账口径，反而腐蚀标准。
6. **文档冻结 + 测试全绿双线合拢**：相关 pytest 回归套件必须全绿（13 场景，清单见 §1.5），作为放行前置。
7. **背离（trigger_ic_audit）唯一定义**：见 QS-C02 §七 bootstrap CI 口径（滚动26周实测IC均值落在研究期IC bootstrap 90% CI 下界之外即触发 IC 审计）。**废除「连续8周<中位数30%」旧定义**，本文档不重抄背离定义，以 QS-C02 为唯一来源。

---

## 一、验收周期与放行条件

### 1.1 三类验收指标

| 类别 | 指标 | 性质 |
|------|------|------|
| 工程硬放行 | 连续 **4 个清洁交易周**（每周对账零未解释差异，FLAG 标记日不计入），作为转 5 万实盘前置条件 | 🔴 必过 |
| 工程硬放行 | QS-C04 §十 T1–T13 必测用例全 PASS（T2≥5次/T5≥3次，见 §3.2） | 🔴 必过 |
| 工程硬放行 | rate_limiter/outbox 三态/签名防重放/连续拒单断路器/EOD/break-glass 扩项/**压力注入** 专项全通过 | 🔴 必过 |
| 工程硬放行 | 每日对账报告自动生成且无未解释差异（position_ledger C 口径，QS-C04 §7.2） | 🔴 必过 |
| 工程硬放行 | 执行→研究数据反哺链路每日生成（schema 校验通过），管道至少完整跑通一次 | 🔴 必过 |
| 工程硬放行 | pytest 回归套件全绿（**13 场景**，清单见 §1.5） | 🔴 必过 |
| 工程硬放行 | 真实券商被动观察期满足最小样本量（见 §六），且前置门禁 A 通过 | 🔴 必过 |
| 工程硬放行 | M3 末全链路上线验收：数据→信号→风控→模拟执行→对账→告警闭环跑通 | 🔴 必过（M3 里程碑） |
| 策略观察 | 实盘 IC/夏普 vs 回测背离度、滑点偏差、回挂审计触发标记、realized_ic 置信区间（≥50 笔成交后） | 🟡 仅观察，不作放行依据 |

> **模拟盘周数计入 B1 观测窗说明**（对齐 QS-CAL-001 §四）：模拟盘阶段的 IC 数据可计入 B1 的 26 周观测窗（模拟盘 IC 用收盘后撮合近似）。但 **5 万→20 万加仓决策前，至少 13 周必须来自真实 5 万实盘**（成本与成交行为只有实盘可信）。模拟盘 IC 序列须与实盘 IC 序列在 execution_ledger 中清晰区分（`is_paper_trade` 字段）。

### 1.2 放行判定（三态 verdict）

```python
def simdry_verdict(clean_weeks: int,          # 清洁交易周数（每周≥5清洁交易日）
                   clean_days: int,            # 总清洁交易日（用于 M3 全链路验收）
                   flag_days: int,
                   anomaly_drills: dict,
                   must_test_T: dict,          # T1..T13
                   special_drills: dict,
                   today_event: dict,
                   env_anomaly_history: list,
                   feedback_pipe_ok: bool,
                   pytest_green: bool,         # 13 场景全绿
                   real_obs_ok: bool,
                   m3_pipeline_ok: bool) -> str:
    """返回 'RESET' / 'CONTINUE_WITH_FLAG' / 'CONTINUE' / 'PASS_ENGINEERING'

    --- 当日事件分类（锚点=系统行为是否符合 QS-C04 状态机迁移规定）---
    RESET 当且仅当命中任一:
      (a) 立即清单: 重复成交 / 未知态盲目下单 / 超卖 / 越限继续提交     # 系统行为错误
      (b) 同类环境异常超频次上限: 60 日内 > 3 次, 或 7 日内 > 2 次       # 偶发累积成系统性
      (c) RCA 根因指向代码逻辑(含边界处理不当)                           # 非环境
      (d) 策略逻辑改动(定义见 §1.3)
    -> RESET: clean_weeks/clean_days/flag_days 清零, 重新计时

    CONTINUE_WITH_FLAG 当且仅当同时满足:
      单次环境异常 + execution_ledger 证明系统按 FAIL-CLOSED 正确处置
      + 当日 RCA 归档(进 verdict.json 不可删除) + 未超频次上限
    -> 天数继续累计, 当日打标记, 该日不计入 clean_days 与 clean_weeks

    PASS_ENGINEERING 须同时满足:
      clean_weeks >= 4                               # 连续4清洁交易周（5万实盘前置）
      flag_days <= 3                                 # 标记日上限
      anomaly_drills 七类全 PASS
      must_test_T(T1..T13) 全 PASS                  # v2.0: 含 T13 三级回撤
      special_drills 全 PASS(含 stress_injection)
      feedback_pipe_ok and pytest_green(13场景) and real_obs_ok
      m3_pipeline_ok                                 # M3 全链路上线验收
    """
```

**阈值定稿（v2.0 冻结）：**
- 同类环境异常频次上限：**60 日内 ≤ 3 次 且 7 日内 ≤ 2 次**（超出即 RESET）
- PASS 允许的标记日上限：**flag_days ≤ 3 天**
- 转 5 万实盘前置条件：**clean_weeks ≥ 4（每周≥5清洁交易日）**

### 1.3 RESET 触发与"策略逻辑改动"操作定义

- **工程 bug 修复 / RCA 根因指向代码** → RESET 重新计时
- **策略逻辑改动 → RESET**；操作定义（收窄，避免误伤研究侧）：仅以下变更触发——
  改动**下单/风控/对账链路代码**，或改变 **target_weight 生成逻辑**（策略规格 hash、因子集合、权重法、universe、cost_model_id、target generation 代码）。
- **不触发 RESET**：research_ledger 的常规 append（每次回测/trial logging）、纯观察/审计记录——这些不碰下单链路。
- **WATCH 标注（不阻塞）**：模拟盘期内 IC 达 QS-C02 §七 WATCH 衰减标准时，verdict.json 标注但不阻塞放行。

### 1.4 差异分类引擎与 RCA 归档规范

- **客观锚点**：当日是否计入清洁天数，由 execution_ledger **行为轨迹**对照 QS-C04 §二迁移矩阵客观判定——系统处置动作（停单/告警/双查归位/未盲目下单）符合规定即"行为正确"，不依赖主观"是否偶发"。
- **RCA 报告（当日生成，前置且不可自证）**：必含 `anomaly_type / root_cause(env|code) / ledger_trace 证据 / fail_closed_verified(bool)`；写入 verdict.json 且不可删除、可复核。
- **演练失败根因分类闭环**：演练失败先分类——代码 bug（修复后 RESET）vs 状态机设计缺陷（回挂 QS-C04 评审，可能触发状态机版本升级），而非一律 RESET。

### 1.5 pytest 放行套件清单（v2.0 · 13 场景）

放行要求以下套件**全绿**（共 **13 个场景**，唯一口径来自 QS-C03 v2.0，参见 SSOT §3）：

| 序号 | 套件 | 来源文档 | 场景数 |
|------|------|---------|--------|
| 1 | 点时契约回归测试（全部 visible_at 重放场景） | QS-C03 | 13 |
| 2 | QS-C04 状态机迁移矩阵测试（全部合法/非法迁移，15态） | QS-C04 | 含于T1-T13 |
| 3 | execution_ledger DDL 约束测试（order_remark/cancel_fill_type/halt_reason/broker唯一/reservation一致） | QS-C04 §七 | — |
| 4 | outbox 三态恢复单元/集成测试 | QS-C04 §4.3 | — |
| 5 | 签名重放防护测试（四类签名+nonce+过期） | QS-C04 §4.4 | — |
| 6 | 对账差异分类测试（active/corp_action/odd_lot/cash_tail/unknown 五分类） | QS-C04 §3.1 | — |

```bash
# 执行命令
pytest tests/contract tests/state_machine tests/ledger \
       tests/outbox tests/signature tests/reconcile -q
# 门槛: 全绿 + 上述模块行覆盖率 >= 各文档约定(无约定者 >= 80%)
# v2.0 说明: "13 场景"特指点时契约 QS-C03 v2.0 定义的 13 个 visible_at 重放场景
#           状态机迁移测试场景数不与此数字混淆
```

> **场景数唯一口径说明**：SSOT §3 规定"pytest 回归场景数：13"，以 QS-C03《点时数据契约 v2.0》的 13 个 visible_at 重放场景为准。本文档提及"pytest 13 场景全绿"特指此 13 个点时场景，状态机迁移测试（T1-T13 用例）是附加项，不包含在此计数中。

### 1.6 分阶段门禁 A/B/C

| 门禁 | 时点 | 通过条件 |
|------|------|----------|
| **门禁 A** | M3 第 2 周末 | 基线 7 类（含 4a/4b）+ T1/T2/T5 核心路径 PASS + order_remark 反查初步验证 → 方可启动真实券商观察期与正式计时 |
| **门禁 B** | M3 第 6 周末 | 专项演练全过（含 T13 三级回撤）+ 反哺链路跑通 + order_remark 命中率≥95% |
| **门禁 C** | M3 第 12 周末 | 4 清洁交易周 + 压力注入 + 真实观察数据评审 + M3 全链路上线验收 → PASS_ENGINEERING |

---

## 二、每日对账报告模板（position_ledger C 口径 · v2.0 更新）

对账真值为 position_ledger 口径 C（成交事件流推导，QS-C04 §7.2），目标仓位只作意图不参与硬对账。

### 2.1 对账三层口径（对齐 QS-C04 §3.1）

- **A**：策略 target_weight（理想，含未执行）——仅意图，不对账
- **B**：risk_guard 裁剪后 order_intent（已下单未确认）——记录不硬阻
- **C**：position_ledger = prior_position + cumulative_fills − corporate_action_delta —— **对账真值**（含 CANCELLED 终态 cancel_fill_type IN ('FULL','PARTIAL') 的成交量）

### 2.2 报告结构（v2.0）

```
============ QuantSolo 模拟盘日终对账报告 ============
日期: 2026-06-08   清洁天数: N   清洁交易周: W/4   标记日: F/3   计时状态: 正常/FLAG
data_cut_id: dc_20260608   cost_model_id: cm_v3   risk_policy_version: rp_v1.3
状态机版本: QS-C04 v1.3（15态）   点时契约版本: QS-C03 v2.0
cost_model_id 一致性: ✓ 与研究协议回测一致(断言通过)
-----------------------------------------------------
[1] 持仓三方对账 (真值=position_ledger C; 已排除 is_drill/is_paper_trade 行)
  标的       理论C  券商   下单  主动差  除权Δ  零股  现金尾差   状态
  000001.SZ  1000   1000  1000   0      0     0    0.00      ✓一致(主动)
  600519.SH  1300   1300  1000   0    +300    0    0.00      ✓一致(送转豁免)
  主动交易差容忍: 0 股 | 除权差: 已豁免(N=1, corporate_action_ledger) | 零股: 单独建账
  现金尾差容忍: 1.00 元 | 实际最大尾差: 0.05 元
  reserved_cash 汇总: x.xx 元 | reserved_qty 汇总: x 股 (冻结/释放平衡: ✓)
[2] 当日订单状态机统计 (覆盖 QS-C04 v1.3 全部 15 个运行态)
  目标链路: TARGET_GEN→RISK_CLIP→ORDER_SIZING→ORDER_INTENT→PRE_FIRE_CHECK→SUBMITTED
  挂单/成交: LIVE→PARTIAL→CANCEL_REQUESTED→FILLED/CANCELLED(NONE/FULL/PARTIAL)→REJECTED
  异常: UNKNOWN→归位/暂停   对账: RECONCILE→IDLE
  cancel_fill_type 分布: NONE=x FULL=x PARTIAL=x
  暂停态分布: halt_reason MANUAL_REVIEW=x REJECT_BREAKER=x BREAK_GLASS=0(必须为0除演练)
  幂等去重命中: x | 重复成交(broker event 级): 0(必须为0)
[3] 合规限速核对 (内部硬约束 + 监管合计口径)
  内部硬约束: 最高每秒申报 x笔/s(硬上限1) | 单日申报 x笔(硬上限200) | 撤单频率峰值 x次/min(上限10)
  监管口径: 申报+撤单合计峰值 x笔/s(沪深高频认定线300) | 单日合计 x笔(认定线2万)
  触限次数: x(含 mock 注入 y) | 真实触限: z | 进入暂停: x
[4] order_remark 对账核对 (QS-C04 §八)
  order_remark反查命中率: x%(B3工程判线需连续4周≥95%) | 误匹配率: 0(必须为0)
  模糊匹配兜底次数: x | 降级暂停(MANUAL_REVIEW)次数: x
[5] 成本与滑点核对 (双口径分列)
  单笔成交均价偏差(工程容忍 0.5%=50bp): 0.03%
  策略层口径敏感性(VWAP vs 开盘, 告警 5bp): x bp
[6] 执行→研究反哺 (每日生成, 无调仓日为空文件但 schema 必过)
  avg_fill_price vs signal_ref_price 偏差已写出: ✓
  realized_ic/cost_attribution/fill_quality schema 校验: ✓ | 累计成交笔数: x(≥50 后报置信区间)
  is_paper_trade 标记: ✓(模拟盘 IC 与真实 IC 分列)
[7] 风控触发记录: 账户回撤 x% | 一级预警(20%降仓50%先卖卫星) | 二级硬止损(25%全清仓续运行) | 子策略止损
[8] 异常与告警: UNKNOWN x | 心跳丢失 x | 数据闸门 PASS(99.3%) | VOIDED告警 x
    当日 RCA: 无 / 有(anomaly_type=..., root_cause=env, fail_closed_verified=✓ → CONTINUE_WITH_FLAG)
[9] B 类闸门观测（仅观察，不作放行依据）
  当前模拟盘周数: W（可计入B1观测窗；真实5万实盘周数: 0，加仓需≥13周真实实盘）
  B1 观测窗进度: W/26周（含模拟盘）
  B2 滚动26周IC均值: x（维持判线: 研究IC−1.645×SE且>0）
[10] 当日结论: ✓ 清洁 / ⚑ FLAG(环境偶发已归档) / ✗ RESET(系统性)
     模拟盘→实盘前置条件: 4清洁交易周 进度 W/4 | 合规报备: [ ]待完成
=====================================================
```

### 2.3 报告字段判定规则（v2.0 关键项）

| 字段 | 通过标准 | 失败动作 |
|------|----------|----------|
| 主动交易持仓差 | = 0 股 | 系统行为错误→RESET；环境异常且行为正确→FLAG+RCA |
| 除权差/零股/现金尾差 | corp_action 解释/单独建账/≤1元 | 无法解释→暂停(MANUAL_REVIEW) |
| reserved 冻结平衡 | 冻结=释放 | 不平衡→排查 reservation 泄漏 |
| 重复成交（broker event 级） | = 0 | 任一重复 → **立即 RESET** |
| 越限继续提交/未知态盲目下单/超卖 | 永不发生 | 任一发生 → **立即 RESET** |
| 单日申报笔数 | ≤ 200 笔（内部硬约束） | 超限→暂停(MANUAL_REVIEW)+RESET |
| order_remark 反查命中率 | ≥ 95%（连续4周均达到方可 B3 通过） | 不达→T1 FAIL 回挂 QS-C04 评审 |
| 单笔成交均价偏差 | ≤ 0.5% | 超出→检查滑点模型 |
| cost_model_id 一致性 | 与研究回测完全一致 | 不一致→断言失败，阻断放行 |
| 反哺 schema | 每日通过；缺失连续 2 日 | 连续缺失→CONTINUE/RESET |
| cancel_fill_type 分布 | FULL/PARTIAL 均正确更新 position_ledger | 错误更新→RESET |

---

## 三、异常演练（七类基线 + 承接 QS-C04 §十 + 专项）

注入式触发，不依赖真实异常自然发生；演练 ledger 行**必须带 `is_drill / test_run_id / scenario_id`，默认排除出 position_ledger C 与清洁天数统计**（防污染对账真值）。注入机制：依赖注入替换券商适配层 `BrokerAdapter → MockBrokerAdapter`，状态机代码本身不感知 mock/real 切换。

### 3.1 七类基线演练（第 4 项区分入场/出场端）

| # | 异常类型 | 注入 | 期望行为 | 通过判定 |
|---|----------|------|----------|----------|
| 1 | QMT 断连 | 心跳中断 3 次 | UNKNOWN→查询失败→暂停(MANUAL_REVIEW)，停新单 | 无任何自动下单 |
| 2 | 部分成交 | 部分回报 | PARTIAL，记已成量，剩余不盲目补单 | 持仓=已成量 |
| 3 | 撤单失败 | 撤单超时 | 查真实剩余→UNKNOWN/归位 | 不盲目重撤/补单 |
| 4a | 入场端一字板 | t+1 一字涨停 | execution_filter_t1 拦截 | 不在不可成交价下单 |
| 4b | 出场端一字板 | t+H+1 跌停一字板 | 顺延而非剔除，挂排队不撤不补 | 持仓保留续处理 |
| 5 | 停牌 | 停牌标的 | 跳过不下单 | 无停牌标的订单 |
| 6 | 数据缺失 | 行情缺失/VOIDED | 数据闸门拦截+告警 | 无残缺数据下单 |
| 7 | 重复启动 | 启动第二实例 | 单例锁拒绝+PID 存活检查 | 仅一实例下单 |

### 3.2 承接 QS-C04 §十 必测用例 T1–T13（v2.0 完整验收标准）

> 细节以 QS-C04 §十为准；本表列验收通过标准。真实环境补充观察对象统一为 **T1/T5/T6**（T2 为崩溃注入专项，不依赖被动观察）。

| 编号 | 场景 | 验收通过标准 | 次数/优先级 |
|------|------|------------|---------|
| T1 | order_remark 反查（xtquant client_order_id 对账） | 可反查按 id 归位（order_remark = client_order_id）；不可反查则模糊匹配须**唯一候选+时间窗/标的/方向/数量/价格全匹配**，多候选或低置信度→暂停(MANUAL_REVIEW)（不算通过）。真实环境命中率 ≥95% 且误匹配率 = 0 | ⭐ 最高 |
| T2 | outbox 已发送未受理窗口崩溃 | **≥5 次**覆盖不同时序（刚发送/+1s/+5s），全部判 MAYBE_SENT_UNKNOWN 绝不误重发；任一误重发→RESET | ⭐ 最高 |
| T3 | 撤单在途收到全部成交 | CANCELLED(cancel_fill_type=FULL)，累计量幂等不重复；position_ledger 正确更新 | 高 |
| T4 | CANCELLED(PARTIAL) 缺口自愈 | 下周期 ORDER_SIZING 基于 C 重捕获，不立即补单 | 高 |
| T5 | xtquant 查询延迟与旧态 | **≥3 次**覆盖不同延迟（刚提交/+5s/+10s），全部 UNKNOWN 双查一致才归位 | ⭐ 最高 |
| T6 | 回报乱序/重复推送 | event_seq + 累计成交量幂等，无重复成交 | 高 |
| T7 | rate_limiter 触限 | 超速率排队(≥1000ms)；超日上限(200笔)→暂停(MANUAL_REVIEW)；超撤单频率→降速或暂停 | 中 |
| T8 | 签名重放/伪造 | 无 command_signature 拒收；重放旧签名/过期 payload 拦截；无 manual_operator_signature 拒绝人工恢复；无 break_glass_signature 拒绝旁路 | 中 |
| T9 | 除权日现金对账 | corporate_action_ledger 同推 position+cash，不误触暂停(MANUAL_REVIEW) | 中 |
| T10 | EOD 收盘 LIVE/PARTIAL 处置 | LIVE(DAY) 等自动撤；LIVE(GTC) 跨日；未提交意图(ORDER_SIZING/PRE_FIRE_CHECK)>14:55 作废+释放 reservation | 中 |
| T11 | 原七类异常 | 沿用 §3.1 全过 | 基线 |
| T12 | 进程假死接管 + 夺令牌 | 旧进程复活无法下单 | 中 |
| **T13** | **三级回撤状态机路径** | **20%：RISK_CLIP 设降仓目标，先卖卫星，系统续运行；25%：RISK_CLIP 触发全清仓+冻结，系统继续做对账（非 BREAK_GLASS 全停）；break-glass：暂停(BREAK_GLASS)，物理脚本执行，令牌单向夺取，券商APP手动清仓 SOP 路径可演练** | **高** |

### 3.3 新增专项演练

**A · 合规限速触限（QS-C04 §6.1 硬约束）**：超速率→排队延迟(≥1000ms)；超日上限(200)→暂停(MANUAL_REVIEW)；撤单频率超限(>10/min)→降速或暂停；break-glass 清仓限速绕过且全量写 ledger。

**B · 进程崩溃与 outbox 三态恢复**：未发崩溃→NOT_SENT_CAN_SEND 安全重发；已发未受理崩溃→MAYBE_SENT_UNKNOWN 绝不误重发；多单并发崩溃逐单正确归位。

**C · 签名与重放防护**：伪造无 command_signature 拒收；重放旧签名/过期 payload 拦截；人工恢复无 manual_operator_signature 拒绝；break-glass 无 break_glass_signature 拒绝旁路。

**D · 连续拒单断路器**：5min 内 ≥3 笔 REJECTED → 冻结该子策略新开仓 + 告警 + 暂停(REJECT_BREAKER)；halt_reason 字段正确记录。

**E · EOD 收盘处置**：LIVE(DAY) 等自动撤；LIVE(GTC) 跨日维持；未提交意图(ORDER_SIZING/PRE_FIRE_CHECK)>14:55 作废+释放 reservation。

**F · 压力注入（硬条件）**：指数 -5%、多票跌停、券商秒级延迟、连续拒单断路器、二级回撤(-25%)清仓续运行——逐项注入并断言行为符合 QS-C04 §6.3 风控触发汇总表。

**G · order_remark 对账专项（v2.0 新增）**：注入完整调仓周期，验证 order_remark 写入→委托回调→反查→execution_ledger 归位全链路；验证模糊匹配唯一性防线（多候选应降级暂停，不算通过）。

### 3.4 物理熔断专项演练（6 项 + 环境边界声明）

**环境边界（v2.0 对齐 QS-C04 §5.2）：**
- **Mock 环境执行**：先撤活跃单（CANCEL_REQUESTED/UNKNOWN 路径）、sellable_qty 清仓、跌停熔断、崩溃重启续清（券商行为可控）
- **真实环境仅在安全窗口执行**：踢主进程 session 前置条件、令牌互斥——须在**非调仓日且空仓/极轻仓**时执行 + 人工二次确认，verdict.json 标注 `env=real_restricted`

| 子项 | 注入 | 期望 | 环境 |
|------|------|------|------|
| break-glass 先撤活跃单 | 在途委托 | 复用 CANCEL_REQUESTED/UNKNOWN 先撤（QS-C04 §5.2 STEP 2） | mock |
| 撤单失败时清仓 | 撤单查询失败 | 仅按 sellable_qty 清仓 | mock |
| 跌停时熔断/崩溃重启续清 | 跌停/清仓中 kill | 挂跌停排队；重启读 ledger 续清(幂等) | mock |
| 券商APP手动清仓 SOP 演练 | mock xtquant 不可达 | 降级 APP 手动路径可执行，补录 execution_ledger | mock |
| 踢主进程 session 前置条件 | 主进程假死(PID存活无响应) | 仅心跳连丢>45s 且无响应才踢 | real_restricted |
| 熔断与主进程互斥（令牌） | 同时触发主进程下单 | 令牌互斥+单向(仅人工归还) | real_restricted |

### 3.5 跨周期与回挂审计验证用例

| 用例 | 注入 | 期望 |
|------|------|------|
| 缺口跨周期自愈（T4 配套） | 横跨两调仓周本周 CANCELLED(PARTIAL) 留缺口 | 下周 ORDER_SIZING 按真实 C 差量重捕获，不立即补单 |
| trigger_ic_audit 逻辑验证 | mock 一组"实盘 IC 落在 bootstrap 90% CI 下界之外"数据 | 断言 trigger_ic_audit 正确触发并归档（验**逻辑本身**，非真实触发）；口径须与 QS-C02 §七 bootstrap CI 口径一致 |
| 模拟盘 IC vs 实盘 IC 分列 | is_paper_trade 标记 | position_ledger 正确隔离；B1/B2 观测窗统计正确 |

### 3.6 演练调度建议（v2.0，对应分阶段门禁 §1.6）

| 演练类别 | 建议窗口 |
|----------|----------|
| 基线 7+4a/4b | 第 1-2 周 |
| T1/T2/T5（⭐ 最高）| 第 1 周 mock + 持续真实观察 |
| T13 三级回撤 | 第 2-3 周 mock |
| outbox 崩溃恢复（≥5 次）| 第 2-3 周 |
| G · order_remark 对账专项 | 第 1-4 周持续 |
| Break-glass（real_restricted）| 非调仓日、空/极轻仓 |
| 压力注入 | 第 6-8 周（系统稳定后） |

---

## 四、执行→研究数据反哺与实盘回挂审计

### 4.1 反哺链路验收（每日生成）

执行层按格式把 `avg_fill_price` vs `signal_ref_price` 偏差回流 QS-C02 成本归因/IC 回挂表，**每日生成**（无调仓日为空文件但 schema 校验必过；连续缺失 2 日→CONTINUE/RESET）。归档：
- `realized_ic.csv`（口径对齐 QS-C02 walk-forward IC，含 is_paper_trade 字段区分模拟盘/实盘）
- `cost_attribution.csv`（成交价 vs 信号理论价偏差）
- `fill_quality.csv`（滑点、成交率、撤单率）

**两层验收**：管道验收（≥1 次完整跑通+字段齐全→硬放行）；统计验收（累计 ≥50 笔成交后报 realized_ic 及置信区间→仅观察，写 strategy_observation.csv）。

### 4.2 实盘回挂触发与审计（口径对齐 QS-C02 §七 bootstrap CI）

```python
def trigger_ic_audit(research_ic_bootstrap_ci_lower: float,
                     live_ic_rolling26w: float,
                     n_weeks: int) -> bool:
    """背离（trigger_ic_audit）唯一定义：见 QS-C02 §七 bootstrap CI 口径。
    
    触发条件：滚动26周实测IC均值落在研究期IC bootstrap 90% CI 下界之外。
    
    注意：
    1. 旧定义「连续8周<中位数30%」已废除（SSOT §3）
    2. 本函数口径须与 QS-C02 §七 保持一致，以 QS-C02 为唯一定义来源
    3. 模拟盘期内无法真实触发（仅约12次调仓），故 §3.5 用 mock 数据验证审计逻辑本身
    4. 仅作策略观察，不阻塞工程放行、不作 Alpha 放行依据
    """
    return live_ic_rolling26w < research_ic_bootstrap_ci_lower
```

**B2 维持判线滚动监控**（引用 QS-CAL-001 数字）：滚动 26 周 IC 均值 > 研究 IC − 1.645×SE 且 > 0；跌破→降仓复盘。此为终身滚动判线，模拟盘阶段开始累积。

---

## 五、验收报告归档结构（v2.0）

```
acceptance_report/
├── daily/                          # 每日对账报告(含 FLAG 标记)
├── weekly/                         # 每周清洁交易周汇总（4周零差错追踪）
├── anomaly_drills/                 # 基线7 + T1..T13 + 专项(限速/outbox/签名/断路器/EOD/熔断6项/压力/order_remark)
├── rca/                            # 每个 CONTINUE_WITH_FLAG 日的 RCA 报告(不可删除)
├── feedback/                       # 每日 realized_ic/cost_attribution/fill_quality
├── pytest_report.xml               # §1.5 套件全绿证明（13 场景）
├── dashboard/                      # 趋势图(对账错误/滑点/UNKNOWN/冻结释放/FLAG日/order_remark命中率)
├── gates/                          # 门禁 A/B/C 评审记录
├── real_broker_observation.md      # §六 三项未知数采集结论（含 order_remark 命中率）
├── m3_pipeline_checklist.md        # M3 全链路上线验收清单
├── summary.md
├── strategy_observation.csv        # 实盘IC/夏普 + 回挂审计标记(仅观察) + is_paper_trade分列
└── verdict.json                    # RESET/CONTINUE_WITH_FLAG/PASS_ENGINEERING + flag_days + clean_weeks + RCA 留痕
```

---

## 六、真实券商被动观察期（最小样本量 + 前置）

**前置定位**：观察期**前置于**正式计时——先观察 ≥2 周确认 xtquant 行为与 QS-C04 状态机假设成立（门禁 A），再启动 4 周清洁交易周正式计时；否则末期才发现假设错误须推倒重来。

| 被观察行为 | 最小时长/样本 | 必须覆盖条件 | 通过标准 |
|-----------|--------------|-------------|----------|
| **order_remark 可反查性**（QS-C04 §八） | ≥2 完整调仓周期、≥30 笔委托 | 至少一次提交→受理→成交完整链路 | 命中率 ≥95%、误匹配率 = 0；否则 T1 FAIL 回挂 QS-C04 评审 |
| 查询延迟分布 | 连续 4 周 | 须含开盘后 30min + 收盘前 30min 高峰样本 | 记录 P50/P95/P99，与 QS-C04 §三超时假设一致 |
| 回报乱序/重复 | 全程被动采集，≥10 次撤单/查询 | LIVE/PARTIAL/CANCEL_REQUESTED 各 ≥1 次 | 任一乱序/重复自动告警写观察日志，event_seq 正确吸收 |

样本不足 → CONTINUE 不能 PASS。**Mock 与真实冲突裁决**：mock T1 PASS 但真实命中率 <95% 或误匹配 >0 → 以真实为准判 T1 FAIL，回挂 QS-C04 评审 outbox 恢复策略。结论与状态机假设不符须回挂评审而非强行放行。

---

## 七、M3 末全链路上线验收口径（v2.0 新增 · 对齐 SSOT §7）

> **SSOT 定义**（baseline_spec.md §7）：M3 末「上线可用」= 模拟盘全链路上线（数据→信号→风控→模拟执行→对账→告警闭环），开始累积 B 类行为数据。

### 7.1 M3 全链路上线验收清单

| 验收项 | 口径 | 通过标准 |
|--------|------|----------|
| 数据管道 | AKShare/Tushare/BaoStock 三源 + DuckDB/SQLite 落库 | 每日盘后自动采集，三源两票制裁决，零人工干预 |
| 点时契约 | QS-C03 v2.0（13 场景 pytest 全绿） | 见 §1.5 |
| 信号生成 | 因子→target_weight，visible_at 重放正确 | 当日信号在 14:55 前生成完毕 |
| 风控层 | risk_guard 唯一入口，合规限速（1笔/秒/200笔/日），三级回撤 | QS-C04 §六 |
| 执行层 | xtquant 下单+撤单，order_remark 写入，outbox 三态 | QS-C04 §四/§八 |
| 模拟执行 | QMT 模拟盘空跑，非真实资金 | QMT 模拟盘账户确认 |
| 对账系统 | 每日日终对账报告自动生成，position_ledger C 口径 | 见 §二 |
| 监控告警 | Server酱/钉钉机器人推送，关键告警（风控触发/下单失败/数据管道失败/进程失联/UPS切换） | 至少一次端到端告警测试通过 |
| 反哺链路 | realized_ic/cost_attribution/fill_quality 每日生成 | 见 §四.1 |
| 程序化交易报备 | M3 期间准备报备材料（开户勾选/先报告后交易） | 材料准备完成，M4 前报备 |

### 7.2 M3 末验收结论要求

```
M3 末验收报告（m3_pipeline_checklist.md）须包含：
- 全链路上线日期确认
- 首次完整调仓周期（含下单→成交→对账→反哺）截图/日志证明
- 门禁 A 通过记录
- 程序化交易报备材料清单
- 结论：「模拟盘全链路上线，B 类行为数据开始累积，M4 起转入 5 万真实实盘（前置条件：合规报备完成 + 4 清洁交易周）」
```

---

## 八、模拟盘 → 实盘的过渡条件（v2.0 · 对齐 SSOT 与 QS-CAL-001）

工程验收通过（PASS_ENGINEERING）后进入实盘但**仍非"策略已验证"**：

### 8.1 5 万起步条件（M4 起）

| 前置条件 | 口径 | 来源 |
|---------|------|------|
| 合规报备完成 | 开户如实勾选程序化交易，先报告后交易，软件变更更新报备 | SSOT §4 |
| 模拟盘 4 周零差错 | 连续 4 清洁交易周，每周日终对账零未解释差异 | QS-CAL-001 B3 工程判线 |
| M3 全链路上线验收通过 | 见 §七 | SSOT §7 |
| 门禁 C 通过（PASS_ENGINEERING） | 见 §1.6 | 本文档 §一 |

**5 万起步规则**：单票上限放宽至 20%（保证持有 ≥5 只）；满仓降仓线 2.5 万（先于账户级 3 万）；A2 降级路径触发则起步 2.5 万。

### 8.2 B 类闸门进度追踪（引用 QS-CAL-001 数字，不重抄）

| 闸门 | 进度追踪项 | 数字来源 |
|------|-----------|---------|
| **B1 加仓判线** | 模拟盘+5万阶段累计 ≥26 周；实测周度 rank-IC 均值 > 研究IC−1.0×SE；**加仓前至少 13 周必须来自真实 5 万实盘** | QS-CAL-001 §三 |
| **B2 维持判线** | 终身滚动 26 周 IC 均值 > 研究IC−1.645×SE 且 >0；跌破→降仓复盘 | QS-CAL-001 §三 |
| **B3 工程判线** | 成本偏差 ≤+30%；对账连续 4 周零差错（同本文档 4清洁交易周）；风控触发行为与 QS-C04 一致 | QS-CAL-001 §三 |

> **B1 观测窗说明**：模拟盘阶段 IC 数据可计入 26 周观测窗（is_paper_trade=True，模拟盘 IC 用收盘后撮合近似）。但 **5 万→20 万加仓前，至少 13 周须来自真实 5 万实盘**（is_paper_trade=False）。5 万→20 万加仓 = B1 + B3 同时满足，最早发生在 M4 起约 6 个月后（物理下限）。

### 8.3 实盘阶段监控

1. 实盘从 5 万起（单票上限放宽至 20%，保证持有 ≥5 只）
2. 加仓门槛为**行为一致**（实盘 IC/夏普与回测无显著背离 + 对账零错误 + 成本偏差达标 + 风控演练通过），**收益仅作观察项**；默认 5 万阶段观察周期 **N = 8–12 周**（计入 B1 观测窗）
3. 满仓后取更保守降仓线（2.5 万即降，先于账户级）
4. 实盘滚动夏普连续 **M = 3 个月**低于回测下限百分位 → 强制停盘复盘

---

## 九、验收自检清单（v2.0 全文版）

**前置依赖版本锁定：**
- [ ] 状态机版本：**QS-C04《执行与风控状态机 v1.3》**（15 个运行态，含 cancel_fill_type/halt_reason/order_remark 字段）
- [ ] 研究协议版本：**QS-C02《研究协议 v2.0》**
- [ ] 点时契约版本：**QS-C03《点时数据契约 v2.0》**
- [ ] 闸门数字来源：**QS-CAL-001《统计闸门校准报告 v1.0》**

**工程验收核心项：**
- [ ] M3 全链路上线验收通过（见 §七），m3_pipeline_checklist.md 已归档
- [ ] 连续 **4 个清洁交易周**（每周≥5清洁交易日，FLAG 标记日不计），日终对账零未解释差异（B3 工程判线前置）
- [ ] flag_days ≤ 3；每个 FLAG 日均有 RCA 报告（root_cause=env + fail_closed_verified）归档且 verdict.json 留痕
- [ ] 无任一立即 RESET 事件（重复成交/盲目下单/超卖/越限继续提交）
- [ ] 同类环境异常未超频次上限（60 日 ≤3 次 且 7 日 ≤2 次）
- [ ] 主动交易差=0；除权经 corp_action 解释；零股单独建账；现金尾差 ≤1 元；reserved 平衡
- [ ] **七类基线（含 4a/4b）+ T1–T13 全 PASS**（T2≥5 次、T5≥3 次、T1 唯一性匹配、T13 三级回撤路径）
- [ ] rate_limiter(1笔/秒/200笔/日硬约束) / outbox 三态 / 签名 / 断路器 / EOD / break-glass 6 项 / G·order_remark 专项 / **压力注入** 全 PASS
- [ ] **trigger_ic_audit 经 mock 数据验证逻辑正确触发并归档，口径对齐 QS-C02 §七 bootstrap CI（不使用旧「连续8周<中位数30%」定义）**
- [ ] 执行→研究反哺每日生成，schema 校验通过，管道至少完整跑通一次
- [ ] **pytest 套件全绿：点时契约 13 场景（QS-C03 v2.0）/迁移矩阵(15态)/DDL 约束/outbox/签名/对账分类**
- [ ] cost_model_id 与研究回测一致性断言通过
- [ ] 真实券商观察期满足最小样本量，三项未知数与 QS-C04 假设一致（含 order_remark 命中率≥95%）
- [ ] 门禁 A/B/C 均通过；演练 ledger 行带 is_drill 标记且未污染对账真值
- [ ] **order_remark 反查命中率连续 4 周 ≥95%，误匹配率=0（B3 工程判线对账条件）**

**B 类闸门追踪（观察项）：**
- [ ] 模拟盘 IC 序列以 is_paper_trade=True 标记，不与实盘 IC 混淆
- [ ] B1 观测窗进度追踪：当前周数 W/26（模拟盘+实盘合计）
- [ ] 加仓前确认：至少 13 周来自真实 5 万实盘（QS-CAL-001 B1 判线要求）
- [ ] B2 维持判线滚动监控启动（实盘开始后终身滚动）

**转实盘前置条件最终确认：**
- [ ] PASS_ENGINEERING 且 verdict = "工程验收通过，非策略放行"
- [ ] 合规报备完成（开户勾选程序化交易 + 先报告后交易）
- [ ] 4 清洁交易周零差错（B3 对账条件）
- [ ] M3 全链路上线通过

---

## 附录 A · 滑点双口径说明

| 口径 | 阈值 | 作用对象 |
|------|------|----------|
| 单笔成交工程容忍 | 0.5%（=50bp） | 单笔订单实际 vs 预期成交均价 |
| 策略层口径敏感性告警 | 5bp | VWAP vs 开盘双口径夏普差（QS-C02） |

---

## 附录 B · 背离定义演进与废除记录

| 版本 | 背离定义 | 状态 |
|------|---------|------|
| v1.0/v1.1/v1.2 | 「连续8周<中位数30%」 | **废除**（SSOT §3 明确废除） |
| **v2.0** | **QS-C02 §七 bootstrap CI 口径**：滚动26周实测IC均值落在研究期IC bootstrap 90% CI 下界之外即触发 IC 审计 | **唯一有效定义，本文档只引用 QS-C02 编号，不重抄** |

> 任何代码或脚本中使用「连续8周<中位数30%」判定的均为过时实现，须按 QS-C02 §七 更新。

---

## 附录 C · v2.0 变更日志（相对 v1.2）

| 编号 | 优先级 | 章节 | v1.2 状态 | v2.0 变更 | SSOT 依据 |
|------|--------|------|-----------|-----------|----------|
| E1 | 🔴 P0 | 全文 | 前置依赖锁定 v1.2 | **版本锁定更新：点时契约 v2.0、研究协议 v2.0、状态机 v1.3（QS-C02/C03/C04）** | SSOT §8 |
| E2 | 🔴 P0 | §〇.7/§四.2/附录B | 「连续8周<中位数30%」旧定义 | **废除旧定义，统一为 QS-C02 §七 bootstrap CI 口径；本文档不重抄定义，只引用编号** | SSOT §3 |
| E3 | 🔴 P0 | §1.5 | pytest 12 场景 | **统一为 13 场景（QS-C03 v2.0 唯一口径）** | SSOT §3 |
| E4 | 🔴 P0 | §一/§八 | 无 4 周零差错条件 | **新增：模拟盘 4 周零差错=转 5 万实盘前置条件；验收清单与 B3 工程判线对齐** | SSOT §7 + QS-CAL-001 B3 |
| E5 | 🔴 P0 | §七 | 无 M3 末全链路验收口径 | **新增 §七 M3 末全链路上线验收口径（含完整清单）** | SSOT §7 |
| E6 | 🟡 P1 | §八.2 | B1 加仓判线无追踪 | **明确：模拟盘周数计入 B1 26周观测窗；加仓前至少13周真实5万实盘；引用 QS-CAL-001 数字** | QS-CAL-001 §三/§四 |
| E7 | 🟡 P1 | 全文状态名 | HALTED/MANUAL/FILLED_AFTER_CANCEL等 | **全文状态名对齐 QS-C04 v1.3 15态**：CANCELLED(cancel_fill_type)/halt_reason字段；告警名更新 | QS-C04 §一 |
| E8 | 🟡 P1 | §三.2-T1/§三.3-G | T1 描述为 client_order_id | **T1 更新为"order_remark 反查"（QS-C04 §八方案正式采纳）；新增 G·order_remark 对账专项** | QS-C04 §八 |
| E9 | 🟡 P1 | §三.2-T13 | 无 T13 | **新增 T13：三级回撤状态机路径验证（20%/25%/break-glass）** | QS-C04 §六.2 |
| E10 | 🟡 P1 | §二.2 | 合规限速为观察项 | **报告[3]更新：1笔/秒/200笔/日为硬约束，breach=暂停(MANUAL_REVIEW)** | QS-C04 §六.1 |
| E11 | 🟢 P2 | §二.2/§四 | 无 is_paper_trade 区分 | **新增 is_paper_trade 字段，模拟盘IC与实盘IC分列，B1观测窗正确统计** | QS-CAL-001 §四 |
| E12 | 🟢 P2 | §五 | 无 m3_pipeline_checklist | **归档结构新增 weekly/ 目录和 m3_pipeline_checklist.md** | SSOT §7 |
| E13 | 🟢 P2 | §1.2 | verdict 无 clean_weeks | **verdict 新增 clean_weeks 字段，追踪 4 周清洁交易周进度** | E4 |
| E14 | 🟢 P2 | 附录B | 无废除记录 | **新增附录B：背离定义演进与废除记录** | SSOT §3 |
| E15 | 🟢 P2 | 全文 | 全文版 | **v2.0 为全文版（非增量补丁），版本演进表补全** | SSOT §8 |

---

## 附录 D · 交叉引用索引

| 引用对象 | 本文档引用位置 | 目标文档编号 |
|---------|--------------|------------|
| 15 个运行态状态清单 | §三.2 T1-T13、§二.2 [2]、§九 | QS-C04 §一 |
| order_remark 对账方案 | §三.2-T1、§三.3-G、§二.2 [4]、§六 | QS-C04 §八 |
| 三级回撤（20%/25%）定义 | §三.2-T13、§二.2 [7] | QS-C04 §六.2 |
| 合规限速硬约束（1笔/秒/200笔/日） | §二.2 [3]、§三.3-A | QS-C04 §六.1 |
| cancel_fill_type 字段 | §二.2 [2]、§三.2-T3/T4 | QS-C04 §一.2/§七 |
| halt_reason 字段 | §二.2 [2]、§三.3-D | QS-C04 §一.2 |
| break-glass 简化执行（脚本+APP SOP） | §三.4 | QS-C04 §五.2 |
| position_ledger 推导（含 cancel_fill_type） | §二.1、§三.5 | QS-C04 §7.2 |
| 背离（trigger_ic_audit）唯一定义 | §〇.7、§四.2、附录B | QS-C02 §七 |
| A1/A2/B1/B2/B3 闸门数字 | §一.1、§八.2 | QS-CAL-001 |
| 13 场景 pytest 唯一口径 | §1.5 | QS-C03 v2.0 |
| M3 末上线可用定义 | §〇.3、§七 | SSOT §7 |
| 五条铁律 | — | QS-C01 |

---

*本文档为 QuantSolo v2.0 宪法文档体系 QS-C05，v2.0 全文版（非增量补丁）。冻结后变更须版本号递增并记录于 research_ledger，并同步 QS-C04 交叉引用。模拟盘验收通过（PASS_ENGINEERING）后，系统进入 M4 真实 5 万实盘阶段，B 类闸门开始正式裁决。*
