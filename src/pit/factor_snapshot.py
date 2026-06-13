"""点时因子快照查询（QS-C03 §3.3，§4）。

factor_snapshot_asof：点时取因子截面快照，支持 factor_variant 三枚举。

factor_variant 枚举（QS-C03 §4.1，写死不扩展）：
  'raw'          : 原始因子值（归因用）
  'processed'    : 标准化/去极值后的因子值（训练用）
  'orthogonal'   : 正交化后的因子值（训练用）
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

from src.pit.query_engine import canonical_select

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# factor_variant 枚举（写死，QS-C03 §4.1）
# ---------------------------------------------------------------------------

FACTOR_VARIANT_RAW = "raw"
FACTOR_VARIANT_PROCESSED = "processed"
FACTOR_VARIANT_ORTHOGONAL = "orthogonal"

VALID_FACTOR_VARIANTS: frozenset[str] = frozenset(
    {FACTOR_VARIANT_RAW, FACTOR_VARIANT_PROCESSED, FACTOR_VARIANT_ORTHOGONAL}
)


# ---------------------------------------------------------------------------
# factor_snapshot_asof
# ---------------------------------------------------------------------------

def factor_snapshot_asof(
    ts_code: str,
    trade_date: str,
    as_of: str,
    *,
    factor_names: Optional[Sequence[str]] = None,
    factor_variant: str = FACTOR_VARIANT_RAW,
    data_cut_id: Optional[int] = None,
    snapshot_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """点时取因子截面快照（QS-C03 §3.3，§4）。

    对 (ts_code, trade_date, factor_name, factor_variant) 执行
    canonical_select，取 visible_at <= as_of 的最新 ACTIVE 版本。

    Args:
        ts_code:        股票代码，如 '000001.SZ'。
        trade_date:     因子计算日，'YYYY-MM-DD'。
        as_of:          时间截止点，ISO datetime 字符串。
        factor_names:   需要查询的因子名称列表；None 返回所有因子。
        factor_variant: 因子变体，枚举见 VALID_FACTOR_VARIANTS。
        data_cut_id:    数据切割 ID，附加到 PIT_META。
        snapshot_df:    测试注入：直接传入快照 DataFrame，跳过 Parquet 读取。

    Returns:
        DataFrame，每行为一个 (ts_code, trade_date, factor_name, factor_variant)
        的最新 ACTIVE 版本，含 PIT_META 六字段。

    Raises:
        ValueError: factor_variant 不在合法枚举内。
    """
    if factor_variant not in VALID_FACTOR_VARIANTS:
        raise ValueError(
            f"factor_variant={factor_variant!r} 不合法。"
            f"合法值：{sorted(VALID_FACTOR_VARIANTS)}"
        )

    if snapshot_df is None:
        snapshot_df = _load_snapshot_parquet(ts_code, trade_date)

    if snapshot_df.empty:
        return pd.DataFrame()

    df = snapshot_df.copy()

    # 过滤 ts_code
    if "ts_code" in df.columns:
        df = df[df["ts_code"] == ts_code]

    # 过滤 trade_date
    if "trade_date" in df.columns:
        df = df[df["trade_date"] == trade_date]

    # 过滤 factor_variant
    if "factor_variant" in df.columns:
        df = df[df["factor_variant"] == factor_variant]

    # 过滤 factor_names
    if factor_names is not None and "factor_name" in df.columns:
        df = df[df["factor_name"].isin(factor_names)]

    if df.empty:
        return pd.DataFrame()

    # 确保有 snapshot_rank
    if "snapshot_rank" not in df.columns:
        df = df.copy()
        df["snapshot_rank"] = 0

    # canonical_select：业务键 = (ts_code, trade_date, factor_name, factor_variant)
    result = canonical_select(
        df=df,
        as_of=as_of,
        key_cols=["ts_code", "trade_date", "factor_name", "factor_variant"],
        data_cut_id=data_cut_id,
    )

    return result


def factor_snapshot_batch_asof(
    ts_codes: Sequence[str],
    trade_date: str,
    as_of: str,
    *,
    factor_names: Optional[Sequence[str]] = None,
    factor_variant: str = FACTOR_VARIANT_RAW,
    data_cut_id: Optional[int] = None,
    snapshot_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """批量点时取因子截面快照（多只股票）。

    Args:
        ts_codes:       股票代码列表。
        trade_date:     因子计算日，'YYYY-MM-DD'。
        as_of:          时间截止点，ISO datetime 字符串。
        factor_names:   需要查询的因子名称列表；None 返回所有因子。
        factor_variant: 因子变体，见 VALID_FACTOR_VARIANTS。
        data_cut_id:    数据切割 ID，附加到 PIT_META。
        snapshot_df:    测试注入。

    Returns:
        合并后的 DataFrame，含所有请求股票的因子快照。
    """
    if factor_variant not in VALID_FACTOR_VARIANTS:
        raise ValueError(
            f"factor_variant={factor_variant!r} 不合法。"
            f"合法值：{sorted(VALID_FACTOR_VARIANTS)}"
        )

    if snapshot_df is None:
        snapshot_df = _load_snapshot_parquet_batch(list(ts_codes), trade_date)

    if snapshot_df.empty:
        return pd.DataFrame()

    df = snapshot_df.copy()

    # 过滤 ts_codes
    if "ts_code" in df.columns:
        df = df[df["ts_code"].isin(ts_codes)]

    # 过滤 trade_date
    if "trade_date" in df.columns:
        df = df[df["trade_date"] == trade_date]

    # 过滤 factor_variant
    if "factor_variant" in df.columns:
        df = df[df["factor_variant"] == factor_variant]

    # 过滤 factor_names
    if factor_names is not None and "factor_name" in df.columns:
        df = df[df["factor_name"].isin(factor_names)]

    if df.empty:
        return pd.DataFrame()

    if "snapshot_rank" not in df.columns:
        df = df.copy()
        df["snapshot_rank"] = 0

    result = canonical_select(
        df=df,
        as_of=as_of,
        key_cols=["ts_code", "trade_date", "factor_name", "factor_variant"],
        data_cut_id=data_cut_id,
    )

    return result


# ---------------------------------------------------------------------------
# 内部：Parquet 加载
# ---------------------------------------------------------------------------

def _load_snapshot_parquet(ts_code: str, trade_date: str) -> pd.DataFrame:
    """从 Parquet 加载单只股票的因子快照。"""
    return _load_snapshot_parquet_batch([ts_code], trade_date)


def _load_snapshot_parquet_batch(
    ts_codes: list[str],
    trade_date: str,
) -> pd.DataFrame:
    """从 Parquet 批量加载因子快照（按 year 分区）。"""
    repo_root = Path(__file__).resolve().parents[2]
    year = trade_date[:4] if trade_date else "*"
    glob_pattern = str(
        repo_root / "data" / "factor_snapshot" / f"year={year}" / "part-*.parquet"
    )

    try:
        import glob as glob_mod

        files = glob_mod.glob(glob_pattern)
        if not files:
            # 尝试全通配
            glob_all = str(repo_root / "data" / "factor_snapshot" / "year=*" / "part-*.parquet")
            files = glob_mod.glob(glob_all)
        if not files:
            return pd.DataFrame()

        frames = [pd.read_parquet(f) for f in files]
        df = pd.concat(frames, ignore_index=True)

        if "ts_code" in df.columns:
            df = df[df["ts_code"].isin(ts_codes)]
        if "trade_date" in df.columns and trade_date:
            df = df[df["trade_date"] == trade_date]

        return df.copy()
    except Exception as exc:
        logger.warning(
            "_load_snapshot_parquet_batch 失败 ts_codes=%s error=%s",
            ts_codes[:3],
            exc,
        )
        return pd.DataFrame()
