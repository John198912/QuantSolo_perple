"""编排层冒烟测试（M4 Orchestration Smoke Tests）。

覆盖：
  - demo_data 种子生成（session-scope 共享，每次 session 只运行一次）
  - research_pipeline 因子→回测→闸门全流程（session-scope）
  - trading_pipeline 信号→风控→状态机→对账全流程（mock sleep）
  - e2e.run_e2e 端到端串联（session-scope，mock sleep）

规则：
  - 所有测试不依赖 live 数据源（不标记 @pytest.mark.live）
  - 不依赖 xtquant（仅使用 BacktestAdapter）
  - 金额使用 Decimal（R6）
  - 慢操作（seed / e2e）用 session-scope fixture 避免重复执行
  - time.sleep 被 mock 为 no-op（保证测试速度）
"""
from __future__ import annotations

import os
import sqlite3
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# 小型合成数据辅助函数（不写磁盘，不调用 seed_demo_data）
# ---------------------------------------------------------------------------

def _make_tiny_bar_df(n_stocks: int = 3, n_days: int = 30) -> pd.DataFrame:
    """生成超小行情 DataFrame（与 conftest sample_bar_df schema 一致）。

    额外包含 close_adj / amount 供 VectorizedBacktest 使用。
    """
    import numpy as np

    rng = np.random.default_rng(seed=99)
    stocks = [f"{i:06d}.SZ" for i in range(1, n_stocks + 1)]
    dates = pd.date_range("2022-01-04", periods=n_days, freq="B")

    rows = []
    record_id = 1
    for stock in stocks:
        price = 10.0
        for dt in dates:
            price = max(1.0, price * (1 + rng.normal(0, 0.02)))
            vol = float(rng.integers(500_000, 2_000_000))
            rows.append({
                "ts_code": stock,
                "trade_date": dt.strftime("%Y-%m-%d"),
                "visible_at": dt.strftime("%Y-%m-%dT17:00:00+08:00"),
                "revision_seq": 1,
                "snapshot_rank": 0,
                "record_id": record_id,
                "record_status": "ACTIVE",
                "close": round(price, 2),
                "close_adj": round(price, 2),  # 复权价 = 原始价（测试用）
                "volume": vol,
                "amount": vol * price,         # 成交额（元）
            })
            record_id += 1
    return pd.DataFrame(rows)


def _make_tiny_factor_df(bar_df: pd.DataFrame) -> pd.DataFrame:
    """基于行情 DF 生成最小因子快照（schema 与 conftest sample_snapshot_df 一致）。"""
    import numpy as np

    rng = np.random.default_rng(seed=99)
    rows = []
    record_id = 5000
    for _, row in bar_df.iterrows():
        rows.append({
            "ts_code": row["ts_code"],
            "trade_date": row["trade_date"],
            "factor_name": "momentum_20",
            "factor_variant": "processed",
            "factor_value": float(rng.normal(0, 1)),
            "visible_at": row["visible_at"],
            "revision_seq": 1,
            "snapshot_rank": 0,
            "record_id": record_id,
            "record_status": "ACTIVE",
            "close_adj": row["close"],
            "amount": row["amount"],
        })
        record_id += 1
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Session-scope fixtures（慢操作只跑一次）
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def seeded_data_dir(tmp_path_factory):
    """Session-scope: 生成一次演示数据目录，供所有测试复用。"""
    from src.orchestration.demo_data import seed_demo_data

    base = tmp_path_factory.mktemp("demo_data")
    db_path = base / "quant.db"
    seed_demo_data(data_root=base, db_path=db_path, force=True)
    return {"data_root": base, "db_path": db_path}


@pytest.fixture(scope="session")
def research_result(seeded_data_dir):
    """Session-scope: 运行一次研究管线，供所有相关测试复用。"""
    from src.orchestration.demo_data import load_demo_bar_df, load_demo_factor_df
    from src.orchestration.research_pipeline import run_research_pipeline

    bar_df = load_demo_bar_df(data_root=seeded_data_dir["data_root"])
    factor_df = load_demo_factor_df(data_root=seeded_data_dir["data_root"])
    trial_db = str(seeded_data_dir["db_path"].parent / "research.db")

    result = run_research_pipeline(
        bar_df=bar_df,
        factor_df=factor_df,
        start_date="2022-01-04",
        end_date="2024-06-28",
        trial_db_path=trial_db,
    )
    return result


