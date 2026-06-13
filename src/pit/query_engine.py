"""点时查询引擎核心（QS-C03 §3，QS-C01 §1 铁律③）。

canonical 四键排序：
  ORDER BY visible_at DESC, revision_seq DESC, snapshot_rank DESC, record_id DESC

CANONICAL_ORDER 常量：系统中所有点时查询的唯一合法排序依据，不可更改。

canonical_select：
  1. 过滤 visible_at <= as_of
  2. 按四键排序
  3. 每个业务键取第一行（ROW_NUMBER=1）
  4. 最新行为 VOIDED → 该键返空（但触发告警，不静默丢弃）

绝不先 WHERE 过滤 VOIDED，必须取全候选后按业务键取最新，
最新版本为 VOIDED 则返空（QS-C03 §1.2）。
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# canonical 四键排序常量（QS-C03 §3.2，不可更改）
# ---------------------------------------------------------------------------

CANONICAL_ORDER: list[str] = [
    "visible_at DESC",
    "revision_seq DESC",
    "snapshot_rank DESC",
    "record_id DESC",
]

# PIT_META 附加字段（QS-C03 §3.4）
PIT_META_COLUMNS: list[str] = [
    "pit_as_of",
    "pit_data_cut_id",
    "pit_visible_at",
    "pit_revision_seq",
    "pit_snapshot_rank",
    "corp_actions_known_ver",
]


# ---------------------------------------------------------------------------
# 纯函数：canonical_select
# ---------------------------------------------------------------------------

def canonical_select(
    df: pd.DataFrame,
    as_of: str,
    key_cols: Sequence[str],
    *,
    data_cut_id: Optional[int] = None,
    voided_callback: Optional[callable] = None,  # type: ignore[type-arg]
) -> pd.DataFrame:
    """给定 as_of（visible_at <= as_of），按四键排序取每个实体最新 ACTIVE 行。

    绝不先 WHERE 过滤 VOIDED/was_revised：先取全候选，再按业务键取最新，
    最新版本为 VOIDED 则该键返空并触发 voided_callback（QS-C03 §1.2）。

    Args:
        df:              候选 DataFrame，须包含以下列：
                         - visible_at (str, ISO datetime)
                         - revision_seq (int)
                         - snapshot_rank (int)
                         - record_id (int or float)
                         - record_status (str: 'ACTIVE'|'VOIDED')
                         - 各 key_cols 列
        as_of:           时间截止点，ISO datetime 字符串（含时区）。
        key_cols:        业务键列名序列，如 ['ts_code', 'trade_date']。
        data_cut_id:     可选：附加到 pit_data_cut_id 列的数据切割 ID。
        voided_callback: 可选：callable(voided_df)，在发现 VOIDED 最新版本时调用。

    Returns:
        每个业务键的最新 ACTIVE 行 DataFrame，附带 PIT_META 元字段。
        若某键最新版本为 VOIDED，该键不在结果中（但 voided_callback 被调用）。

    Raises:
        KeyError: df 缺少 canonical 四键所需列。
    """
    _validate_required_columns(df)

    if df.empty:
        return _empty_with_meta(df)

    # Step 1: 过滤 visible_at <= as_of
    candidates = df[df["visible_at"] <= as_of].copy()

    if candidates.empty:
        return _empty_with_meta(df)

    # Step 2: 按四键排序（降序），确定性
    candidates = candidates.sort_values(
        by=["visible_at", "revision_seq", "snapshot_rank", "record_id"],
        ascending=[False, False, False, False],
        kind="mergesort",  # 稳定排序
    )

    # Step 3: 每个业务键取第一行（最新版本）
    key_list = list(key_cols)
    latest = candidates.groupby(key_list, sort=False).first().reset_index()

    # 但 groupby().first() 不保留排序顺序，改用 drop_duplicates
    # 重新用 drop_duplicates 在已排序的 df 上取第一行，更可靠
    latest = candidates.drop_duplicates(subset=key_list, keep="first")

    # Step 4: 分离 ACTIVE 与 VOIDED
    active_mask = latest["record_status"] == "ACTIVE"
    voided_mask = latest["record_status"] == "VOIDED"

    voided_df = latest[voided_mask]
    active_df = latest[active_mask].copy()

    # 触发 VOIDED 回调告警（不静默丢弃）
    if not voided_df.empty:
        if voided_callback is not None:
            voided_callback(voided_df)
        else:
            _default_voided_alert(voided_df)

    # Step 5: 附加 PIT_META
    active_df["pit_as_of"] = as_of
    active_df["pit_data_cut_id"] = data_cut_id
    # 命中行自身的 visible_at / revision_seq / snapshot_rank
    active_df["pit_visible_at"] = active_df["visible_at"]
    active_df["pit_revision_seq"] = active_df["revision_seq"]
    active_df["pit_snapshot_rank"] = active_df.get("snapshot_rank", pd.Series(0, index=active_df.index))
    active_df["corp_actions_known_ver"] = None  # 非复权查询置 NULL

    return active_df.reset_index(drop=True)


def _validate_required_columns(df: pd.DataFrame) -> None:
    """检查 canonical 查询所需列是否存在。"""
    required = {"visible_at", "revision_seq", "record_status"}
    missing = required - set(df.columns)
    if missing:
        raise KeyError(
            f"canonical_select: DataFrame 缺少必要列 {missing}。"
            "请确保数据包含 visible_at / revision_seq / record_status。"
        )


def _empty_with_meta(df: pd.DataFrame) -> pd.DataFrame:
    """返回含 PIT_META 列的空 DataFrame（保留原有列结构）。"""
    result = df.iloc[0:0].copy()
    for col in PIT_META_COLUMNS:
        if col not in result.columns:
            result[col] = pd.Series(dtype="object")
    return result


def _default_voided_alert(voided_df: pd.DataFrame) -> None:
    """VOIDED 行情默认告警（QS-C03 §1.1：不静默丢数据）。"""
    for _, row in voided_df.iterrows():
        logger.error(
            "VOIDED_BAR_DETECTED ts_code=%s trade_date=%s visible_at=%s "
            "revision_seq=%s quality_flag=VOIDED_ALERT",
            row.get("ts_code", "?"),
            row.get("trade_date", "?"),
            row.get("visible_at", "?"),
            row.get("revision_seq", "?"),
        )


# ---------------------------------------------------------------------------
# 确定性重跑断言工具（QS-C03 §12.4）
# ---------------------------------------------------------------------------

def assert_deterministic_rerun(
    query_fn: callable,  # type: ignore[type-arg]
    *args,
    **kwargs,
) -> None:
    """同一查询函数执行两次，断言结果逐行一致（验证 canonical 四键的确定性）。

    Args:
        query_fn: 待验证的查询函数（返回 pd.DataFrame）。
        *args, **kwargs: 传递给 query_fn 的参数。

    Raises:
        AssertionError: 两次结果不一致时抛出。
    """
    result1 = query_fn(*args, **kwargs)
    result2 = query_fn(*args, **kwargs)
    pd.testing.assert_frame_equal(
        result1.reset_index(drop=True),
        result2.reset_index(drop=True),
        check_exact=True,
        check_like=False,
    )
