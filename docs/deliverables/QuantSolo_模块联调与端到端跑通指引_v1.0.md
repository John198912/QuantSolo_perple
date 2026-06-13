# QuantSolo 模块联调与端到端跑通指引（QS-E09）

| 项 | 值 |
|---|---|
| 文档编号 | QS-E09 |
| 版本 | v1.0 |
| 角色 | 工程文档（QS-E 系列）——离线一键跑通全链路、CLI、合成数据、上线就绪门、真实数据源切换 |
| 适用代码版本 | 仓库 v0.1.0（M1 数据基建 + M2 研究/执行 + M3 运维 + M4 编排层） |
| 上位约束 | 与宪法（QS-C 系列）冲突时以宪法为准；红线见仓库 `AGENTS.md`（=QS-E07） |

> 一句话目的：让任何人在**无真实数据源、无券商凭据**的家用 PC 上，用一条命令把"数据→因子→信号→回测→闸门→模拟交易→对账→监控"整条链路**离线跑通**，并用可机检的"上线就绪门"判断能否进入下一阶段。

---

## 一、读者与前置

- **读者**：项目负责人（一人公司本人）、接手的工程师、以及后续维护的 CodeAgent。
- **前置**：Python 3.12；已 `pip install -e .`（或安装 `pandas numpy duckdb scipy pyyaml pyarrow`）。无需任何外部账号即可跑通"演示数据"路径。
- **配套文档**：研究口径见 QS-C02、数据契约见 QS-C03、状态机见 QS-C04、模拟盘验收见 QS-C05、功能设计见 QS-E03、运维 SOP 见 QS-E08。

---

## 二、系统数据流全景

```
                         config/ (frozen.toml 冻结 / tunable.yaml 可调)
                                     │ load_frozen / load_tunable（R3：唯一参数来源）
                                     ▼
  [数据源]            ┌──────────────────────────────────────────────────────────┐
  AKShare 主         │ src/data        三源适配器→两票制裁决→visible_at 赋值→盘后管道 │
  Tushare 对账   →   │ src/pit         点时表(daily_bar/financials_pit/factor_snapshot)│
  BaoStock 冗余      │                 canonical 四键确定性查询 + 校验器               │
  (演示: demo_data)  └───────────────────────────────┬──────────────────────────────┘
                                                      ▼
                       ┌──────────────── 研究侧（离线，可重复） ───────────────┐
                       │ src/factor   三变体因子（raw/processed/orthogonal）      │
                       │ src/signal   core_factor 选股 + market_timing 择时        │
                       │ src/research backtest(向量化+事件驱动) → gates(BH-FDR/    │
                       │              ICIR/Bonferroni/A1·A2·B1·B2·B3) + DSR        │
                       │              + walk_forward(Purged&Embargo) + trial_log   │
                       └───────────────────────────────┬──────────────────────────┘
                                                        ▼ 仅"通过闸门"的因子可进交易
                       ┌──────────────── 交易侧（模拟/实盘同构） ──────────────┐
                       │ src/signal/merger  核心+卫星 → 目标权重                  │
                       │ src/risk/guard     ★唯一下单入口（R1）：约束裁剪+风控令牌│
                       │ src/execution      order_sizing → 15 态状态机 → 适配器   │
                       │                    模拟: backtest_adapter（实盘: xtquant）│
                       │ src/reconcile      daily_recon 三方对账 + cost_attribution│
                       │ src/monitor        alerter / watchdog / dashboard        │
                       └──────────────────────────────────────────────────────────┘
```

**关键不变式**：① 所有下单只能经 `src/risk/guard.py`（R1）；② 点时表只追加（R2）；③ 闸门/风控/成本/验收数字只来自 `config/`（R3）；④ 资金计算用 `Decimal`（R6）；⑤ `import xtquant` 仅限 3 个白名单文件。这些由 `tools/static_guard_scan.py` 在 CI 与就绪门中强制。

---

## 三、五分钟最小可跑通路径

```bash
cd <仓库根>
pip install -e .            # 或：pip install pandas numpy duckdb scipy pyyaml pyarrow
python -m src seed-demo     # ① 生成合成数据（~30 只标的、~2.5 年日频）
python -m src e2e           # ② 端到端跑通全链路，产出 run/e2e_report_*.md
python -m src golive-check  # ③ 上线就绪门 G1–G8（exit 0 = 可进入下一阶段）
```

