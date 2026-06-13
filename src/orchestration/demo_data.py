"""合成 A 股式数据种子（QS-E09 §1）。

生成约 30–40 只虚拟标的、~2.5 年日频行情数据，
含真实涨跌停约束（±10%，ST股±5%）、随机停牌、复权因子，
financials_pit 季度财报（带 visible_at 滞后），trade_calendar。

数据写入：
  - DuckDB/Parquet: data/<QUANTSOLO_DATA_ROOT>/daily_bar/year={YYYY}/
  - SQLite:         db/quant.db（trade_calendar、financials_pit）

列名/类型与 tests/conftest.py 夹具（sample_bar_df/sample_financials_df）一致，
确保真实 query_engine 能读。

固定随机种子：保证可复现。
"""
from __future__ import annotations

import logging
import os
import random
import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量与路径
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = REPO_ROOT / "data"
DEFAULT_DB_PATH = REPO_ROOT / "db" / "quant.db"

RANDOM_SEED = 42

# 30 只虚拟标的（格式与真实 ts_code 一致）
DEMO_STOCKS = [
    "000001.SZ", "000002.SZ", "000004.SZ", "000005.SZ", "000006.SZ",
    "000007.SZ", "000008.SZ", "000009.SZ", "000010.SZ", "000011.SZ",
    "000012.SZ", "000014.SZ", "000016.SZ", "000017.SZ", "000019.SZ",
    "000020.SZ", "000021.SZ", "000023.SZ", "000025.SZ", "000026.SZ",
    "000027.SZ", "000028.SZ", "000029.SZ", "000030.SZ", "000031.SZ",
    "000032.SZ", "000033.SZ", "000034.SZ", "000035.SZ", "000036.SZ",
]

# 行业映射（6 个行业，每只股票分配一个）
INDUSTRIES = ["金融", "科技", "消费", "医药", "工业", "能源"]

DEMO_START_DATE = date(2022, 1, 4)  # ~2.5 年
DEMO_END_DATE = date(2024, 6, 28)

# 上市信息（虚构上市日期）
LISTING_DATES = {
    code: date(2015, 1, 1) + timedelta(days=i * 30)
    for i, code in enumerate(DEMO_STOCKS)
}

INDUSTRY_MAP = {
    code: INDUSTRIES[i % len(INDUSTRIES)]
    for i, code in enumerate(DEMO_STOCKS)
}


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _get_data_root() -> Path:
    root = Path(os.getenv("QUANTSOLO_DATA_ROOT", str(DEFAULT_DATA_ROOT)))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _get_db_path() -> Path:
    db = DEFAULT_DB_PATH
    db.parent.mkdir(parents=True, exist_ok=True)
    return db


def _is_trading_day(d: date) -> bool:
    """简化版：排除周六周日（不处理节假日，demo用途）。"""
    return d.weekday() < 5


def get_trading_days(start: date, end: date) -> list[date]:
    """生成交易日序列（排除周末）。"""
    current = start
    days = []
    while current <= end:
        if _is_trading_day(current):
            days.append(current)
        current += timedelta(days=1)
    return days


# ---------------------------------------------------------------------------
# 行情数据生成
# ---------------------------------------------------------------------------

def _generate_price_series(
    n_days: int,
    start_price: float,
    rng: np.random.Generator,
    is_st: bool = False,
) -> np.ndarray:
    """生成带涨跌停约束的价格序列。"""
    limit_pct = 0.05 if is_st else 0.10
    prices = np.zeros(n_days)
    prices[0] = start_price

    for i in range(1, n_days):
        # 随机日收益率（正态分布，年化波动率约25%）
        daily_ret = rng.normal(0.0003, 0.016)
        # 涨跌停约束
        daily_ret = np.clip(daily_ret, -limit_pct, limit_pct)
        prices[i] = round(prices[i-1] * (1 + daily_ret), 2)
        prices[i] = max(prices[i], 0.01)  # 不低于0.01元

    return prices