@pytest.fixture(scope="session")
def trading_result(seeded_data_dir):
    """Session-scope: 运行一次交易管线，供所有相关测试复用（mock sleep）。"""
    from src.orchestration.demo_data import (
        load_demo_bar_df, load_demo_factor_df, INDUSTRY_MAP,
    )
    from src.orchestration.trading_pipeline import run_trading_pipeline

    bar_df = load_demo_bar_df(data_root=seeded_data_dir["data_root"])
    factor_df = load_demo_factor_df(data_root=seeded_data_dir["data_root"])

    all_dates = sorted(bar_df["trade_date"].unique())
    trade_date = all_dates[len(all_dates) // 2]

    with patch("src.orchestration.trading_pipeline.time.sleep"):
        result = run_trading_pipeline(
            bar_df=bar_df,
            factor_df=factor_df,
            industry_map=INDUSTRY_MAP,
            trade_date=trade_date,
            initial_cash=Decimal("1000000"),
        )
    return result


@pytest.fixture(scope="session")
def e2e_result(tmp_path_factory):
    """Session-scope: 运行一次端到端，供所有相关测试复用。

    在 module 级别 mock time.sleep，避免每次订单间隔 1.05s。
    """
    from unittest.mock import patch as _patch
    from src.orchestration.e2e import run_e2e
    import src.orchestration.trading_pipeline as _tp

    base = tmp_path_factory.mktemp("e2e_run")

    # patch time.sleep 在 trading_pipeline 模块内
    patcher = _patch.object(_tp.time, "sleep", return_value=None)
    patcher.start()
    try:
        result = run_e2e(
            force_seed=True,
            trade_date="2024-06-28",
            data_root=base / "data",
            db_path=base / "quant.db",
            quiet=True,
        )
    finally:
        patcher.stop()
    return result


# ---------------------------------------------------------------------------
# demo_data tests
# ---------------------------------------------------------------------------

class TestDemoData:
    """测试合成数据种子模块。"""

    def test_seed_creates_parquet_files(self, seeded_data_dir):
        """seed_demo_data 应在 data_root 下创建 Parquet 文件。"""
        data_root = seeded_data_dir["data_root"]
        parquet_files = list(data_root.rglob("*.parquet"))
        assert len(parquet_files) > 0, "未生成任何 Parquet 文件"

    def test_load_bar_df_schema(self, seeded_data_dir):
        """load_demo_bar_df 应返回符合 PIT schema 的 DataFrame。"""
        from src.orchestration.demo_data import load_demo_bar_df

        df = load_demo_bar_df(data_root=seeded_data_dir["data_root"])

        assert len(df) > 0
        required_cols = {
            "ts_code", "trade_date", "visible_at",
            "revision_seq", "snapshot_rank", "record_id",
            "record_status", "close", "volume",
        }
        assert required_cols.issubset(set(df.columns)), (
            f"缺少列: {required_cols - set(df.columns)}"
        )

    def test_load_factor_df_schema(self, seeded_data_dir):
        """load_demo_factor_df 应返回符合因子快照 schema 的 DataFrame。"""
        from src.orchestration.demo_data import load_demo_factor_df

        df = load_demo_factor_df(data_root=seeded_data_dir["data_root"])

        assert len(df) > 0
        required_cols = {
            "ts_code", "trade_date", "factor_name", "factor_variant",
            "factor_value", "visible_at", "revision_seq",
            "snapshot_rank", "record_id", "record_status",
        }
        assert required_cols.issubset(set(df.columns)), (
            f"缺少列: {required_cols - set(df.columns)}"
        )

    def test_no_future_leakage(self, seeded_data_dir):
        """visible_at 不应早于 trade_date（防未来函数检查）。"""
        from src.orchestration.demo_data import load_demo_bar_df

        df = load_demo_bar_df(data_root=seeded_data_dir["data_root"])
        df["_trade_dt"] = pd.to_datetime(df["trade_date"])
        df["_visible_dt"] = (
            pd.to_datetime(df["visible_at"], utc=True).dt.tz_localize(None)
        )
        leakage = df[df["_visible_dt"].dt.date < df["_trade_dt"].dt.date]
        assert len(leakage) == 0, (
            f"检测到 {len(leakage)} 行 visible_at < trade_date（未来函数泄漏）"
        )

    def test_db_has_tables(self, seeded_data_dir):
        """SQLite 中至少有一张表。"""
        conn = sqlite3.connect(seeded_data_dir["db_path"])
        try:
            cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in cur.fetchall()]
            assert len(tables) > 0, "数据库中无表"
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# research_pipeline tests
# ---------------------------------------------------------------------------