等价的 Make 命令：`make seed && make e2e && make golive`。

预期：三条命令均 `exit 0`；`e2e` 末尾打印 `E2E 全流程: PASS`；`golive-check` 打印 `8/8 PASS`。

---

## 四、CLI 子命令（`python -m src <cmd>`）

| 子命令 | 作用 | 典型用途 |
|---|---|---|
| `selfcheck` | 跑 静态守卫 + 冻结参数校验 + `pytest -m "not live"`（隔离编排层慢测试） | 每次改代码后的快速回归 |
| `seed-demo` | 生成合成 A 股式数据到 `data/`+`db/`（gitignored，固定随机种子可复现） | 离线联调的数据来源 |
| `e2e` | 串联 自检→种子→研究→模拟交易→对账，逐阶段 checkpoint，写 `run/e2e_report_*.md` | 一键验证整条链路 |
| `research` | 仅跑研究管线（因子→信号→回测→闸门），返回各阶段指标与闸门判定 | 调试因子/闸门 |
| `paper-trade` | 仅跑模拟交易管线（信号→风控→状态机→撮合→对账） | 调试执行/对账 |
| `datasource-doctor` | 检查 `.env` 凭据是否就绪（**不强连网**），缺失项给出提示 | 切真实数据源前的体检 |
| `golive-check` | 调 `tools/golive_readiness.py`，输出 G1–G8 PASS/FAIL | 阶段放行的硬门禁 |

---

## 五、阶段逐步联调（含真实输出样例）

> 以下输出取自本仓库 v0.1.0 的真实运行（`python -m src e2e`）。合成数据是随机游走，**没有真实 alpha**，因此研究闸门"正确地"否决它——这恰好验证了闸门在起作用。

### 阶段 0 · 自检
- 调用链：`load_frozen()` 读 `config/frozen.toml`（schema_version=1.0）、`load_tunable()` 读 `config/tunable.yaml`。
- 验收 checkpoint：两者均 OK，否则后续阶段不应启动。

### 阶段 1 · 数据种子（合成数据规格）
- 调用链：`orchestration/demo_data.py` → 写 `data/daily_bar/`、`data/financials_pit/`、`data/factor_snapshot/`（DuckDB/Parquet）+ `db/quant.db`（SQLite）。列名/类型与 `tests/conftest.py` 夹具及 QS-C03 点时契约一致，确保真实 `src/pit/query_engine.py` 能直接读。
- 真实产出：**30 只标的、649 个交易日（2022-01-04 ~ 2024-06-28）、19,179 行日线、508 行财报、55,737 行因子快照**；含真实涨跌停约束、随机停牌、复权因子、财报 `visible_at` 滞后。
- 验收 checkpoint：`seed-demo` exit 0；点时查询能按 canonical 四键确定性返回。

### 阶段 2 · 研究管线
- 函数级调用链：`factor/pipeline.py`（三变体）→ `signal/core_factor.py` 候选 → `research/backtest/vectorized.py`（截面 rank-IC、Sharpe）+ `event_driven.py`（T+1/涨跌停/100 股取整双层互验）→ `research/gates.py`（阶段一 BH-FDR+单侧 t>3 → 阶段二 ICIR/换手 → 阶段三 Bonferroni；A1 硬否决 / A2 弱否决降级 / B1 加仓门）+ `deflated_sharpe.py`（Harvey-Liu SR0 + N_eff 全维度）→ `trial_log.py` 登记（N_eff 预算 ≤6、查看轮次 ≤5）。
- 真实输出：`阶段一通过 1/1 → 阶段二通过 1/1 → 阶段三入选 1 个`；`回测 Sharpe=0.0000`；`A1 硬否决触发：合并段夏普 0.0000 <= 0.0000`；`闸门判定 verdict=FAIL_A1`；`trial_log 行 ID 已写入`。
- 验收 checkpoint：研究管线**能跑通并产出明确闸门判定**（即使判定是否决）。换成真实数据后，期望 Sharpe 达到 `frozen['acceptance']` 底线（线性基线 ≥0.6、现实档 ≥0.8）方可进入交易侧。

