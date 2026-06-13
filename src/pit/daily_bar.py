"""点时日线行情查询（QS-C03 §3.3，§2）。

daily_bar_asof：给定 ts_code 和 as_of 时间点，取后复权日线行情。

复权逻辑（QS-C03 §2）：
  base_event_ver = MAX(event_ver) FROM corporate_action
                   WHERE ts_code=? AND visible_at <= as_of AND record_status='ACTIVE'
  adj_factor 从 adj_factor_pit 按 base_event_ver 获取整条序列。
  后复权价格 = raw_price × adj_factor。

行情 VOIDED 告警（QS-C03 §1.1）：最新版本为 VOIDED 的业务键触发告警，
不静默丢数据，不进入研究截面。
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

from src.pit.query_engine import CANONICAL_ORDER, canonical_select

logger = logging.getLogger(__name__)

# 后复权价格列（原始列 → 复权列）
_PRICE_COLS: list[str] = ["open", "high", "low", "close", "pre_close"]

# 默认 Parquet 路径模板（相对仓库根目录）
_DEFAULT_BAR_GLOB = "data/daily_bar/year=*/part-*.parquet"
_DEFAULT_ADJ_GLOB = "data/adj_factor_pit/year=*/ts_code=*/part-*.parquet"


def daily_bar_asof(
    ts_code: str,
    as_of: str,
    *,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    adjust: str = "qfq_pit",
    repo_root: Optional[Path] = None,
    sqlite_path: Optional[Path] = None,
    data_cut_id: Optional[int] = None,
    bar_df: Optional[pd.DataFrame] = None,
    adj_df: Optional[pd.DataFrame] = None,
    corp_action_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """点时取后复权日线行情（QS-C03 §3.3）。

    Args:
        ts_code:         股票代码，如 '000001.SZ'。
        as_of:           时间截止点，ISO datetime 字符串（含时区）。
        start_date:      起始日期（含），'YYYY-MM-DD'；None 不过滤。
        end_date:        结束日期（含），'YYYY-MM-DD'；None 不过滤。
        adjust:          复权方式：
                         'qfq_pit' = 点时前复权（QS-C03 §2，默认）；
                         'none'    = 不复权，返回原始价格。
        repo_root:       仓库根目录，用于定位 Parquet 文件。
        sqlite_path:     SQLite 路径（用于查询 corporate_action 表）。
        data_cut_id:     数据切割 ID，附加到 PIT_META。
        bar_df:          测试注入：直接传入日线 DataFrame，跳过 Parquet 读取。
        adj_df:          测试注入：直接传入复权因子 DataFrame，跳过 Parquet 读取。
        corp_action_df:  测试注入：直接传入公司行动 DataFrame，跳过 SQLite 查询。

    Returns:
        DataFrame，含 canonical 四键筛选后的日线行情，
        adjust='qfq_pit' 时附加 {col}_adj 列（后复权价格），
        附带 PIT_META 六字段。
        行情最新版本为 VOIDED 的 (ts_code, trade_date) 不在结果中（触发告警）。
    """
    # Step 1: 加载日线 Parquet
    if bar_df is None:
        bar_df = _load_bar_parquet(ts_code, start_date, end_date, repo_root)

    if bar_df.empty:
        logger.warning("daily_bar_asof: 无日线数据 ts_code=%s", ts_code)
        return pd.DataFrame()

    # 过滤当前 ts_code（若注入的 bar_df 含多只股票）
    if "ts_code" in bar_df.columns:
        bar_df = bar_df[bar_df["ts_code"] == ts_code].copy()

    # 过滤日期范围
    if start_date and "trade_date" in bar_df.columns:
        bar_df = bar_df[bar_df["trade_date"] >= start_date]
    if end_date and "trade_date" in bar_df.columns:
        bar_df = bar_df[bar_df["trade_date"] <= end_date]

    # 确保有 snapshot_rank
    if "snapshot_rank" not in bar_df.columns:
        bar_df = bar_df.copy()
        bar_df["snapshot_rank"] = 0

    # Step 2: canonical_select（过滤 visible_at <= as_of，四键排序，取最新 ACTIVE）
    result = canonical_select(
        df=bar_df,
        as_of=as_of,
        key_cols=["ts_code", "trade_date"],
        data_cut_id=data_cut_id,
        voided_callback=_voided_bar_alert,
    )

    if result.empty:
        return result

    # Step 3: PIT 复权（qfq_pit）
    if adjust == "qfq_pit":
        result = _apply_pit_adj_factor(
            result,
            ts_code=ts_code,
            as_of=as_of,
            repo_root=repo_root,
            sqlite_path=sqlite_path,
            adj_df=adj_df,
            corp_action_df=corp_action_df,
        )
        # 附加 corp_actions_known_ver
        # （已在 _apply_pit_adj_factor 中设置）

    return result


# ---------------------------------------------------------------------------
# 复权逻辑（QS-C03 §2）
# ---------------------------------------------------------------------------

def _apply_pit_adj_factor(
    df: pd.DataFrame,
    ts_code: str,
    as_of: str,
    repo_root: Optional[Path],
    sqlite_path: Optional[Path],
    adj_df: Optional[pd.DataFrame],
    corp_action_df: Optional[pd.DataFrame],
) -> pd.DataFrame:
    """应用 PIT 前复权因子（QS-C03 §2）。

    base_event_ver = MAX(event_ver) FROM corporate_action
                     WHERE ts_code=ts_code AND visible_at<=as_of
                     AND record_status='ACTIVE'
    """
    # Step A: 获取 base_event_ver
    base_event_ver = _get_base_event_ver(
        ts_code, as_of, sqlite_path, corp_action_df
    )
    df = df.copy()
    df["corp_actions_known_ver"] = base_event_ver

    if base_event_ver is None:
        # 无除权记录，adj_factor = 1.0
        for col in _PRICE_COLS:
            if col in df.columns:
                df[f"{col}_adj"] = df[col]
        return df

    # Step B: 加载复权因子序列
    if adj_df is None:
        adj_series = _load_adj_factor(ts_code, base_event_ver, repo_root)
    else:
        adj_series = adj_df[
            (adj_df["ts_code"] == ts_code)
            & (adj_df["base_event_ver"] == base_event_ver)
        ][["trade_date", "adj_factor"]].copy()

    if adj_series.empty:
        # 无复权因子序列，adj_factor = 1.0
        for col in _PRICE_COLS:
            if col in df.columns:
                df[f"{col}_adj"] = df[col]
        return df

    # Step C: merge 复权因子并计算后复权价格
    df = df.merge(
        adj_series[["trade_date", "adj_factor"]],
        on="trade_date",
        how="left",
    )
    df["adj_factor"] = df["adj_factor"].fillna(1.0)

    for col in _PRICE_COLS:
        if col in df.columns:
            df[f"{col}_adj"] = pd.to_numeric(df[col], errors="coerce") * df["adj_factor"]

    return df


def _get_base_event_ver(
    ts_code: str,
    as_of: str,
    sqlite_path: Optional[Path],
    corp_action_df: Optional[pd.DataFrame],
) -> Optional[int]:
    """获取 as_of 时刻的最新 base_event_ver（QS-C03 §2.1）。"""
    if corp_action_df is not None:
        # 测试注入路径
        active = corp_action_df[
            (corp_action_df["ts_code"] == ts_code)
            & (corp_action_df["visible_at"] <= as_of)
            & (corp_action_df["record_status"] == "ACTIVE")
        ]
        if active.empty:
            return None
        return int(active["event_ver"].max())

    if sqlite_path is None:
        return None

    try:
        with sqlite3.connect(str(sqlite_path)) as conn:
            row = conn.execute(
                """
                SELECT MAX(event_ver) AS base_event_ver
                FROM corporate_action
                WHERE ts_code = ?
                  AND visible_at <= ?
                  AND record_status = 'ACTIVE'
                """,
                (ts_code, as_of),
            ).fetchone()
        if row and row[0] is not None:
            return int(row[0])
    except sqlite3.OperationalError as exc:
        logger.warning(
            "_get_base_event_ver SQLite 查询失败 ts_code=%s error=%s", ts_code, exc
        )
    return None


def _load_bar_parquet(
    ts_code: str,
    start_date: Optional[str],
    end_date: Optional[str],
    repo_root: Optional[Path],
) -> pd.DataFrame:
    """从 Parquet 加载日线行情（按 year 分区）。"""
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[2]

    glob_pattern = str(repo_root / _DEFAULT_BAR_GLOB)

    try:
        import pyarrow.dataset as ds  # type: ignore

        dataset = ds.dataset(
            str(repo_root / "data" / "daily_bar"),
            format="parquet",
            partitioning="hive",
        )
        filters = [("ts_code", "=", ts_code)]
        if start_date:
            filters.append(("trade_date", ">=", start_date))
        if end_date:
            filters.append(("trade_date", "<=", end_date))

        return dataset.to_table(filter=_build_pyarrow_filter(filters)).to_pandas()
    except Exception:
        pass

    # 后备：glob + pandas read_parquet
    try:
        import glob as glob_mod

        files = glob_mod.glob(glob_pattern)
        if not files:
            return pd.DataFrame()
        frames = [pd.read_parquet(f) for f in files]
        df = pd.concat(frames, ignore_index=True)
        df = df[df["ts_code"] == ts_code]
        if start_date:
            df = df[df["trade_date"] >= start_date]
        if end_date:
            df = df[df["trade_date"] <= end_date]
        return df
    except Exception as exc:
        logger.warning("_load_bar_parquet 失败 ts_code=%s error=%s", ts_code, exc)
        return pd.DataFrame()


def _load_adj_factor(
    ts_code: str,
    base_event_ver: int,
    repo_root: Optional[Path],
) -> pd.DataFrame:
    """从 Parquet 加载复权因子序列。"""
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[2]

    try:
        import glob as glob_mod

        pattern = str(repo_root / _DEFAULT_ADJ_GLOB)
        files = glob_mod.glob(pattern)
        if not files:
            return pd.DataFrame()
        frames = [pd.read_parquet(f) for f in files]
        df = pd.concat(frames, ignore_index=True)
        return df[
            (df["ts_code"] == ts_code)
            & (df["base_event_ver"] == base_event_ver)
        ][["trade_date", "adj_factor"]]
    except Exception as exc:
        logger.warning(
            "_load_adj_factor 失败 ts_code=%s base_event_ver=%s error=%s",
            ts_code,
            base_event_ver,
            exc,
        )
        return pd.DataFrame()


def _build_pyarrow_filter(filters: list[tuple]):  # type: ignore[return]
    """将简单的 (col, op, val) 列表转换为 PyArrow 过滤表达式。"""
    try:
        import pyarrow.compute as pc  # type: ignore
        import pyarrow.dataset as ds  # type: ignore

        exprs = []
        for col, op, val in filters:
            field_expr = ds.field(col)
            if op == "=":
                exprs.append(field_expr == val)
            elif op == ">=":
                exprs.append(field_expr >= val)
            elif op == "<=":
                exprs.append(field_expr <= val)
        if not exprs:
            return None
        result = exprs[0]
        for expr in exprs[1:]:
            result = result & expr
        return result
    except Exception:
        return None


def _voided_bar_alert(voided_df: pd.DataFrame) -> None:
    """VOIDED 行情告警（QS-C03 §1.1）。"""
    for _, row in voided_df.iterrows():
        logger.error(
            "VOIDED_BAR_DETECTED ts_code=%s trade_date=%s visible_at=%s "
            "quality_flag=VOIDED_ALERT",
            row.get("ts_code", "?"),
            row.get("trade_date", "?"),
            row.get("visible_at", "?"),
        )