class TestResearchPipeline:
    """测试因子研究管线（复用 session-scope research_result）。"""

    def test_returns_dict(self, research_result):
        """run_research_pipeline 应返回 dict。"""
        assert isinstance(research_result, dict), "返回值应为 dict"

    def test_gate_result_has_verdict(self, research_result):
        """gate_result 应包含 verdict 字段。"""
        assert "gate_result" in research_result, (
            f"缺少 gate_result 键，实际: {list(research_result.keys())}"
        )
        gate = research_result["gate_result"]
        assert "verdict" in gate, f"gate_result 缺少 verdict: {gate}"
        verdict = str(gate["verdict"])
        assert verdict, f"verdict 为空"

    def test_sharpe_key_present(self, research_result):
        """sharpe 键应存在（允许为 0 或 NaN）。"""
        assert "sharpe" in research_result, "缺少 sharpe 键"

    def test_no_xtquant_import(self):
        """research_pipeline 中不得有 `import xtquant` 语句（注释不算）。"""
        import ast

        src_path = (
            Path(__file__).parent.parent.parent
            / "src/orchestration/research_pipeline.py"
        )
        tree = ast.parse(src_path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names]
                module = getattr(node, "module", "") or ""
                all_names = names + [module]
                assert not any("xtquant" in n for n in all_names), (
                    f"research_pipeline.py 有 xtquant import 语句（违反 R1）"
                )


# ---------------------------------------------------------------------------
# trading_pipeline tests
# ---------------------------------------------------------------------------

class TestTradingPipeline:
    """测试交易管线（复用 session-scope trading_result）。"""

    def test_returns_ok_status(self, trading_result):
        """run_trading_pipeline 应返回 status='ok'。"""
        assert trading_result["status"] == "ok", (
            f"交易管线返回非 ok: {trading_result.get('status')}"
        )

    def test_fills_list_is_list(self, trading_result):
        """fills 应为 list 类型。"""
        assert isinstance(trading_result["fills"], list), "fills 应为 list"

    def test_recon_result_has_passed(self, trading_result):
        """recon_result 应有 passed 属性。"""
        recon = trading_result["recon_result"]
        assert hasattr(recon, "passed"), "recon_result 缺少 passed 属性"
        assert isinstance(recon.passed, bool), "recon_result.passed 应为 bool"

    def test_cash_decimal_precision(self, trading_result):
        """final_cash 应为有限非负数。"""
        final_cash = trading_result["final_cash"]
        assert final_cash >= 0, f"final_cash 为负: {final_cash}"
        assert final_cash <= 2_000_000, f"final_cash 异常过大: {final_cash}"

    def test_no_xtquant_import(self):
        """trading_pipeline 中不得有 `import xtquant` 语句（注释不算）。"""
        import ast

        src_path = (
            Path(__file__).parent.parent.parent
            / "src/orchestration/trading_pipeline.py"
        )
        tree = ast.parse(src_path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names]
                module = getattr(node, "module", "") or ""
                all_names = names + [module]
                assert not any("xtquant" in n for n in all_names), (
                    f"trading_pipeline.py 有 xtquant import 语句（违反 R1）"
                )

    def test_backtest_adapter_used(self):
        """trading_pipeline 应使用 BacktestAdapter 而非 XtQuantAdapter。"""
        src_path = (
            Path(__file__).parent.parent.parent
            / "src/orchestration/trading_pipeline.py"
        )
        source = src_path.read_text()
        assert "BacktestAdapter" in source or "backtest_adapter" in source, (
            "trading_pipeline 未使用 BacktestAdapter"
        )
        assert "XtQuantAdapter" not in source, (
            "trading_pipeline 不应直接引用 XtQuantAdapter"
        )


# ---------------------------------------------------------------------------
# e2e tests
# ---------------------------------------------------------------------------

