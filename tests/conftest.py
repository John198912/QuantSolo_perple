"""共享 pytest fixtures（QS-C03 §12 自动化点时回归测试）。

提供：
  - 内存 SQLite 连接（mem_sqlite）
  - 内存 DuckDB 连接（mem_duckdb）
  - 点时表样例 DataFrame 工厂（make_pit_df）
  - 样例交易日历（sample_calendar）
  - 样例多源数据（sample_multi_source_data）
  - 样例财报数据（sample_financials_df）
  - 样例因子快照数据（sample_snapshot_df）
"""
from __future__ import annotations

import sqlite3
from decimal import Decimal
from typing import Optional

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# pytest mark 注册（避免 PytestUnknownMarkWarning）
# ---------------------------------------------------------------------------

def pytest_configure(config):
    config.addinivalue_line("markers", "pit: 点时回归场景测试")
    config.addinivalue_line("markers", "live: 需要 live 数据源（akshare/tushare/baostock/xtquant），默认 skip")


# ---------------------------------------------------------------------------
# 内存 SQLite fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def mem_sqlite():
    """内存 SQLite 连接（每个测试独立）。"""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# 内存 DuckDB fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def mem_duckdb():
    """内存 DuckDB 连接（每个测试独立）。"""
    try:
        import duckdb
        conn = duckdb.connect(":memory:")
        yield conn
        conn.close()
    except ImportError:
        pytest.skip("duckdb 未安装")


# ---------------------------------------------------------------------------
# 点时 DataFrame 工厂 fixture
# ---------------------------------------------------------------------------

def _build_pit_row(
    ts_code: str,
    trade_date: str,
    visible_at: str,
    revision_seq: int,
    snapshot_rank: int,
    record_id: int,
    record_status: str = "ACTIVE",
    close: float = 10.0,
    volume: float = 1000000.0,
    **kwargs,
) -> dict:
    """构造单行点时行情记录。"""
    row = {
        "ts_code": ts_code,
        "trade_date": trade_date,
        "visible_at": visible_at,
        "revision_seq": revision_seq,
        "snapshot_rank": snapshot_rank,
        "record_id": record_id,
        "record_status": record_status,
        "close": close,
        "volume": volume,
    }
    row.update(kwargs)
    return row