### 阶段 3 · 模拟交易管线
- 函数级调用链：`signal/merger.py`（核心+卫星→目标权重，Decimal）→ `risk/guard.py`（**唯一下单入口**：硬约束裁剪 + 不可伪造风控令牌）→ `execution/order_sizing.py`（100 股整手）→ `execution/state_machine.py`（15 态：IDLE→TARGET_GEN→RISK_CLIP→ORDER_SIZING→ORDER_INTENT→PRE_FIRE_CHECK→SUBMITTED→…→FILLED/CANCELLED/RECONCILE）→ `execution/adapters/backtest_adapter.py` 模拟撮合（**绝不碰 xtquant**）→ `reconcile/daily_recon.py` 三方对账 → `reconcile/cost_attribution.py` 成本归因 → `monitor` 汇总。
- 真实输出（CLI 全速运行）：`合并信号 10 只 → 订单定量 10 笔 BUY → risk_guard APPROVE ×10 → 撮合 通过=10 拒绝=0 → 对账 passed=True diff_count=0 → 组合市值≈999,395 元`，状态机轨迹逐步打印。
- **限速说明（重要）**：`config/frozen.toml [compliance] max_orders_per_second=1` 是内部合规硬线（QS-C04 §六），由 `RiskGuard` 内嵌令牌桶强制。`trading_pipeline` 在相邻订单间 `sleep(1.05s)` 来"合规地"放行——这不是绕过，而是按真实节流节奏报单；因此 10 笔报单约耗时 10 秒属正常。单测中用 mock 消除睡眠以加速。
- 验收 checkpoint：所有目标订单经风控放行后能撮合、对账 `passed=True`、`diff_count=0`。

### 阶段 4 · 对账与监控
- 调用链：`daily_recon`（理论持仓 C 由成交事件流推导 vs 券商持仓 B）→ 差异分级（CORP_ACTION/ODD_LOT/UNEXPLAINED）→ `cost_attribution`（实际成本 vs 模型成本，阈值 `b3_cost_deviation_max=0.30`）→ `monitor/alerter`（演示用注入式假客户端，不发真实网络）。
- 验收 checkpoint：`passed=True, cash_diff≈0`；连续零差错周数对接 B3 工程判线（`b3_recon_zero_error_weeks=4`）。

---

## 六、上线就绪门（`tools/golive_readiness.py`，G1–G8）

把 QS-C05 模拟盘验收手册与 QS-C02 闸门固化成**可机检的 PASS/FAIL**；exit 0 才允许进入下一阶段。

| 门 | 检查项 | 通过判据 |
|---|---|---|
| G1 | 静态守卫（R1/R2/R6 红线） | `static_guard_scan.py` exit 0 |
| G2 | 冻结参数 SHA256 校验 | `frozen_params_check.py` exit 0 |
| G3 | 存量回归（含 13 场景点时回归） | `pytest -m "not live"`（隔离编排层）全绿 |
| G4 | 演示数据已生成 | `data/`+`db/` 就位 |
| G5 | 研究管线可跑通 | 产出闸门判定（pipeline 可跑通=True） |
| G6 | 模拟交易可跑通 | fills>0、风控放行、对账 passed=True |
| G7 | 关键配置文件存在 | `frozen.toml`/`tunable.yaml`/`source_priority.yaml` |
| G8 | 无 xtquant 非法 import | R1 红线 AST 校验 |

> 真实结果（v0.1.0）：**8/8 PASS**。注意 G5 在演示数据下闸门判定为 `FAIL_A1` 仍计 PASS——因为 G5 只验证"管线能跑通并给出判定"，而非"演示数据能通过策略闸门"（后者要等真实数据）。

---

## 七、合成数据 → 真实数据源切换

演示路径用 `demo_data.py`；切到真实数据按以下步骤，无需改动研究/交易侧任何代码（点时表 schema 不变）：

1. **填凭据**：`cp .env.example .env`，填入 `TUSHARE_TOKEN`（Tushare Pro，对账需 2000 积分，见 QS-C03 §9）、`QUANTSOLO_DATA_ROOT` 等；AKShare/BaoStock 免凭据。
2. **体检**：`python -m src datasource-doctor` 确认凭据就绪（不强连网）。
3. **采集落库**：用 `src/data` 的三源适配器跑真实采集（盘后管道），三源两票制裁决，冲突剔除入库且告警、**绝不插值**（QS-C03）。落库后点时表与演示同构，`query_engine` 透明读取。
4. **重跑研究**：`python -m src research` 用真实数据；期望 Sharpe 达 `frozen['acceptance']` 底线方可放行交易侧。
5. **注意北向数据**：北向资金明细自 2024-08-19 起停更（外部审查事实），相关因子需以替代口径处理或停用（见 QS-C03 / 审查报告）。