def generate_daily_bar_df(
    rng: np.random.Generator,
    trading_days: list[date],
) -> pd.DataFrame:
    """生成全部标的的日线行情 DataFrame（点时表格式）。"""
    rows = []
    record_id = 1

    for i, ts_code in enumerate(DEMO_STOCKS):
        start_price = 5.0 + rng.random() * 95  # 5~100元
        # ~5% 概率为ST
        is_st = (i % 20 == 0)

        prices = _generate_price_series(len(trading_days), start_price, rng, is_st)

        # 随机复权因子（~50%有分红，每年一次）
        adj_factor = 1.0
        adj_changes = {}
        for d in trading_days:
            # 每年随机一天有除权
            if d.month == 6 and d.day == 15 and rng.random() < 0.5:
                adj_factor *= rng.uniform(0.85, 0.99)
            adj_changes[d] = round(adj_factor, 6)

        # 随机停牌（每年约2次，每次1-3天）
        suspended_days: set[date] = set()
        for _ in range(int(len(trading_days) / 120)):
            start_idx = rng.integers(0, len(trading_days) - 3)
            duration = rng.integers(1, 4)
            for j in range(duration):
                if start_idx + j < len(trading_days):
                    suspended_days.add(trading_days[start_idx + j])

        for j, trade_day in enumerate(trading_days):
            if trade_day in suspended_days:
                continue  # 停牌日不生成行情

            close = prices[j]
            pre_close = prices[j-1] if j > 0 else close
            high = round(close * (1 + rng.uniform(0, 0.03)), 2)
            low = round(close * (1 - rng.uniform(0, 0.03)), 2)
            open_p = round(pre_close * (1 + rng.normal(0, 0.005)), 2)
            high = max(high, open_p, close)
            low = min(low, open_p, close)

            volume = int(rng.lognormal(13, 0.8))  # ~万手量级
            amount = round(close * volume * 100, 2)  # 元
            close_adj = round(close * adj_changes[trade_day], 4)

            # visible_at = 当日 17:00 北京时间
            visible_at = f"{trade_day}T17:00:00+08:00"

            rows.append({
                "ts_code": ts_code,
                "trade_date": str(trade_day),
                "open": open_p,
                "high": high,
                "low": low,
                "close": close,
                "pre_close": pre_close,
                "volume": float(volume),
                "amount": amount,
                "close_adj": close_adj,
                "adj_factor": adj_changes[trade_day],
                "is_st": is_st,
                "industry": INDUSTRY_MAP[ts_code],
                "listing_date": str(LISTING_DATES[ts_code]),
                # 点时表必要字段
                "visible_at": visible_at,
                "revision_seq": 1,
                "snapshot_rank": 0,
                "record_id": record_id,
                "record_status": "ACTIVE",
                # 行情标准字段 ingested_at
                "ingested_at": datetime.now(timezone.utc).isoformat(),
                # 数据质量
                "quality_flag": None,
                "source": "demo",
            })
            record_id += 1

    df = pd.DataFrame(rows)
    logger.info("生成日线行情: %d 行，%d 只标的，%d 个交易日",
                len(df), len(DEMO_STOCKS), len(trading_days))
    return df


# ---------------------------------------------------------------------------
# 财报数据生成（PIT 格式）
# ---------------------------------------------------------------------------

