# QuantSolo — 项目测试验收方案 v1.0

---

| 字段 | 内容 |
|------|------|
| **文档编号** | QS-E04 |
| **版本** | v1.0 |
| **日期** | 2026-06-12 |
| **状态** | 正式发布 |
| **上游依赖** | QS-C01《系统设计文档 v5.0》· QS-C03《点时数据契约 v2.0》· QS-C04《执行与风控状态机 v1.3》· QS-C05《模拟盘验收手册 v2.0》· baseline_spec.md（SSOT） |
| **下游文档** | QS-E05《执行行动指导手册 v1.0》· QS-E06《项目实施计划与里程碑》 |
| **冲突裁决** | 与 SSOT（baseline_spec.md）冲突时以 SSOT 为准；已全文校验。 |

---

## 版本演进表

| 版本 | 日期 | 主要变更 |
|------|------|---------|
| **v1.0** | **2026-06-12** | **初版全文；覆盖测试金字塔四层、M1/M2/M3 验收清单、回归策略、缺陷管理、测试数据管理** |

---

## 目录

- [§1 测试哲学与范围](#1-测试哲学与范围)
- [§2 测试金字塔（四层）](#2-测试金字塔)
- [§3 单元测试层](#3-单元测试层)
- [§4 集成测试层](#4-集成测试层)
- [§5 系统测试层（模拟盘全链路）](#5-系统测试层)
- [§6 验收测试层（M1/M2/M3 里程碑）](#6-验收测试层)
- [§7 回归策略](#7-回归策略)
- [§8 缺陷管理（单人版 issue 标签法）](#8-缺陷管理)
- [§9 测试数据管理](#9-测试数据管理)
- [§10 测试环境与工具链](#10-测试环境与工具链)
- [§11 测试报告与归档](#11-测试报告与归档)
- [附录 A：pytest 13 场景清单](#附录a)
- [附录 B：交叉引用索引](#附录b)

---

## §1 测试哲学与范围

### 1.1 测试定位

本方案服务于 QuantSolo 一人公司 A 股量化系统的**工程正确性验证**，而非策略盈利验证。核心测试命题是：

- **点时正确性**：数据按 visible_at 重放，无未来函数泄漏（铁律三，QS-C01 §1）
- **风控凌驾策略**：下单唯一入口必经风控守卫，守卫旁路不可达（铁律一，QS-C01 §1）
- **状态机确定性**：15 个运行态迁移可测试、可审计（QS-C04 §一唯一口径）
- **对账零差错**：position_ledger C 口径对账连续 4 周零差错（QS-CAL-001 B3 判线）
- **异常演练可证**：T1–T13 用例全 PASS，压力注入行为符合 QS-C04 §6.3 汇总表

### 1.2 测试范围边界

| 范围 | 说明 |
|------|------|
| **范围内** | 数据管道正确性、因子计算可复现性、执行状态机行为、风控守卫、对账系统、告警链路、break-glass 路径 |
| **范围外** | 策略盈利性（由统计闸门 A1/A2 及 B 类行为闸门裁决，不属于工程测试范畴）|
| **范围外** | 回测 Alpha 真实性（由 QS-C02 研究协议负责）|

### 1.3 五条铁律对测试的约束

测试设计必须与 QS-C01 §1 五条铁律保持一致：

- **铁律一约束**：风控守卫旁路不可达测试是强制项，不得以任何理由豁免
- **铁律二约束**：测试数据管理必须遵循样本冻结规则，测试用例不得使用 test 段实际数据
- **铁律三约束**：所有基于数据的单元测试必须使用 visible_at 重放接口，严禁使用事后复权数据
- **铁律五约束**：测试结果须写入审计日志，自动化测试不替代每日人工巡检义务

---

## §2 测试金字塔

### 2.1 四层架构

```
                    ┌──────────────────┐
                    │  验收测试（UAT）   │  M1/M2/M3 里程碑验收清单
                    │  里程碑放行门禁   │  人工+自动化
                    └──────────────────┘
                  ┌────────────────────────┐
                  │    系统测试（E2E）      │  模拟盘全链路
                  │  数据→信号→风控→执行   │  QMT 模拟盘 + 全链路日终对账
                  └────────────────────────┘
            ┌──────────────────────────────────┐
            │       集成测试                    │  盘后管道端到端
            │  盘后管道 + 执行链路模拟           │  执行链路模拟（MockBroker）
            └──────────────────────────────────┘
      ┌──────────────────────────────────────────────┐
      │               单元测试（底座）                 │  pytest 13 场景（点时契约）
      │  点时契约 · 因子计算 · 状态机迁移 · 风控守卫   │  + 状态机 T1-T13 + 专项
      └──────────────────────────────────────────────┘
```

### 2.2 各层职责与覆盖目标

| 层级 | 职责 | 主要工具 | 覆盖目标 |
|------|------|---------|---------|
| **单元测试** | 最小可测试单元；点时 13 场景为必测底座 | pytest, unittest.mock | 行覆盖率 ≥ 80%；点时 13 场景 100% 通过 |
| **集成测试** | 模块间协作；盘后管道端到端；MockBroker 执行链路 | pytest + MockBrokerAdapter | 核心管道无缺失、执行链路状态转换正确 |
| **系统测试** | 全链路闭环（含 QMT 模拟盘）；告警推送 | QMT 模拟盘 + watchdog | M3 全链路上线验收清单（QS-C05 §七）|
| **验收测试** | 里程碑放行门禁 | 人工核查 + CI 报告 | M1/M2/M3 验收用例表全 PASS |

### 2.3 测试密度分配原则

- 底座（单元）最厚：风控守卫、点时重放、状态机迁移 —— 每个分支都有对应用例
- 中层（集成）适中：关键数据流和执行链路的端到端路径
- 顶层（系统/验收）最薄：全链路冒烟确认，不替代底层覆盖

---

## §3 单元测试层

### 3.1 测试目录结构

```
tests/
├── contract/           # 点时契约 13 场景（QS-C03 v2.0 唯一口径）
├── state_machine/      # 状态机迁移矩阵（15 态）
├── ledger/             # execution_ledger DDL 约束
├── outbox/             # outbox 三态恢复
├── signature/          # 签名重放防护
├── reconcile/          # 对账差异分类（五分类）
├── risk_guard/         # 风控守卫单测（含旁路不可达）
├── factor/             # 因子计算纯函数
└── conftest.py         # 共享 Fixture（visible_at 注入、MockBroker）
```

### 3.2 点时契约 13 场景（单元测试底座）

> **唯一口径**：以下 13 个 visible_at 重放场景来自 QS-C03《点时数据契约 v2.0》，是 pytest 回归套件强制放行项（QS-C05 §1.5）。场景编号 PT-01 至 PT-13。

| 场景编号 | 场景名称 | 测试目标 | 关键断言 |
|---------|---------|---------|---------|
| PT-01 | 基础 visible_at 过滤 | 只取 visible_at ≤ 当日 17:00 的记录 | 事后记录不出现在结果集 |
| PT-02 | 修改追加（ACTIVE） | 修改 = 追加新行 ACTIVE，旧行保留 | canonical 排序后取最新 ACTIVE |
| PT-03 | 撤销追加（VOIDED） | 撤销 = 追加 VOIDED，原 ACTIVE 不物理删除 | VOIDED 行不参与因子计算 |
| PT-04 | canonical 四键排序 | visible_at DESC, revision_seq DESC, snapshot_rank DESC, record_id DESC | 排序结果唯一确定 |
| PT-05 | 盘后 17:00 时间戳规则 | 盘后采集统一打 17:00 visible_at | 采集时间偏差 < 1s 不影响可见性 |
| PT-06 | 财报 PIT 保守处理 | 财报 visible_at = 公告日 + 1 交易日 | 公告当日不可见 |
| PT-07 | 新闻时间窗口归属 | t-1日 15:00 ~ t 日 15:00 → 归 t+1 交易日 | 边界案例：恰好 15:00 归哪日 |
| PT-08 | 多源数据冲突（三源两票制） | AKShare/Tushare/BaoStock 三源，两票裁决 | 冲突时取多数票，单一来源被覆盖 |
| PT-09 | adj_factor_pit 大表年分区 | 按年分区 Parquet 读取复权因子 | 跨年查询合并正确，无重叠 |
| PT-10 | 停复牌标记点时 | 停牌期间 visible_at 窗口内不生成信号 | 停牌股不出现在 target_weight 中 |
| PT-11 | ST 状态历史追踪 | ST 状态变更按 visible_at 追踪，不用当前状态回溯 | 退 ST 当日之前仍按 ST 过滤 |
| PT-12 | 接口六字段完整性 | visible_at, ingested_at, source, snapshot_id, calendar_version, corp_action_known_at | 任一字段缺失则抛出 PointInTimeError |
| PT-13 | 未来函数检测（防泄漏） | 注入未来日期数据，断言不可见 | 研究管道拒绝未来 visible_at 数据 |

**执行命令：**

```bash
pytest tests/contract -v --tb=short -q
# 通过标准：13/13 PASSED；行覆盖率 ≥ 80%（tests/contract 模块）
```

### 3.3 状态机单元测试

**覆盖目标**：15 个运行态的全部合法迁移 + 非法迁移拒绝。

| 测试集 | 测试场景数 | 说明 |
|--------|----------|------|
| 正常调仓主链路 | IDLE→TARGET_GEN→...→RECONCILE→IDLE | 至少 1 个完整路径用例 |
| 各态的非法迁移 | 每态至少 2 个非法事件 | 断言 InvalidStateTransition 异常 |
| UNKNOWN 归位 | 5 个时序组合（立即/+1s/+5s/+10s/两次不一致） | 连续两次一致才归位 |
| cancel_fill_type 三分支 | NONE/FULL/PARTIAL 各 1 个 | position_ledger 更新正确 |
| halt_reason 四类型 | BREAK_GLASS/WIND_CTRL_LV2/MANUAL_REVIEW/REJECT_BREAKER | 恢复路径各自正确 |
| EOD 四类事件 | LIVE(DAY)/LIVE(GTC)/PARTIAL/ORDER_SIZING | 各自正确处置 |
| 连续拒单断路器 | 5min 内 3 笔 REJECTED 触发 | halt_reason=REJECT_BREAKER |

**风控守卫旁路不可达测试（必测，不可豁免）：**

```python
# tests/risk_guard/test_bypass_impossible.py
def test_risk_guard_cannot_be_bypassed():
    """铁律一：下单唯一入口必经守卫，守卫旁路路径断言不可达"""
    # 1. 遍历策略层所有模块，断言无 import xtquant
    for module_path in glob("strategy/**/*.py"):
        source = Path(module_path).read_text()
        assert "import xtquant" not in source, f"策略层 {module_path} 直接导入 xtquant，违反铁律一"
    # 2. 断言下单调用必须通过 place_order() → risk_guard()
    with patch("execution.place_order") as mock_place:
        strategy.generate_orders(test_signal)
        for call in mock_place.call_args_list:
            assert call.kwargs.get("via_risk_guard") is True
    # 3. 断言 risk_guard 无 skip_check 路径
    assert not hasattr(risk_guard, "__skip_check__"), "risk_guard 存在旁路属性"
```

### 3.4 执行层单元测试（15 态全覆盖）

**每个运行态至少有一个单元测试场景：**

```python
# tests/state_machine/test_all_15_states.py
REQUIRED_STATES = [
    "IDLE", "TARGET_GEN", "RISK_CLIP", "ORDER_SIZING", "ORDER_INTENT",
    "PRE_FIRE_CHECK", "SUBMITTED", "LIVE", "PARTIAL", "CANCEL_REQUESTED",
    "FILLED", "CANCELLED", "REJECTED", "UNKNOWN", "RECONCILE"
]

@pytest.mark.parametrize("state", REQUIRED_STATES)
def test_state_is_reachable(state, mock_execution_engine):
    """验证 15 个运行态每个都能在测试中到达"""
    result = mock_execution_engine.reach_state(state)
    assert result.current_state == state
```

### 3.5 因子计算单元测试

| 测试项 | 测试目标 |
|--------|---------|
| MAD 去极值 | 边界：全同值序列、单一极值 |
| 市值/行业中性化 | 残差均值近零（< 1e-10）；不改变排名方向 |
| z-score 标准化 | 输出均值=0、标准差=1；NaN 处理 |
| visible_at 注入 | 因子计算函数必须接受并传递 visible_at 参数 |
| 因子变体三枚举 | processed/orthogonal/raw 三个函数输出格式一致 |
| BH-FDR 多重检验 | 对已知 p 值序列，验证 BH 临界值计算正确 |

---

## §4 集成测试层

### 4.1 盘后数据管道端到端测试

**测试目标**：AKShare/Tushare/BaoStock 三源采集→清洗→入库→三源两票裁决→visible_at 打戳→DuckDB/SQLite 落库的完整管道。

**用例设计：**

| 用例编号 | 场景 | 输入 | 期望输出 |
|---------|------|------|---------|
| INT-P-01 | 正常日数据入库 | 模拟三源数据（行情+龙虎榜+资金流） | 落库记录数正确，visible_at = 当日 17:00 ±60s |
| INT-P-02 | 三源冲突裁决 | AKShare/BaoStock 一致，Tushare 不同 | 多数票选 AKShare/BaoStock 值 |
| INT-P-03 | 数据源单源失败 | AKShare 超时返回空 | Tushare+BaoStock 两票，告警记录，不阻断落库 |
| INT-P-04 | 覆盖率质检 | 注入 0.5% 缺失率行情 | 质检告警触发（阈值 < 0.5%）|
| INT-P-05 | 财报 PIT 入库 | 财报公告日 T，入库时间 T 当日 | 财报 visible_at = T+1 交易日 |
| INT-P-06 | 复权因子年分区 | 跨年查询 adj_factor_pit | 结果与单年查询合并一致 |
| INT-P-07 | 停复牌事件入库 | 停牌股次日 visible_at | 停牌期间不生成信号 |

**执行方式：**

```bash
pytest tests/integration/test_pipeline.py -v -m "not slow"
# 完整管道测试：
pytest tests/integration/test_pipeline.py -v --run-slow
# 通过标准：所有用例 PASS；无未解释的 data_cut_id 不一致
```

### 4.2 执行链路集成测试（MockBrokerAdapter）

**测试原则**：使用依赖注入替换 `BrokerAdapter → MockBrokerAdapter`，状态机代码本身不感知 mock/real 切换（与 QS-C05 §三演练注入机制一致）。

**关键用例：**

| 用例编号 | 场景 | MockBroker 注入 | 验证目标 |
|---------|------|---------------|---------|
| INT-E-01 | 完整调仓周期 | 正常受理+成交回报 | execution_ledger 完整记录 15 态迁移 |
| INT-E-02 | 部分成交自愈 | 部分回报→下周期不补单 | position_ledger C 口径正确，缺口下周自愈 |
| INT-E-03 | UNKNOWN 归位 | 超时→双查一致 | UNKNOWN→FILLED，无重复提交 |
| INT-E-04 | outbox 崩溃恢复 | send_started 未 committed 时 kill | MAYBE_SENT_UNKNOWN 不重发 |
| INT-E-05 | 幂等键去重 | 同一 client_order_id 提交两次 | DB UNIQUE 约束触发，第二次 no-op |
| INT-E-06 | 合规限速 | 超过 1 笔/秒注入 | 排队延迟 ≥ 1000ms，不拒单 |
| INT-E-07 | 超日上限 201 笔 | 注入第 201 笔申报 | 暂停 MANUAL_REVIEW + 告警 |
| INT-E-08 | 风控守卫裁剪 | 超单票 8% 仓位 | RISK_CLIP 裁减至 8%，不拒单 |
| INT-E-09 | 二级回撤清仓 | 注入账户净值回撤 25% | 尽力清仓+冻结，系统续运行对账 |
| INT-E-10 | EOD 未提交意图作废 | 14:55 后 ORDER_SIZING 未完成 | 意图作废+释放 reservation |

**执行命令：**

```bash
pytest tests/integration/test_execution_chain.py -v \
    --mock-broker=MockBrokerAdapter
```

### 4.3 对账系统集成测试

| 用例编号 | 场景 | 测试目标 |
|---------|------|---------|
| INT-R-01 | 正常日终对账 | position_ledger C = 券商持仓，对账报告生成 |
| INT-R-02 | 除权处理 | corporate_action_ledger 同推 position+cash，不误触暂停 |
| INT-R-03 | 零股单独建账 | 零股不进零容忍对账，单独建账 |
| INT-R-04 | 现金尾差容忍 | ≤1.00 元自动通过 |
| INT-R-05 | 无法解释差异 | 股数差 > 0 → 暂停 MANUAL_REVIEW |
| INT-R-06 | order_remark 反查 | 命中率统计，误匹配率 = 0 断言 |

---

## §5 系统测试层

### 5.1 模拟盘全链路测试（M3 里程碑）

**测试范围**：数据→信号→风控→模拟执行→对账→告警，完整闭环，使用 QMT 模拟盘账户，非真实资金。

**系统测试通过标准**：QS-C05 §七 M3 全链路上线验收清单全部打勾。

| 验收项 | 测试方式 | 通过标准 |
|--------|---------|---------|
| 数据管道自动化 | 任务计划 17:00 触发，观察日志 | 三源两票裁决无人工干预，新鲜度 ≤ 盘后 2h |
| 点时契约 | pytest tests/contract（13 场景）| 全绿 |
| 信号生成 | 盘后运行因子→target_weight，检查 visible_at | 14:55 前完成，无未来函数警告 |
| 风控层唯一入口 | 检查 execution_ledger command_signature 字段 | 所有下单行均有合法签名 |
| 模拟执行 | QMT 模拟盘空跑，观察委托回报 | 状态机迁移日志无 RESET 类错误 |
| 对账系统 | 日终对账报告自动生成 | position_ledger C = 券商持仓，零差异 |
| 监控告警 | 手动注入测试告警事件 | Server酱/钉钉机器人推送到手机，≤ 60s 延迟 |
| 反哺链路 | 检查 realized_ic.csv 等每日文件 | schema 校验通过，日志无 schema error |

### 5.2 告警链路端到端测试

**测试方法**：直接调用告警触发函数（不依赖真实异常发生），验证推送链路通畅。

| 告警类型 | 测试操作 | 验证点 |
|---------|---------|--------|
| 风控触发（20% 回撤） | mock_trigger_risk_alert("WIND_CTRL_LV1") | 手机收到推送，内容含触发阈值 |
| 下单失败 | mock_trigger_order_fail("REJECTED") | 推送内容含 ts_code 和 error_code |
| 数据管道失败 | mock_trigger_pipeline_fail("AKShare") | 推送内容含失败数据源名称 |
| 进程失联 | 临时停止执行守护进程 | ≤ 3 个心跳丢失间隔（45s）内告警 |
| UPS 切换 | 模拟 UPS 电池事件信号 | 推送内容含「UPS 切换至电池」 |

### 5.3 冒烟测试（每次代码变更后）

```bash
# 全链路冒烟测试（非交易时段运行）
python scripts/smoke_test_e2e.py \
    --mode=mock \
    --date=last_trade_day \
    --assert-no-future-leak \
    --assert-risk-guard-signed

# 通过标准：
# - 数据→因子→信号→风控→模拟下单全链路无异常
# - 点时契约 13 场景全绿
# - 所有下单带 command_signature
# - 无 xtquant 出现在 strategy/ 目录
```

---

## §6 验收测试层

### 6.1 M1 验收：数据基建完成

**验收日期目标**：第 1 月末（约第 4 周，见 QS-C01 §16.2）

#### M1 验收前置条件

- [ ] AKShare/Tushare Pro/BaoStock 三源管道代码开发完成
- [ ] 点时契约 9 表 DDL 已建
- [ ] 国金账户开户完成，入金 10 万（miniQMT 计时启动）

#### M1 验收用例表

| 用例编号 | 验收项目 | 验收方式 | 通过标准 |
|---------|---------|---------|---------|
| M1-001 | 三源管道数据入库 | 查询最新数据日期 | AKShare/Tushare/BaoStock 三源当日数据均已入库，visible_at 时间戳正确 |
| M1-002 | 行情覆盖率质检 | 运行数据质检脚本 | 每交易日 ≥ 99% 股票有日线数据 |
| M1-003 | 复权因子完整性 | 检查 adj_factor_pit | 缺失率 = 0；历史调整因子可追溯 |
| M1-004 | pytest 13 场景全绿 | `pytest tests/contract -v` | 13/13 PASSED，无警告 |
| M1-005 | visible_at 重放验证 | 注入历史日期 t，查询 t 日可见数据 | 只返回 visible_at ≤ t 日 17:00 的记录；未来修订不可见 |
| M1-006 | 三源两票裁决落库 | 人工构造三源冲突数据 | 多数票字段写入 data_cut 表，冲突记录有告警日志 |
| M1-007 | DuckDB/SQLite 落库验证 | 用 DuckDB CLI 查询因子表 | 因子面板表结构正确，可按 (ts_code, visible_at) 索引查询 |
| M1-008 | 9 表 DDL 约束完整性 | 运行 DDL 测试脚本 | 9 表均存在；UNIQUE/NOT NULL/CHECK 约束通过插入测试 |
| M1-009 | 停复牌/ST/退市名录 | 查询已知停牌股历史 | 历史停牌记录完整，visible_at 正确 |
| M1-010 | 数据新鲜度 | 盘后 2h 后检查入库时间 | `ingested_at - market_close ≤ 2h` 对 ≥99% 的股票 |
| M1-011 | 因子计算可复现 | 指定 visible_at 运行因子 | 同参数两次运行结果 bit-identical（无随机性） |
| M1-012 | trial_registry 启动 | 检查 factor_registry 表 | 至少 5 个候选因子已预注册（含假设/方向/变体数） |

**M1 验收结论格式：**

```
M1 验收报告
日期：YYYY-MM-DD
验收人：（单人记录）
用例通过：12/12  失败：0
结论：M1 数据基建验收通过 / 待修复（列出失败用例编号）
下一步：启动 M2 研究+执行阶段
```

### 6.2 M2 验收：研究+执行完成

**验收日期目标**：第 2 月末（约第 8 周）

#### M2 验收前置条件

- [ ] 线性基线回测完成（夏普 ≥ 0.6，cost_model_id=cm_v3_baseline）
- [ ] walk-forward 跑通
- [ ] 执行层三层隔离实现完成
- [ ] 风控守卫和状态机 15 态实现完成

#### M2 验收用例表

| 用例编号 | 验收项目 | 验收方式 | 通过标准 |
|---------|---------|---------|---------|
| M2-001 | 线性基线夏普达标（validation 段） | 运行回测，查看 research_ledger | validation 段（2022–2023）扣成本年化夏普 ≥ 0.6（cost_model_id=cm_v3_baseline）|
| M2-002 | walk-forward 完整跑通 | 运行 walk-forward 脚本 | purged/embargo 分段无泄漏；每折 OOS 结果记录在 research_ledger |
| M2-003 | test 段 A1 评估（消耗 N_eff 1 次） | 运行 test 段终评（须事前登记 trial_registry） | 年化夏普 > 0（A1 硬否决未触发）；trial_registry 记录本次评估 |
| M2-004 | test 段 A2 评估 | 同上 | DSR（按 N_eff 计）≥ 0.5（未触发降级路径）或触发降级路径并记录降级决策 |
| M2-005 | N_eff 预算登记 | 查看 research_ledger | 本次 test 评估消耗 N_eff 1 次；全生命周期累计 ≤ 6 次 |
| M2-006 | 执行层 15 态单测覆盖 | `pytest tests/state_machine -v` | 15 个运行态全部有测试覆盖；全 PASS |
| M2-007 | 风控守卫旁路不可达 | `pytest tests/risk_guard/test_bypass_impossible.py` | 断言通过：策略层无 xtquant 导入；所有下单经 risk_guard |
| M2-008 | 三层隔离架构验证 | 静态代码分析（grep） | `strategy/` 目录无 `import xtquant`；`execution/` 目录有且只有一个 place_order 入口 |
| M2-009 | outbox 三态恢复 | `pytest tests/outbox -v` | NOT_SENT_CAN_SEND/MAYBE_SENT_UNKNOWN/SENT_CONFIRMED 三态全部测试通过 |
| M2-010 | 签名重放防护 | `pytest tests/signature -v` | 无签名拒收、旧签名拒收、过期拒收均断言正确 |
| M2-011 | 对账差异五分类 | `pytest tests/reconcile -v` | active/corp_action/odd_lot/cash_tail/unknown 五类正确分类 |
| M2-012 | 合规限速硬约束 | 执行链路集成测试 INT-E-06/E-07 | 1笔/秒排队、200笔/日暂停 PASS |
| M2-013 | cm_v3_advanced 就绪后重算 | 运行 cm_v3_advanced 成本模型 | 线性基线与 DSR 用 cm_v3_advanced 重算，结果记录 research_ledger |
| M2-014 | 成本档一致性断言 | 运行对账报告生成脚本 | cost_model_id 一致性断言通过（回测与对账口径相同）|

**M2 验收特殊事项（A2 降级路径处理）：**

若 M2-004 触发 A2 弱否决（DSR < 0.5），按 QS-CAL-001 §三 降级规则执行：
- 实盘起步 5万→2.5万
- 行为观察窗 13→26 周全实盘
- 记录降级决策于 research_ledger，不作为 M2 验收失败项（仍可通过 M2 验收，执行降级后继续）

### 6.3 M3 验收：模拟盘全链路上线

**验收日期目标**：第 3 月末（约第 12 周）

**M3 验收口径**：引用 QS-C05 §七 M3 全链路上线验收清单，本文不重抄细节，以下仅列关键通过门禁。

#### M3 验收前置条件

- [ ] M1、M2 验收均已通过
- [ ] QMT 模拟盘账户已开通
- [ ] 监控告警链路（Server酱/钉钉）接通
- [ ] 程序化交易报备材料已准备

#### M3 验收通过门禁

| 门禁项 | 引用来源 | 通过标准 |
|--------|---------|---------|
| 分阶段门禁 A（M3 第 2 周末）| QS-C05 §1.6 | 基线 7 类（含 4a/4b）+ T1/T2/T5 核心路径 PASS + order_remark 初步验证 |
| 分阶段门禁 B（M3 第 6 周末）| QS-C05 §1.6 | 专项演练全过（含 T13 三级回撤）+ 反哺链路跑通 |
| 分阶段门禁 C（M3 第 12 周末）| QS-C05 §1.6 | 4 清洁交易周 + 压力注入 + 真实观察数据评审 → PASS_ENGINEERING |

#### M3 异常演练验收清单

以下演练均以「QS-C05 §七 M3 全链路上线验收清单」为最终判定口径：

| 演练编号 | 演练场景 | 引用 | 通过标准 |
|---------|---------|------|---------|
| T1 | order_remark 反查（xtquant 对账）| QS-C04 §八 | 命中率 ≥95%，误匹配率=0 |
| T2 | outbox 已发送未受理窗口崩溃 | QS-C04 §4.3 | ≥5 次，全部 MAYBE_SENT_UNKNOWN 不误重发 |
| T3 | 撤单在途收到全部成交 | QS-C04 §2.2 | CANCELLED(cancel_fill_type=FULL)，幂等不重复 |
| T4 | CANCELLED(PARTIAL) 缺口自愈 | QS-C04 §2.2 | 下周期 ORDER_SIZING 自愈，不立即补单 |
| T5 | xtquant 查询延迟与旧态 | QS-C04 §2.3 | ≥3 次，双查一致才归位 |
| T6 | 回报乱序/重复推送 | QS-C04 §4.1 | event_seq+累计量幂等，无重复成交 |
| T7 | rate_limiter 触限 | QS-C04 §6.1 | 超速率排队≥1000ms；超日上限暂停 |
| T8 | 签名重放/伪造 | QS-C04 §4.4 | 无签名/旧签名/伪造均拒绝 |
| T9 | 除权日现金对账 | QS-C04 §3.1 | 不误触暂停(MANUAL_REVIEW) |
| T10 | EOD 收盘 LIVE/PARTIAL 处置 | QS-C04 §2.3 | DAY 单自动撤，GTC 跨日，意图 >14:55 作废 |
| T11 | 七类基线异常 | QS-C05 §3.1 | 断连/部成/撤单失败/涨跌停/停牌/数据缺失/重复启动 全 PASS |
| T12 | 进程假死接管+夺令牌 | QS-C04 §4.2 | 旧进程复活无法下单 |
| T13 | 三级回撤状态机路径 | QS-C04 §6.2 | 20%降仓先卖卫星；25%全清仓+冻结系统续运行；break-glass SOP 路径可演练 |

#### M3 物理熔断演练验收

| 演练子项 | 环境 | 通过标准 |
|---------|------|---------|
| break-glass 先撤活跃单 | mock | 复用 CANCEL_REQUESTED/UNKNOWN 先撤 |
| 撤单失败时仅按 sellable_qty 清仓 | mock | 不依赖 position_ledger |
| 跌停时熔断/崩溃重启续清 | mock | 挂跌停价排队；重启读 ledger 续清（幂等）|
| 券商 APP 手动清仓 SOP 演练 | mock xtquant 不可达 | 降级 APP 手动路径可执行，补录 execution_ledger |
| 踢主进程 session 前置条件 | real_restricted（非调仓日空仓）| 心跳连丢>45s 且无响应才踢 |
| 熔断与主进程令牌互斥 | real_restricted | 令牌互斥+单向（仅人工归还）|

#### M3 告警链路测试

| 告警类型 | 测试方式 | 通过标准 |
|---------|---------|---------|
| 风控触发（20% 回撤模拟） | 调用告警触发函数 | 手机推送 ≤ 60s |
| 下单失败告警 | mock 下单失败事件 | 推送内容含错误详情 |
| 数据管道失败 | mock 管道失败信号 | 推送内容含失败来源 |
| 进程失联（执行守护进程停止）| 停止进程②，等待监控进程⑤告警 | ≤ 45s 内告警推送到手机 |
| UPS 切换 | mock UPS 事件 | 推送到手机且内容正确 |

#### M3 模拟盘 4 周零差错追踪

> 4 周零差错 = 转 5 万实盘前置条件（QS-CAL-001 B3 工程判线，QS-C05 §1.1）

| 周次 | 清洁交易日数 | FLAG 日 | 结论 |
|------|------------|---------|------|
| 第 1 清洁周 | ≥ 5 | ≤1（如有须 RCA）| PASS/FLAG/RESET |
| 第 2 清洁周 | ≥ 5 | ≤1 | PASS/FLAG/RESET |
| 第 3 清洁周 | ≥ 5 | ≤1 | PASS/FLAG/RESET |
| 第 4 清洁周 | ≥ 5 | ≤1（累计 ≤3 FLAG）| **4 周满足 → B3 前置达成** |

---

## §7 回归策略

### 7.1 触发机制

| 触发场景 | 回归集合 | 执行时间 |
|---------|---------|---------|
| **每次代码变更（git commit）** | 最小回归集（点时 13 场景 + 风控守卫旁路测试 + 冒烟测试）| ≤ 5 分钟，非交易时段 |
| **每周全量** | 完整 pytest 套件（全部 tests/ 目录）| 周末，约 15–30 分钟 |
| **里程碑验收前** | 验收用例表（M1/M2/M3 对应章节）+ 全量 pytest | 人工触发 |
| **模型重训练后（季度）** | 因子计算单元测试 + 回测可复现性测试 | 重训练完成后 24h 内 |

### 7.2 最小回归集（每次提交必跑）

```bash
# .git/hooks/pre-commit 或 CI pipeline
pytest tests/contract \
       tests/risk_guard/test_bypass_impossible.py \
       tests/state_machine/test_all_15_states.py \
       -q --tb=short --timeout=300

# 通过标准：全部 PASS；时间 < 5 分钟
# 失败：阻断提交/合并，不得绕过
```

### 7.3 每周全量回归

```bash
# 每周末（Saturday 22:00，通过 Windows 任务计划触发）
pytest tests/ -v \
    --html=reports/weekly_$(date +%Y%m%d).html \
    --cov=src --cov-report=term-missing \
    --timeout=1800

# 通过标准：全部 PASS + 总覆盖率 ≥ 80%
# 失败：创建 GitHub issue（标签：bug, regression, priority:high）
```

### 7.4 冻结参数变更 = 宪法修订流程

> 以下参数被视为「宪法级参数」，任何变更须履行宪法修订流程，不得通过普通 commit 修改：

| 宪法级参数 | 所在文档 | 不得擅自修改的理由 |
|-----------|---------|----------------|
| 点时契约 canonical 四键排序 | QS-C03 | 影响所有历史数据重放结果 |
| pytest 13 场景定义 | QS-C03 | 改变底座验收标准 |
| 状态机 15 态枚举 | QS-C04 | 影响 DDL 约束和所有状态机测试 |
| 合规限速（1笔/秒、200笔/日）| QS-C04 §6.1 / SSOT §4 | 影响合规义务 |
| 三级回撤阈值（20%/25%）| QS-C04 §6.2 / SSOT §4 | 影响风控核心逻辑 |
| A1/A2/B1/B2/B3 闸门数字 | QS-CAL-001 | 统计闸门权威来源，不得改动 |
| N_eff 预算（6 次）| QS-CAL-001 / SSOT §2 | 影响测试段评估次数控制 |

**宪法修订流程（单人版）：**

1. 创建 GitHub issue，标签：`constitutional-amendment`
2. 在 issue 中详细说明：变更原因 / 受影响的下游文档 / 预期影响范围
3. 在 research_ledger 记录变更决策（含日期、理由）
4. 更新所有受影响文档（宪法文档须更新版本号）
5. 全量回归通过后合并
6. 在 QS-C00 项目总览索引更新版本状态

### 7.5 回归失败处置流程

```
回归失败
    ↓
判断失败类型
    ├── 点时契约场景失败 → 立即停止，不得上线任何数据相关变更
    ├── 风控守卫旁路测试失败 → 立即停止，高优先级修复（铁律一）
    ├── 状态机迁移测试失败 → 停止执行链路变更，排查状态转换代码
    └── 其他测试失败 → 创建 bug issue，按优先级处理
            ↓
        创建 GitHub issue（标签：bug, test-failure, P1/P2/P3）
            ↓
        修复 → 单独验证 → 合并 → 再跑全量
```

---

## §8 缺陷管理（单人版 issue 标签法）

### 8.1 GitHub Issue 标签体系

| 标签分类 | 标签名 | 含义 |
|---------|--------|------|
| **类型** | `bug` | 代码错误 |
| **类型** | `test-failure` | 测试用例失败 |
| **类型** | `regression` | 回归引入的问题 |
| **类型** | `constitutional-amendment` | 宪法级参数变更申请 |
| **优先级** | `priority:critical` | 影响风控/数据正确性，立即修复 |
| **优先级** | `priority:high` | 影响模拟盘验收，本周内修复 |
| **优先级** | `priority:medium` | 影响功能完整性，下一个里程碑前修复 |
| **优先级** | `priority:low` | 改进项，时间充裕时处理 |
| **组件** | `component:data` | 数据管道组件 |
| **组件** | `component:execution` | 执行链路组件 |
| **组件** | `component:risk` | 风控组件 |
| **组件** | `component:reconcile` | 对账组件 |
| **组件** | `component:alert` | 告警组件 |
| **状态** | `status:investigating` | 正在排查 |
| **状态** | `status:fixed` | 已修复，待验证 |
| **状态** | `status:verified` | 修复已验证 |

### 8.2 缺陷优先级判定规则

| 场景 | 优先级 | 说明 |
|------|--------|------|
| 风控守卫旁路可达 | **critical** | 铁律一违反，立即停止开发修复 |
| 点时 13 场景任一失败 | **critical** | 数据正确性基础，立即修复 |
| 未来函数泄漏被检测到 | **critical** | 研究纪律，铁律三违反 |
| 状态机 RESET 类错误（重复成交/盲目下单）| **critical** | 模拟盘验收立即 RESET |
| 合规限速硬约束代码失效 | **high** | 影响合规义务 |
| 对账差错无法解释 | **high** | B3 工程判线影响 |
| 告警推送失败 | **high** | 监控链路断裂 |
| 非关键路径单元测试失败 | **medium** | 下一里程碑前修复 |
| 文档不一致 | **low** | 时间充裕时处理 |

### 8.3 缺陷生命周期

```
发现（issue 创建 + 标签分类）
    ↓
排查（status:investigating）
    ↓
判断根因
    ├── 代码 bug → 修复 → 回归测试（单测 + 最小回归集）
    └── 设计缺陷 → 宪法修订流程（§7.4）
            ↓
        status:fixed + 关闭 issue
            ↓
        下次全量回归验证 → status:verified
```

### 8.4 单人运维的缺陷管理原则

- **不堆积 critical/high**：critical 问题当日不睡觉前解决；high 问题 3 天内关闭
- **每周 issue 清理**：周度对账复盘（见 QS-E05 §§每周对账复盘）时回顾开放 issue
- **research_ledger 连动**：对影响回测或因子计算的修复，须在 research_ledger 补记影响说明

---

## §9 测试数据管理

### 9.1 数据集分级管理

| 数据集 | 时间段 | 用途 | 管理规则 |
|--------|-------|------|---------|
| **train_data** | 2016–2021 | 模型训练、因子开发 | 可自由使用，回测/研究均可 |
| **validation_data** | 2022–2023 | 调参与方向验证 | ≤5 轮查看预算（QS-CAL-001 §一旧规保留）|
| **test_data** | 2024–2025 | 最终统计闸门评估 | **物理隔离，测试用例绝对禁止使用**；仅允许在 N_eff 预算范围内运行终评 |
| **mock_data** | — | 单元/集成测试专用 | 人工构造，覆盖边界场景，不含真实行情 |
| **smoke_data** | 最近 5 个交易日 | 冒烟测试 | 可使用真实数据，但只做格式/接口验证，不做策略判断 |

### 9.2 测试 mock 数据规范

**点时契约 mock 数据要求：**

```python
# conftest.py
@pytest.fixture
def mock_pit_data():
    """构造包含 visible_at/revision_seq/snapshot_rank/record_id 四键的 mock 数据"""
    return pd.DataFrame({
        "visible_at": ["2023-01-10 17:00:00", "2023-01-10 17:00:00", "2023-01-09 17:00:00"],
        "revision_seq": [2, 1, 1],
        "snapshot_rank": [1, 1, 1],
        "record_id": [3, 2, 1],
        "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
        "close": [10.5, 10.3, 10.1],
        "source": ["AKShare", "AKShare", "AKShare"],
    })
```

**状态机 mock 数据要求：**

```python
@pytest.fixture
def mock_broker_adapter():
    """MockBrokerAdapter：支持注入指定回报序列"""
    class MockBrokerAdapter:
        def __init__(self, response_sequence):
            self.responses = iter(response_sequence)
        def query_order(self, client_order_id):
            return next(self.responses, None)
    return MockBrokerAdapter
```

### 9.3 测试数据隔离规则

- 测试数据存放于 `tests/fixtures/` 目录，**不得放入数据管道目录**
- 演练 ledger 行必须带 `is_drill=True / test_run_id / scenario_id`，默认排除出 position_ledger C 对账统计（QS-C05 §三）
- 压力测试数据不得混入生产 execution_ledger
- 周度恢复演练使用独立的恢复目标路径，不覆盖生产数据

### 9.4 可复现性要求

- 所有回测必须绑定 git commit hash（写入 research_ledger 的 `git_commit_hash` 字段）
- 因子计算函数无副作用：同参数、同 visible_at、同 git commit 下输出 bit-identical
- 测试随机数种子：有随机性的测试（如 bootstrap）须固定 `random.seed(42)` 并在 CI 中锁定

---

## §10 测试环境与工具链

### 10.1 测试环境配置

| 组件 | 版本/规格 | 说明 |
|------|---------|------|
| 操作系统 | Windows 11（生产/测试同机）| QMT/xtquant 仅支持 Windows |
| Python | ≥ 3.10 | 与生产版本一致 |
| pytest | ≥ 7.4 | 主测试框架 |
| pytest-cov | ≥ 4.0 | 覆盖率报告 |
| pytest-html | ≥ 3.2 | HTML 测试报告 |
| pytest-timeout | ≥ 2.1 | 超时保护 |
| DuckDB | ≥ 0.10 | 测试用 in-memory 模式 |
| SQLite | 内置 | 测试用临时数据库 |
| MockBrokerAdapter | 自研 | 替换 xtquant 的 mock 适配器 |

### 10.2 CI/CD（Windows 任务计划版）

由于系统运行在家用 Windows PC，采用 Windows 任务计划替代 GitHub Actions：

| 任务名 | 触发时间 | 脚本 |
|--------|---------|------|
| `ci_minimum_regression` | git pre-commit hook | `pytest tests/contract tests/risk_guard/test_bypass_impossible.py -q` |
| `ci_weekly_full` | 每周六 22:00 | `pytest tests/ -v --html=reports/...` |
| `ci_smoke_e2e` | 每日 06:00（盘前）| `python scripts/smoke_test_e2e.py --mode=mock` |

> **注**：若代码托管于 GitHub，可同步配置 GitHub Actions 运行非 Windows 依赖的测试（排除 xtquant 相关测试，运行 mock 模式）。

### 10.3 测试覆盖率目标

| 模块 | 最低覆盖率 | 说明 |
|------|----------|------|
| `tests/contract`（点时契约）| **100%** | 13 场景全绿为强制放行项 |
| `tests/risk_guard` | **100%** | 铁律一，守卫代码无死角 |
| `tests/state_machine` | **≥ 95%** | 15 态迁移矩阵 |
| `tests/reconcile` | **≥ 90%** | 对账差异分类 |
| `tests/outbox` | **≥ 90%** | 三态恢复 |
| 其他模块 | **≥ 80%** | 通用目标 |

---

## §11 测试报告与归档

### 11.1 测试报告目录结构

```
test_reports/
├── unit/
│   ├── contract_YYYYMMDD.html      # 点时 13 场景报告
│   ├── state_machine_YYYYMMDD.html # 状态机迁移报告
│   └── ...
├── integration/
│   ├── pipeline_YYYYMMDD.html      # 管道端到端报告
│   └── execution_chain_YYYYMMDD.html
├── system/
│   └── e2e_smoke_YYYYMMDD.log      # 冒烟测试日志
├── acceptance/
│   ├── M1_acceptance_YYYYMMDD.md   # M1 验收报告
│   ├── M2_acceptance_YYYYMMDD.md   # M2 验收报告
│   └── M3_acceptance_YYYYMMDD.md   # M3 验收报告（含 QS-C05 门禁 C 记录）
├── regression/
│   ├── weekly_YYYYMMDD.html        # 每周全量回归报告
│   └── coverage_YYYYMMDD.xml       # 覆盖率报告
└── issues/
    └── open_issues_YYYYMMDD.md     # 开放 issue 快照（每周复盘时更新）
```

### 11.2 里程碑验收报告模板

```markdown
# QuantSolo MX 验收报告

**验收日期**：YYYY-MM-DD
**验收人**：（单人）
**对应里程碑**：M1 / M2 / M3

## 验收用例汇总

| 用例编号 | 验收项 | 结论 | 备注 |
|---------|--------|------|------|
| MX-001  | ...    | PASS | —    |
| MX-002  | ...    | PASS | —    |

**总计**：X/X PASS，0 FAIL

## 遗留问题

（无 / 列出 FAIL 用例及跟进计划）

## 结论

MX 验收通过 / 待修复（预计日期 YYYY-MM-DD 重新验收）

## 下一步

启动 MX+1 阶段 / 等待修复后重验收
```

---

## 附录A：pytest 13 场景清单

> **唯一口径来源**：QS-C03《点时数据契约 v2.0》，参见 SSOT §3（pytest 回归场景数：13）。

| 序号 | 场景代码 | 场景描述 | 对应 §3.2 用例 |
|------|---------|---------|--------------|
| 1 | PT-01 | 基础 visible_at 过滤 | §3.2 PT-01 |
| 2 | PT-02 | 修改追加（ACTIVE）canonical 排序 | §3.2 PT-02 |
| 3 | PT-03 | 撤销追加（VOIDED）不计入计算 | §3.2 PT-03 |
| 4 | PT-04 | canonical 四键排序（唯一确定）| §3.2 PT-04 |
| 5 | PT-05 | 盘后 17:00 时间戳规则 | §3.2 PT-05 |
| 6 | PT-06 | 财报 PIT 保守处理（公告日+1）| §3.2 PT-06 |
| 7 | PT-07 | 新闻时间窗口归属（t-1 15:00~t 15:00）| §3.2 PT-07 |
| 8 | PT-08 | 多源冲突三源两票制裁决 | §3.2 PT-08 |
| 9 | PT-09 | adj_factor_pit 年分区跨年查询 | §3.2 PT-09 |
| 10 | PT-10 | 停复牌期间不生成信号 | §3.2 PT-10 |
| 11 | PT-11 | ST 状态历史点时追踪 | §3.2 PT-11 |
| 12 | PT-12 | 接口六字段完整性校验 | §3.2 PT-12 |
| 13 | PT-13 | 未来函数检测（防泄漏）| §3.2 PT-13 |

**执行命令（放行门禁）：**

```bash
pytest tests/contract -v --tb=long -q
# 门槛：13/13 PASSED + 行覆盖率 ≥ 80%
# 任一 FAIL = 阻断放行
```

---

## 附录B：交叉引用索引

| 引用对象 | 本文档位置 | 目标文档编号 |
|---------|----------|------------|
| pytest 13 场景唯一口径 | §3.2、附录 A | QS-C03 v2.0 |
| 15 个运行态唯一口径 | §3.3、§6.2 T1-T13 | QS-C04 §一 |
| 风控守卫唯一入口（铁律一）| §3.3 守卫旁路测试 | QS-C01 §1 |
| 点时正确性（铁律三）| §1.3、§3.2 | QS-C01 §1 |
| A1/A2 评估流程 | §6.2 M2-003/004/005 | QS-CAL-001 |
| N_eff 预算（6 次）| §6.2 M2-005 | QS-CAL-001 / SSOT §2 |
| B3 工程判线（4 周零差错）| §6.3 M3 4 周追踪 | QS-CAL-001 / QS-C05 §一 |
| T1–T13 必测用例 | §6.3 异常演练验收清单 | QS-C04 §十 / QS-C05 §三.2 |
| 物理熔断 break-glass 流程 | §6.3 物理熔断演练 | QS-C04 §五 |
| M3 全链路上线验收口径 | §6.3 M3 验收门禁 | QS-C05 §七 |
| 对账差异五分类 | §4.3 INT-R-xx | QS-C04 §3.1 |
| order_remark 对账方案 | §4.2 INT-E-xx / §6.3 T1 | QS-C04 §八 |
| 三级回撤阈值（20%/25%）| §3.3、§6.3 T13 | QS-C04 §6.2 / SSOT §4 |
| 合规限速（1笔/秒、200笔/日）| §4.2 INT-E-06/07 | QS-C04 §6.1 / SSOT §4 |
| 模拟盘验收哲学 | §1.1 | QS-C05 §〇 |
| 五条铁律全文 | §1.3（仅引用编号）| QS-C01 §1 |

---

*本文档为 QuantSolo v2.0 工程文档体系 QS-E04，v1.0 初版。冻结后变更须版本号递增并记录于 research_ledger。与 SSOT（baseline_spec.md）冲突时以 SSOT 为准，已全文校验。*

---

**文档编号：QS-E04 | 版本：v1.0 | 日期：2026-06-12 | 与 SSOT (baseline_spec.md) 冲突时以 SSOT 为准，已全文校验。**