> ⚠️ 切换数据源**不要**改 `config/frozen.toml` 里的闸门/验收数字；那些受 SHA256 保护（R3），任何修订须走 QS-C00 §四 修宪流程并重跑 `gate_calibration.py`。

---

## 八、模拟 → 实盘的演进（接 xtquant）

- 实盘下单走 `src/execution/adapters/xtquant_adapter.py`（国金 QMT/miniQMT），它是**唯一**允许 `import xtquant` 的业务文件（另两处为 `src/execution/break_glass.py`、`scripts/break_glass.py` 熔断脚本）。
- 切换只改"适配器选择"，状态机/风控/对账逻辑不变（模拟与实盘同构）。
- 实盘前必须满足 QS-C05 §七 上线口径：模拟盘全链路连续运行、对账连续零差错达标周数、就绪门 8/8 PASS。资金按 QS-E06 节奏（5 万实盘 M4 起，加仓最早 M9）。
- 物理熔断（break-glass）为最后手段，独立于风控守卫，见 QS-E05 Runbook。

---

## 九、目录与产物

| 路径 | 内容 | 是否进 Git |
|---|---|---|
| `src/orchestration/` | 编排层：demo_data / research_pipeline / trading_pipeline / e2e / cli | 是 |
| `src/__main__.py` | `python -m src` 入口 | 是 |
| `tools/golive_readiness.py` | 上线就绪门 | 是 |
| `Makefile` / `.env.example` / `CHANGELOG.md` | 开发快捷命令 / 凭据模板 / 版本史 | 是 |
| `data/`、`db/`、`run/` | 合成数据、SQLite 库、e2e 报告 | **否（.gitignore）** |
| `.env` | 真实凭据 | **否（.gitignore）** |

---

## 十、故障排查（实测踩坑）

| 现象 | 根因 | 处理 |
|---|---|---|
| 模拟交易大量 `RATE_LIMIT_PER_SEC` 拒单 | 风控 1 笔/秒令牌桶（合规硬线） | 报单循环已 `sleep(1.05s)` 合规放行；测试用 mock 消除睡眠 |
| 回测报 `['close_adj','amount'] not in index` | 因子表缺价格/成交额列 | 研究管线已用 `bar_df` merge 补 `close_adj`/`amount` |
| 脚本报 `No module named 'src'` | 直接跑 `tools/*.py` 时仓库根不在 `sys.path` | 工具脚本头部已 `sys.path.insert(0, REPO_ROOT)`；或用 `python -m` |
| `pytest`/就绪门超时 | 编排层 session fixture 重（seed+research+e2e） | `selfcheck`/G3 已 `--ignore=tests/orchestration`，编排层由 G5/G6 验收 |
| 演示数据闸门判定 FAIL_A1 | 合成随机游走无 alpha | 符合预期；换真实数据后再看 |

---

## 十一、红线与文档关系（务必牢记）

- 五条红线（R1 风控唯一入口 / R2 点时表只追加 / R3 冻结参数 SHA256 / R5 15 态+order_remark / R6 金额 Decimal）见仓库 `AGENTS.md`（=QS-E07），由 `static_guard_scan.py` 强制。
- 本文档（QS-E09）只讲"怎么把已实现的模块串起来跑通"；模块内部设计见 QS-E03，研究口径见 QS-C02/QS-CAL-001，验收口径见 QS-C05/QS-E04，人工与运维分阶段事项见 QS-E08。
- 与宪法冲突时以宪法为准。

---

## 附录 A · Makefile 目标
`make help` 查看全部；常用：`make selfcheck` / `make seed` / `make e2e` / `make golive` / `make test` / `make clean`。

## 附录 B · .env.example 字段
`TUSHARE_TOKEN`（Tushare Pro，对账用，需 2000 积分）、`QMT_USERDATA_PATH`（国金 QMT 实盘路径）、`SERVERCHAN_SENDKEY` / `DINGTALK_WEBHOOK`（监控告警，可空）、`QUANTSOLO_DATA_ROOT`（数据根，默认 `./data`）。

---

*QS-E09 v1.0 · 与代码仓库 v0.1.0（含 M4 编排层）一致 · 所有命令与指标取自真实运行*
