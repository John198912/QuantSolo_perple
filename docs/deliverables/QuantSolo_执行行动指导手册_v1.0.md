# QuantSolo — 执行行动指导手册（Runbook）v1.0

---

| 字段 | 内容 |
|------|------|
| **文档编号** | QS-E05 |
| **版本** | v1.0 |
| **日期** | 2026-06-12 |
| **状态** | 正式发布 |
| **上游依赖** | QS-C01《系统设计文档 v5.0》· QS-C04《执行与风控状态机 v1.3》· QS-C05《模拟盘验收手册 v2.0》· baseline_spec.md（SSOT） |
| **下游文档** | QS-E04《项目测试验收方案 v1.0》（测试演练参考）· QS-E06《项目实施计划与里程碑》|
| **冲突裁决** | 与 SSOT（baseline_spec.md）冲突时以 SSOT 为准；已全文校验。 |

---

## 版本演进表

| 版本 | 日期 | 主要变更 |
|------|------|---------|
| **v1.0** | **2026-06-12** | **初版全文；覆盖 10 大操作场景、联系人资源表、术语速查** |

---

## 目录

- [§1 手册定位与使用方法](#1-手册定位与使用方法)
- [§2 每日盘后巡检（5–10 分钟清单）](#2-每日盘后巡检)
- [§3 每周对账复盘（2h 清单）](#3-每周对账复盘)
- [§4 开盘前检查（9:15 前）](#4-开盘前检查)
- [§5 盘中告警响应分级处置](#5-盘中告警响应分级处置)
- [§6 物理一键熔断 + 券商 APP 手动清仓 SOP（break-glass）](#6-物理熔断与手动清仓)
- [§7 PC 故障灾备恢复（T+1 目标）](#7-pc故障灾备恢复)
- [§8 备份与恢复演练](#8-备份与恢复演练)
- [§9 程序化交易报备操作](#9-程序化交易报备)
- [§10 月度/季度复盘模板](#10-月度季度复盘模板)
- [§11 B1/B2 闸门评估操作](#11-b1b2闸门评估操作)
- [§12 联系人与资源表](#12-联系人与资源表)
- [§13 术语速查](#13-术语速查)

---

## §1 手册定位与使用方法

### 1.1 手册定位

本手册是 QuantSolo 量化系统的**一线操作手册**，面向系统运行期间的日常维护、应急处置和定期审查操作。

- **不是策略文档**：不讲 Alpha 逻辑，不讲因子研究
- **不是设计文档**：不重复系统架构，只说「做什么、怎么做、怎么验证」
- **是操作手册**：每个场景给出「触发条件 → 逐步操作 → 验证点 → 回滚」四段式

### 1.2 场景组织

| 章节 | 场景 | 频率 | 关键性 |
|------|------|------|--------|
| §2 | 每日盘后巡检 | 每交易日 | 铁律五强制 |
| §3 | 每周对账复盘 | 每周末 | 铁律五强制 |
| §4 | 开盘前检查 | 每交易日 | 关键 |
| §5 | 盘中告警响应 | 按需 | 高 |
| §6 | 物理熔断+手动清仓 | 应急 | **最高（break-glass）** |
| §7 | PC 故障灾备恢复 | 应急 | 高 |
| §8 | 备份与恢复演练 | 每周 | 中 |
| §9 | 程序化交易报备 | 开户/变更时 | 合规义务 |
| §10 | 月度/季度复盘 | 月度/季度 | 中 |
| §11 | B1/B2 闸门评估 | 按周期 | 高 |

### 1.3 铁律五提醒

> 引用 QS-C01 §1 铁律五：**每日人工巡检（5–10 分钟）+ 周度对账（2h），自动化 ≠ 无人值守。总运维 ≤ 4h/周，超出触发简化评审。**

本手册中的 §2（每日巡检）和 §3（每周复盘）是不可豁免的强制操作，不得以「系统没告警」为由跳过。

---

## §2 每日盘后巡检（5–10 分钟清单）

### 触发条件

每个交易日收盘后（通常 15:30 后、数据管道完成后，约 17:30–18:00），必须执行。

### 逐步操作

**第一步：检查进程健康（1–2 分钟）**

```bash
# 1a. 检查五个进程存活状态
# Windows 任务管理器 → 查看以下进程：
# ① QMT/迅投终端（qmt.exe 或类似）
# ② 执行守护进程（在交易时段后可以已停止，收盘后可不必运行）
# ③ 盘后数据管道进程（约 17:00 触发，此时应已完成）
# ④ 研究环境（正常情况下应已关闭）
# ⑤ 监控告警进程（必须运行）

# 命令行检查（PowerShell）：
Get-Process | Where-Object {$_.Name -match "qmt|quant|monitor"} | Select Name, Id, CPU
```

**第二步：检查数据管道结果（2–3 分钟）**

```bash
# 2a. 检查今日数据入库情况（DuckDB 查询）
python scripts/check_pipeline.py --date today
# 期望输出：
# [OK] AKShare 行情：XXXX 条，覆盖率 99.X%
# [OK] Tushare 补强：XXXX 条
# [OK] BaoStock 校验：XXXX 条
# [OK] 三源冲突裁决：X 条冲突已处理
# [OK] visible_at 时间戳：2026-XX-XX 17:00:00 ±60s

# 2b. 检查复权因子完整性
python scripts/check_adj_factor.py --date today
# 期望输出：[OK] 缺失率 = 0.000%
```

**第三步：检查对账报告（2–3 分钟）**

```bash
# 3a. 查看今日日终对账报告
cat acceptance_report/daily/recon_YYYYMMDD.txt

# 重点关注以下字段（见 QS-C05 §二）：
# [1] 持仓三方对账：主动差 = 0，状态标 ✓
# [2] 订单状态机：重复成交 = 0（必须为 0）
# [8] 异常与告警：当日 RCA 有无
# [10] 当日结论：✓ 清洁 / ⚑ FLAG / ✗ RESET
```

**第四步：检查告警记录（1 分钟）**

```bash
# 4a. 查看今日告警日志
tail -50 logs/alerts_YYYYMMDD.log

# 4b. 检查手机是否有未处理的推送告警
# 如有 FLAG 或 RESET 类告警，按 §5 分级处置
```

**第五步：记录巡检结果（1 分钟）**

```markdown
# 填写每日巡检记录（acceptance_report/daily/inspection_YYYYMMDD.md）
日期：YYYY-MM-DD
巡检时间：HH:MM
数据管道：OK / 告警（说明）
对账结论：清洁 / FLAG / RESET
进程健康：全部存活 / 异常（说明）
今日备注：（无）
```

### 验证点

- 数据管道报告无 ERROR 级别日志
- 对账报告主动差 = 0，当日结论为「清洁」或「FLAG」（FLAG 须有 RCA）
- 监控告警进程⑤存活
- 手机端无未处理的高优先级推送

### 回滚

| 发现问题 | 处置 |
|---------|------|
| 数据管道失败 | 手动重跑 `python scripts/run_pipeline.py --date today --retry`；若仍失败，记录 issue 并次日补采 |
| 对账 RESET | 停止执行守护进程，按 §5 告警响应处置，创建 priority:critical issue |
| 进程失联 | 重启对应进程，若无法重启按 §6 或 §7 处置 |
| 数据质检未通过（覆盖率 < 99%）| 记录 issue，尝试补采；不影响次日开盘（除非财报/除权日）|

---

## §3 每周对账复盘（2h 清单）

### 触发条件

每周末（建议周六或周日），必须执行，约 2 小时。对账复盘是铁律五强制项，不得豁免。

### 逐步操作

**第一步：本周基础对账汇总（20 分钟）**

```bash
# 1a. 生成本周对账汇总报告
python scripts/weekly_reconcile.py \
    --start YYYY-MM-DD \
    --end YYYY-MM-DD \
    --output acceptance_report/weekly/week_YYYYMMDD.md

# 重点核查：
# - 本周清洁交易日数（目标：5天全绿）
# - FLAG 天数（上限：每 4 周累计 ≤ 3 天）
# - RESET 事件（理想：0次）
```

**第二步：因子 IC 走势核查（20 分钟）**

```bash
# 2a. 输出本周因子 IC 数据
python scripts/check_ic_weekly.py \
    --window 26 \
    --output reports/ic_rolling26w_YYYYMMDD.csv

# 核查要点：
# - 滚动 26 周 IC 均值是否 > 研究IC - 1.645×SE 且 > 0（B2 维持判线）
# - IC 是否出现连续下降趋势（WATCH 信号）
# - 本周 realized_ic 与历史均值的偏差
```

**第三步：成本偏差分析（15 分钟）**

```bash
# 3a. 检查本周成本归因文件
python scripts/check_cost_attribution.py \
    --week YYYY-WXX \
    --output reports/cost_attr_YYYYMMDD.csv

# 核查要点：
# - 实际成本 vs 回测成本偏差是否 ≤ +30%（B3 工程判线）
# - 滑点是否在预期范围内（单笔 ≤ 0.5%）
# - order_remark 反查命中率（连续 4 周 ≥ 95%）
```

**第四步：风控触发记录回顾（15 分钟）**

```bash
# 4a. 查询本周风控触发事件
python scripts/query_risk_events.py --week YYYY-WXX

# 核查要点：
# - 一级预警（20%）触发次数及处置情况
# - 二级硬止损（25%）触发情况（理想：0次）
# - 单票/行业超限拦截次数
# - 连续拒单断路器触发情况
```

**第五步：B2 维持判线评估（20 分钟）**

参见 §11 B1/B2 闸门评估操作，本步骤为每周度简查：

```python
# 简查脚本
python scripts/check_b2_gate.py --as-of YYYY-MM-DD

# 输出：
# 滚动26周IC均值: X.XXXX
# 研究IC - 1.645×SE: X.XXXX
# B2 状态: 维持 / 跌破（需降仓复盘）
```

**第六步：开放 issue 清理（15 分钟）**

```bash
# 检查 GitHub Issues 中的开放缺陷
# 重点：关闭本周已修复的 priority:high 及以上 issue
# 更新 test_reports/issues/open_issues_YYYYMMDD.md
```

**第七步：B 类闸门进度更新（10 分钟）**

```markdown
# 更新 B 类闸门进度表（手动维护 acceptance_report/weekly/gates_progress.md）
| 闸门 | 本周进度 | 累计进度 | 状态 |
|------|---------|---------|------|
| B1 观测窗 | 模拟盘第X周 + 实盘第Y周 | 合计Z/26周 | 累积中 |
| B2 维持判线 | IC均值=X > 研究IC-1.645×SE=Y | 本周:维持 | 正常 |
| B3 工程判线 | 成本偏差X% ≤30%；对账X/4周零差错 | B3:进行中 | 正常 |
```

**第八步：周复盘小结（5 分钟）**

```markdown
# 填写 acceptance_report/weekly/summary_YYYYMMDD.md
本周日期：YYYY-MM-DD 至 YYYY-MM-DD
清洁交易日：X/5
B2 状态：维持/跌破
成本偏差：X%（ ≤30% OK / >30% 告警）
本周亮点/问题：（自由填写）
下周关注点：（自由填写）
```

### 验证点

- 每周对账汇总报告已生成并归档
- B2 维持判线状态已更新
- 成本偏差 ≤ +30%
- 开放的 priority:high/critical issue 全部有跟进计划

### 回滚

| 发现问题 | 处置 |
|---------|------|
| B2 跌破维持判线 | 启动降仓复盘流程：参见 §11.3 |
| 成本偏差 > +30% | 创建 priority:high issue，检查 cost_model_id 一致性，排查成交逻辑 |
| 本周连续出现 FLAG | 检查 FLAG 根因（环境异常 vs 代码 bug），代码 bug → 立即修复 + RESET |
| order_remark 命中率 < 95% | 检查 xtquant order_remark 写入逻辑，参见 QS-C04 §八 |

---

## §4 开盘前检查（9:15 前）

### 触发条件

每个交易日开盘前（建议 9:00–9:14），约 5 分钟。

### 逐步操作

**第一步：进程健康检查（1–2 分钟）**

```bash
# PowerShell
Get-Process | Where-Object {$_.Name -match "qmt|quant|monitor"} | Select Name, Id, StartTime

# 核查：
# ① QMT/迅投终端：必须运行
# ② 执行守护进程：必须运行（或准备启动）
# ⑤ 监控告警进程：必须运行
```

**第二步：数据完整性确认（1 分钟）**

```bash
# 确认昨日盘后数据已入库
python scripts/check_data_freshness.py --date yesterday
# 期望：[OK] 昨日数据已入库，visible_at 正确
```

**第三步：持仓与目标对比（1–2 分钟）**

```bash
# 盘前 dry-run：对比前一日信号变化
python scripts/pre_market_dryrun.py --date today
# 输出：
# 当前持仓（来自 position_ledger C）：
#   000001.SZ: 1000 股
#   ...
# 今日目标（来自昨日信号）：
#   000001.SZ: 1000 股（持平）
#   ...
# 差量订单：X 笔（预计）
```

**第四步：风控参数确认（30 秒）**

```bash
# 确认风控参数版本
python scripts/check_risk_policy.py
# 期望：risk_policy_version = rp_v1.3（与 QS-C04 版本一致）
```

**第五步：UPS 状态检查（30 秒）**

```
硬件检查：
- UPS 面板指示灯：绿色（市电供电）
- 若为橙色/红色（电池供电）：立即排查市电，参见 §5.5 UPS 切换处置
```

### 验证点

- 五个进程状态全部如预期（见 QS-C01 §14.2 进程拓扑）
- 昨日数据入库完整
- UPS 市电供电正常
- 无未处理的昨日遗留 RESET 类告警

### 回滚

| 问题 | 处置 |
|------|------|
| QMT/迅投终端未运行 | 启动 QMT 客户端，等待登录完成（约 1–2 分钟）|
| 数据未入库 | 手动重跑管道，9:15 前无法完成则暂停当日自动下单，人工评估 |
| 执行守护进程异常 | 重启进程，检查日志；若无法启动按 §7 处置 |
| 存在昨日 RESET 未处置 | 停止今日自动下单，先完成 RESET 处置再恢复 |

---

## §5 盘中告警响应分级处置

### 5.1 告警分级总览

| 告警类型 | 严重级别 | 响应时限 | 首要操作 |
|---------|---------|---------|---------|
| 风控触发（20% 预警）| 🔴 P1 | 立即（≤5分钟）| 确认降仓已执行 |
| 风控触发（25% 硬止损）| 🔴 P0 | 立即（≤2分钟）| 确认清仓+冻结 |
| 下单失败 | 🔴 P1 | ≤10 分钟 | 检查 xtquant 连接 |
| 数据管道失败 | 🟡 P2 | ≤1 小时 | 评估影响范围 |
| 进程失联（②或⑤）| 🔴 P1 | ≤5 分钟 | 检查，必要时 break-glass |
| UPS 切换至电池 | 🟡 P2 | ≤10 分钟 | 准备手动清仓预案 |

### 5.2 风控触发处置

**触发条件**：Server酱/钉钉推送「风控触发」告警，或盘中查看 Streamlit 看板时发现回撤超阈值。

#### 5.2.1 一级预警（20% 回撤）

```
【触发信号】
  手机推送：「QuantSolo 告警：账户回撤已达 20%，一级预警触发」

【逐步操作】
  Step 1：确认告警真实性
    - 打开 Streamlit 监控看板（localhost:8501 或手机访问）
    - 核对当前账户净值与初始资金的比例
    - 确认非数据错误（查看 PnL 是否与行情一致）

  Step 2：确认系统已自动降仓
    - 查看 execution_ledger 最新记录：应有「RISK_CLIP：降仓至50%」记录
    - 确认「先卖卫星仓」（趋势卫星 20–25% 先平）已执行
    - 查看 position_ledger C：当前总仓位应 ≤ 初始资金的 50%

  Step 3：记录与追踪
    - 在日志中记录触发时间、触发价位、执行结果
    - 观察后续走势，若回撤继续扩大至 25% 则触发二级

  Step 4：盘后复盘（见 §3 每周对账复盘）
    - 分析触发原因
    - 检查是否需要调整风控参数（须宪法修订流程）

【验证点】
  - execution_ledger 有 RISK_CLIP 降仓记录且 to_state 正确
  - 卫星仓已先平，核心仓降幅符合 50% 目标
  - 系统继续运行，未进入 BREAK_GLASS 态

【回滚】
  系统一级预警是自动执行的，无需人工回滚；若系统未自动执行，
  手动检查 risk_guard 代码逻辑，创建 priority:critical issue。
```

#### 5.2.2 二级硬止损（25% 回撤）

```
【触发信号】
  手机推送：「QuantSolo 告警：账户回撤已达 25%，二级硬止损触发，全清仓执行中」

【逐步操作】
  Step 1：确认告警并立即响应（≤2分钟）
    - 打开手机/PC 监控看板
    - 核对净值，确认 25% 回撤成立

  Step 2：确认系统已自动清仓
    - 查看 execution_ledger：应有全仓清仓记录，冻结新开仓标记
    - 查看 position_ledger C：目标仓位应趋近 0
    - 注：系统继续运行做对账，不是全停（QS-C04 §5.1）

  Step 3：人工复盘确认
    - 登录券商 APP 确认持仓状态
    - 若有未成交挂单（如跌停无法成交），记录 pending_liquidation

  Step 4：冻结新开仓（系统自动，人工复核）
    - 确认 execution_ledger 中后续的 RISK_CLIP 记录：冻结新开仓
    - 系统后续只允许对账操作，不得开新仓

  Step 5：创建复盘记录
    - 记录触发原因、当时市场情况、持仓明细
    - 计划恢复时间（见 QS-C01 §7.1 恢复规则）

【恢复条件（须满足其一）】
  - 净值创近期新高
  - 回撤修复至预警线（20%）上方
  - 固定冷静期结束且非熊态（大盘 vs 200日MA）

【验证点】
  - 全仓清仓记录完整
  - 无新开仓记录（冻结有效）
  - 对账报告今日无未解释差异

【回滚】
  硬止损是保本机制，不应回滚；若误触发（数据错误导致），
  立即停止系统，人工核对净值，记录 priority:critical issue。
```

### 5.3 下单失败处置

**触发条件**：告警推送「下单失败：{ts_code} {error_code}」

```
【逐步操作】
  Step 1：查看失败详情
    - 查看 execution_ledger 最新 REJECTED 记录
    - 记录 error_code 和 ts_code

  Step 2：按 error_code 分类处置
    ├── 资金不足（InsufficientFunds）
    │   → 检查 reserved_cash 是否正确释放
    │   → 检查其他挂单是否占用过多资金
    │   → 不需要立即重下，等 ORDER_SIZING 下周期重捕获
    │
    ├── 价格错误（InvalidPrice）
    │   → 检查是否遇到涨跌停限价
    │   → 系统会在 PRE_FIRE_CHECK 中过滤一字板，此处若出错检查逻辑
    │
    ├── 连续拒单（REJECT_BREAKER 触发）
    │   → 查看是否 5min 内 ≥3 笔 REJECTED
    │   → 系统应已暂停该子策略新开仓（halt_reason=REJECT_BREAKER）
    │   → 检查拒单原因，解决后人工恢复（写 manual_operator_signature）
    │
    └── xtquant 连接异常
        → 进入 §5.3 进程失联处置

  Step 3：验证对账不受影响
    - 确认 position_ledger C 未因失败产生差异
    - REJECTED 终态正确记录，reservation 已释放

【验证点】
  - REJECTED 记录有 error_code
  - reservation 已正确释放（checked_cash 平衡）
  - 无重复下单（幂等键保证）

【回滚】
  REJECTED 是终态，无需回滚；下周期 ORDER_SIZING 会基于真实持仓 C 自动重新捕获差量。
```

### 5.4 数据管道失败处置

**触发条件**：告警推送「数据管道失败：{data_source}」

```
【逐步操作】
  Step 1：评估影响范围
    - 确认失败的数据源（AKShare/Tushare/BaoStock）
    - 确认失败的数据类型（行情/龙虎榜/资金流/财报）
    - 今天是否是关键日期（财报发布日/除权除息日）？

  Step 2：尝试重采
    python scripts/run_pipeline.py \
        --date today \
        --source {failed_source} \
        --retry --max-retries 3
    
  Step 3：三源两票制降级
    - 若单源失败，另两源两票制仍可裁决
    - 若两源同时失败，暂停当日信号生成，等到下一个正常交易日

  Step 4：重要日期升级处置
    - 财报/除权日数据失败 → 升级为 P1 告警
    - 不下单，等数据修复后重新生成信号

  Step 5：记录与归档
    - 记录失败时间、失败原因、处置方式
    - 若影响对账，创建 FLAG 记录（see QS-C05 §1.4 RCA 规范）

【验证点】
  - 重采后数据质检通过（覆盖率 ≥99%）
  - 今日 visible_at 时间戳正确
  - 若发出 FLAG，RCA 已归档

【回滚】
  数据失败不影响执行（当日持仓维持），次日正常采集即可恢复。
```

### 5.5 进程失联处置

**触发条件**：告警推送「进程失联：{进程名}」，或监控进程⑤检测到进程②心跳连丢 3 次（45s 窗口）。

```
【逐步操作】
  Step 1：立即确认（≤5分钟）
    - 打开手机券商 APP，查看当前持仓状态
    - 判断市场是否异常（大盘是否有急跌）

  Step 2a：市场正常 → 尝试重启
    - 检查 PC 是否可访问（远程桌面或本地）
    - 查看进程日志（logs/execution_daemon.log）
    - 重启失败进程：
      python scripts/start_execution_daemon.py
    - 重启后执行开盘前 dry-run，与持仓对齐（QS-C04 §4.2）

  Step 2b：PC 不可访问 → 进入 §7 灾备恢复流程

  Step 2c：市场异常（大盘急跌/个股跌停）→ 进入 §6 物理熔断流程

  Step 3：重启成功后验证
    - 检查 position_ledger C 与券商持仓一致
    - 确认 outbox 三态恢复正确（无重复下单）
    - 检查下单令牌是否正确归还

【验证点】
  - 进程重启后立即执行 RECONCILE，持仓对齐
  - execution_ledger 无因进程重启产生的重复成交
  - 监控告警进程⑤重新开始发送心跳

【回滚】
  若重启后持仓与 position_ledger C 不一致，停止自动交易，
  手动核对并修复 ledger，记录 priority:high issue。
```

### 5.6 UPS 切换至电池处置

**触发条件**：告警推送「UPS 切换至电池供电」，或 UPS 面板告警灯变橙/红。

```
【重要背景】
  UPS 电池容量 ≥600VA，理论支持撤单清仓约 10 分钟（具体视负载而定）。

【逐步操作】
  Step 1：立即评估时间窗口（≤1分钟）
    - 确认停电类型：瞬间断电（UPS 自动恢复）vs 持续停电
    - 检查手机是否有停电通知（如物业短信）

  Step 2a：瞬间断电（≤2分钟）
    - 观察 UPS 是否切回市电（面板变绿）
    - 若切回：检查进程是否仍然存活，恢复正常监控

  Step 2b：持续停电（>2分钟）
    - 预计 PC 还有约 10 分钟运行时间
    - 在 PC 上执行优雅停止（关闭执行守护进程，保存状态）：
        python scripts/graceful_shutdown.py --save-state
    - 同时准备手机券商 APP 手动操作（见 §6）

  Step 3：若停电期间持仓需要处理
    - 评估当前市场情况
    - 若市场剧烈下跌：执行 §6 券商 APP 手动清仓 SOP
    - 若市场平稳：保持持仓，等待 PC 恢复

  Step 4：电力恢复后
    - PC 正常启动
    - 执行 §4 开盘前检查（若在交易时段内）
    - 验证 position_ledger C 与券商持仓一致

【验证点】
  - UPS 切回市电后进程存活状态正常
  - 若执行了优雅停止，重启后 outbox 三态恢复正确

【回滚】
  无需回滚，UPS 是电源保护装置。若停电期间未能正常关闭，
  重启后执行全量 RECONCILE 再恢复自动交易。
```

---

## §6 物理一键熔断 + 券商 APP 手动清仓 SOP（break-glass）

> **警告：本操作是最终应急手段（break-glass），执行后系统进入暂停态（BREAK_GLASS），须人工复盘后才能恢复交易。执行前须二次确认。**

> **引用**：QS-C04 §五.2（break-glass 执行动作简化版）；五条铁律第①条（物理熔断是唯一允许绕过策略层的例外，仍须写入执行审计日志）。

### 触发条件

以下任一情况触发：
- QMT 断连且无法恢复（进程失联超过 45s + PID 存活无响应）
- PC 故障无法访问（灾备场景）
- 市场极端异常且持仓风险极高（人工判断）
- UPS 电池即将耗尽且持仓未清

### 6.1 物理一键熔断（xtquant 可达时）

**执行路径**：独立进程脚本，直接调 xtquant 撤单+市价平仓，不经策略层（QS-C04 §5.2 STEP 2）。

```
【二次确认】
  在执行前确认以下两项：
  □ 我确认需要紧急平仓（非误操作）
  □ 我已检查券商 APP 中的实时持仓

【Step 1：触发物理熔断脚本（独立进程）】
  # 在 PC 命令行（管理员权限）：
  python break_glass.py --confirm --operator-id "solo" \
                        --reason "手动触发/灾备/[填写具体原因]"
  
  # 脚本执行过程：
  # 1. 夺取全局下单令牌（令牌单向，仅人工归还）
  # 2. 踢主进程 session（前置条件已满足：心跳连丢>45s 且 PID 无响应）
  # 3. 调用 xtquant 撤销所有在途委托（复用 CANCEL_REQUESTED/UNKNOWN 路径）
  # 4. 按 xtquant 实时 sellable_qty 市价平仓
  # 5. 全量写 execution_ledger（带 break_glass_signature）

【Step 2：等待脚本完成，查看执行日志】
  tail -f logs/break_glass.log
  
  # 期望输出：
  # [OK] 令牌夺取成功
  # [OK] 主进程 session 已踢出
  # [OK] 撤单：X 笔在途委托已撤销
  # [OK] 平仓：X 笔市价卖出已提交
  # [OK] execution_ledger 记录 X 条（带 break_glass_signature）
  # [DONE] break-glass 完成，系统进入 BREAK_GLASS 暂停态

【Step 3：用券商 APP 确认持仓清零】
  → 打开手机券商 APP（国金证券 APP）
  → 底部「交易」→「持仓」
  → 确认所有股票持仓为 0（或仅剩跌停无法成交的标的）
  
  [截图位置 1]：持仓列表截图（持仓清零确认），保存到 emergency/screenshots/bg_YYYYMMDD_HHMMSS_positions.png

【Step 4：若有跌停标的无法清仓】
  → 挂跌停价排队（不撤不补）
  → 标记 pending_liquidation
  → 次日优先卖出

【Step 5：记录 execution_ledger 补录】
  # 若 xtquant 脚本未能自动记录，手工补录：
  python scripts/manual_ledger_entry.py \
      --action break_glass \
      --timestamp "YYYY-MM-DD HH:MM:SS" \
      --operator "solo" \
      --reason "手动触发..."
```

**验证点**：
- `execution_ledger` 有带 `break_glass_signature` 的完整记录
- 系统状态为 `BREAK_GLASS` 暂停态
- 券商 APP 持仓清零（或仅剩跌停标的）
- 令牌已由熔断脚本夺取，主进程无法下单

### 6.2 券商 APP 手动清仓 SOP（xtquant 不可达时）

> 此为兜底路径（QS-C04 §5.2 STEP 3）：xtquant 不可达，或脚本执行失败时使用。

```
【Step 1：打开券商 APP（国金证券）】
  → 手机解锁，打开「国金证券」APP
  → 登录账户（确保账户密码已记忆/生物识别）

  [截图位置 2]：APP 登录成功截图，保存时间戳

【Step 2：撤销全部在途委托】
  → 底部「交易」→「当日委托」
  → 找到所有状态为「待成交」「部分成交」的委托
  → 点击「全撤」或逐笔点击「撤单」

  [截图位置 3]：撤单前「当日委托」列表截图
  [截图位置 4]：撤单操作确认截图

  等待 30–60 秒，确认撤单完成。

【Step 3：卖出全部持仓】
  → 底部「交易」→「持仓」
  → 对每个持仓股票：
      a. 点击股票名称
      b. 选择「卖出」
      c. 价格选择「市价」（或跌停时选「跌停价」）
      d. 数量选择「全部」
      e. 确认提交

  [截图位置 5]：每笔卖出操作截图（含股票代码、数量、价格）

  提示：若遇到涨跌停，按以下规则处理：
  - 跌停无法成交：挂跌停价排队，标注 pending_liquidation
  - 涨停（持仓涨停）：暂不卖出，次日竞价再卖

【Step 4：确认持仓清零】
  → 所有卖出操作完成后，刷新持仓列表
  → 持仓应全部为 0（或仅剩跌停无法成交的标的）

  [截图位置 6]：最终持仓清零截图，包含时间戳

【Step 5：事后补录 execution_ledger（PC 恢复后）】
  python scripts/manual_ledger_entry.py \
      --action manual_liquidation \
      --timestamp "YYYY-MM-DD HH:MM:SS" \
      --operator "solo" \
      --reason "券商APP手动清仓 SOP，xtquant 不可达" \
      --manual_operator_signature "solo_YYYYMMDD"
  
  # 手动录入每笔卖出交易，格式：
  # ts_code, qty, price, fill_time
```

**验证点**：
- 券商 APP 持仓列表全部清零（6张截图存档）
- 截图有时间戳可审计
- 事后 execution_ledger 已补录（`manual_operator_signature` 字段）

### 6.3 熔断后系统恢复流程

```
【系统处于 BREAK_GLASS 暂停态时，严禁直接恢复交易，须完成以下步骤】

Step 1：人工复盘
  - 分析触发熔断的原因
  - 评估是否满足恢复条件（参见 QS-C01 §7.1）：
    □ 净值创近期新高，或
    □ 回撤修复至 20% 预警线以上，或
    □ 固定冷静期结束（建议至少 1 个交易日）且非熊态

Step 2：RECONCILE 对账
  python scripts/run_reconcile.py --date today
  # 持仓对账通过（position_ledger C = 券商持仓）后才能进入下一步

Step 3：人工归还下单令牌
  python scripts/release_break_glass_token.py \
      --operator "solo" \
      --manual_operator_signature "solo_release_YYYYMMDD"
  
  # 系统状态：BREAK_GLASS → MANUAL_REVIEW → RECONCILE → IDLE

Step 4：恢复正常运营
  - 重启执行守护进程
  - 执行开盘前检查（§4）
  - 恢复后第一个完整交易日必须执行每日盘后巡检（§2）
```

---

## §7 PC 故障灾备恢复（T+1 目标）

**目标**：PC 故障后，在 T+1 交易日内恢复自动化交易（不超过一个交易日停摆）。

> **引用**：QS-C01 §14.5 灾备 SOP；QS-C01 §6 部署拓扑。

### 触发条件

- PC 无法开机（硬件故障）
- 操作系统崩溃无法修复
- 其他导致无法启动系统进程的故障

### 逐步操作

**立即响应（T+0，故障发生时）**

```
【发现故障】
  → 手机收到「进程失联」告警，或直接发现 PC 无响应

【立即动作（3分钟内）】
  Step A：打开手机券商 APP，查看持仓状态
  Step B：评估是否需要立即手动清仓：
    - 市场剧烈下跌（当日跌幅 > 3%）→ 执行 §6.2 手动清仓 SOP
    - 市场平稳 → 保持持仓，等待 PC 恢复
  Step C：记录故障发现时间和初步症状
```

**诊断与修复（T+0 当日，故障发现后 2–4 小时）**

```
【硬件诊断】
  □ 电源是否正常（电源线/UPS）
  □ 开机自检是否有报错（BIOS 告警音/屏幕提示）
  □ 是否可以进入 Windows 安全模式

【快速修复场景】
  - 蓝屏/内存错误 → 重启，若持续创建 Windows 修复盘
  - 硬盘问题 → 备用硬盘/恢复分区
  - 电源问题 → 更换电源或 UPS

【若当日无法修复】
  → 当日继续手机监控，保持持仓（或手动清仓，见 §6.2）
  → 准备备用机（如有）或 T+1 修复
```

**数据恢复（T+0 晚或 T+1 早）**

```
【从备份恢复数据库】
  Step 1：连接外置硬盘（备份盘）
    - 备份位置：D:\QuantSolo_Backup\（或外置盘对应路径）
    - 最新备份：latest_backup/ 目录

  Step 2：恢复 SQLite 数据库（execution_ledger）
    cp D:\QuantSolo_Backup\sqlite\execution_ledger_latest.db \
       C:\QuantSolo\data\execution_ledger.db
    
    # 验证恢复完整性
    python scripts/verify_db_integrity.py \
        --db C:\QuantSolo\data\execution_ledger.db
    # 期望输出：[OK] DB 完整性检查通过

  Step 3：恢复 DuckDB（行情/因子面板）
    # DuckDB 文件较大，可只恢复最近 30 天
    cp D:\QuantSolo_Backup\duckdb\market_latest.ddb \
       C:\QuantSolo\data\market.ddb
    
    # 若云盘备份更新（rclone）：
    rclone copy remote:quant_backup/duckdb/ C:\QuantSolo\data\duckdb\

  Step 4：恢复 Parquet 文件（因子面板）
    rclone copy remote:quant_backup/parquet/ C:\QuantSolo\data\parquet\
    # 或从外置盘：
    robocopy D:\QuantSolo_Backup\parquet\ C:\QuantSolo\data\parquet\ /MIR

  Step 5：恢复代码
    cd C:\QuantSolo
    git pull origin main
    # 或从远程仓库重新 clone
    git clone https://github.com/[用户名]/quant_solo.git
```

**系统重启验证（T+1 开盘前，9:00 前）**

```
Step 1：安装依赖（若新机器）
  pip install -r requirements.txt
  # 安装 xtquant（从 QMT 客户端复制）

Step 2：启动 QMT/迅投终端
  → 启动 QMT 客户端
  → 登录账户

Step 3：执行开盘前对账（RECONCILE）
  python scripts/pre_open_reconcile.py --date today
  # 对比 position_ledger C 与券商实时持仓
  # 期望：[OK] 持仓对齐

Step 4：执行 §4 开盘前检查

Step 5：若持仓存在差异
  → 手动修正 position_ledger（补录缺失的成交记录）
  → 创建 priority:high issue 记录灾备情况

Step 6：恢复自动交易
  → 启动执行守护进程
  → 启动监控告警进程
  → 确认 Server酱/钉钉告警推送正常
```

### 验证点

- T+1 开盘前完成系统恢复
- position_ledger C 与券商持仓一致（零差异）
- 五个进程全部正常运行
- 告警链路正常

### 回滚

若 T+1 仍无法完成恢复，继续手动监控持仓，操作升级为：
- 联系券商（国金证券客服），确认账户状态
- 评估是否需要继续手动平仓（视市场情况）
- 记录 issue，目标 T+2 前完成恢复

---

## §8 备份与恢复演练

### 触发条件

每周末（建议随每周对账复盘 §3 一并执行），约 15–20 分钟。

### 8.1 每日增量备份（自动，任务计划 17:30）

```bash
# 每日盘后增量备份脚本（Windows 任务计划调用）
# scripts/daily_backup.bat

@echo off
SET BACKUP_DATE=%DATE:~0,4%%DATE:~5,2%%DATE:~8,2%
SET BACKUP_ROOT=D:\QuantSolo_Backup

:: 1. SQLite 备份（execution_ledger + trade calendar）
copy C:\QuantSolo\data\execution_ledger.db ^
     %BACKUP_ROOT%\sqlite\execution_ledger_%BACKUP_DATE%.db

:: 2. DuckDB 备份
copy C:\QuantSolo\data\market.ddb ^
     %BACKUP_ROOT%\duckdb\market_%BACKUP_DATE%.ddb

:: 3. Parquet 增量同步
robocopy C:\QuantSolo\data\parquet\ ^
         %BACKUP_ROOT%\parquet\ /MIR /XO /LOG:%BACKUP_ROOT%\logs\backup_%BACKUP_DATE%.log

:: 4. 云盘同步（rclone）
rclone sync C:\QuantSolo\data\ remote:quant_backup\ ^
       --log-file=%BACKUP_ROOT%\logs\rclone_%BACKUP_DATE%.log

echo [%TIME%] 备份完成 >> %BACKUP_ROOT%\logs\backup_status.log
```

### 8.2 每周恢复演练（手动）

```bash
# 每周演练：从备份恢复最新 SQLite，验证可读性

Step 1：找到最新 SQLite 备份
  ls D:\QuantSolo_Backup\sqlite\ | sort -desc | head -1
  # 应为 execution_ledger_YYYYMMDD.db（最新）

Step 2：恢复到测试路径
  copy D:\QuantSolo_Backup\sqlite\execution_ledger_latest.db ^
       C:\QuantSolo\test\recovery_test.db

Step 3：验证可读性
  python scripts/verify_db_integrity.py --db C:\QuantSolo\test\recovery_test.db
  # 期望输出：
  # [OK] execution_ledger 表存在，记录数 XXXX
  # [OK] 15 态状态枚举约束通过
  # [OK] 最新记录日期：YYYY-MM-DD

Step 4：记录演练结果
  # 填写 acceptance_report/weekly/backup_drill_YYYYMMDD.md
  演练日期：YYYY-MM-DD
  备份文件：execution_ledger_YYYYMMDD.db
  恢复耗时：X 分钟
  验证结果：[OK] 可读 / [FAIL] 说明
  云盘备份状态：rclone 最后同步 YYYY-MM-DD HH:MM
```

### 验证点

- 每日备份文件存在（外置盘 + 云盘）
- 每周演练恢复可读性验证通过
- 备份日志无 ERROR
- rclone 云盘同步最近状态 ≤ 2 天前

### 回滚

| 问题 | 处置 |
|------|------|
| 备份脚本失败 | 检查磁盘空间（外置盘）；检查 rclone 配置；手动触发备份 |
| 恢复验证失败 | 尝试次新备份；检查数据库文件完整性（MD5 对比）|
| rclone 同步失败 | 检查网络连接；检查云存储账户配额；手动上传 |

---

## §9 程序化交易报备操作

> **引用**：QS-C01 §13 合规章；SSOT §4（合规义务清单）；《程序化交易管理实施细则》2025-07-07 施行。

### 9.1 开户时一次性操作

**触发条件**：开立国金证券账户时（仅执行一次）。

```
【操作步骤】
  Step 1：在开户流程中选择交易类型
    → 在「交易方式」或「账户类型」选项中
    → 如实勾选「程序化交易」
    → 如实填报：
       - 策略类型：多因子量化选股 + 趋势跟踪
       - 使用软件：迅投 QMT (miniQMT) + 自研 Python 量化框架
       - 最高申报速率：1 笔/秒（内部硬约束）
       - 单日最高申报笔数：200 笔/日

  Step 2：确认勾选完成
    → 保存开户资料截图
    → 截图位置：compliance/screenshots/opening_programmatic_YYYYMMDD.png

【合规底线提醒】
  只要用程序自动生成/下达指令，即属程序化交易。
  开户如实勾选是一次性动作，不如实填报才是监管风险。
```

### 9.2 首次程序化交易前报告

**触发条件**：首次执行程序化交易（M4 起步 5 万实盘）前，须完成报告。

```
【报告内容（向交易所/券商提交）】
  必须包含：
  □ 账户信息（账户号、资金情况）
  □ 资金规模：5 万元（起步）
  □ 策略类型：A 股中低频多因子量化选股
  □ 软件名称与版本：迅投 miniQMT + QuantSolo v2.0（自研）
  □ 最高申报速率：1 笔/秒（1/300 监管线）
  □ 单日最高申报笔数：200 笔/日（1/100 监管线）

【提交方式】
  → 登录国金证券 APP 或 PC 客户端
  → 联系客户经理（见 §12 联系人表），询问具体提交入口
  → 或通过交易所官方渠道：沪市（上交所会员服务平台）/ 深市（深交所会员报备系统）

【提交时间要求】
  先报告，后交易（不得在未报告前执行程序化交易）

【归档】
  → 保存报告回执截图：compliance/screenshots/first_report_YYYYMMDD.png
  → 在 research_ledger 记录：报告日期、报告编号（如有）
```

### 9.3 软件变更更新报备

**触发条件**：以下任一变更触发月度检查，重大变更立即报备。

| 变更类型 | 是否需要报备 | 说明 |
|---------|------------|------|
| 策略大幅调整（因子集/权重/风控参数重大变更）| 是 | 立即报备 |
| 软件版本升级（QMT 版本/QuantSolo 重大版本）| 是 | 升级前报备 |
| 最高申报速率变更 | 是 | 必须报备 |
| 日常 bug 修复/参数微调 | 否 | 月度汇总时记录 |

```
【月度合规检查（每月第一个周末）】
  Step 1：回顾本月代码变更
    git log --since="1 month ago" --oneline

  Step 2：判断是否触发报备义务
    □ 有策略逻辑变更 → 需报备
    □ 有软件版本升级 → 需报备
    □ 仅 bug 修复/参数微调 → 记录，不报备

  Step 3：若需报备
    → 准备变更说明材料（变更内容/变更日期/新软件版本）
    → 联系国金证券客户经理，询问更新报备流程
    → 报备完成后保存回执截图

  Step 4：记录合规日志
    # compliance/monthly_check_YYYYMM.md
    检查日期：YYYY-MM-DD
    本月代码变更：X 次（见 git log）
    是否触发报备：是/否
    报备内容（如有）：
    回执截图：compliance/screenshots/update_report_YYYYMM.png（如有）
```

---

## §10 月度/季度复盘模板

### 10.1 月度复盘（每月第一个周末，约 1–1.5h）

```markdown
# QuantSolo 月度复盘报告

**报告月份**：YYYY 年 MM 月
**复盘日期**：YYYY-MM-DD
**当前阶段**：M1 数据基建 / M2 研究执行 / M3 模拟盘 / M4 小实盘 / M4+ 积累

---

## 一、系统运行状况

| 指标 | 本月数值 | 目标/参考 | 状态 |
|------|---------|----------|------|
| 交易日总数 | X 天 | — | — |
| 清洁交易日 | X 天 | 全部清洁 | OK/需关注 |
| FLAG 天数 | X 天 | 本月 ≤ 3 天 | OK/需关注 |
| RESET 事件 | X 次 | 0 次 | OK/需修复 |
| 数据覆盖率均值 | X.XX% | ≥ 99% | OK/需关注 |
| 对账零差错连续天数 | X 天 | 积累中（B3 目标 4 周）| — |

## 二、策略观察（仅观察，不作放行依据）

| 指标 | 本月数值 | 回测参考 | 背离评估 |
|------|---------|---------|---------|
| 月度收益（模拟盘）| X.X% | — | 观察 |
| 实盘月度 IC 均值 | X.XXXX | 研究 IC = X.XXXX | OK/WATCH |
| 成本偏差（实测/建模）| +X.X% | ≤ +30% | OK/告警 |
| B2 维持判线状态 | 维持/跌破 | 研究IC-1.645×SE=X.XXXX | — |

## 三、本月主要事件

| 日期 | 事件 | 处置 | 结论 |
|------|------|------|------|
| YYYY-MM-DD | ... | ... | OK |

## 四、开放 issue 状态

| Issue # | 描述 | 优先级 | 状态 |
|---------|------|--------|------|
| #X | ... | P1 | 进行中 |

## 五、下月计划

1. ...（里程碑相关任务）
2. ...（待修复 issue）
3. ...（合规检查）

## 六、合规检查

- [ ] 月度代码变更回顾（§9.3）
- [ ] 报备义务评估（是否有重大变更）
```

### 10.2 季度复盘（每季度末，约 2–3h）

```markdown
# QuantSolo 季度复盘报告

**报告季度**：YYYY 年 QX（MM–MM）
**复盘日期**：YYYY-MM-DD

---

## 一、三个月里程碑进展

| 里程碑 | 目标完成时间 | 实际状态 | 说明 |
|--------|-----------|---------|------|
| M1 数据基建 | 第 1 月末 | 完成/进行中 | — |
| M2 研究+执行 | 第 2 月末 | 完成/进行中 | — |
| M3 模拟盘上线 | 第 3 月末 | 完成/进行中 | — |

## 二、B 类闸门进度（QS-CAL-001 数字）

| 闸门 | 当前进度 | 通过条件 | 预计时间 |
|------|---------|---------|---------|
| B1 观测窗 | X/26 周（含模拟盘Y周+实盘Z周）| ≥26周且其中≥13周真实5万 | 约 YYYY-MM |
| B2 维持判线 | IC均值=X > 判线Y | 滚动26周 IC>0 且>研究IC-1.645×SE | 终身滚动 |
| B3 工程判线 | 对账X周/4周；成本偏差Y% | 4周零差错 + 成本偏差≤30% + 风控一致 | — |

## 三、季度模型重训练（M4+ 阶段）

```bash
# 季度重训练（按 QS-C01 §12.5）
python scripts/quarterly_retrain.py \
    --data-window 5y \
    --validate-last 12m \
    --output models/lgbm_YYYYQX.pkl

# 验收条件：
# - 验证集夏普 > 0.6 且 > 当前线上模型 90%
# - 不通过 → 保留线上模型，人工审查
```

## 四、系统资产评估

本项目第一目标是构建可复用的系统资产与研究能力（SSOT §1）：

| 资产 | 当前状态 | 质量评估 |
|------|---------|---------|
| 点时数据管道 | 运行中/待完善 | 覆盖率X% |
| 因子库（15–20候选）| X个已注册 | IC通过X个 |
| 状态机+执行层 | 完成/待完善 | T1-T13 通过X个 |
| 对账体系 | 运行中 | 零差错周数X |

## 五、ROI 诚实评估

**本项目经济现实（引自 SSOT §1，不回避）：**
- 项目第一目标：系统资产与研究能力，前两年 ROI 大概率为负
- 本金保全（25% 硬止损）是不可妥协底线

| 指标 | 本季度实际 | 说明 |
|------|---------|------|
| 数据成本（Tushare Pro 等）| ¥XX | 年化约¥200 |
| 时间投入 | 约Xh（目标≤4h/周）| — |
| 模拟盘累计收益（观察）| X% | 不作放行依据 |

## 六、下季度计划

1. ...
```

---

## §11 B1/B2 闸门评估操作

> **引用**：QS-CAL-001《统计闸门校准报告 v1.0》（数字不得改动）；QS-C05 §八.2 B 类闸门进度追踪。
> **注意**：B 类闸门数字的唯一权威来源是 QS-CAL-001，本手册只引用结论，不重抄推导。

### 11.1 何时进行 B1 闸门正式评估

| 条件 | 说明 |
|------|------|
| 模拟盘 + 5 万实盘累计 ≥ 26 周 | 26 周为物理下限，不可压缩（QS-CAL-001 B1 判线）|
| 其中至少 13 周来自真实 5 万实盘 | 模拟盘 IC 单独计，实盘 IC 单独计（is_paper_trade 字段）|
| B3 工程判线同时满足 | 成本偏差 ≤+30%、对账4周零差错、风控一致 |
| **B1+B3 同时满足** | 才可 5 万→20 万加仓 |

### 11.2 如何取数计算 B1 判线（按 QS-CAL-001 数字）

**Step 1：取出实盘 26 周 rank-IC 序列**

```python
# scripts/evaluate_b1_gate.py
import duckdb
import pandas as pd
from scipy import stats

def get_live_ic_series(as_of_date: str, n_weeks: int = 26) -> pd.Series:
    """
    取出 B1 评估所需的 rank-IC 序列
    注意：模拟盘（is_paper_trade=True）和实盘（is_paper_trade=False）分列
    """
    con = duckdb.connect("C:/QuantSolo/data/market.ddb")
    
    # 从 realized_ic.csv（反哺文件汇总）读取
    ic_data = pd.read_csv("acceptance_report/feedback/realized_ic_all.csv",
                          parse_dates=["week_end"])
    
    # 筛选最近 26 周
    ic_data = ic_data[ic_data["week_end"] <= as_of_date].tail(n_weeks)
    
    print(f"总样本周数：{len(ic_data)}")
    print(f"  模拟盘周数：{ic_data['is_paper_trade'].sum()}")
    print(f"  真实实盘周数：{(~ic_data['is_paper_trade']).sum()}")
    
    return ic_data["rank_ic"]
```

**Step 2：计算 rank-IC 均值与研究 IC 的比较**

```python
def evaluate_b1(as_of_date: str):
    """
    B1 加仓判线评估（引用 QS-CAL-001 §三）
    
    B1 判线：实测周度 rank-IC 均值 > 研究IC − 1.0×SE
    其中 SE = 研究期 IC 序列的均值标准误
    """
    # 研究期 IC 均值和 SE（从研究记录获取，数字来自 QS-CAL-001）
    research_ic_mean = float(open("research/research_ic_mean.txt").read().strip())
    research_ic_se = float(open("research/research_ic_se.txt").read().strip())
    
    b1_threshold = research_ic_mean - 1.0 * research_ic_se
    
    # 实盘 IC 序列
    live_ic = get_live_ic_series(as_of_date, n_weeks=26)
    live_ic_mean = live_ic.mean()
    
    # 额外检查：至少 13 周来自真实实盘
    ic_data = pd.read_csv("acceptance_report/feedback/realized_ic_all.csv",
                          parse_dates=["week_end"])
    ic_data = ic_data[ic_data["week_end"] <= as_of_date].tail(26)
    real_weeks = (~ic_data["is_paper_trade"]).sum()
    
    print(f"\n===== B1 闸门评估（{as_of_date}）=====")
    print(f"研究 IC 均值：{research_ic_mean:.4f}")
    print(f"研究 IC SE：{research_ic_se:.4f}")
    print(f"B1 判线阈值：研究IC - 1.0×SE = {b1_threshold:.4f}")
    print(f"实测 26 周 rank-IC 均值：{live_ic_mean:.4f}")
    print(f"真实实盘周数：{real_weeks}/26（加仓需 ≥13）")
    print(f"\nB1 判线结论：{'✓ 通过' if live_ic_mean > b1_threshold else '✗ 未通过'}")
    print(f"真实实盘周数条件：{'✓ 满足' if real_weeks >= 13 else f'✗ 不满足（差 {13-real_weeks} 周）'}")
    
    return {
        "b1_pass": live_ic_mean > b1_threshold,
        "real_weeks_ok": real_weeks >= 13,
        "live_ic_mean": live_ic_mean,
        "b1_threshold": b1_threshold,
    }

# 执行
result = evaluate_b1("2027-MM-DD")  # 填实际日期
```

### 11.3 B2 维持判线（终身滚动）每周评估

```python
def evaluate_b2(as_of_date: str):
    """
    B2 维持判线（终身滚动，引用 QS-CAL-001 §三）
    
    B2 判线：滚动26周 IC 均值 > 研究IC − 1.645×SE 且 > 0
    跌破 → 降仓复盘
    """
    research_ic_mean = float(open("research/research_ic_mean.txt").read().strip())
    research_ic_se = float(open("research/research_ic_se.txt").read().strip())
    
    b2_threshold = research_ic_mean - 1.645 * research_ic_se
    b2_threshold_effective = max(b2_threshold, 0.0)  # 同时要求 > 0
    
    live_ic = get_live_ic_series(as_of_date, n_weeks=26)
    live_ic_mean = live_ic.mean()
    
    b2_pass = (live_ic_mean > b2_threshold) and (live_ic_mean > 0)
    
    print(f"\n===== B2 维持判线（{as_of_date}）=====")
    print(f"B2 判线阈值：max(研究IC - 1.645×SE, 0) = {b2_threshold_effective:.4f}")
    print(f"实测滚动26周 IC 均值：{live_ic_mean:.4f}")
    print(f"\nB2 判线结论：{'✓ 维持' if b2_pass else '✗ 跌破 → 降仓复盘'}")
    
    if not b2_pass:
        print("\n【降仓复盘触发】")
        print("1. 减仓至当前仓位的 50%（先卖卫星仓）")
        print("2. 分析 IC 下降原因（因子衰减/市场 regime 切换）")
        print("3. 若持续 3 个月负 IC → 考虑因子退役（QS-C01 §15.2 N=3 退役规则）")
    
    return {"b2_pass": b2_pass, "live_ic_mean": live_ic_mean}

# 每周评估脚本（加入每周对账复盘 §3）
```

### 11.4 B3 工程判线检查

```python
def evaluate_b3(as_of_date: str):
    """
    B3 工程判线（QS-CAL-001 §三）
    - 成本偏差 ≤ +30%（实测/建模）
    - 对账连续 4 周零差错
    - 风控触发行为与 QS-C04 状态机一致
    """
    # 从每周对账数据读取
    weekly = pd.read_csv("acceptance_report/weekly/reconcile_summary.csv",
                         parse_dates=["week_end"])
    latest_4w = weekly.tail(4)
    
    # 成本偏差（取最近 4 周均值）
    cost_deviation = latest_4w["cost_deviation_pct"].mean()
    cost_ok = cost_deviation <= 30.0
    
    # 对账零差错（最近 4 周全部 clean）
    recon_ok = (latest_4w["is_clean_week"] == True).all()
    clean_weeks = latest_4w["is_clean_week"].sum()
    
    # 风控一致性（order_remark 反查命中率）
    order_remark_rate = latest_4w["order_remark_hit_rate"].mean()
    risk_ok = order_remark_rate >= 0.95
    
    print(f"\n===== B3 工程判线（{as_of_date}）=====")
    print(f"成本偏差（最近4周均值）：+{cost_deviation:.1f}%（≤+30% OK）：{'✓' if cost_ok else '✗'}")
    print(f"对账零差错（最近4周）：{clean_weeks}/4 周（需全4周）：{'✓' if recon_ok else '✗'}")
    print(f"order_remark 命中率：{order_remark_rate:.1%}（≥95% OK）：{'✓' if risk_ok else '✗'}")
    
    b3_pass = cost_ok and recon_ok and risk_ok
    print(f"\nB3 工程判线：{'✓ 通过' if b3_pass else '✗ 未通过'}")
    
    return {"b3_pass": b3_pass}
```

---

## §12 联系人与资源表

### 12.1 券商联系人（占位，开户后填写）

| 机构 | 角色 | 联系方式 | 适用场景 |
|------|------|---------|---------|
| 国金证券（主力）| 开户客户经理 | [待填写：客户经理姓名/手机] | miniQMT 开通、报备问题、账户异常 |
| 国金证券 | 客服热线 | [待填写：官方客服电话，可在 APP 中查找] | 账户问题、委托查询 |
| 华泰证券（备选）| 客服热线 | [待填写] | 备用账户 |
| 中信证券（备选）| 客服热线 | [待填写] | 备用账户 |

### 12.2 数据源文档链接

| 数据源 | 文档/官方地址 | 说明 |
|--------|-------------|------|
| AKShare | https://akshare.akfun.cn/data/stock/stock_history.html | 主采集源文档 |
| Tushare Pro | https://tushare.pro/document/2 | 积分档 API 文档 |
| BaoStock | http://baostock.com/baostock/index.php | 冗余校验源 |
| xtquant（迅投）| [从 QMT 客户端内下载，联系客户经理获取文档链接] | 执行层 SDK |
| Server酱（告警）| https://sct.ftqq.com/ | 手机推送告警配置 |
| 钉钉机器人 | https://open.dingtalk.com/document/robots/custom-robot-access | 告警推送备选 |
| rclone（备份）| https://rclone.org/docs/ | 云盘同步工具 |

### 12.3 监管文档

| 文档 | 链接/说明 |
|------|---------|
| 《程序化交易管理实施细则》| 2025-07-07 施行，向交易所/券商查询最新版本 |
| 上交所程序化交易报备 | 通过券商客户经理了解入口 |
| 深交所程序化交易报备 | 通过券商客户经理了解入口 |

### 12.4 系统内部资源

| 资源 | 路径/位置 |
|------|---------|
| 监控看板 | http://localhost:8501（Streamlit，本机访问）|
| 主日志 | C:\QuantSolo\logs\ |
| 对账报告 | C:\QuantSolo\acceptance_report\ |
| 研究日志 | C:\QuantSolo\research_ledger.db |
| 备份目录 | D:\QuantSolo_Backup\（外置盘）|
| 云端备份 | remote:quant_backup\（rclone 配置）|
| 代码仓库 | https://github.com/[用户名]/quant_solo（远程）|
| break_glass 脚本 | C:\QuantSolo\scripts\break_glass.py |

---

## §13 术语速查

| 术语 | 含义 | 引用来源 |
|------|------|---------|
| **visible_at** | 数据在某交易日可被策略合法使用的时间戳；点时正确性核心字段 | QS-C03 |
| **position_ledger C** | 由成交事件流推导的理论持仓（对账真值）；`prior_position + cumulative_fills - corporate_action_delta` | QS-C04 §7.2 |
| **clean_week**（清洁交易周）| 该周每日日终对账零未解释差异（FLAG 标记日不计入）| QS-C05 §1.1 |
| **RESET** | 模拟盘计时归零；触发条件：系统行为错误（重复成交/盲目下单/超卖/越限提交）或代码 bug | QS-C05 §〇 |
| **FLAG** | 计时继续但当日打标记；触发条件：单次环境异常且系统行为正确（已记录 RCA） | QS-C05 §〇 |
| **BREAK_GLASS** | 物理一键熔断暂停态（15 态之一）；独立进程直接 xtquant 撤单+市价平仓，令牌单向 | QS-C04 §五 |
| **break-glass SOP** | 券商 APP 手动清仓操作程序；xtquant 不可达时的兜底路径 | QS-C04 §5.2 |
| **UNKNOWN** | 查不到券商状态/连接异常态；悲观默认：连续两次查询一致才归位 | QS-C04 §一 |
| **cancel_fill_type** | 撤单终态细分：NONE/FULL/PARTIAL；区分正常撤单/撤后全成/撤后部成 | QS-C04 §1.2 |
| **halt_reason** | 暂停原因字段：BREAK_GLASS/WIND_CTRL_LV2/MANUAL_REVIEW/REJECT_BREAKER | QS-C04 §1.2 |
| **order_remark** | xtquant 下单 `order_remark` 参数，写入 client_order_id，用于对账反查 | QS-C04 §八 |
| **outbox 三态** | NOT_SENT_CAN_SEND / MAYBE_SENT_UNKNOWN / SENT_CONFIRMED；防崩溃重发误重发 | QS-C04 §4.3 |
| **rank-IC** | 截面收益排名与因子值排名的相关系数；B 类闸门核心统计量 | QS-C01 附录 A |
| **SE**（标准误）| IC 序列的均值标准误；B1/B2 判线计算所需 | QS-CAL-001 |
| **N_eff** | 等价独立试验次数；全生命周期封顶 6 次 test 评估 | QS-CAL-001 / SSOT §2 |
| **B1 判线** | 加仓门槛：模拟盘+实盘累计 ≥26 周，实测 rank-IC 均值 > 研究IC−1.0×SE；加仓前 ≥13 周真实实盘 | QS-CAL-001 |
| **B2 判线** | 维持判线：滚动 26 周 IC 均值 > 研究IC−1.645×SE 且 >0；跌破→降仓复盘 | QS-CAL-001 |
| **B3 判线** | 工程判线：成本偏差 ≤+30%、对账连续4周零差错、风控触发行为与状态机一致 | QS-CAL-001 |
| **A1 硬否决** | test 段扣成本年化夏普 ≤0 → 不上模拟盘（一票否决）| QS-CAL-001 |
| **A2 弱否决** | 合并段 DSR < 0.5 → 降级路径（实盘起步 2.5 万，观察窗延至 26 周全实盘）| QS-CAL-001 |
| **trigger_ic_audit** | 背离触发词；唯一定义：见 QS-C02 §七 bootstrap CI 口径（滚动26周实测IC均值落在研究期 IC bootstrap 90% CI 下界之外） | QS-C02 §七 |
| **miniQMT** | 迅投 QMT 轻量版；国金证券入金 10 万保持 3 个月后永久开通 | QS-C01 §10.2 |
| **M1/M2/M3** | 三个月上线里程碑：M1 数据基建 / M2 研究+执行 / M3 模拟盘全链路上线 | QS-C01 §16 |
| **PASS_ENGINEERING** | 模拟盘工程验收通过；≠ 策略已验证；B 类闸门最终裁决 | QS-C05 §1.2 |
| **is_paper_trade** | execution_ledger 字段；True=模拟盘 IC，False=真实实盘 IC；B1 观测窗须分列 | QS-C05 §1.1 |
| **cost_model_id** | 回测成本模型标识；`cm_v3_baseline`（第2月）vs `cm_v3_advanced`（第3月起）| QS-C01 §6.4 |
| **宪法修订流程** | 单人版：创建 constitutional-amendment issue → 说明原因 → 记录 research_ledger → 更新文档 → 全量回归 | QS-E04 §7.4 |

---

*本文档为 QuantSolo v2.0 工程文档体系 QS-E05，v1.0 初版。冻结后变更须版本号递增并记录于 research_ledger。与 SSOT（baseline_spec.md）冲突时以 SSOT 为准，已全文校验。*

---

**文档编号：QS-E05 | 版本：v1.0 | 日期：2026-06-12 | 与 SSOT (baseline_spec.md) 冲突时以 SSOT 为准，已全文校验。**
