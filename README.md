# QuantSolo

一人公司 A 股周度中低频量化交易系统。20 万本金、家用 Windows 11 PC、国金 QMT/xtquant 实盘通道。

> **当前阶段：M1→M2 可运行骨架（v0.1.0）**
> 本仓库已落地全链路可运行骨架并通过自检（289 项测试通过、6 跳过）：仓库结构、冻结参数与守卫工具、三源数据适配器、点时查询引擎、13 场景点时回归；以及 M2 研究/执行模块——因子计算引擎、信号生成器、回测引擎（向量化+事件驱动双层互验）、研究统计闸门（BH-FDR/DSR/walk-forward/N_eff 预算/A·B 闸门）、风控守卫、15 态执行状态机；M3 运维模块——三方对账、成本归因、监控告警、watchdog、看板。实盘接入与参数标定按 `docs/deliverables/QuantSolo_项目实施计划_v1.0.md` 逐周推进。

## 第一性约束（红线，见 `AGENTS.md`）

1. **风控不可绕过**：`src/risk/guard.py` 是唯一下单入口；`import xtquant` 仅限 `src/execution/adapters/xtquant_adapter.py` 与熔断脚本。
2. **点时正确性**：一切研究数据按 `visible_at` 重放，canonical 四键排序；点时表只追加不修改。
3. **冻结参数不可改**：`config/frozen.toml` 受 SHA256 校验保护，改动须走宪法修订流程。
4. **单人可运维**：DuckDB/Parquet + SQLite 两层存储，零运维数据库。

## 目录结构

```
config/      冻结参数(frozen.toml) / 可调参数(tunable.yaml) / 三源优先级
src/data/    三源适配器 + 两票制裁决 + visible_at 赋值 + 盘后管道
src/pit/     点时查询引擎（canonical 四键）+ 校验器
src/factor/  因子计算：transforms(去极值/中性化/正交) + 动量 + 质量 + 三变体流水线（纯函数）
src/research/ 回测引擎(向量化 vectorized + 事件驱动 event_driven) + 双成本档 cost_models
src/research/ 统计闸门 gates(BH-FDR/Bonferroni/A1·A2·B1·B2·B3) + walk_forward + deflated_sharpe + trial_log(N_eff 预算)
src/signal/  核心多因子 core_factor + 大盘择时 market_timing + 核心·卫星合并 merger
src/risk/    风控守卫（唯一下单入口）+ 三级回撤 + 硬约束
src/execution/ 15 态状态机 + 幂等 + outbox + 限速 + xtquant 适配器
src/reconcile/ 三方对账 daily_recon + 成本偏差归因 cost_attribution
src/monitor/ 告警器 alerter(Server酱/钉钉) + watchdog 双向互查 + Streamlit 看板 dashboard
tools/       静态守卫扫描 / 冻结参数校验 / 闸门校准复现
tests/       point-in-time 13 场景回归 + factor/signal/research/risk/execution/reconcile/monitor/integration 单测
docs/        全套宪法(QS-C)+工程(QS-E)文档
```

## 快速开始

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev,data]"
python tools/frozen_params_check.py --register      # 首次登记冻结参数哈希
pre-commit install

# 自检命令（QS-E07 §3.3，提交前必跑）
python tools/static_guard_scan.py
python tools/frozen_params_check.py
pytest tests/pit -q
pytest -q -m "not live"
```

## 文档导航

从 `docs/deliverables/QuantSolo_项目总览索引_v2.0.md`（QS-C00）读起。开发遵循 `AGENTS.md`（QS-E07）。
人工执行事项与运维 SOP 见 `docs/deliverables/QuantSolo_人工执行事项与部署运维迭代SOP_v1.0.md`（QS-E08）。

## 许可

私有项目，保留所有权利。
