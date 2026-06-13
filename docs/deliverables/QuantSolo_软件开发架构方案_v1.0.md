# QuantSolo — 软件开发架构方案 v1.0

| 字段 | 内容 |
|------|------|
| **文档编号** | QS-E02 |
| **版本** | v1.0 |
| **日期** | 2026-06-12 |
| **状态** | 正式发布 |
| **上游依赖** | baseline_spec.md（SSOT）· QS-C01 系统设计文档 v5.0 · QS-C03 点时数据契约 v2.0 · QS-C04 执行与风控状态机 v1.3 |
| **下游文档** | QS-E03 软件开发功能设计文档 v1.0 · QS-E04 项目测试验收方案 · QS-E05 执行行动指导手册 |
| **冲突裁决** | 与 SSOT（baseline_spec.md）冲突时以 SSOT 为准；已全文校验。 |

---

## 版本演进表

| 版本 | 日期 | 核心变更 |
|------|------|---------|
| v1.0 | 2026-06-12 | 初版，依据 SSOT + QS-C01/C03/C04 全量落地 |

---

## 目录

- [§1 技术栈选型与理由](#1-技术栈选型与理由)
- [§2 部署架构与进程拓扑](#2-部署架构与进程拓扑)
- [§3 代码仓库结构与模块依赖](#3-代码仓库结构与模块依赖)
- [§4 三层隔离架构与接口定义](#4-三层隔离架构与接口定义)
- [§5 配置管理规范](#5-配置管理规范)
- [§6 日志规范](#6-日志规范)
- [§7 错误处理策略](#7-错误处理策略)
- [§8 幂等与恢复设计](#8-幂等与恢复设计)
- [§9 数据流图](#9-数据流图)
- [§10 安全设计](#10-安全设计)
- [附录 A 依赖库版本清单](#附录-a-依赖库版本清单)

---

## §1 技术栈选型与理由

### 1.1 选型总览

| 类别 | 技术 | 版本要求 | 角色 | 选型理由 |
|------|------|---------|------|---------|
| 运行时语言 | Python | 3.11+ | 全栈 | 3.11 tomllib 原生支持；match 语句简化状态机；typing 增强；速度优化 |
| 数据处理 | pandas | ≥2.0 | 研究层/信号层 | Copy-on-Write 语义，内存安全；与 AlphaLens/sklearn 生态成熟 |
| 列式计算 | polars | ≥0.20 | 盘后管道批处理 | 零拷贝 Parquet 读写；比 pandas 快 5-10×；盘后管道大批量处理首选 |
| 分析引擎 | DuckDB | ≥0.10 | 行情/因子查询 | 嵌入式列存，零服务进程，Parquet 直查，回测查询快；符合零运维原则 |
| 本地存储（行情/因子）| Parquet + PyArrow | ≥14.0 | 数据持久层 | 列压缩存储，按年分区，DuckDB 分区裁剪；adj_factor_pit 大表约 1.5-2 亿行可控 |
| 本地存储（交易/审计）| SQLite | 内置（WAL 模式）| 事务安全层 | WAL 模式支持读写并发；foreign_keys=ON；单文件备份；符合零运维原则；砍掉 PostgreSQL |
| ML 模型 | LightGBM | ≥4.0 | 因子合成 | 中等深度（叶子 64-128）+ L1/L2 正则；SHAP 归因；A 股等价独立样本 20-50 万的稳定区间 |
| 行情接口 | xtquant | 随 QMT 版本 | 执行层 | 迅投 miniQMT Python SDK，A 股 Windows 唯一可用方案；order_remark 对账字段（QS-C04 §8）|
| 数据源 · 主 | AKShare | ≥1.12 | 数据采集主 | 免费；日线/龙虎榜/资金流/停复牌，三源中 source_priority=1 |
| 数据源 · 辅 | Tushare Pro | 2000 积分档 | 对账与补强 | ~200 元/年；财报/股东户数/龙虎榜明细；source_priority=2 |
| 数据源 · 冗余 | BaoStock | 免费 | 冗余校验 | 日线第三源；source_priority=3；三源两票制 |
| 任务调度 | APScheduler + Windows 任务计划 | APScheduler≥3.10 | 定时任务 | APScheduler 用于进程内精细调度；Windows 任务计划用于进程级 17:00 盘后管道触发，开机自启 |
| 监控看板 | Streamlit | ≥1.30 | 可视化看板 | 零前端代码；Python 直写；盘后巡检看板首选 |
| 告警推送 | Server 酱 / 钉钉机器人 | HTTP API | 手机告警 | 风控触发/进程失联/数据异常→推手机；支持 Webhook |
| LLM 舆情（P2）| 本地 Qwen 或低价 API | P2 阶段 | 情绪因子 | 批量结构化打分，Parquet 缓存，避免重复调用；M3 后启动 |
| 备份同步 | rclone | ≥1.66 | 云盘/外置盘备份 | 增量同步 SQLite/DuckDB/Parquet；支持 OneDrive/百度网盘/坚果云 |

### 1.2 关键选型决策说明

#### 1.2.1 砍掉 PostgreSQL

依据 SSOT §5 及 QS-C03 §8.1：PostgreSQL 的优势全部在分布式/高并发场景，家用 Windows PC 单机场景完全不需要。改用 DuckDB/Parquet + SQLite 两层：
- 行情/因子面板 → DuckDB/Parquet（列式分析快，零运维）
- 交易/持仓/信号/审计 → SQLite WAL 模式（事务安全，单文件备份）

#### 1.2.2 Python 3.11+ 的具体好处

```python
# match 语句简化状态机（替代 if-elif 链）
match state:
    case "IDLE":
        handle_idle()
    case "RISK_CLIP":
        handle_risk_clip()
    case "BREAK_GLASS":
        handle_break_glass()

# tomllib 原生解析配置（无需第三方依赖）
import tomllib
with open("config/frozen.toml", "rb") as f:
    config = tomllib.load(f)
```

#### 1.2.3 pandas vs polars 分工

| 场景 | 选用 | 理由 |
|------|------|------|
| 研究层因子计算（AlphaLens/sklearn 集成）| pandas | 生态兼容，Copy-on-Write 内存安全 |
| 盘后管道批处理（AKShare→Parquet 写入）| polars | 零拷贝写 Parquet，大批量快 5-10× |
| 回测向量化截面 | polars | 列式操作，内存峰值低 |
| 执行层实时信号 | pandas | xtquant 回调数据结构 |

#### 1.2.4 APScheduler + Windows 任务计划分工

| 任务 | 调度方式 | 理由 |
|------|---------|------|
| 进程级启动（17:00 盘后管道）| Windows 任务计划 | 系统级可靠，重启后自动恢复 |
| 进程内子任务（心跳、限速计数器刷新）| APScheduler | Python 内 cron，无需系统权限 |
| 盘前 9:15 dry-run | Windows 任务计划 | 保证开盘前完成 |
| 告警发送重试 | APScheduler BackgroundScheduler | 非阻塞，独立线程 |

---

## §2 部署架构与进程拓扑

### 2.1 硬件配置（推荐）

| 组件 | 最低配置 | 推荐配置 | 说明 |
|------|---------|---------|------|
| CPU | 4 核 | 6 核+ | 研究回测峰值 CPU 密集；LightGBM 多线程 |
| 内存 | 16 GB | 32 GB | 研究回测峰值 >16 GB；行情面板全量加载 |
| 存储 | 512 GB SSD | 1 TB NVMe | DuckDB + Parquet（全量行情约 50-100 GB）+ SQLite + 备份缓存 |
| 操作系统 | Windows 10 | **Windows 11** | QMT/xtquant 仅支持 Windows |
| UPS | 400 VA | **≥600 VA** | 停电支撑撤单清仓（约 10 分钟）；关键基础设施 |
| 网络主链路 | 100 Mbps | 有线以太网 | 稳定优先，有线 < WiFi 延迟 |
| 网络备链路 | — | **手机热点** | 主链路故障时手动/自动切换 |

### 2.2 五进程拓扑

```
┌─────────────────────────────────────────────────────────────────┐
│                    家用 Windows 11 PC                            │
│                                                                  │
│  ┌─────────────┐    ┌──────────────────┐    ┌───────────────┐   │
│  │ ① QMT/迅投  │    │  ② 执行守护进程   │    │ ③ 盘后数据管道 │   │
│  │   终端      │◄───│  (xtquant)       │    │ (任务计划17:00)│   │
│  │  (常驻)     │    │  9:15–15:00      │    │               │   │
│  └─────────────┘    └────────┬─────────┘    └───────┬───────┘   │
│         ▲                   │                      │            │
│         │                   ▼                      ▼            │
│         │           ┌───────────────┐    ┌──────────────────┐   │
│         │           │  SQLite       │    │  DuckDB/Parquet   │   │
│         │           │  execution_   │    │  行情/因子面板     │   │
│         │           │  ledger +     │◄───│  (adj_factor_pit, │   │
│         │           │  trade_audit  │    │  daily_bar_raw,   │   │
│         │           └───────┬───────┘    │  factor_snapshot) │   │
│         │                   │            └──────────────────┘   │
│  ┌──────┴──────┐             │                                   │
│  │ ⑤ 监控告警  │◄────────────┘                                   │
│  │   进程      │    ┌──────────────────┐                         │
│  │  (watchdog, │    │ ④ 研究环境        │                         │
│  │   常驻独立) │    │  (Jupyter/脚本,   │                         │
│  └─────────────┘    │  非交易时段)      │                         │
│         │           └──────────────────┘                         │
│         ▼                                                        │
│   Server酱/钉钉机器人 → 手机推送                                   │
└─────────────────────────────────────────────────────────────────┘
```

### 2.3 进程详细说明

| 进程编号 | 名称 | 运行时段 | 启动方式 | PID 文件 | 关键职责 |
|---------|------|---------|---------|---------|---------|
| ① | QMT/迅投终端 | 交易日常驻（手动/开机自启）| 手动或任务计划 | N/A（GUI 进程）| xtquant 依赖此终端在线；不在 Python 代码控制范围 |
| ② | 执行守护进程 | 9:15–15:30（含对账）| 任务计划 9:10 触发 | `run/execution.pid` | 风控守卫 + 下单 + 状态机（QS-C04）+ 盘中告警 |
| ③ | 盘后数据管道 | 每日 17:00（任务计划）| 任务计划 17:00 触发 | `run/pipeline.pid` | AKShare/Tushare/BaoStock 三源采集 → 三源两票制 → 入库 → visible_at 打戳 |
| ④ | 研究环境 | 非交易时段（手动）| 手动启动 | N/A | 因子研究/回测/LightGBM 训练；**禁止交易时段运行**（资源竞争） |
| ⑤ | 监控告警进程 | 常驻（独立）| 任务计划开机自启 | `run/monitor.pid` | watchdog 互查（与进程②双向）；Streamlit 看板；Server酱/钉钉推送 |

### 2.4 进程间通信与互查机制

#### 2.4.1 文件锁 + PID 心跳

```python
# 单例锁：防止重复启动（QS-C04 §4.2）
class SingletonLock:
    def __init__(self, lock_file: str, ttl_s: int = 120):
        self.lock_file = lock_file
        self.ttl_s = ttl_s

    def acquire(self) -> bool:
        """
        1. 检查 lock_file 是否存在
        2. 若存在：读取 {pid, heartbeat_ts}
           - kill -0 <pid> 检查进程存活
           - 若存活且心跳未过期 → 拒绝启动
           - 若 PID 不存活或心跳连续 ≥2 次过期 → 接管
        3. 接管后：写入本进程 {pid, heartbeat_ts}，启动心跳定时器
        """

    def heartbeat(self):
        """定时更新 heartbeat_ts（APScheduler 每 30s 调一次）"""
```

#### 2.4.2 监控进程 ⑤ ↔ 执行进程 ② 双向互查

```
监控进程⑤ → 定时检查 run/execution.pid 的心跳时间戳
           → 心跳超过 45s（3个心跳间隔）未更新 → 告警：进程失联
           → 心跳超过 120s → 触发告警升级

执行进程② → 订阅监控进程⑤的健康检查端点（简单 HTTP /health）
           → 连续 3 次 /health 超时 → 告警：监控进程失联
           → 记录 execution_ledger（watchdog_miss 事件）
```

#### 2.4.3 进程间数据共享

所有进程间通信通过**共享文件（SQLite / Parquet / YAML 状态文件）**，不使用进程间内存共享或消息队列：

| 数据 | 共享方式 | 说明 |
|------|---------|------|
| 执行状态 | SQLite `execution_ledger` | 盘后对账、监控进程只读 |
| 告警队列 | SQLite `alert_queue` 表 | 监控进程写；执行进程写；告警发送进程消费 |
| 下单令牌 | 文件锁 `run/order_token.lock` | break-glass 夺令牌；人工归还 |
| 持仓快照 | SQLite `position_ledger` 视图 | 由 execution_ledger 推导（QS-C04 §7.2） |
| 系统配置 | `config/frozen.toml` + `config/tunable.yaml` | 只读挂载；变更需重启 |

### 2.5 网络备链路切换 SOP

```
检测：监控进程⑤每 60s ping 外网（8.8.8.8）
  → 连续 3 次失败 → 触发手机告警
  → 执行进程②收到网络异常事件 → 暂停下单（MANUAL_REVIEW）
  → 人工手动将手机热点连接 PC（USB 共享网络）
  → 确认连通后人工恢复执行进程
```

---

## §3 代码仓库结构与模块依赖

### 3.1 Monorepo 目录树

```
quant-solo/                          # Git monorepo 根目录
├── README.md
├── pyproject.toml                   # 依赖管理（pip-tools 或 Poetry）
├── .pre-commit-config.yaml          # pre-commit hooks（pytest + ruff + mypy）
├── .gitignore                       # 排除 data/、run/、secrets/、*.db
│
├── config/                          # 配置管理（见 §5）
│   ├── frozen.toml                  # 冻结参数（不得运行时修改）
│   ├── tunable.yaml                 # 可调参数（盘后人工调整）
│   └── source_priority.yaml         # 三源优先级与容差（QS-C03 §9.2）
│
├── data/                            # 数据目录（.gitignore 排除，rclone 备份）
│   ├── daily_bar/                   # Parquet，按 year={YYYY}/ 分区
│   ├── adj_factor_pit/              # Parquet，按 year={YYYY}/ts_code={code}/ 分区
│   ├── factor_snapshot/             # Parquet，按 year={YYYY}/ 分区
│   ├── financials_pit/              # Parquet，按 year={YYYY}/ 分区
│   └── sentiment/                   # Parquet，按月分区（P2 阶段）
│
├── db/                              # SQLite 数据库文件
│   ├── quant.db                     # 主库（点时契约 9 表 + trade_calendar）
│   └── quant_test.db                # 测试用内存数据库（pytest fixture）
│
├── run/                             # 进程运行时文件（.gitignore 排除）
│   ├── execution.pid                # 执行进程 PID + 心跳
│   ├── pipeline.pid                 # 盘后管道 PID + 心跳
│   ├── monitor.pid                  # 监控进程 PID + 心跳
│   └── order_token.lock             # 下单令牌（break-glass 夺取）
│
├── src/                             # 源码主目录
│   ├── __init__.py
│   │
│   ├── data/                        # 数据层（Layer 1）
│   │   ├── __init__.py
│   │   ├── adapters/                # 三源适配器
│   │   │   ├── akshare_adapter.py   # AKShare 主采集
│   │   │   ├── tushare_adapter.py   # Tushare Pro 对账
│   │   │   └── baostock_adapter.py  # BaoStock 冗余校验
│   │   ├── arbitrator.py            # 三源两票制裁决器
│   │   ├── pipeline.py              # 盘后数据管道调度
│   │   ├── calendar.py              # trade_calendar 工具函数
│   │   └── visible_at.py            # visible_at 赋值规则（QS-C03 §10）
│   │
│   ├── pit/                         # 点时查询引擎（Layer 1）
│   │   ├── __init__.py
│   │   ├── query_engine.py          # canonical 四键查询（QS-C03 §3）
│   │   ├── daily_bar.py             # daily_bar_asof
│   │   ├── financials.py            # financials_pit_asof + get_pit_ttm
│   │   ├── factor_snapshot.py       # factor_snapshot_asof
│   │   └── validator.py             # validate_data_gate + validate_adj_coverage
│   │
│   ├── factor/                      # 因子计算引擎（Layer 2，纯函数）
│   │   ├── __init__.py
│   │   ├── registry.py              # factor_registry 管理
│   │   ├── transforms.py            # MAD 去极值、中性化、z-score
│   │   ├── quality.py               # ROE、毛利率、OCF/净利润、资产负债率
│   │   ├── momentum.py              # 20/60 日收益（剔除最近 5 日反转）
│   │   ├── volatility.py            # 60 日波动率、最大回撤
│   │   ├── flow.py                  # 主力净流入、龙虎榜净买
│   │   ├── chips.py                 # 股东户数环比
│   │   └── sentiment.py             # LLM 舆情因子（P2 阶段）
│   │
│   ├── research/                    # 研究层（离线，纯函数）
│   │   ├── __init__.py
│   │   ├── backtest/                # 回测引擎
│   │   │   ├── vectorized.py        # 向量化回测
│   │   │   ├── event_driven.py      # 事件驱动回测（精确成本）
│   │   │   └── cost_models.py       # cm_v3_baseline / cm_v3_advanced
│   │   ├── factor_selection.py      # 三阶段因子筛选（BH-FDR + DSR）
│   │   ├── lgbm_model.py            # LightGBM 因子合成
│   │   ├── ledger.py                # research_ledger 登记
│   │   └── dsr.py                   # Deflated Sharpe Ratio 计算
│   │
│   ├── signal/                      # 信号生成层（纯函数）
│   │   ├── __init__.py
│   │   ├── core_factor.py           # 核心多因子选股（75-80%）
│   │   ├── trend_satellite.py       # 趋势卫星（唐奇安 20 突破 + ATR）
│   │   ├── market_timing.py         # 大盘择时总开关（沪深 300 vs 200MA）
│   │   └── merger.py                # 信号合并 → 目标持仓表
│   │
│   ├── risk/                        # 风控层（唯一下单入口守卫）
│   │   ├── __init__.py
│   │   ├── guard.py                 # risk_guard 装饰器 + 唯一下单入口
│   │   ├── constraints.py           # 仓位/行业/流动性硬约束
│   │   └── drawdown.py              # 三级回撤检测（20%/25%）
│   │
│   ├── execution/                   # 执行层（三层隔离）
│   │   ├── __init__.py
│   │   ├── interfaces.py            # 抽象接口（ABC，平台无关）
│   │   ├── state_machine.py         # 15 态状态机（QS-C04）
│   │   ├── order_sizing.py          # 差量计算 + T+1 + 取整 + reservation
│   │   ├── idempotency.py           # 幂等键生成与去重
│   │   ├── outbox.py                # outbox 三态恢复
│   │   ├── adapters/
│   │   │   ├── base.py              # 适配器抽象基类
│   │   │   ├── xtquant_adapter.py   # xtquant 实盘适配器（禁止在策略层 import）
│   │   │   └── backtest_adapter.py  # 回测适配器（历史 Parquet）
│   │   ├── rate_limiter.py          # 申报限速（1笔/秒，200笔/日）
│   │   └── break_glass.py           # 物理一键熔断脚本（独立进程）
│   │
│   ├── reconcile/                   # 对账器
│   │   ├── __init__.py
│   │   ├── daily_recon.py           # 日终三方对账（理论/券商/ledger）
│   │   └── cost_attribution.py      # 成本偏差归因 → 研究层反哺
│   │
│   └── monitor/                     # 监控告警层
│       ├── __init__.py
│       ├── watchdog.py              # 进程互查
│       ├── alerter.py               # Server 酱/钉钉告警发送
│       ├── dashboard.py             # Streamlit 看板
│       └── health.py                # 系统健康检查（HTTP /health）
│
├── scripts/                         # 独立可执行脚本
│   ├── break_glass.py               # 物理一键熔断（独立进程，直调 xtquant）
│   ├── run_pipeline.py              # 盘后管道入口（任务计划调用）
│   ├── run_execution.py             # 执行守护进程入口
│   ├── run_monitor.py               # 监控进程入口
│   ├── build_adj_factors.py         # 一次性构建 adj_factor_pit（离线）
│   └── weekly_recon.py              # 周度对账复盘脚本
│
├── tests/                           # 测试套件
│   ├── conftest.py                  # pytest fixtures（SQLite in-memory + DuckDB in-memory）
│   ├── pit/                         # 13 场景点时回归测试（QS-C03 §12）
│   ├── risk/                        # 风控守卫单元测试
│   ├── execution/                   # 状态机单元测试（QS-C04）
│   ├── factor/                      # 因子纯函数单元测试
│   └── integration/                 # 端到端冒烟测试
│
├── notebooks/                       # Jupyter 研究笔记（.gitignore 排除输出）
│   ├── factor_research/
│   ├── backtest_analysis/
│   └── model_training/
│
└── docs/                            # 文档目录
    └── deliverables/                # 输出文档（软链至 workspace/deliverables）
```

### 3.2 模块依赖图

```
┌──────────────────────────────────────────────────────────────────┐
│                        外部依赖                                    │
│  AKShare  Tushare  BaoStock  xtquant  QMT终端  LLM API/本地Qwen   │
└───┬──────────┬──────────┬──────────┬──────────────────────────────┘
    │          │          │          │
    ▼          ▼          ▼          ▼
┌──────────────────────────┐  ┌──────────────────────────────────┐
│    data/adapters/         │  │    execution/adapters/           │
│  akshare_adapter          │  │   xtquant_adapter                │
│  tushare_adapter          │  │   backtest_adapter               │
│  baostock_adapter         │  └──────────────┬───────────────────┘
└──────────┬───────────────┘                 │ 仅执行层使用
           │                                 ▼
           ▼                    ┌──────────────────────────────────┐
    ┌─────────────┐             │         risk/guard.py            │
    │  data/      │             │   （唯一下单入口，强制前置）        │
    │  pipeline   │             └──────────────┬───────────────────┘
    │  arbitrator │                            │
    └──────┬──────┘                            ▼
           │                    ┌──────────────────────────────────┐
           ▼                    │  execution/state_machine.py      │
    ┌─────────────┐             │  order_sizing / outbox           │
    │   pit/      │             │  rate_limiter / idempotency      │
    │  query_     │◄────────────┤  (引用 pit/ 查询接口)             │
    │  engine     │             └──────────────────────────────────┘
    └──────┬──────┘
           │ 点时数据接口（canonical 四键）
           ▼
    ┌─────────────┐    ┌─────────────┐    ┌──────────────┐
    │  factor/    │    │  research/  │    │   signal/    │
    │  quality    │    │  backtest   │    │  core_factor │
    │  momentum   │───►│  lgbm_model │───►│  trend_sat   │
    │  volatility │    │  factor_sel │    │  mkt_timing  │
    │  flow/chips │    │  ledger/dsr │    │  merger      │
    └─────────────┘    └─────────────┘    └──────┬───────┘
                                                 │ 目标持仓表
                                                 ▼
                                          ┌─────────────┐
                                          │   risk/     │
                                          │  guard      │
                                          │  drawdown   │
                                          │  constraints│
                                          └──────┬──────┘
                                                 │ 风控裁剪后订单
                                          ┌──────▼──────┐
                                          │ reconcile/  │     ┌─────────────┐
                                          │  daily_recon│◄────│  monitor/   │
                                          │  cost_attr  │     │  watchdog   │
                                          └─────────────┘     │  alerter    │
                                                              │  dashboard  │
                                                              └─────────────┘
```

**关键约束：**
- `execution/adapters/xtquant_adapter.py` 是系统中**唯一** `import xtquant` 的文件
- `factor/`、`signal/`、`research/` 均为**纯函数模块**，无副作用，无 IO
- `risk/guard.py` 是唯一下单入口，任何路径都必须通过

---

## §4 三层隔离架构与接口定义

### 4.1 三层隔离原则

```
┌────────────────────────────────────────────────────────────┐
│ 第一层：策略逻辑层（纯 Python，平台无关）                      │
│   · 因子计算 / 选股逻辑 / 信号合并 / 风控校验，全部纯函数      │
│   · 禁止 import xtquant                                     │
│   · 不依赖系统时间（由调用方注入 visible_at）                  │
└────────────────────────┬───────────────────────────────────┘
                         │
                         │ 统一数据/订单接口（抽象层 ABC）
                ┌────────┴────────┐
                ▼                 ▼
┌──────────────────┐    ┌──────────────────────────┐
│ 第二层A：回测适配器│    │  第二层B：实盘适配器        │
│  BacktestAdapter │    │  XtquantAdapter           │
│  · 历史 Parquet  │    │  · QMT (xtquant)          │
│  · 模拟撮合      │    │  · order_remark 对账       │
└──────────────────┘    └──────────────┬────────────┘
                                       │
                                       ▼
                          ┌────────────────────────┐
                          │  第三层：风控守卫          │
                          │  risk_guard（唯一入口）   │
                          │  ↓                      │
                          │  xtquant 下单 API        │
                          │  ↓                      │
                          │  execution_ledger        │
                          └────────────────────────┘
```

### 4.2 数据接口 ABC（策略层可见）

```python
# src/execution/interfaces.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd


@dataclass
class PitMeta:
    """点时元数据六字段（QS-C03 §3.4，接口必带）"""
    pit_as_of: str               # 查询使用的 as_of 时间戳
    pit_data_cut_id: int         # data_cut_id
    pit_visible_at: str          # 命中行的 visible_at
    pit_revision_seq: int        # 命中行的 revision_seq
    pit_snapshot_rank: int       # 命中行的 snapshot_rank
    corp_actions_known_ver: Optional[int] = None  # 复权查询时的 base_event_ver


@dataclass
class BarData:
    """单日行情数据（含点时元数据）"""
    ts_code: str
    trade_date: str
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: Optional[float]
    pre_close: Optional[float]
    volume: Optional[float]
    amount: Optional[float]
    turnover_rate: Optional[float]
    adj_factor: Optional[float]   # PIT 前复权因子
    pit_meta: PitMeta


@dataclass
class OrderIntent:
    """订单意图（含幂等键和风控签名）"""
    client_order_id: str          # 幂等键（hash，见 QS-C04 §4.1）
    account_id: str
    strategy_id: str
    ts_code: str
    side: str                     # 'BUY' | 'SELL'
    order_type: str               # 'LIMIT' | 'MARKET'
    time_in_force: str            # 'DAY' | 'IOC' | 'GTC'
    target_qty: int
    limit_price: Optional[float]
    risk_signature: Optional[str] = None   # 风控签名（由 risk_guard 填充）
    order_remark: Optional[str] = None     # = client_order_id（xtquant 对账）
    rebalance_seq: int = 0
    parent_intent_id: Optional[str] = None


@dataclass
class FillEvent:
    """成交回报事件"""
    client_order_id: str
    broker_order_id: str
    ts_code: str
    side: str
    filled_qty: int
    avg_fill_price: float
    event_ts: str
    broker_status: str             # 'FILLED' | 'PARTIAL' | 'CANCELLED' | 'REJECTED'
    cancel_fill_type: str = 'NONE' # 'NONE' | 'FULL' | 'PARTIAL'（QS-C04 §1.2）
    raw_event_hash: Optional[str] = None


class DataFeedInterface(ABC):
    """
    数据接入抽象接口（策略层可见）
    回测适配器和实盘适配器均实现此接口。
    """

    @abstractmethod
    def get_daily_bars(
        self,
        ts_codes: list[str],
        date_range: tuple[str, str],
        as_of: str,
        data_cut_id: int,
        adjust: str = "qfq_pit"
    ) -> pd.DataFrame:
        """
        获取点时日线行情（canonical 四键排序）。
        返回 DataFrame 含 PIT_META 六字段。
        adjust: 'raw' | 'qfq_pit' | 'hfq_pit'
        """

    @abstractmethod
    def get_factor_snapshot(
        self,
        ts_codes: list[str],
        trade_date: str,
        as_of: str,
        data_cut_id: int,
        factor_names: list[str],
        variant: str = "processed"
    ) -> pd.DataFrame:
        """
        获取因子截面快照。
        variant: 'raw' | 'processed' | 'orthogonal'（QS-C03 §4.1）
        """

    @abstractmethod
    def get_active_universe(
        self,
        as_of: str,
        data_cut_id: int
    ) -> list[str]:
        """
        获取当日在市股票列表（含历史退市股，不做停牌过滤）。
        停牌/ST/流动性过滤交策略层处理（QS-C03 §7.3）。
        """


class BrokerInterface(ABC):
    """
    券商接入抽象接口（仅执行层可见，策略层禁止 import）
    实盘：XtquantAdapter；回测：BacktestAdapter
    """

    @abstractmethod
    def place_order(self, intent: OrderIntent) -> str:
        """
        下单。返回 broker_order_id。
        必须携带 risk_signature（由 risk_guard 填充）。
        """

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> bool:
        """撤单"""

    @abstractmethod
    def query_order(self, broker_order_id: str) -> Optional[FillEvent]:
        """查询委托状态（UNKNOWN 归位使用）"""

    @abstractmethod
    def get_positions(self, account_id: str) -> dict[str, int]:
        """获取实时持仓（broker_order_id → qty）"""

    @abstractmethod
    def get_cash(self, account_id: str) -> float:
        """获取可用资金"""
```

### 4.3 风控守卫装饰器（唯一下单入口）

```python
# src/risk/guard.py
import functools
import hashlib
import hmac
from dataclasses import dataclass
from typing import Callable, Optional

from src.execution.interfaces import OrderIntent, FillEvent
from src.risk.constraints import check_all_constraints
from src.risk.drawdown import check_drawdown_level
from src.execution.rate_limiter import RateLimiter
from src.execution.idempotency import IdempotencyStore


@dataclass
class RiskCheckResult:
    passed: bool
    signature: Optional[str]
    rejection_reason: Optional[str] = None
    adjusted_qty: Optional[int] = None   # 仓位裁剪后数量


class RiskGuard:
    """
    唯一下单入口守卫（QS-C01 铁律一）。
    任何路径——包括紧急操作、研究调试、手工指令——
    都不得绕过此守卫直接下单。
    """

    def __init__(
        self,
        broker: 'BrokerInterface',
        ledger: 'ExecutionLedger',
        rate_limiter: RateLimiter,
        idempotency_store: IdempotencyStore,
        risk_policy_version: str,
        secret_key: bytes
    ):
        self.broker = broker
        self.ledger = ledger
        self.rate_limiter = rate_limiter
        self.idempotency_store = idempotency_store
        self.risk_policy_version = risk_policy_version
        self._secret_key = secret_key

    def place_order(self, intent: OrderIntent) -> Optional[str]:
        """
        唯一下单入口。流程：
        1. 合规限速检查（先于业务逻辑，QS-C04 §6.1）
        2. 幂等键去重（DB UNIQUE 兜底）
        3. 风控约束校验（仓位/行业/流动性/回撤级别）
        4. 风控签名
        5. outbox 落 PENDING_SEND
        6. 调 broker.place_order
        返回 broker_order_id 或 None（被拒/重复）
        """
        # Step 1: 合规限速（QS-C04 §6.1，先于一切）
        if not self.rate_limiter.allow(intent.account_id):
            self._log_block(intent, "RATE_LIMIT_EXCEEDED")
            return None

        # Step 2: 幂等键去重
        if self.idempotency_store.exists(intent.client_order_id):
            return None  # no-op，不记录（静默去重）

        # Step 3: 风控约束校验
        check_result = self._run_risk_checks(intent)
        if not check_result.passed:
            self._log_block(intent, check_result.rejection_reason)
            return None

        # Step 4: 风控签名
        intent.risk_signature = self._sign(intent, check_result)
        intent.order_remark = intent.client_order_id  # xtquant 对账（QS-C04 §8）

        # Step 5: 幂等键注册 + outbox 落 PENDING_SEND
        self.idempotency_store.register(intent.client_order_id)
        self.ledger.record_pending(intent)

        # Step 6: 执行下单
        try:
            broker_order_id = self.broker.place_order(intent)
            self.ledger.record_submitted(intent, broker_order_id)
            return broker_order_id
        except Exception as e:
            self.ledger.record_failed(intent, str(e))
            raise

    def _run_risk_checks(self, intent: OrderIntent) -> RiskCheckResult:
        """调用所有风控规则，返回汇总结果"""
        # 回撤级别检查
        drawdown_level = check_drawdown_level()
        if drawdown_level >= 2:  # 25% 硬止损，仅允许减仓
            if intent.side == 'BUY':
                return RiskCheckResult(False, None, "LV2_DRAWDOWN_BLOCK_BUY")
        # 仓位/行业/流动性约束
        result = check_all_constraints(intent)
        if not result.passed:
            return result
        return RiskCheckResult(passed=True, signature=None)

    def _sign(self, intent: OrderIntent, result: RiskCheckResult) -> str:
        """HMAC-SHA256 风控签名（QS-C04 §4.4）"""
        payload = (
            f"{intent.account_id}|{intent.strategy_id}|"
            f"{intent.client_order_id}|{intent.ts_code}|"
            f"{intent.side}|{intent.target_qty}|"
            f"{self.risk_policy_version}"
        ).encode()
        return hmac.new(self._secret_key, payload, hashlib.sha256).hexdigest()

    def _log_block(self, intent: OrderIntent, reason: str):
        """记录被拒下单（铁律一要求：物理熔断写日志，不得静默）"""
        self.ledger.record_risk_block(intent, reason)
```

### 4.4 回测适配器 ABC

```python
# src/execution/adapters/base.py
from abc import ABC, abstractmethod
from src.execution.interfaces import (
    DataFeedInterface, BrokerInterface, OrderIntent, FillEvent
)


class BaseExecutionAdapter(DataFeedInterface, BrokerInterface, ABC):
    """
    回测/实盘适配器共同基类。
    策略逻辑层只能看到 DataFeedInterface + BrokerInterface，
    不直接依赖此基类。
    """

    @abstractmethod
    def initialize(self, config: dict) -> None:
        """初始化适配器（连接/打开数据库/登录）"""

    @abstractmethod
    def teardown(self) -> None:
        """清理资源"""
```

---

## §5 配置管理规范

### 5.1 冻结参数 vs 可调参数分离原则

| 分类 | 文件 | 格式 | 变更策略 | 示例 |
|------|------|------|---------|------|
| **冻结参数** | `config/frozen.toml` | TOML | 需重启进程；变更须 git commit + code review | 样本切分日期、铁律参数、状态机硬约束、成本模型 ID |
| **可调参数** | `config/tunable.yaml` | YAML | 盘后人工调整，次日生效；不需重启 | 因子权重、大盘择时档位阈值、告警阈值 |
| **机密参数** | `secrets/.env` | .env | 不入 Git；本机加密存储；OS 环境变量注入 | API Token、HMAC 密钥、钉钉 Webhook URL |

### 5.2 冻结参数示例（frozen.toml）

```toml
# config/frozen.toml
# 冻结参数：任何修改须重启进程并记录 git commit

[project]
version = "2.0"
ssot_version = "baseline_spec_v2"

[data_split]
train_start = "2016-01-01"
train_end   = "2021-12-31"
val_start   = "2022-01-01"
val_end     = "2023-12-31"
test_start  = "2024-01-01"   # 物理封锁：research cut_date 必须 < 此值（QS-C03 §7.1）
test_end    = "2025-12-31"

[risk]
drawdown_warning_pct  = 0.20   # 一级预警（降仓 50%）
drawdown_halt_pct     = 0.25   # 二级硬止损（全清仓）
single_stock_max_pct  = 0.08   # 单票上限 8%
industry_max_pct      = 0.30   # 单行业上限 30%
min_daily_turnover    = 50_000_000   # 日均成交额下限

[execution]
rate_limit_per_sec    = 1       # 1 笔/秒（QS-C04 §6.1）
rate_limit_per_day    = 200     # 200 笔/日（QS-C04 §6.1）
cancel_timeout_s      = 15
order_report_timeout_s = 30
heartbeat_interval_s  = 15
heartbeat_miss_max    = 3
cutoff_new_signal     = "14:55"   # 常规调仓截止时间

[cost]
cost_model_id         = "cm_v3_baseline"   # M2 阶段；M3 后改 cm_v3_advanced
stamp_duty_sell_pct   = 0.0005
commission_pct        = 0.00025
min_commission        = 5.0
transfer_fee_pct      = 0.00001
default_slippage_pct  = 0.002

[neff]
test_eval_budget      = 6       # test 段评估次数上限（QS-CAL-001）
val_view_budget       = 5       # validation 查看预算

[compliance]
regulation_date       = "2025-07-07"   # 程序化交易细则施行日
```

### 5.3 可调参数示例（tunable.yaml）

```yaml
# config/tunable.yaml
# 可调参数：盘后人工调整，进程重载时生效（无需重启）

factor_weights:
  roe:             0.15
  momentum_60d:    0.20
  volatility_60d: -0.15
  moneyflow_net:   0.20
  holder_chg:      0.15
  lgbm_score:      0.15

portfolio:
  core_factor_weight:    0.78  # 75-80%
  trend_satellite_weight: 0.22  # 20-25%

market_timing:
  bull_threshold_pct: 0.0    # 沪深 300 在 200MA 上方
  bear_threshold_pct: 0.0    # 跌破 200MA
  bull_position_cap:  1.00   # 90-100%
  neutral_position_cap: 0.60
  bear_position_cap:  0.30
  confirmation_days:  3      # N 日确认延迟

alerts:
  pipeline_fail_level: "MEDIUM"
  risk_trigger_level:  "HIGH"
  process_miss_level:  "HIGH"
  recon_diff_level:    "MEDIUM"
```

### 5.4 配置加载工具

```python
# src/config.py
import tomllib
import yaml
import os
from pathlib import Path
from dotenv import load_dotenv

_CONFIG_DIR = Path(__file__).parent.parent / "config"
_SECRETS_DIR = Path(__file__).parent.parent / "secrets"


def load_frozen() -> dict:
    """加载冻结参数（不可运行时修改）"""
    with open(_CONFIG_DIR / "frozen.toml", "rb") as f:
        return tomllib.load(f)


def load_tunable() -> dict:
    """加载可调参数（热重载安全）"""
    with open(_CONFIG_DIR / "tunable.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_secrets() -> None:
    """从 .env 文件加载机密参数到环境变量"""
    load_dotenv(_SECRETS_DIR / ".env")


# 全局单例（进程启动时初始化一次）
FROZEN = load_frozen()
```

---

## §6 日志规范

### 6.1 日志层级与格式

```python
# src/logger.py
import logging
import json
from datetime import datetime, timezone


class StructuredLogger:
    """
    结构化日志（JSON Lines 格式）。
    每行一个 JSON 对象，便于机器解析与 Streamlit 看板读取。
    """

    LOG_DIR = "logs/"
    ROTATE_DAYS = 90  # 90 天滚动压缩归档

    LEVELS = {
        "DEBUG":    logging.DEBUG,
        "INFO":     logging.INFO,
        "WARNING":  logging.WARNING,
        "ERROR":    logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }

    @staticmethod
    def log(
        level: str,
        module: str,
        event: str,
        **kwargs
    ) -> None:
        """
        结构化日志条目格式：
        {
          "ts": "2026-06-12T09:30:01.123+08:00",
          "level": "INFO",
          "module": "execution.state_machine",
          "event": "STATE_TRANSITION",
          "from_state": "ORDER_INTENT",
          "to_state": "PRE_FIRE_CHECK",
          "client_order_id": "abc123",
          ...
        }
        """
        entry = {
            "ts": datetime.now(tz=timezone.utc).astimezone().isoformat(),
            "level": level,
            "module": module,
            "event": event,
            **kwargs
        }
        print(json.dumps(entry, ensure_ascii=False))
```

### 6.2 必须记录的关键事件

| 事件类型 | 级别 | 必含字段 | 说明 |
|---------|------|---------|------|
| 状态机迁移 | INFO | from_state, to_state, client_order_id, event | QS-C04 全程留痕 |
| 风控拒单 | WARNING | ts_code, side, qty, rejection_reason, risk_policy_version | 铁律一要求 |
| 物理熔断触发 | CRITICAL | break_glass_signature, trigger_reason, positions_at_trigger | 不可静默 |
| 下单成功 | INFO | broker_order_id, order_remark, filled_qty, avg_price | |
| 数据质量异常 | WARNING | table_name, ts_code, trade_date, quality_flag | |
| 三源冲突 | ERROR | ts_code, trade_date, akshare_val, tushare_val, baostock_val | CRITICAL 告警 |
| 进程心跳 | DEBUG | pid, heartbeat_ts | 每 30s |
| 对账差异 | WARNING/ERROR | ts_code, theory_qty, broker_qty, ledger_qty, diff_qty | |
| IC 背离告警 | WARNING | rolling_26w_ic, research_ic, bootstrap_lower_bound | 引用 QS-C02 |

### 6.3 日志文件管理

```
logs/
├── execution/
│   ├── execution_2026-06-12.jsonl     # 当日执行日志
│   └── execution_2026-06-12.jsonl.gz  # 90 天后压缩
├── pipeline/
│   ├── pipeline_2026-06-12.jsonl
├── monitor/
│   ├── monitor_2026-06-12.jsonl
└── audit/
    └── audit_2026-06-12.jsonl         # 风控拒单 + 熔断（永久保留，不滚动删除）
```

---

## §7 错误处理策略

### 7.1 错误分类与处理原则

| 错误类别 | 示例 | 处理策略 | 状态机响应 |
|---------|------|---------|---------|
| **可重试错误** | xtquant 网络超时（单次）| 最多重试 3 次，指数退避（1s/2s/4s） | 保持当前状态 |
| **不可重试错误** | 券商拒单（资金不足/权限）| 立即记录，不重试 | → REJECTED |
| **未知态错误** | 查不到委托 / 连接断开 | 进入 UNKNOWN，双查后归位 | → UNKNOWN |
| **系统级错误** | 进程崩溃/OOM | outbox 三态恢复（QS-C04 §4.3）| 重启后恢复 |
| **数据质量错误** | 三源冲突 / 缺失 | 告警升级 + quality_flag='CONFLICT' | 阻塞因子计算 |
| **配置错误** | 冻结参数非法值 | 进程启动时验证，失败则拒绝启动 | 不启动 |

### 7.2 重试装饰器

```python
# src/utils/retry.py
import time
import functools
from typing import Type, tuple as Tuple


def retry(
    max_attempts: int = 3,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    base_delay_s: float = 1.0,
    backoff_factor: float = 2.0,
    on_fail_raise: bool = True
):
    """
    指数退避重试装饰器。
    仅用于可重试错误（网络超时等），
    不用于券商拒单、风控拒单等业务错误。
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt < max_attempts - 1:
                        delay = base_delay_s * (backoff_factor ** attempt)
                        time.sleep(delay)
            if on_fail_raise:
                raise last_exc
            return None
        return wrapper
    return decorator
```

### 7.3 悲观默认原则（Fail-Closed）

```python
# 任何未知/超时/不一致状态，默认动作为停单（QS-C04 设计原则 §0.2）
def handle_unknown_state(order_id: str, context: dict) -> str:
    """
    遇到不确定状态时的默认处理：
    - 不猜测，不继续下单
    - 记录上下文，触发告警
    - 进入 UNKNOWN 态等待人工或双查归位
    """
    logger.log("ERROR", "state_machine", "UNKNOWN_STATE_ENTERED",
               order_id=order_id, **context)
    alert_manager.send_alert("HIGH", f"订单 {order_id} 进入 UNKNOWN 态")
    return "UNKNOWN"
```

---

## §8 幂等与恢复设计

### 8.1 幂等键设计（引用 QS-C04 §4.1）

```python
# src/execution/idempotency.py
import hashlib
from typing import Optional
import sqlite3


def gen_idempotency_key(
    account_id: str,
    strategy_id: str,
    trade_date: str,
    ts_code: str,
    side: str,
    rebalance_seq: int      # 全局单调递增（非按日重置）
) -> str:
    """
    幂等键 = HMAC-SHA256 截断 32 字节十六进制。
    只锚定「哪一笔下单决策」，绝不含 target_weight（会因重算抖动）。
    rebalance_seq 全局单调递增，杜绝跨日键碰撞。
    """
    raw = f"{account_id}|{strategy_id}|{trade_date}|{ts_code}|{side}|{rebalance_seq}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class IdempotencyStore:
    """SQLite 幂等键存储（DB UNIQUE 约束兜底）"""

    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS idempotency_keys (
                client_order_id TEXT PRIMARY KEY,
                registered_at   TEXT NOT NULL
            )
        """)

    def exists(self, client_order_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM idempotency_keys WHERE client_order_id = ?",
            (client_order_id,)
        ).fetchone()
        return row is not None

    def register(self, client_order_id: str) -> None:
        """注册幂等键（唯一约束保护）"""
        self.conn.execute(
            "INSERT OR IGNORE INTO idempotency_keys VALUES (?, datetime('now'))",
            (client_order_id,)
        )
        self.conn.commit()
```

### 8.2 outbox 三态恢复（引用 QS-C04 §4.3）

```python
# src/execution/outbox.py
from enum import Enum
from dataclasses import dataclass
from typing import Optional


class OutboxState(str, Enum):
    NOT_SENT_CAN_SEND  = "NOT_SENT_CAN_SEND"   # send_started_at 为空 → 安全重发
    MAYBE_SENT_UNKNOWN = "MAYBE_SENT_UNKNOWN"   # 已发未 committed → 不可重发
    SENT_CONFIRMED     = "SENT_CONFIRMED"        # 已 committed → 按 broker_order_id 归位


def classify_outbox_state(
    send_started_at: Optional[str],
    send_committed_at: Optional[str],
    broker_order_id: Optional[str]
) -> OutboxState:
    """
    三态分类（崩溃恢复核心逻辑）：
    防止「已发送未回报」被误重发（重复下单）。
    """
    if send_started_at is None:
        return OutboxState.NOT_SENT_CAN_SEND
    if send_committed_at is not None and broker_order_id is not None:
        return OutboxState.SENT_CONFIRMED
    return OutboxState.MAYBE_SENT_UNKNOWN


def recover_pending_orders(ledger, broker, alert_manager, resend_window_s: int = 300):
    """
    启动时恢复 PENDING_SEND 订单。
    MAYBE_SENT_UNKNOWN 类型绝不重发，进入 MANUAL_REVIEW。
    """
    for intent in ledger.get_pending_send_orders():
        state = classify_outbox_state(
            intent.send_started_at,
            intent.send_committed_at,
            intent.broker_order_id
        )
        match state:
            case OutboxState.NOT_SENT_CAN_SEND:
                # 安全重发（窗口内）
                import time
                age_s = (time.time() - intent.event_ts_epoch)
                if age_s < resend_window_s:
                    broker.place_order(intent)
                    alert_manager.send_alert("INFO", f"重发订单 {intent.client_order_id}")
                else:
                    ledger.mark_expired(intent.client_order_id)

            case OutboxState.SENT_CONFIRMED:
                # 按 broker_order_id 归位
                ledger.reconcile_confirmed(intent)

            case OutboxState.MAYBE_SENT_UNKNOWN:
                # 绝不重发，进入 MANUAL_REVIEW
                ledger.mark_manual_review(intent.client_order_id, "MAYBE_SENT_UNKNOWN")
                alert_manager.send_alert("HIGH", f"订单 {intent.client_order_id} MAYBE_SENT_UNKNOWN，需人工处理")
```

---

## §9 数据流图

### 9.1 盘后管道时序（17:00 采集 → 次日委托单生成）

```
17:00  盘后数据管道启动（Windows 任务计划触发）
  │
  ├─► 【三源并行采集】
  │     AKShare（source_priority=1）：日线行情 + 龙虎榜 + 资金流 + 停复牌/ST
  │     Tushare Pro（source_priority=2）：日线 + 财报 + 股东户数
  │     BaoStock（source_priority=3）：日线（冗余校验）
  │     visible_at = 当日 17:00+08:00（QS-C03 §10.1）
  │
  ├─► 【三源两票制裁决（QS-C03 §9.2）】
  │     AKShare == Tushare → 采用 AKShare，BaoStock 差异标注 WARNING
  │     AKShare == BaoStock → 采用 AKShare，Tushare 差异人工次日复核
  │     Tushare == BaoStock → AKShare 异常，采用 Tushare，升级告警
  │     三源均不一致 → CRITICAL 告警，quality_flag='CONFLICT'，暂停入库
  │
  ├─► 【数据落库】
  │     DuckDB/Parquet：daily_bar_raw（按年分区）
  │     SQLite：corporate_action（除权除息）
  │     SQLite：snapshot_manifest（记录本次采集元数据）
  │
  ├─► 【validate_data_gate 质检】
  │     行情覆盖率 ≥99%
  │     核心字段缺失率 <0.5%
  │     复权因子完整性 = 0
  │     validate_adj_coverage（QS-C03 §11.9）
  │     → 质检不通过：告警 + 阻塞后续因子计算
  │
18:00  因子计算（质检通过后触发）
  │
  ├─► 【因子计算（纯函数，visible_at 严格遵守）】
  │     调用 pit/query_engine.py 的 canonical 四键查询接口
  │     各因子（质量/动量/低波/资金流/筹码）计算
  │     MAD 去极值 + 市值/行业中性化 + z-score（processed 变体）
  │     Cholesky 残差化（orthogonal 变体，LightGBM 用）
  │     写入 factor_snapshot（DuckDB/Parquet）
  │
  ├─► 【LightGBM 因子合成（如模型已训练）】
  │     输入：orthogonal 变体因子截面
  │     输出：lgbm_score（0-1 排名得分）
  │     写入 factor_snapshot（lgbm_score 因子）
  │
19:00  信号生成
  │
  ├─► 【核心多因子信号】
  │     全市场过滤：剔除 ST/停牌/上市<250日/日均成交额<5000万
  │     线性加权打分 + LightGBM 排名融合 → 融合排序
  │     选 Top 10-15 只 → 波动率倒数加权 → 目标仓位
  │
  ├─► 【趋势卫星信号】
  │     唐奇安 20 日突破检测
  │     2×ATR 跟踪止损位计算
  │     → 卫星持仓目标
  │
  ├─► 【大盘择时总开关】
  │     沪深 300 vs 200 日 MA → 三档仓位上限
  │     N 日确认（防假信号）
  │     → 组合仓位上限
  │
  ├─► 【信号合并 → 目标持仓表】
  │     core_positions + satellite_positions
  │     仓位上限裁剪（大盘择时总开关）
  │     → target_positions（SQLite，带 strategy_id）
  │
20:00  次日委托单生成
  │
  └─► 【ORDER_SIZING 预计算（次日待确认）】
        差量 = target_positions - current_positions
        T+1 可卖量检查
        100 股取整
        现金约束
        → pending_orders（SQLite，状态 PRE_MARKET）
        → 盘前 9:15 dry-run 对比（确认无异常）

次日 9:15  执行守护进程启动
  │
  ├─► 开盘前 dry-run 对齐（拉券商真实持仓 vs ledger 末态）
  ├─► 不一致 → 先进 RECONCILE
  ├─► 一致 → 进入 IDLE
  └─► 9:30 收到交易信号 → 开始执行（TARGET_GEN → RISK_CLIP → ... → SUBMITTED）
```

### 9.2 盘中执行时序

```
09:30  交易时段开始
  │
  ├─► 【接收调仓信号（来自盘后预生成的 pending_orders）】
  │     14:55 后拒收常规调仓信号（排队次日）
  │     减仓/break-glass 不受 14:55 限制
  │
  ├─► 【状态机主循环（QS-C04）】
  │     IDLE → TARGET_GEN → RISK_CLIP → ORDER_SIZING
  │               ↓ 20% 回撤触发           → ORDER_INTENT → PRE_FIRE_CHECK
  │           降仓路径（先卖卫星）                               ↓
  │               ↓ 25% 回撤触发                           SUBMITTED
  │           清仓路径（全清+冻结新开仓）              LIVE/PARTIAL/FILLED/REJECTED
  │
  ├─► 【盘中风控监控（每 15s 心跳）】
  │     账户净值实时计算 → 回撤检测
  │     持仓偏离监控
  │     进程健康检查（② ↔ ⑤ 互查）
  │
  ├─► 【委托跟踪与撤单】
  │     LIVE 超 order_ttl → 发撤单 → CANCEL_REQUESTED
  │     14:55 LIVE 单等交易所 EOD 自动撤单
  │     UNKNOWN 态 → 双查一致后归位
  │
14:55  停止接收常规调仓信号
15:00  交易结束
  │
  ├─► 【EOD 处理】
  │     DAY 单：等撤单回报 → CANCELLED
  │     未提交意图：作废 + 释放 reservation
  │
15:30  日终对账
  │
  └─► FILLED/CANCELLED/REJECTED → RECONCILE
        理论持仓 C（position_ledger）vs 券商实际 vs execution_ledger
        差异分类（corp_action/零股/现金尾差）
        可解释 → 归档 + 标记日终完成 → IDLE
        不可解释 → 暂停（MANUAL_REVIEW）+ 告警
```

---

## §10 安全设计

### 10.1 账户凭据管理

```
secrets/.env（不入 Git）：
  TUSHARE_TOKEN=xxx          # Tushare Pro token
  DINGTALK_WEBHOOK=https://...  # 钉钉机器人 Webhook
  SERVER_CHAN_KEY=xxx         # Server 酱推送密钥
  RISK_HMAC_KEY=xxx           # 风控签名密钥（32 字节十六进制）
  BREAK_GLASS_KEY=xxx         # 熔断专用密钥（独立于 risk_hmac）
  XTQUANT_ACCOUNT=xxx         # QMT 账户 ID
```

**原则：**
- 所有机密通过 OS 环境变量注入，代码中禁止硬编码任何 Token/密钥
- secrets/ 目录加入 .gitignore，永不上传
- xtquant 账户密码不在代码中出现（由 QMT 客户端登录管理）
- 定期轮换：Server 酱/钉钉 Webhook 每 3 个月轮换

### 10.2 本机防护

| 防护措施 | 具体实施 |
|---------|---------|
| Windows 防火墙 | 仅允许本机进程访问 SQLite/DuckDB 文件（无 TCP 监听） |
| 用户权限 | 用非 Administrator 账户运行量化进程（最小权限原则）|
| 屏幕锁定 | 离开时锁屏（Windows + L），防止物理访问 |
| Streamlit 看板 | 仅绑定 127.0.0.1，不暴露外网 |
| 断路器文件 | run/order_token.lock 仅当前用户可写 |
| 研究环境隔离 | Jupyter 仅非交易时段运行；禁止研究环境访问 xtquant |

### 10.3 备份加密（rclone + 外置盘 + 云盘）

```bash
# rclone 配置示例（每日盘后 17:30 任务计划触发）

# 1. SQLite 备份到外置盘
rclone copy db/quant.db "外置盘:/QuantSolo/backup/$(date +%Y%m%d)/" \
    --checksum --log-file=logs/rclone.log

# 2. SQLite 备份到云盘（加密）
rclone copy db/quant.db "OneDrive:QuantSolo/encrypted/" \
    --crypt-password="$RCLONE_CRYPT_KEY" \
    --checksum

# 3. Parquet 增量同步
rclone sync data/ "外置盘:/QuantSolo/data/" \
    --checksum --exclude "*.tmp"

# 4. 代码备份（Git push）
git push origin main

# 周度恢复演练（每周日自动触发）：
rclone copy "外置盘:/QuantSolo/backup/latest/quant.db" /tmp/restore_test/
python scripts/verify_restore.py /tmp/restore_test/quant.db
```

**备份策略：**

| 备份对象 | 频率 | 保留期 | 目标 | 加密 |
|---------|------|--------|------|------|
| SQLite（execution_ledger）| 每日盘后 | 永久 | 外置盘 + 云盘 | 可选（rclone crypt）|
| DuckDB（行情/因子）| 每日盘后 | 1 年滚动 | 外置盘 + 云盘 | 可选 |
| Parquet 文件 | 每日盘后 | 3 年（按年 tar.gz）| 外置盘 | 无（无机密）|
| 代码仓库 | 每次 commit | 永久 | GitHub/Gitee 私有仓库 | Git 仓库权限控制 |
| audit 日志 | 每日 | 永久（不滚动删除）| 外置盘 | 无 |

### 10.4 安全应急 SOP

```
场景：PC 感染病毒/遭入侵
  1. 立即断开网络（拔网线 + 关闭热点）
  2. 打开手机券商 APP，人工检查持仓（break-glass SOP）
  3. 如有异常持仓，手动平仓
  4. 从外置盘恢复系统（重装 Windows + 恢复备份）
  5. 轮换所有 Token/密钥
  6. 恢复后重新合规报备（程序化交易软件变更更新报备）
```

---

## 附录 A 依赖库版本清单

| 库名 | 最低版本 | 用途 | 安装说明 |
|------|---------|------|---------|
| Python | 3.11 | 运行时 | Windows 官网下载 |
| pandas | 2.0.0 | 数据处理 | `pip install pandas` |
| polars | 0.20.0 | 批量列处理 | `pip install polars` |
| pyarrow | 14.0.0 | Parquet IO | `pip install pyarrow` |
| duckdb | 0.10.0 | 分析查询 | `pip install duckdb` |
| lightgbm | 4.0.0 | ML 模型 | `pip install lightgbm` |
| scikit-learn | 1.4.0 | 预处理/Ridge | `pip install scikit-learn` |
| akshare | 1.12.0 | 主数据源 | `pip install akshare` |
| tushare | 1.2.89 | 辅数据源 | `pip install tushare` |
| baostock | 0.8.9 | 冗余数据源 | `pip install baostock` |
| xtquant | 随 QMT | 实盘执行 | 从 QMT 客户端复制到 site-packages |
| apscheduler | 3.10.0 | 任务调度 | `pip install apscheduler` |
| streamlit | 1.30.0 | 看板 | `pip install streamlit` |
| requests | 2.31.0 | HTTP 告警推送 | `pip install requests` |
| pyyaml | 6.0.1 | 配置解析 | `pip install pyyaml` |
| python-dotenv | 1.0.0 | 机密加载 | `pip install python-dotenv` |
| rclone | 1.66.0 | 备份同步 | rclone.org 下载 Windows 可执行文件 |
| pytest | 8.0.0 | 测试框架 | `pip install pytest` |
| pytest-cov | 4.1.0 | 覆盖率 | `pip install pytest-cov` |
| ruff | 0.4.0 | Lint | `pip install ruff` |
| mypy | 1.9.0 | 类型检查 | `pip install mypy` |

---

*文档编号 QS-E02 · 版本 v1.0 · 日期 2026-06-12 · 上游依赖 SSOT/QS-C01/QS-C03/QS-C04 · 与 SSOT 冲突时以 SSOT 为准，已全文校验。*