def generate_financials_df(
    rng: np.random.Generator,
    trading_days: list[date],
) -> pd.DataFrame:
    """生成 financials_pit 格式的季度财报 DataFrame。

    visible_at 遵循真实滞后规律：
      - 一季报（3-31）：4月底公告
      - 半年报（6-30）：8月底公告
      - 三季报（9-30）：10月底公告
      - 年报（12-31）：次年3-4月公告
    """
    rows = []
    record_id = 10001

    # 财报期末日与 visible_at 延迟
    quarters = []
    for year in range(DEMO_START_DATE.year, DEMO_END_DATE.year + 1):
        quarters.extend([
            (date(year, 3, 31), date(year, 4, 30), "Q1"),
            (date(year, 6, 30), date(year, 8, 31), "H1"),
            (date(year, 9, 30), date(year, 10, 31), "Q3"),
            (date(year, 12, 31), date(year + 1, 3, 31), "FY"),
        ])

    all_dates = set(str(d) for d in trading_days)

    for ts_code in DEMO_STOCKS:
        base_revenue = rng.uniform(50, 500)  # 亿元
        growth_rate = rng.uniform(0.02, 0.15)

        for end_date, visible_base, period_tag in quarters:
            # 检查：不生成超出演示区间的数据
            if visible_base > DEMO_END_DATE + timedelta(days=180):
                continue

            # 随机延迟公告（0-20天）
            delay_days = int(rng.uniform(0, 20))
            visible_date = visible_base + timedelta(days=delay_days)
            visible_at = f"{visible_date}T09:00:00+08:00"

            # 基于时间增长的财务数据
            years_elapsed = (end_date - date(2022, 1, 1)).days / 365
            revenue = round(base_revenue * (1 + growth_rate) ** years_elapsed, 2)
            net_profit = round(revenue * rng.uniform(0.08, 0.20), 2)
            ocf = round(net_profit * rng.uniform(0.8, 1.5), 2)
            total_assets = round(revenue * rng.uniform(2, 5), 2)
            total_equity = round(total_assets * rng.uniform(0.3, 0.7), 2)

            rows.append({
                "ts_code": ts_code,
                "end_date": str(end_date),
                "stage": "OFFICIAL",
                "visible_at": visible_at,
                "revision_seq": 1,
                "snapshot_rank": 0,
                "record_id": record_id,
                "record_status": "ACTIVE",
                "revenue": revenue,
                "net_profit": net_profit,
                "ocf": ocf,
                "total_assets": total_assets,
                "total_equity": total_equity,
                "forecast_low": None,
                "period_tag": period_tag,
            })
            record_id += 1

            # 快报（EXPRESS）：提前30天（50%概率）
            if rng.random() < 0.5:
                express_visible = visible_base - timedelta(days=30)
                rows.append({
                    "ts_code": ts_code,
                    "end_date": str(end_date),
                    "stage": "EXPRESS",
                    "visible_at": f"{express_visible}T09:00:00+08:00",
                    "revision_seq": 1,
                    "snapshot_rank": 0,
                    "record_id": record_id,
                    "record_status": "ACTIVE",
                    "revenue": None,
                    "net_profit": round(net_profit * rng.uniform(0.9, 1.1), 2),
                    "ocf": None,
                    "total_assets": None,
                    "total_equity": None,
                    "forecast_low": None,
                    "period_tag": period_tag,
                })
                record_id += 1

    df = pd.DataFrame(rows)
    logger.info("生成财报数据: %d 行，%d 只标的", len(df), len(DEMO_STOCKS))
    return df


# ---------------------------------------------------------------------------
# 因子快照数据生成
# ---------------------------------------------------------------------------

