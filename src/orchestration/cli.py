"""CLI 子命令处理器（QS-E09 §5）。

子命令：
  selfcheck       — 跑静态守卫+冻结校验+pytest not live
  seed-demo       — 生成合成演示数据
  e2e             — 端到端跑通
  research        — 仅运行研究管线
  paper-trade     — 仅运行模拟交易管线
  datasource-doctor — 检查 .env/凭据是否就绪
  golive-check    — 调 tools/golive_readiness
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]


def cmd_selfcheck(args) -> int:
    """运行静态守卫 + 冻结参数校验 + pytest not live。"""
    print("=" * 60)
    print("  自检：静态守卫 + 冻结参数 + 测试")
    print("=" * 60)

    all_pass = True

    # 1. static_guard_scan.py
    print("\n[1/3] 静态守卫扫描...")
    r = subprocess.run(
        [sys.executable, "tools/static_guard_scan.py"],
        cwd=str(REPO_ROOT), capture_output=True, text=True
    )
    if r.returncode == 0:
        print("  [✓] static_guard_scan: PASS")
    else:
        print(f"  [✗] static_guard_scan: FAIL\n{r.stdout}\n{r.stderr}")
        all_pass = False

    # 2. frozen_params_check.py
    print("\n[2/3] 冻结参数校验...")
    r = subprocess.run(
        [sys.executable, "tools/frozen_params_check.py"],
        cwd=str(REPO_ROOT), capture_output=True, text=True
    )
    if r.returncode == 0:
        print("  [✓] frozen_params_check: PASS")
    else:
        print(f"  [✗] frozen_params_check: FAIL\n{r.stdout}\n{r.stderr}")
        all_pass = False

    # 3. pytest -q -m "not live"（排除编排层测试，编排层由 golive_readiness G5/G6 验收）
    print("\n[3/3] 运行测试 (not live)...")
    r = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-q",
            "-m", "not live", "--tb=short",
            "--ignore=tests/orchestration",
        ],
        cwd=str(REPO_ROOT), capture_output=True, text=True
    )
    print(r.stdout[-3000:] if len(r.stdout) > 3000 else r.stdout)
    if r.returncode == 0:
        print("  [✓] pytest: 全绿")
    else:
        print(f"  [✗] pytest: 有失败\n{r.stderr}")
        all_pass = False

    print()
    if all_pass:
        print("✓ 自检全部通过")
        return 0
    else:
        print("✗ 自检有项目失败")
        return 1


def cmd_seed_demo(args) -> int:
    """生成合成演示数据。"""
    from src.orchestration.demo_data import seed_demo_data
    print("生成演示数据...")
    try:
        result = seed_demo_data(force=getattr(args, "force", False))
        print(f"[✓] 演示数据生成完成")
        for k, v in result.items():
            print(f"    {k}: {v}")
        return 0
    except Exception as e:
        print(f"[✗] 生成失败: {e}")
        import traceback; traceback.print_exc()
        return 1


def cmd_e2e(args) -> int:
    """端到端跑通。"""
    import logging as _logging
    level = _logging.WARNING if getattr(args, "quiet", False) else _logging.INFO
    _logging.basicConfig(level=level, format="%(levelname)s:%(name)s:%(message)s")

    from src.orchestration.e2e import run_e2e
    try:
        result = run_e2e(
            force_seed=getattr(args, "force_seed", False),
            trade_date=getattr(args, "trade_date", None),
            quiet=getattr(args, "quiet", False),
        )
        print(f"\n报告路径: {result.get('report_path', 'N/A')}")
        return 0 if result.get("success") else 1
    except Exception as e:
        print(f"[✗] E2E 失败: {e}")
        import traceback; traceback.print_exc()
        return 1


def cmd_research(args) -> int:
    """仅运行研究管线。"""
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

    from src.orchestration.demo_data import load_demo_bar_df, load_demo_factor_df, DEMO_START_DATE, DEMO_END_DATE
    from src.orchestration.research_pipeline import run_research_pipeline

    print("加载演示数据...")
    try:
        bar_df = load_demo_bar_df()
        factor_df = load_demo_factor_df()
    except FileNotFoundError:
        print("[✗] 演示数据不存在，请先运行: python -m src seed-demo")
        return 1

    print("运行研究管线...")
    try:
        result = run_research_pipeline(
            bar_df=bar_df,
            factor_df=factor_df,
            start_date=str(DEMO_START_DATE),
            end_date=str(DEMO_END_DATE),
        )
        print(f"\n研究管线结果:")
        print(f"  Sharpe:   {result.get('sharpe', 0):.4f}")
        print(f"  MaxDD:    {result.get('max_drawdown', 0):.4f}")
        print(f"  IC均值:   {result.get('ic_mean', 0):.4f}")
        print(f"  ICIR:     {result.get('ic_ir', 0):.4f}")
        print(f"  闸门判定: {result.get('gate_result', {}).get('verdict', 'N/A')}")
        return 0
    except Exception as e:
        print(f"[✗] 研究管线失败: {e}")
        import traceback; traceback.print_exc()
        return 1


def cmd_paper_trade(args) -> int:
    """仅运行模拟交易管线。"""
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

    from decimal import Decimal
    from src.orchestration.demo_data import load_demo_bar_df, load_demo_factor_df, INDUSTRY_MAP
    from src.orchestration.trading_pipeline import run_trading_pipeline

    print("加载演示数据...")
    try:
        bar_df = load_demo_bar_df()
        factor_df = load_demo_factor_df()
    except FileNotFoundError:
        print("[✗] 演示数据不存在，请先运行: python -m src seed-demo")
        return 1

    # 取中间日期
    all_dates = sorted(bar_df["trade_date"].unique())
    trade_date = getattr(args, "trade_date", None) or all_dates[len(all_dates) // 2]
    print(f"交易日: {trade_date}")

    try:
        result = run_trading_pipeline(
            bar_df=bar_df,
            factor_df=factor_df,
            industry_map=INDUSTRY_MAP,
            trade_date=trade_date,
            initial_cash=Decimal("1000000"),
        )
        if result.get("status") != "ok":
            print(f"[!] 管线状态: {result.get('status')}")
            return 0

        recon = result.get("recon_result")
        print(f"\n模拟交易结果:")
        print(f"  通过风控: {result.get('approved_orders', 0)} 笔")
        print(f"  拒单:     {result.get('rejected_orders', 0)} 笔")
        print(f"  成交:     {len(result.get('fills', []))} 笔")
        print(f"  对账:     {'PASS' if recon and recon.passed else 'FAIL'}")
        print(f"  组合市值: {result.get('portfolio_value', 0):,.2f} 元")
        return 0
    except Exception as e:
        print(f"[✗] 模拟交易失败: {e}")
        import traceback; traceback.print_exc()
        return 1


def cmd_datasource_doctor(args) -> int:
    """检查 .env/凭据是否就绪。"""
    print("=" * 60)
    print("  数据源诊断（datasource-doctor）")
    print("=" * 60)

    checks = [
        ("TUSHARE_TOKEN", "Tushare 数据源"),
        ("QMT_USERDATA_PATH", "QMT 行情接口"),
        ("SERVERCHAN_SENDKEY", "Server酱推送"),
        ("DINGTALK_WEBHOOK", "钉钉机器人"),
        ("QUANTSOLO_DATA_ROOT", "数据根目录"),
    ]

    any_missing = False
    for env_key, desc in checks:
        val = os.getenv(env_key, "")
        if val:
            print(f"  [✓] {env_key}: 已配置 ({desc})")
        else:
            print(f"  [!] {env_key}: 未配置 ({desc})")
            any_missing = True

    # 检查演示数据
    from src.orchestration.demo_data import _get_data_root
    data_root = _get_data_root()
    marker = data_root / ".demo_seeded"
    if marker.exists():
        print(f"  [✓] 演示数据: 已生成 ({data_root})")
    else:
        print(f"  [!] 演示数据: 未生成，运行 python -m src seed-demo")
        any_missing = True

    print()
    if any_missing:
        print("部分配置缺失。实盘需完整配置 .env（参考 .env.example）。")
        print("离线演示仅需演示数据，无需 TUSHARE_TOKEN 等外部凭据。")
    else:
        print("所有配置就绪。")

    return 0  # 不强制退出


def cmd_golive_check(args) -> int:
    """调用 tools/golive_readiness.py。"""
    r = subprocess.run(
        [sys.executable, "tools/golive_readiness.py"],
        cwd=str(REPO_ROOT),
    )
    return r.returncode
