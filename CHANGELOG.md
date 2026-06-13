# Changelog

All notable changes to QuantSolo are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- **src/orchestration/** — 编排层（M4）完整包
  - `demo_data.py` — 合成 A 股数据种子（30 只股票, 2022-01-04 ~ 2024-06-28）
  - `research_pipeline.py` — 因子→信号→向量化回测→三阶段闸门完整流水线
  - `trading_pipeline.py` — 信号→风控→状态机→订单→对账→告警完整流水线
  - `e2e.py` — 端到端串联（selfcheck → seed → research → paper-trade → recon → report）
  - `cli.py` — CLI 入口函数集合
- **src/__main__.py** — `python -m src <cmd>` 入口（7 个子命令）
- **tools/golive_readiness.py** — Go-Live 就绪检查（G1-G8 八项，exit 0 = 全绿）
- **Makefile** — 常用开发命令快捷方式
- **.env.example** — 环境变量模板
- **tests/orchestration/** — 编排层冒烟测试
  - `test_e2e_smoke.py` — 端到端冒烟测试套件

---

## [0.3.0] — 2025-Q2 (M3: 监控与对账)

### Added
- `src/monitor/` — 系统监控模块
  - `alerter.py` — AlertManager，支持企业微信/飞书/Slack Webhook
  - `watchdog.py` — WatchDog，实盘健康检查
- `src/reconcile/` — 每日对账模块
  - `daily_recon.py` — DailyRecon，持仓 / 现金 / 成本三重校验
  - `cost_attribution.py` — 成本归因（摩擦成本拆解）
- 289 个测试全绿（含 PIT 完整性测试）

### Changed
- `config/frozen.toml` — 新增 `[acceptance]` 与 `[compliance]` 区块

---

## [0.2.0] — 2025-Q1 (M2: 因子与信号)

### Added
- `src/factor/` — 因子计算管线
  - `pipeline.py` — FactorPipeline，支持 stage1/stage2/stage3 过滤
- `src/signal/` — 信号合成模块
  - `core_factor.py` — CoreFactorSignal
  - `satellite_factor.py` — SatelliteFactorSignal
  - `merger.py` — merge_core_satellite_signals, apply_industry_cap
  - `market_timing.py` — 择时模块
- `src/research/` — 研究回测
  - `backtest/vectorized.py` — VectorizedBacktest
  - `gates.py` — run_full_gate_check（三阶段 + 压力测试）
  - `trial_log.py` — log_trial，SQLite 实验记录

### Changed
- `src/data/` — PIT 查询引擎升级，支持 `revision_seq` 排序

---

## [0.1.0] — 2024-Q4 (M1: 数据与基础设施)

### Added
- `src/data/` — 数据层
  - `pipeline.py` — DataPipeline（日线/财务/因子快照）
  - `ingestor.py` — 数据摄取
- `src/pit/` — PIT（Point-in-Time）查询引擎
  - `query_engine.py` — PITQueryEngine，防未来函数
- `src/execution/` — 执行层
  - `interfaces.py` — Order, Fill, Position, PositionTarget, GuardDecision
  - `state_machine.py` — OrderStateMachine（15 状态）
  - `order_sizing.py` — compute_portfolio_sizing
  - `adapters/backtest_adapter.py` — BacktestAdapter（纸面/回测）
  - `adapters/xtquant_adapter.py` — XtQuantAdapter（实盘，受 xtquant 白名单保护）
  - `break_glass.py` — 紧急熔断（白名单）
- `src/risk/` — 风控
  - `guard.py` — RiskGuard，单股/行业/组合三层护栏
- `src/common/` — 通用工具
  - `config.py` — load_frozen, load_tunable（MappingProxyType 不可变）
  - `decimal_utils.py` — Decimal 工具函数
- `tools/static_guard_scan.py` — 静态 xtquant 扫描（exit 0 = 无违规）
- `tools/frozen_params_check.py` — frozen.toml 完整性检查（exit 0 = 通过）
- `config/frozen.toml` — 不可变参数（gates/risk/compliance/cost/acceptance）

---

[Unreleased]: https://github.com/your-org/quant-solo/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/your-org/quant-solo/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/your-org/quant-solo/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/your-org/quant-solo/releases/tag/v0.1.0