@pytest.fixture
def make_pit_df():
    """工厂函数：返回一个构造点时行情 DataFrame 的 callable。

    用法::
        df = make_pit_df([row_dict, ...])
        # 或使用 _build_pit_row 辅助
    """
    def _factory(rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(rows)

    _factory.build_row = _build_pit_row
    return _factory


@pytest.fixture
def sample_bar_df(make_pit_df):
    """包含 ACTIVE/VOIDED 行、多 revision_seq、多 snapshot_rank、
    不同 visible_at 的样例日线行情 DataFrame。

    trade_date=2024-03-01, ts_code=000001.SZ
    """
    rows = [
        # 原始版本（revision_seq=1），visible_at=2024-03-01T17:00
        _build_pit_row(
            ts_code="000001.SZ", trade_date="2024-03-01",
            visible_at="2024-03-01T17:00:00+08:00",
            revision_seq=1, snapshot_rank=0, record_id=1,
            record_status="ACTIVE", close=10.00,
        ),
        # 修正版本（revision_seq=2），visible_at=2024-03-02T17:00（修正后可见）
        _build_pit_row(
            ts_code="000001.SZ", trade_date="2024-03-01",
            visible_at="2024-03-02T17:00:00+08:00",
            revision_seq=2, snapshot_rank=0, record_id=2,
            record_status="ACTIVE", close=10.50,
        ),
        # 2024-03-02 原始，正常记录
        _build_pit_row(
            ts_code="000001.SZ", trade_date="2024-03-02",
            visible_at="2024-03-02T17:00:00+08:00",
            revision_seq=1, snapshot_rank=0, record_id=3,
            record_status="ACTIVE", close=10.80,
        ),
        # 2024-03-03 被撤销：先有 ACTIVE，再追加 VOIDED
        _build_pit_row(
            ts_code="000001.SZ", trade_date="2024-03-03",
            visible_at="2024-03-03T17:00:00+08:00",
            revision_seq=1, snapshot_rank=0, record_id=4,
            record_status="ACTIVE", close=11.00,
        ),
        # VOIDED 追加（revision_seq=2），撤销上条
        _build_pit_row(
            ts_code="000001.SZ", trade_date="2024-03-03",
            visible_at="2024-03-04T09:00:00+08:00",
            revision_seq=2, snapshot_rank=0, record_id=5,
            record_status="VOIDED", close=11.00,
        ),
        # 多 snapshot_rank（snapshot_rank=1 先于 rank=2 可见）
        _build_pit_row(
            ts_code="000002.SZ", trade_date="2024-03-01",
            visible_at="2024-03-01T17:00:00+08:00",
            revision_seq=1, snapshot_rank=1, record_id=6,
            record_status="ACTIVE", close=20.00,
        ),
        _build_pit_row(
            ts_code="000002.SZ", trade_date="2024-03-01",
            visible_at="2024-03-01T17:00:00+08:00",
            revision_seq=1, snapshot_rank=2, record_id=7,
            record_status="ACTIVE", close=20.10,
        ),
    ]
    return make_pit_df(rows)


# ---------------------------------------------------------------------------
# 样例交易日历 fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_calendar():
    """包含 2024 年部分交易日的样例 TradeCalendar。"""
    from src.data.calendar import TradeCalendar
    # 构造 2024-01 至 2024-04 的交易日（排除周末，简化版）
    days = [
        "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
        "2024-02-01", "2024-02-02", "2024-02-05", "2024-02-06",
        "2024-02-07", "2024-02-08", "2024-02-09",
        "2024-02-18", "2024-02-19", "2024-02-20",
        "2024-03-01", "2024-03-04", "2024-03-05", "2024-03-06",
        "2024-03-07", "2024-03-08", "2024-03-11", "2024-03-12",
        "2024-03-13", "2024-03-14", "2024-03-15",
        "2024-03-18", "2024-03-19", "2024-03-20",
        "2024-04-01", "2024-04-02", "2024-04-03",
        "2024-06-28", "2024-06-30",
        "2024-09-27", "2024-09-30",
        "2024-12-30", "2024-12-31",
    ]
    return TradeCalendar(trading_days=days)


# ---------------------------------------------------------------------------
# 样例多源数据 fixture（用于裁决器测试）
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_multi_source_data():
    """三源样例数据（akshare/tushare/baostock）。"""
    return {
        "akshare": {"close": 10.01, "volume": 1000000.0, "amount": 10010000.0},
        "tushare": {"close": 10.01, "volume": 1000000.0, "amount": 10010000.0},
        "baostock": {"close": 10.01, "volume": 1000100.0, "amount": 10011000.0},  # volume 稍有差异
    }


# ---------------------------------------------------------------------------
# 样例财报 DataFrame fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_financials_df():
    """包含 OFFICIAL/EXPRESS/FORECAST 三阶段的样例财报 DataFrame。"""
    rows = [
        # 2023-12-31 年报（OFFICIAL），2024-03-20 公告，2024-03-21 可见
        {
            "ts_code": "000001.SZ", "end_date": "2023-12-31", "stage": "OFFICIAL",
            "visible_at": "2024-03-21T09:00:00+08:00",
            "revision_seq": 1, "snapshot_rank": 0, "record_id": 10,
            "record_status": "ACTIVE",
            "revenue": 100.0, "net_profit": 20.0, "ocf": 25.0,
            "total_assets": 1000.0, "total_equity": 300.0,
            "forecast_low": None,
        },
        # 2023-12-31 快报（EXPRESS），更早公告
        {
            "ts_code": "000001.SZ", "end_date": "2023-12-31", "stage": "EXPRESS",
            "visible_at": "2024-01-15T09:00:00+08:00",
            "revision_seq": 1, "snapshot_rank": 0, "record_id": 11,
            "record_status": "ACTIVE",
            "revenue": None, "net_profit": 19.5, "ocf": None,
            "total_assets": None, "total_equity": None,
            "forecast_low": None,
        },
        # 2023-09-30 季报（OFFICIAL），2023-10-30 可见
        {
            "ts_code": "000001.SZ", "end_date": "2023-09-30", "stage": "OFFICIAL",
            "visible_at": "2023-10-31T09:00:00+08:00",
            "revision_seq": 1, "snapshot_rank": 0, "record_id": 12,
            "record_status": "ACTIVE",
            "revenue": 75.0, "net_profit": 15.0, "ocf": 18.0,
            "total_assets": 950.0, "total_equity": 280.0,
            "forecast_low": None,
        },
        # 2023-06-30 半年报（OFFICIAL），2023-08-25 可见
        {
            "ts_code": "000001.SZ", "end_date": "2023-06-30", "stage": "OFFICIAL",
            "visible_at": "2023-08-26T09:00:00+08:00",
            "revision_seq": 1, "snapshot_rank": 0, "record_id": 13,
            "record_status": "ACTIVE",
            "revenue": 50.0, "net_profit": 10.0, "ocf": 12.0,
            "total_assets": 900.0, "total_equity": 260.0,
            "forecast_low": None,
        },
        # 2023-03-31 一季报（OFFICIAL），2023-04-28 可见
        {
            "ts_code": "000001.SZ", "end_date": "2023-03-31", "stage": "OFFICIAL",
            "visible_at": "2023-04-29T09:00:00+08:00",
            "revision_seq": 1, "snapshot_rank": 0, "record_id": 14,
            "record_status": "ACTIVE",
            "revenue": 24.0, "net_profit": 4.5, "ocf": 5.5,
            "total_assets": 880.0, "total_equity": 250.0,
            "forecast_low": None,
        },
        # 2024-03-31 一季报预告（FORECAST），2024-04-10 可见
        {
            "ts_code": "000001.SZ", "end_date": "2024-03-31", "stage": "FORECAST",
            "visible_at": "2024-04-11T09:00:00+08:00",
            "revision_seq": 1, "snapshot_rank": 0, "record_id": 15,
            "record_status": "ACTIVE",
            "revenue": None, "net_profit": None, "ocf": None,
            "total_assets": None, "total_equity": None,
            "forecast_low": 5.0,  # 预告下限
        },
    ]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 样例因子快照 DataFrame fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_snapshot_df():
    """包含三个 factor_variant 枚举的样例因子快照 DataFrame。"""
    rows = [
        {
            "ts_code": "000001.SZ", "trade_date": "2024-03-01",
            "factor_name": "momentum_20", "factor_variant": "raw",
            "factor_value": 0.05,
            "visible_at": "2024-03-01T17:00:00+08:00",
            "revision_seq": 1, "snapshot_rank": 0, "record_id": 100,
            "record_status": "ACTIVE",
        },
        {
            "ts_code": "000001.SZ", "trade_date": "2024-03-01",
            "factor_name": "momentum_20", "factor_variant": "processed",
            "factor_value": 1.23,
            "visible_at": "2024-03-01T17:00:00+08:00",
            "revision_seq": 1, "snapshot_rank": 0, "record_id": 101,
            "record_status": "ACTIVE",
        },
        {
            "ts_code": "000001.SZ", "trade_date": "2024-03-01",
            "factor_name": "momentum_20", "factor_variant": "orthogonal",
            "factor_value": 0.98,
            "visible_at": "2024-03-01T17:00:00+08:00",
            "revision_seq": 1, "snapshot_rank": 0, "record_id": 102,
            "record_status": "ACTIVE",
        },
        # 未来可见（visible_at 在 as_of 之后），应被过滤
        {
            "ts_code": "000001.SZ", "trade_date": "2024-03-01",
            "factor_name": "momentum_20", "factor_variant": "raw",
            "factor_value": 0.07,
            "visible_at": "2024-03-05T17:00:00+08:00",  # 未来
            "revision_seq": 2, "snapshot_rank": 0, "record_id": 103,
            "record_status": "ACTIVE",
        },
    ]
    return pd.DataFrame(rows)
