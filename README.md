# QuantSolo

一人公司 A 股周度中低频量化交易系统。20 万本金、家用 Windows 11 PC、国金 QMT/xtquant 实盘通道。

> **当前阶段：M1 数据基建基线（v0.1.0）**
> 本仓库已落地 M1 阶段的可运行骨架：仓库结构、冻结参数与守卫工具、三源数据适配器、点时查询引擎、13 场景点时回归测试。M2（研究+执行）、M3（模拟盘）模块为接口骨架，按 `docs/deliverables/QuantSolo_项目实施计划_v1.0.md` 逐周填充。

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
src/factor/  因子计算（纯函数，M2）
src/research/ 回测/筛选/DSR/LightGBM（M2）
src/signal/  核心多因子 + 趋势卫星 + 大盘择时（M2）
src/risk/    风控守卫（唯一下单入口）+ 三级回撤 + 硬约束
src/execution/ 15 态状态机 + 幂等 + outbox + 限速 + xtquant 适配器（M2-M3）
src/reconcile/ 三方对账 + 成本归因（M3）
src/monitor/ watchdog + 告警 + 看板（M3）
tools/       静态守卫扫描 / 冻结参数校验 / 闸门校准复现
tests/pit/   13 场景点时回归（QS-C03 §12）
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