def generate_factor_snapshot_df(
    bar_df: pd.DataFrame,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """基于行情数据生成因子快照（momentum_20 为主要因子）。"""
    rows = []
    record_id = 20001

    # 按 trade_date 分组，计算动量因子
    bar_sorted = bar_df.sort_values(["ts_code", "trade_date"])

    for ts_code in DEMO_STOCKS:
        stock_bars = bar_sorted[bar_sorted["ts_code"] == ts_code].copy()
        if len(stock_bars) < 21:
            continue

        closes = stock_bars["close"].values
        trade_dates = stock_bars["trade_date"].values
        visible_ats = stock_bars["visible_at"].values

        # 计算20日动量因子
        for i in range(20, len(closes)):
            momentum_20 = (closes[i] / closes[i - 20]) - 1.0 if closes[i - 20] > 0 else 0.0
            # 添加一点噪声
            momentum_20_noisy = momentum_20 + rng.normal(0, 0.005)

            for variant, value in [
                ("raw", momentum_20),
                ("processed", momentum_20_noisy),
                ("orthogonal", momentum_20_noisy * rng.uniform(0.9, 1.1)),
            ]:
                rows.append({
                    "ts_code": ts_code,
                    "trade_date": str(trade_dates[i]),
                    "factor_name": "momentum_20",
                    "factor_variant": variant,
                    "factor_value": round(float(value), 6),
                    "visible_at": str(visible_ats[i]),
                    "revision_seq": 1,
                    "snapshot_rank": 0,
                    "record_id": record_id,
                    "record_status": "ACTIVE",
                })
                record_id += 1

    df = pd.DataFrame(rows)
    logger.info("生成因子快照: %d 行", len(df))
    return df


# ---------------------------------------------------------------------------
# 持久化
# ---------------------------------------------------------------------------

def _write_parquet(df: pd.DataFrame, data_root: Path, table_name: str) -> None:
    """写入 Parquet 文件（按 year 分区）。"""
    if df.empty:
        return

    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
        has_pyarrow = True
    except ImportError:
        has_pyarrow = False

    if "trade_date" in df.columns:
        date_col = "trade_date"
    elif "end_date" in df.columns:
        date_col = "end_date"
    else:
        date_col = None

    if date_col:
        years = df[date_col].str[:4].unique()
        for year in sorted(years):
            year_df = df[df[date_col].str[:4] == year]
            out_dir = data_root / table_name / f"year={year}"
            out_dir.mkdir(parents=True, exist_ok=True)
            if has_pyarrow:
                out_path = out_dir / "part-demo.parquet"
                table = pa.Table.from_pandas(year_df.reset_index(drop=True), preserve_index=False)
                pq.write_table(table, out_path)
            else:
                out_path = out_dir / "demo.csv"
                year_df.to_csv(out_path, index=False)
    else:
        out_dir = data_root / table_name
        out_dir.mkdir(parents=True, exist_ok=True)
        if has_pyarrow:
            out_path = out_dir / "part-demo.parquet"
            table = pa.Table.from_pandas(df.reset_index(drop=True), preserve_index=False)
            pq.write_table(table, out_path)
        else:
            out_path = out_dir / "demo.csv"
            df.to_csv(out_path, index=False)

    logger.info("写入 %s: %d 行 -> %s", table_name, len(df), data_root / table_name)


def _write_sqlite(db_path: Path, table: str, df: pd.DataFrame, if_exists: str = "replace") -> None:
    """写入 SQLite 表（只追加）。"""
    if df.empty:
        return
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    try:
        df.to_sql(table, conn, if_exists=if_exists, index=False)
        conn.commit()
        logger.info("写入 SQLite %s: %d 行 -> %s", table, len(df), db_path)
    finally:
        conn.close()


def _write_trade_calendar(db_path: Path, trading_days: list[date]) -> None:
    """写入交易日历到 SQLite。"""
    rows = [
        {
            "trade_date": str(d),
            "is_trading_day": 1,
            "exchange": "SSE",
        }
        for d in trading_days
    ]
    df = pd.DataFrame(rows)
    _write_sqlite(db_path, "trade_calendar", df, if_exists="replace")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def seed_demo_data(
    data_root: Optional[Path] = None,
    db_path: Optional[Path] = None,
    force: bool = False,
) -> dict:
    """生成并写入全部合成演示数据。

    Args:
        data_root: 数据根目录（默认 QUANTSOLO_DATA_ROOT 环境变量或 ./data）
        db_path:   SQLite 路径（默认 ./db/quant.db）
        force:     True = 强制重新生成（即使数据已存在）

    Returns:
        {
            'trading_days': int,  # 生成的交易日数
            'stocks': int,        # 标的数量
            'bar_rows': int,      # 日线行情行数
            'financial_rows': int,# 财报行数
            'factor_rows': int,   # 因子快照行数
            'data_root': str,     # 数据根目录
            'db_path': str,       # SQLite 路径
        }
    """
    if data_root is None:
        data_root = _get_data_root()
    if db_path is None:
        db_path = _get_db_path()

    # 检查是否已有数据
    marker = data_root / ".demo_seeded"
    if marker.exists() and not force:
        logger.info("演示数据已存在，跳过生成（使用 force=True 强制重新生成）")
        return {"status": "already_seeded", "data_root": str(data_root), "db_path": str(db_path)}

    logger.info("开始生成演示数据... (种子=%d)", RANDOM_SEED)
    rng = np.random.default_rng(RANDOM_SEED)

    # 1. 生成交易日历
    trading_days = get_trading_days(DEMO_START_DATE, DEMO_END_DATE)
    logger.info("交易日: %d 天（%s ~ %s）", len(trading_days), DEMO_START_DATE, DEMO_END_DATE)

    # 2. 生成日线行情
    bar_df = generate_daily_bar_df(rng, trading_days)

    # 3. 生成财报数据
    fin_df = generate_financials_df(rng, trading_days)

    # 4. 生成因子快照
    snap_df = generate_factor_snapshot_df(bar_df, rng)

    # 5. 持久化
    _write_parquet(bar_df, data_root, "daily_bar")
    _write_parquet(fin_df, data_root, "financials_pit")
    _write_parquet(snap_df, data_root, "factor_snapshot")

    _write_trade_calendar(db_path, trading_days)
    _write_sqlite(db_path, "demo_stocks", pd.DataFrame([
        {"ts_code": code, "industry": INDUSTRY_MAP[code],
         "listing_date": str(LISTING_DATES[code]), "is_st": (i % 20 == 0)}
        for i, code in enumerate(DEMO_STOCKS)
    ]), if_exists="replace")

    # 6. 写入完成标记
    marker.write_text(f"seeded at {datetime.now(timezone.utc).isoformat()}\n")

    result = {
        "status": "ok",
        "trading_days": len(trading_days),
        "stocks": len(DEMO_STOCKS),
        "bar_rows": len(bar_df),
        "financial_rows": len(fin_df),
        "factor_rows": len(snap_df),
        "data_root": str(data_root),
        "db_path": str(db_path),
    }
    logger.info("演示数据生成完成: %s", result)
    return result


def load_demo_bar_df(
    data_root: Optional[Path] = None,
    ts_codes: Optional[list[str]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """从磁盘加载演示日线行情 DataFrame。"""
    if data_root is None:
        data_root = _get_data_root()

    bar_dir = data_root / "daily_bar"
    if not bar_dir.exists():
        raise FileNotFoundError(f"演示数据不存在: {bar_dir}，请先运行 seed-demo")

    dfs = []
    for fpath in sorted(bar_dir.rglob("*.parquet")):
        try:
            import pyarrow.parquet as pq
            dfs.append(pq.read_table(fpath).to_pandas())
        except ImportError:
            pass
    for fpath in sorted(bar_dir.rglob("*.csv")):
        dfs.append(pd.read_csv(fpath))

    if not dfs:
        raise FileNotFoundError(f"日线行情文件为空: {bar_dir}")

    df = pd.concat(dfs, ignore_index=True)

    if ts_codes:
        df = df[df["ts_code"].isin(ts_codes)]
    if start_date:
        df = df[df["trade_date"] >= start_date]
    if end_date:
        df = df[df["trade_date"] <= end_date]

    return df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


def load_demo_factor_df(
    data_root: Optional[Path] = None,
    ts_codes: Optional[list[str]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    factor_variant: str = "processed",
) -> pd.DataFrame:
    """从磁盘加载演示因子快照 DataFrame。"""
    if data_root is None:
        data_root = _get_data_root()

    snap_dir = data_root / "factor_snapshot"
    if not snap_dir.exists():
        raise FileNotFoundError(f"因子快照不存在: {snap_dir}")

    dfs = []
    for fpath in sorted(snap_dir.rglob("*.parquet")):
        try:
            import pyarrow.parquet as pq
            dfs.append(pq.read_table(fpath).to_pandas())
        except ImportError:
            pass
    for fpath in sorted(snap_dir.rglob("*.csv")):
        dfs.append(pd.read_csv(fpath))

    if not dfs:
        raise FileNotFoundError(f"因子快照文件为空: {snap_dir}")

    df = pd.concat(dfs, ignore_index=True)
    df = df[df["factor_variant"] == factor_variant]

    if ts_codes:
        df = df[df["ts_code"].isin(ts_codes)]
    if start_date:
        df = df[df["trade_date"] >= start_date]
    if end_date:
        df = df[df["trade_date"] <= end_date]

    return df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
