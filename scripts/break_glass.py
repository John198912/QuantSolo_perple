"""物理一键熔断脚本（薄包装入口，QS-C04 §5.2 · QS-E02 §10）。

此脚本为 src.execution.break_glass.main 的薄包装，
直接作为独立进程执行（不依赖主进程内存/状态）。

白名单说明（架构白名单 §5.2）：
  scripts/break_glass.py 是允许间接调用 xtquant 的白名单脚本之一
  （通过 import src.execution.break_glass 间接使用）。

用法::

    # 直接执行（标准入口）
    python scripts/break_glass.py

    # 指定账户（也可通过 XTQUANT_ACCOUNT 环境变量）
    XTQUANT_ACCOUNT=your_account python scripts/break_glass.py

    # 自动化测试（跳过二次确认，仅供测试环境）
    python scripts/break_glass.py --skip-confirm
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="break_glass",
        description="物理一键熔断：撤所有挂单 + 市价平仓（QS-C04 §5.2）",
    )
    parser.add_argument(
        "--account-id",
        default=None,
        help="资金账户 ID（也可通过 XTQUANT_ACCOUNT 环境变量设置）",
    )
    parser.add_argument(
        "--skip-confirm",
        action="store_true",
        default=False,
        help="跳过二次确认（仅供自动化测试，生产环境禁止使用）",
    )
    parser.add_argument(
        "--token-file",
        default="run/order_token.lock",
        help="下单令牌文件路径（默认: run/order_token.lock）",
    )
    parser.add_argument(
        "--ledger-db",
        default="run/execution_ledger.db",
        help="execution_ledger SQLite 路径（默认: run/execution_ledger.db）",
    )
    parser.add_argument(
        "--halt-state-file",
        default="run/halt_state.json",
        help="暂停状态文件路径（默认: run/halt_state.json）",
    )
    return parser.parse_args()


def main() -> None:
    """CLI 入口。"""
    args = _parse_args()

    # 确保 repo 根目录在 sys.path（直接执行脚本时需要）
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    # 导入并调用核心实现
    from src.execution.break_glass import main as _bg_main

    exit_code = _bg_main(
        account_id=args.account_id,
        token_file=Path(args.token_file),
        ledger_db=Path(args.ledger_db),
        halt_state_file=Path(args.halt_state_file),
        skip_confirm=args.skip_confirm,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
