"""端到端串联（QS-E09 §4）：seed→research→paper-trade→reconcile→report。

每阶段打印 checkpoint（✓/✗ + 关键指标），最后输出一页总结写到 run/e2e_report_<ts>.md。
任一阶段失败清晰报错并非零退出。
"""
from __future__ import annotations

import logging
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_DIR = REPO_ROOT / "run"
RUN_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Checkpoint 工具
# ---------------------------------------------------------------------------

def _checkpoint(label: str, passed: bool, detail: str = "") -> None:
    """打印并记录阶段检查点。"""
    icon = "✓" if passed else "✗"
    msg = f"  [{icon}] {label}"
    if detail:
        msg += f": {detail}"
    print(msg)
    if not passed:
        raise RuntimeError(f"Checkpoint 失败: {label} — {detail}")


def _section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# E2E 管线
# ---------------------------------------------------------------------------

def run_e2e(
    force_seed: bool = False,
    trade_date: Optional[str] = None,
    data_root: Optional[Path] = None,
    db_path: Optional[Path] = None,
    quiet: bool = False,
) -> dict:
    """执行完整端到端流程。

    Args:
        force_seed:  是否强制重新生成演示数据
        trade_date:  指定交易日（默认取演示数据中间某天）
        data_root:   数据根目录
        db_path:     SQLite 路径
        quiet:       静默模式（减少输出）

    Returns:
        { 'success': bool, 'report_path': str, 'summary': dict }
    """
    from src.orchestration.demo_data import (
        seed_demo_data, load_demo_bar_df, load_demo_factor_df,
        DEMO_STOCKS, INDUSTRY_MAP, DEMO_START_DATE, DEMO_END_DATE,
    )
    from src.orchestration.research_pipeline import run_research_pipeline
    from src.orchestration.trading_pipeline import run_trading_pipeline

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = RUN_DIR / f"e2e_report_{ts}.md"

    report_lines = []
    summary = {}

    def _print(msg: str = "") -> None:
        if not quiet:
            print(msg)
        report_lines.append(msg)

    _print(f"# QuantSolo E2E 报告")
    _print(f"生成时间: {datetime.now(timezone.utc).isoformat()}")
    _print()

    # =========================================================
    # 阶段 0: 自检
    # =========================================================
    _section("阶段 0: 自检")
    _print("## 阶段 0: 自检")

    try:
        from src.common.config import load_frozen, load_tunable
        frozen = load_frozen()
        tunable = load_tunable()
        _checkpoint("load_frozen()", True, f"schema_version={frozen.get('schema_version', '?')}")
        _print(f"  [✓] load_frozen: schema_version={frozen.get('schema_version', '?')}")
        _print(f"  [✓] load_tunable: OK")
        summary["selfcheck"] = "PASS"
    except Exception as e:
        _print(f"  [✗] 自检失败: {e}")
        summary["selfcheck"] = f"FAIL: {e}"
        raise

    # =========================================================
    # 阶段 1: 数据种子
    # =========================================================
    _section("阶段 1: 数据种子 (seed-demo)")
    _print("\n## 阶段 1: 数据种子")

    try:
        seed_result = seed_demo_data(
            data_root=data_root,
            db_path=db_path,
            force=force_seed,
        )
        _checkpoint("seed_demo_data()", True,
                    f"stocks={seed_result.get('stocks', '?')}, "
                    f"bar_rows={seed_result.get('bar_rows', '?')}, "
                    f"trading_days={seed_result.get('trading_days', '?')}")
        _print(f"  [✓] 数据种子: {seed_result.get('stocks', '?')} 只标的, "
               f"{seed_result.get('bar_rows', '?')} 行行情, "
               f"{seed_result.get('trading_days', '?')} 个交易日")
        _print(f"  数据目录: {seed_result.get('data_root', '?')}")
        _print(f"  数据库: {seed_result.get('db_path', '?')}")
        summary["seed"] = seed_result
    except Exception as e:
        _print(f"  [✗] 数据种子失败: {e}")
        summary["seed"] = f"FAIL: {e}"
        raise

    # =========================================================
    # 阶段 2: 加载数据
    # =========================================================
    _section("阶段 2: 加载演示数据")
    _print("\n## 阶段 2: 加载演示数据")

    try:
        bar_df = load_demo_bar_df(data_root=data_root)
        _checkpoint("load_demo_bar_df()", not bar_df.empty,
                    f"{len(bar_df)} 行行情")
        _print(f"  [✓] 日线行情: {len(bar_df)} 行, "
               f"标的={bar_df['ts_code'].nunique()}, "
               f"日期范围={bar_df['trade_date'].min()}~{bar_df['trade_date'].max()}")

        factor_df = load_demo_factor_df(data_root=data_root, factor_variant="processed")
        _checkpoint("load_demo_factor_df()", not factor_df.empty,
                    f"{len(factor_df)} 行因子")
        _print(f"  [✓] 因子快照: {len(factor_df)} 行, "
               f"标的={factor_df['ts_code'].nunique()}, "
               f"日期范围={factor_df['trade_date'].min()}~{factor_df['trade_date'].max()}")

        summary["data_load"] = {
            "bar_rows": len(bar_df),
            "factor_rows": len(factor_df),
            "stocks": bar_df["ts_code"].nunique(),
        }
    except Exception as e:
        _print(f"  [✗] 加载数据失败: {e}")
        summary["data_load"] = f"FAIL: {e}"
        raise

    # =========================================================
    # 阶段 3: 研究管线
    # =========================================================
    _section("阶段 3: 研究管线 (research)")
    _print("\n## 阶段 3: 研究管线")

    try:
        research_result = run_research_pipeline(
            bar_df=bar_df,
            factor_df=factor_df,
            start_date=str(DEMO_START_DATE),
            end_date=str(DEMO_END_DATE),
        )

        sharpe = research_result.get("sharpe", 0.0)
        max_dd = research_result.get("max_drawdown", -1.0)
        ic_mean = research_result.get("ic_mean") or 0.0
        ic_ir = research_result.get("ic_ir") or 0.0
        gate_verdict = research_result.get("gate_result", {}).get("verdict", "N/A")
        stage3_count = len(research_result.get("stage3_selected", []))
        trial_id = research_result.get("trial_row_id", "N/A")

        _checkpoint("run_research_pipeline()", True, f"verdict={gate_verdict}")
        _print(f"  [✓] 研究管线完成")
        _print(f"      - 阶段三入选因子: {stage3_count} 个")
        _print(f"      - 回测 Sharpe: {sharpe:.4f}")
        _print(f"      - 最大回撤: {max_dd:.4f}")
        _print(f"      - IC 均值: {ic_mean:.4f}, ICIR: {ic_ir:.4f}")
        _print(f"      - 闸门判定: {gate_verdict}")
        _print(f"      - A1 通过: {research_result.get('gate_result', {}).get('a1_passed', '?')}")
        _print(f"      - trial_log 行 ID: {trial_id}")

        summary["research"] = {
            "sharpe": sharpe,
            "max_drawdown": max_dd,
            "ic_mean": ic_mean,
            "ic_ir": ic_ir,
            "gate_verdict": gate_verdict,
            "stage3_count": stage3_count,
            "trial_row_id": trial_id,
        }
    except Exception as e:
        _print(f"  [✗] 研究管线失败: {e}")
        _print(f"  {traceback.format_exc()}")
        summary["research"] = f"FAIL: {e}"
        raise

    # =========================================================
    # 阶段 4: 模拟交易 (paper-trade)
    # =========================================================
    _section("阶段 4: 模拟交易 (paper-trade)")
    _print("\n## 阶段 4: 模拟交易")

    try:
        # 选取演示数据中间某个交易日
        all_dates = sorted(bar_df["trade_date"].unique())
        if trade_date is None:
            mid_idx = len(all_dates) // 2
            trade_date = all_dates[mid_idx]

        _print(f"  交易日: {trade_date}")

        trade_result = run_trading_pipeline(
            bar_df=bar_df,
            factor_df=factor_df,
            industry_map=INDUSTRY_MAP,
            trade_date=trade_date,
            initial_cash=Decimal("1000000"),
        )

        if trade_result.get("status") == "no_data":
            _print(f"  [!] 无当日行情，跳过")
            summary["trading"] = {"status": "skipped"}
        else:
            fills_count = len(trade_result.get("fills", []))
            recon = trade_result.get("recon_result")
            recon_passed = recon.passed if recon else False
            approved = trade_result.get("approved_orders", 0)
            rejected = trade_result.get("rejected_orders", 0)
            portfolio_val = trade_result.get("portfolio_value", 0.0)

            _checkpoint("run_trading_pipeline()", True, f"fills={fills_count}")
            _print(f"  [✓] 模拟交易完成")
            _print(f"      - 交易日: {trade_date}")
            _print(f"      - 通过风控: {approved} 笔")
            _print(f"      - 拒单: {rejected} 笔")
            _print(f"      - 成交笔数: {fills_count}")
            _print(f"      - 对账结果: {'PASS' if recon_passed else 'FAIL'}")
            _print(f"      - 差异记录: {len(recon.diff_records) if recon else 0} 条")
            _print(f"      - 组合市值: {portfolio_val:,.2f} 元")

            # 状态机历史
            sm_history = trade_result.get("state_machine_history", [])
            sm_states = [f"{from_s.value}→{to_s.value}" for from_s, evt, to_s in sm_history[:5]]
            _print(f"      - 状态机前5步: {' | '.join(sm_states)}")

            summary["trading"] = {
                "trade_date": trade_date,
                "fills": fills_count,
                "approved_orders": approved,
                "rejected_orders": rejected,
                "recon_passed": recon_passed,
                "portfolio_value": portfolio_val,
            }
    except Exception as e:
        _print(f"  [✗] 模拟交易失败: {e}")
        _print(f"  {traceback.format_exc()}")
        summary["trading"] = f"FAIL: {e}"
        raise

    # =========================================================
    # 阶段 5: 对账总结
    # =========================================================
    _section("阶段 5: 对账与成本归因")
    _print("\n## 阶段 5: 对账与成本归因")

    try:
        recon_result = trade_result.get("recon_result")
        if recon_result:
            _checkpoint("日终对账", recon_result.passed,
                        f"diff_count={len(recon_result.diff_records)}")
            _print(f"  [✓] 对账完成: passed={recon_result.passed}, "
                   f"diff_count={len(recon_result.diff_records)}, "
                   f"cash_diff={recon_result.cash_diff:.4f}")
        else:
            _print("  [!] 对账结果不可用（无成交）")

        summary["reconcile"] = {
            "passed": recon_result.passed if recon_result else True,
            "diff_count": len(recon_result.diff_records) if recon_result else 0,
        }
    except Exception as e:
        _print(f"  [✗] 对账失败: {e}")
        summary["reconcile"] = f"FAIL: {e}"
        raise

    # =========================================================
    # 最终报告
    # =========================================================
    _print()
    _print("=" * 60)
    _print("  E2E 总结")
    _print("=" * 60)
    _print()
    _print("### 关键指标汇总")
    _print(f"- 演示数据: {summary.get('seed', {}).get('stocks', '?')} 只标的, "
           f"{summary.get('seed', {}).get('trading_days', '?')} 个交易日")
    _print(f"- 研究回测 Sharpe: {summary.get('research', {}).get('sharpe', 'N/A'):.4f}")
    _print(f"- 研究回测 MaxDD: {summary.get('research', {}).get('max_drawdown', 'N/A'):.4f}")
    _print(f"- IC 均值: {summary.get('research', {}).get('ic_mean', 'N/A'):.4f}")
    _print(f"- 闸门判定: {summary.get('research', {}).get('gate_verdict', 'N/A')}")
    _print(f"- 模拟交易成交: {summary.get('trading', {}).get('fills', 'N/A')} 笔")
    _print(f"- 对账: {'PASS' if summary.get('reconcile', {}).get('passed', False) else 'FAIL'}")
    _print()
    _print(f"**E2E 全流程: PASS**")
    _print()
    _print(f"报告文件: {report_path}")

    # 写入报告
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    logger.info("E2E 报告已写入: %s", report_path)

    summary["success"] = True
    summary["report_path"] = str(report_path)
    return summary


# 引入 Decimal
from decimal import Decimal
