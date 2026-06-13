"""监控看板 — Streamlit 每日巡检（QS-E03 §9.4）。

仅绑定 127.0.0.1，不暴露外网。盘后每日巡检，5-10 分钟目标。

Tab 布局：
  tab1: 持仓对账（recon_results）
  tab2: 因子 IC（占位符）
  tab3: 数据质检（DuckDB 查询）
  tab4: 告警日志

红线遵守：
  R1：不 import xtquant。
  streamlit 惰性导入兜底：缺失时模块仍可 import，提供 main() 守卫。
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Optional


# ---------------------------------------------------------------------------
# 惰性导入 streamlit（缺失时模块仍可 import）
# ---------------------------------------------------------------------------

def _get_st() -> Optional[object]:
    """惰性获取 streamlit；返回 None 表示未安装。"""
    try:
        import streamlit as _st  # 惰性导入
        return _st
    except ImportError:
        return None


def _get_pd():
    import pandas as pd
    return pd


def _get_duckdb() -> Optional[object]:
    """惰性获取 duckdb；返回 None 表示未安装。"""
    try:
        import duckdb as _ddb
        return _ddb
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# 看板主函数
# ---------------------------------------------------------------------------

def run_dashboard(sqlite_path: str, duckdb_path: str) -> None:
    """Streamlit 看板（仅绑定 127.0.0.1，不暴露外网）。

    Args:
        sqlite_path: SQLite 数据库路径（对账/告警日志）。
        duckdb_path: DuckDB 数据库路径（行情/因子数据质检）。

    Raises:
        ImportError: streamlit 未安装时抛出清晰错误。
    """
    st = _get_st()
    if st is None:
        raise ImportError(
            "streamlit 未安装，请运行 `pip install streamlit` 后重启看板。"
        )

    pd = _get_pd()
    ddb = _get_duckdb()

    st.set_page_config(page_title="QuantSolo 监控看板", layout="wide")
    st.title("QuantSolo 每日巡检看板")

    conn_sql = sqlite3.connect(sqlite_path)
    conn_duck = ddb.connect(duckdb_path) if ddb is not None else None

    tab1, tab2, tab3, tab4 = st.tabs(["持仓对账", "因子 IC", "数据质检", "告警日志"])

    with tab1:
        _show_position_recon(st, pd, conn_sql)
    with tab2:
        _show_factor_ic(st)
    with tab3:
        _show_data_quality(st, pd, conn_duck)
    with tab4:
        _show_alerts(st, pd, conn_sql)

    conn_sql.close()
    if conn_duck is not None:
        conn_duck.close()


# ---------------------------------------------------------------------------
# 子页面
# ---------------------------------------------------------------------------

def _show_position_recon(st, pd, conn) -> None:
    """Tab1：最新持仓对账明细。"""
    st.subheader("最新持仓对账")
    try:
        df = pd.read_sql(
            """
            SELECT trade_date, ts_code, theory_qty, broker_qty, diff_qty, category
            FROM recon_results
            WHERE trade_date = (SELECT MAX(trade_date) FROM recon_results)
            ORDER BY ABS(diff_qty) DESC
            """,
            conn,
        )
        st.dataframe(df, use_container_width=True)
        if df[df["category"] == "UNEXPLAINED"].shape[0] > 0:
            st.error("⚠ 存在不可解释差异，需人工处理")
        else:
            st.success("✅ 对账通过")
    except Exception as exc:  # noqa: BLE001
        st.warning(f"对账数据查询失败：{exc}")


def _show_factor_ic(st) -> None:
    """Tab2：滚动 26 周 IC 趋势（占位符）。"""
    st.subheader("滚动 26 周 IC 趋势")
    st.info("IC 走势数据由 research_ledger 提供（此处为占位符）")


def _show_data_quality(st, pd, conn_duck) -> None:
    """Tab3：数据质检（DuckDB）。"""
    st.subheader("数据质检摘要")
    if conn_duck is None:
        st.warning("DuckDB 未安装，跳过数据质检。")
        return
    try:
        df = conn_duck.execute(
            """
            SELECT trade_date, COUNT(*) AS record_count
            FROM daily_bar
            WHERE trade_date >= (CURRENT_DATE - INTERVAL 7 DAY)
            GROUP BY trade_date
            ORDER BY trade_date DESC
            """
        ).df()
        st.dataframe(df, use_container_width=True)
    except Exception as exc:  # noqa: BLE001
        st.warning(f"质检数据查询失败（daily_bar 表可能不存在）：{exc}")


def _show_alerts(st, pd, conn) -> None:
    """Tab4：最近 100 条告警日志。"""
    st.subheader("告警日志（最近 100 条）")
    try:
        df = pd.read_sql(
            """
            SELECT created_at, level, source_module, title, content
            FROM alert_log
            ORDER BY created_at DESC
            LIMIT 100
            """,
            conn,
        )
        st.dataframe(df, use_container_width=True)
    except Exception as exc:  # noqa: BLE001
        st.warning(f"告警日志查询失败（alert_log 表可能不存在）：{exc}")


# ---------------------------------------------------------------------------
# CLI 入口守卫
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI 入口守卫，供 `python -m src.monitor.dashboard` 调用。

    实际由 `streamlit run` 驱动；此处直接调用 run_dashboard 以便单元测试导入。
    """
    import os
    sqlite_path = os.getenv("QUANT_SQLITE_PATH", "data/quant.sqlite")
    duckdb_path = os.getenv("QUANT_DUCKDB_PATH", "data/quant.duckdb")
    run_dashboard(sqlite_path, duckdb_path)


if __name__ == "__main__":
    main()