class TestE2ESmoke:
    """端到端冒烟测试（复用 session-scope e2e_result）。

    e2e 返回的 summary dict 键名: selfcheck, seed, data_load, research,
    trading, reconcile, success, report_path
    """

    def test_run_e2e_returns_success(self, e2e_result):
        """run_e2e 应返回 success=True。"""
        assert isinstance(e2e_result, dict), "run_e2e 应返回 dict"
        assert e2e_result.get("success") is True, (
            f"run_e2e success 异常: success={e2e_result.get('success')}, "
            f"keys={list(e2e_result.keys())}"
        )

    def test_run_e2e_has_stages(self, e2e_result):
        """run_e2e 结果应包含各阶段键（selfcheck/seed/research/trading/reconcile）。"""
        expected_keys = {"selfcheck", "seed", "research", "trading", "reconcile"}
        missing = expected_keys - set(e2e_result.keys())
        assert len(missing) == 0, (
            f"缺少阶段键: {missing}，实际键: {list(e2e_result.keys())}"
        )

    def test_run_e2e_report_file_written(self, e2e_result):
        """run_e2e 应写入 MD 报告文件（若 report_path 存在）。"""
        report_path = e2e_result.get("report_path")
        if report_path:
            assert Path(report_path).exists(), f"报告文件未创建: {report_path}"

    def test_run_e2e_no_xtquant_import(self):
        """e2e.py 中不得有 `import xtquant` 语句（注释不算）。"""
        import ast

        src_path = (
            Path(__file__).parent.parent.parent / "src/orchestration/e2e.py"
        )
        tree = ast.parse(src_path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names]
                module = getattr(node, "module", "") or ""
                all_names = names + [module]
                assert not any("xtquant" in n for n in all_names), (
                    f"e2e.py 有 xtquant import 语句（违反 R1）"
                )


# ---------------------------------------------------------------------------
# Frozen params / config tests
# ---------------------------------------------------------------------------

class TestFrozenConfig:
    """验证 frozen.toml 中关键参数可被正确读取。"""

    def test_load_frozen_acceptance(self):
        """acceptance 区块应包含 linear_baseline_sharpe。"""
        from src.common.config import load_frozen

        frozen = load_frozen()
        assert "acceptance" in frozen, "frozen.toml 缺少 [acceptance] 区块"
        acc = frozen["acceptance"]
        assert "linear_baseline_sharpe" in acc, (
            "frozen.toml [acceptance] 缺少 linear_baseline_sharpe"
        )
        assert float(acc["linear_baseline_sharpe"]) > 0

    def test_load_frozen_risk(self):
        """risk 区块应包含 max_position_per_stock。"""
        from src.common.config import load_frozen

        frozen = load_frozen()
        assert "risk" in frozen, "frozen.toml 缺少 [risk] 区块"
        assert "max_position_per_stock" in frozen["risk"]

    def test_load_frozen_gates(self):
        """gates 区块应包含 a1_hard_veto_sharpe_floor。"""
        from src.common.config import load_frozen

        frozen = load_frozen()
        assert "gates" in frozen, "frozen.toml 缺少 [gates] 区块"
        assert "a1_hard_veto_sharpe_floor" in frozen["gates"]

    def test_frozen_is_immutable(self):
        """load_frozen 返回的对象不可修改（MappingProxyType）。"""
        from src.common.config import load_frozen
        from types import MappingProxyType

        frozen = load_frozen()
        assert isinstance(frozen, MappingProxyType), (
            "load_frozen 应返回 MappingProxyType（不可变）"
        )
        with pytest.raises((TypeError, AttributeError)):
            frozen["risk"] = {}  # type: ignore[index]


# ---------------------------------------------------------------------------
# CLI / __main__ tests
# ---------------------------------------------------------------------------

class TestCLI:
    """测试 CLI 入口点。"""

    def test_main_help_exits_zero(self):
        """python -m src --help 应以 exit 0 终止。"""
        import subprocess
        import sys as _sys

        result = subprocess.run(
            [_sys.executable, "-m", "src", "--help"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent.parent),
        )
        assert result.returncode == 0, (
            f"--help exit code={result.returncode}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )

    def test_main_selfcheck_exits_zero(self):
        """python -m src selfcheck 应以 exit 0 终止。"""
        import subprocess
        import sys as _sys

        result = subprocess.run(
            [_sys.executable, "-m", "src", "selfcheck"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent.parent),
        )
        assert result.returncode == 0, (
            f"selfcheck exit code={result.returncode}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )

    def test_seed_demo_cmd(self):
        """python -m src seed-demo 应以 exit 0 终止。"""
        import subprocess
        import sys as _sys

        # seed-demo 不支持 --data-root/--db-path，使用默认路径（已运行过 seed）
        result = subprocess.run(
            [_sys.executable, "-m", "src", "seed-demo"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent.parent),
        )
        assert result.returncode == 0, (
            f"seed-demo exit code={result.returncode}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
