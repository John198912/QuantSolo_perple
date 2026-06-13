"""python -m src 入口（QS-E09 §5）。

子命令：
  selfcheck       — 跑静态守卫+冻结校验+pytest not live
  seed-demo       — 生成合成演示数据（写入 data/ db/）
  e2e             — 端到端跑通全链路
  research        — 仅运行研究管线（因子→信号→回测→闸门）
  paper-trade     — 仅运行模拟交易管线（信号→风控→撮合→对账）
  datasource-doctor — 检查 .env/凭据是否就绪
  golive-check    — 上线就绪门检查（调用 tools/golive_readiness.py）

用法示例：
  python -m src seed-demo
  python -m src e2e
  python -m src research
  python -m src paper-trade --trade-date 2023-06-15
  python -m src selfcheck
  python -m src datasource-doctor
  python -m src golive-check
"""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src",
        description="QuantSolo 量化系统 CLI（离线一键跑通全链路）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # selfcheck
    p_selfcheck = subparsers.add_parser("selfcheck", help="运行静态守卫+冻结参数+pytest not live")

    # seed-demo
    p_seed = subparsers.add_parser("seed-demo", help="生成合成演示数据（~30只标的，~2.5年日频）")
    p_seed.add_argument("--force", action="store_true", help="强制重新生成（覆盖已有数据）")

    # e2e
    p_e2e = subparsers.add_parser("e2e", help="端到端跑通全链路（seed→research→paper-trade→reconcile→report）")
    p_e2e.add_argument("--force-seed", action="store_true", help="强制重新生成演示数据")
    p_e2e.add_argument("--trade-date", help="指定模拟交易日期（格式 YYYY-MM-DD）")
    p_e2e.add_argument("--quiet", action="store_true", help="静默模式（减少输出）")

    # research
    p_research = subparsers.add_parser("research", help="运行研究管线（因子→信号→回测→闸门）")
    p_research.add_argument("--start-date", default="2022-01-04", help="回测开始日期")
    p_research.add_argument("--end-date", default="2024-06-28", help="回测结束日期")

    # paper-trade
    p_pt = subparsers.add_parser("paper-trade", help="运行模拟交易管线（信号→风控→状态机→撮合→对账）")
    p_pt.add_argument("--trade-date", help="交易日期（默认取演示数据中间日期）")

    # datasource-doctor
    p_ds = subparsers.add_parser("datasource-doctor", help="检查 .env 凭据配置是否就绪（不强连网）")

    # golive-check
    p_golive = subparsers.add_parser("golive-check", help="上线就绪门检查（调用 tools/golive_readiness.py）")

    args = parser.parse_args()

    from src.orchestration.cli import (
        cmd_selfcheck,
        cmd_seed_demo,
        cmd_e2e,
        cmd_research,
        cmd_paper_trade,
        cmd_datasource_doctor,
        cmd_golive_check,
    )

    dispatch = {
        "selfcheck": cmd_selfcheck,
        "seed-demo": cmd_seed_demo,
        "e2e": cmd_e2e,
        "research": cmd_research,
        "paper-trade": cmd_paper_trade,
        "datasource-doctor": cmd_datasource_doctor,
        "golive-check": cmd_golive_check,
    }

    fn = dispatch.get(args.command)
    if fn is None:
        parser.print_help()
        return 1

    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
